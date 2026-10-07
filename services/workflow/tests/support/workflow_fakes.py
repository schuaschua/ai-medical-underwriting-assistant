"""In-memory stand-ins for `workflow`'s store and engine, and builders of stage results.

Unit tests use them in place of PostgreSQL and the Durable Task Scheduler
(coding-style rule 23). They live with the tests, on pytest's `pythonpath`,
so the service package and its image hold no test code.
"""

import json
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import httpx

from contracts.audit import AuditAction, AuditRecord, RouteDetail
from contracts.enums import CaseStatus, ClassifierContender, Decision, PageStatus
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.classification import ClassificationResult
from contracts.models.extraction import FactSetResult
from contracts.models.intake import RedactionResult
from contracts.models.verdict import VerdictRunResult
from contracts.models.workflow import AuditTrail, CaseProgress, PageProgress
from workflow.domain.case_status import case_status_after_gate, case_status_following
from workflow.domain.decisions import case_takes_decisions
from workflow.domain.entities import CaseRecord, PageDecision, SettledCase
from workflow.domain.ports import EngineState
from workflow.domain.recording import (
    Decided,
    DecisionOutcome,
    Recording,
    RecordOutcome,
)
from workflow.domain.transitions import (
    CASE_STATUSES_TAKING_RESULTS,
    CASE_TRANSITIONS,
    PAGE_TRANSITIONS,
    REDACTION_TRANSITIONS,
)

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
OCCURRED_AT = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


class StoreDown(Exception):
    """What a failing stand-in raises. Its message must never reach a log or a body."""

    def __init__(self) -> None:
        super().__init__("secret-store-detail host=10.0.0.1 SELECT 1")


@dataclass
class MemoryPage:
    case_id: str
    page_number: int
    page_status: PageStatus


@dataclass
class MemoryCaseStore:
    """Keeps to the store's contract: a recording is written whole, once, or not at all."""

    cases: dict[str, CaseRecord] = field(default_factory=dict)
    pages: dict[str, MemoryPage] = field(default_factory=dict)
    events: list[tuple[datetime, Recording]] = field(default_factory=list)
    decisions: list[PageDecision] = field(default_factory=list)
    fail: bool = False
    # The audit event cannot be written: everything before it is undone.
    fail_audit_insert: bool = False

    async def start(self, case: CaseRecord) -> CaseRecord:
        if self.fail:
            raise StoreDown
        return self.cases.setdefault(case.case_id, case)

    async def status(self, case_id: str) -> CaseStatus | None:
        if self.fail:
            raise StoreDown
        case = self.cases.get(case_id)
        return case.case_status if case is not None else None

    async def record(
        self, recording: Recording, recorded_at: datetime
    ) -> RecordOutcome:
        if self.fail:
            raise StoreDown
        audit = recording.audit
        case = self.cases.get(audit.case_id)
        if case is None:
            return RecordOutcome.UNKNOWN_CASE
        key = (audit.case_id, audit.page_id, audit.action, audit.ref)
        if any(
            (e.audit.case_id, e.audit.page_id, e.audit.action, e.audit.ref) == key
            for _, e in self.events
        ):
            return RecordOutcome.DUPLICATE
        if case.case_status not in CASE_STATUSES_TAKING_RESULTS:
            return RecordOutcome.CASE_FAILED
        change = recording.page_change
        if change is not None:
            page = self.pages.get(change.page_id)
            if page is None or page.case_id != audit.case_id:
                return RecordOutcome.UNKNOWN_PAGE
            if change.page_status not in PAGE_TRANSITIONS[page.page_status] or (
                change.only_from not in (None, page.page_status)
            ):
                return RecordOutcome.OUT_OF_ORDER
        if recording.new_pages and any(
            page.case_id == audit.case_id for page in self.pages.values()
        ):
            return RecordOutcome.PAGES_ALREADY_TRACKED
        if (
            recording.case_status is not None
            and recording.case_status not in CASE_TRANSITIONS[case.case_status]
        ) or (
            recording.redaction_status is not None
            and recording.redaction_status
            not in REDACTION_TRANSITIONS[case.redaction_status]
        ):
            return RecordOutcome.OUT_OF_ORDER
        if self.fail_audit_insert:
            # Nothing above has been applied yet, as after a rollback.
            raise StoreDown
        if change is not None:
            self.pages[change.page_id].page_status = change.page_status
        for new_page in recording.new_pages:
            self.pages[new_page.page_id] = MemoryPage(
                audit.case_id, new_page.page_number, PageStatus.UPLOADED
            )
        if recording.case_status is not None:
            case = replace(case, case_status=recording.case_status)
        if recording.redaction_status is not None:
            case = replace(case, redaction_status=recording.redaction_status)
        self.cases[audit.case_id] = case
        self.events.append((recorded_at, recording))
        return RecordOutcome.RECORDED

    async def route_of(
        self, case_id: str, page_id: str, ref: str
    ) -> RouteDetail | None:
        if self.fail:
            raise StoreDown
        for _, recording in self.events:
            audit = recording.audit
            if (audit.case_id, audit.page_id, audit.action, audit.ref) == (
                case_id,
                page_id,
                AuditAction.PAGE_ROUTED,
                ref,
            ) and isinstance(audit.detail, RouteDetail):
                return audit.detail
        return None

    def _follow_pages(self, case_id: str, *, at_the_gate: bool) -> SettledCase:
        case = self.cases[case_id]
        statuses = {
            page_id: page.page_status
            for page_id, page in self.pages.items()
            if page.case_id == case_id
        }
        wanted = (
            case_status_after_gate(statuses.values(), case.parameters.stop_after)
            if at_the_gate
            else case_status_following(statuses.values())
        )
        if wanted in CASE_TRANSITIONS[case.case_status]:
            case = replace(case, case_status=wanted)
            self.cases[case_id] = case
        return SettledCase(case.case_status, statuses)

    async def decide(self, recording: Recording, recorded_at: datetime) -> Decided:
        if self.fail:
            raise StoreDown
        audit, wanted, change = (
            recording.audit,
            recording.decision,
            recording.page_change,
        )
        assert wanted is not None and change is not None
        case = self.cases.get(audit.case_id)
        if case is None:
            return Decided(DecisionOutcome.UNKNOWN_CASE)
        page = self.pages.get(change.page_id)
        if page is None or page.case_id != audit.case_id:
            return Decided(DecisionOutcome.UNKNOWN_PAGE)
        for stored in self.decisions:
            if (stored.page_id, stored.decision) == (wanted.page_id, wanted.decision):
                return Decided(DecisionOutcome.REPEATED, stored)
        if (
            not case_takes_decisions(case.case_status, case.parameters.stop_after)
            or page.page_status is not change.only_from
            or change.page_status not in PAGE_TRANSITIONS[page.page_status]
        ):
            return Decided(DecisionOutcome.NOT_AWAITING)
        if self.fail_audit_insert:
            # Nothing above has been applied yet, as after a rollback.
            raise StoreDown
        page.page_status = change.page_status
        self.decisions.append(wanted)
        self._follow_pages(audit.case_id, at_the_gate=False)
        # The event takes the eval run of its case, as the real store sets it.
        stored_audit = audit.model_copy(
            update={"eval_run_id": case.parameters.eval_run_id}
        )
        self.events.append((recorded_at, replace(recording, audit=stored_audit)))
        return Decided(DecisionOutcome.RECORDED, wanted)

    async def settle_case(
        self, case_id: str, settled_at: datetime
    ) -> SettledCase | None:
        if self.fail:
            raise StoreDown
        if case_id not in self.cases:
            return None
        return self._follow_pages(case_id, at_the_gate=True)

    async def progress(self, case_id: str) -> CaseProgress | None:
        if self.fail:
            raise StoreDown
        case = self.cases.get(case_id)
        if case is None:
            return None
        failures = [
            (recording.audit.page_id, recording.error_code)
            for _, recording in self.events
            if recording.audit.case_id == case_id and recording.error_code is not None
        ]
        page_codes: dict[str, ErrorCode] = {}
        for failed_page_id, code in failures:
            if failed_page_id is not None:
                page_codes.setdefault(failed_page_id, code)
        pages = sorted(
            (
                (page.page_number, page_id, page.page_status)
                for page_id, page in self.pages.items()
                if page.case_id == case_id
            ),
        )
        return CaseProgress(
            case_id=case_id,
            case_status=case.case_status,
            redaction_status=case.redaction_status,
            pages=[
                PageProgress(
                    page_id=page_id,
                    page_number=number,
                    page_status=status,
                    error_code=page_codes.get(page_id),
                )
                for number, page_id, status in pages
            ],
            error_code=failures[0][1] if failures else None,
        )

    async def audit_trail(self, case_id: str) -> AuditTrail | None:
        if self.fail:
            raise StoreDown
        if case_id not in self.cases:
            return None
        events = [
            (recording.audit.occurred_at, recorded_at, position, recording.audit)
            for position, (recorded_at, recording) in enumerate(self.events)
            if recording.audit.case_id == case_id
        ]
        return AuditTrail(
            case_id=case_id,
            events=[event[3] for event in sorted(events, key=lambda e: e[:3])],
        )


@dataclass
class FakeEngine:
    """Stands in for the scheduler: one orchestration per case id, never a second."""

    instances: dict[str, CaseRecord] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    fail: bool = False
    # What an orchestration that already exists is reported as.
    existing: EngineState = EngineState.ACTIVE
    # Story 1.10: the decisions the orchestrations were told of, in order,
    # as (case id, page id, the status the page had, the decision).
    told: list[tuple[str, str, PageStatus, Decision]] = field(default_factory=list)
    # The engine cannot be told: no event is raised.
    fail_events: bool = False

    async def ensure_started(self, case: CaseRecord) -> EngineState:
        self.calls.append(case.case_id)
        if self.fail:
            raise StoreDown
        if case.case_id in self.instances:
            return self.existing
        self.instances[case.case_id] = case
        return EngineState.CREATED

    async def decision_made(
        self, case_id: str, page_id: str, awaited: PageStatus, decision: Decision
    ) -> None:
        if self.fail_events:
            raise StoreDown
        self.told.append((case_id, page_id, awaited, decision))


@dataclass
class MemoryTrailGuard:
    writable: bool = False
    fail: bool = False

    async def role_can_change_trail(self) -> bool:
        if self.fail:
            raise StoreDown
        return self.writable


@dataclass
class MemorySchemaRevision:
    revision: str | None
    fail: bool = False

    async def current(self) -> str | None:
        if self.fail:
            raise StoreDown
        return self.revision


# --- Stage results, shaped as the contracts say --------------------------------


def audit_record(case_id: str, action: str, **changes: Any) -> AuditRecord:
    """An audit record as a stage service would send it; synthetic values only."""
    record: dict[str, Any] = {
        "actor_kind": "ai",
        "actor": "intake:azure-ai-language",
        "action": action,
        "occurred_at": OCCURRED_AT,
        "case_id": case_id,
        "page_id": None,
        "ref": new_id(),
        "detail": None,
        "trace_id": TRACE_ID,
        "eval_run_id": None,
    }
    record.update(changes)
    return AuditRecord.model_validate(record)


def redaction_done(
    case_id: str, page_ids: list[str], **audit_changes: Any
) -> RedactionResult:
    counts = {"Person": 2, "PhoneNumber": 1}
    return RedactionResult.model_validate(
        {
            "case_id": case_id,
            "status": "done",
            "error_code": None,
            "audit": audit_record(
                case_id, "document.redacted", detail=counts, **audit_changes
            ),
            "document_id": new_id(),
            "page_ids": page_ids,
            "redaction_counts": counts,
        }
    )


def redaction_failed(
    case_id: str, error_code: str = "redaction_failed", **audit_changes: Any
) -> RedactionResult:
    return RedactionResult.model_validate(
        {
            "case_id": case_id,
            "status": "failed",
            "error_code": error_code,
            "audit": audit_record(case_id, "stage.failed", **audit_changes),
            "document_id": new_id(),
            "page_ids": [],
            "redaction_counts": {},
        }
    )


def classification_done(
    case_id: str,
    page_id: str,
    *,
    page_type: str = "lab_report",
    is_medical: bool = True,
    confidence: float = 0.95,
    **audit_changes: Any,
) -> ClassificationResult:
    classification_id = new_id()
    return ClassificationResult.model_validate(
        {
            "case_id": case_id,
            "status": "done",
            "error_code": None,
            "audit": audit_record(
                case_id,
                "page.classified",
                actor="classification:chat-main",
                page_id=page_id,
                ref=classification_id,
                **audit_changes,
            ),
            "classification_id": classification_id,
            "page_id": page_id,
            "contender": "llm",
            "classification": {
                "classification_id": classification_id,
                "case_id": case_id,
                "page_id": page_id,
                "contender": "llm",
                "page_type": page_type,
                "is_medical": is_medical,
                "confidence": confidence,
                "reason": "A table of laboratory values.",
            },
        }
    )


def classification_failed(
    case_id: str,
    page_id: str,
    error_code: str = "model_unavailable",
    **audit_changes: Any,
) -> ClassificationResult:
    classification_id = new_id()
    return ClassificationResult.model_validate(
        {
            "case_id": case_id,
            "status": "failed",
            "error_code": error_code,
            "audit": audit_record(
                case_id,
                "stage.failed",
                actor="classification:chat-main",
                page_id=page_id,
                ref=classification_id,
                **audit_changes,
            ),
            "classification_id": classification_id,
            "page_id": page_id,
            "contender": "llm",
            "classification": None,
        }
    )


def facts_done(case_id: str, page_id: str, **audit_changes: Any) -> FactSetResult:
    fact_set_id = new_id()
    return FactSetResult.model_validate(
        {
            "case_id": case_id,
            "status": "done",
            "error_code": None,
            "audit": audit_record(
                case_id,
                "facts.extracted",
                actor="extraction:chat-main",
                page_id=page_id,
                ref=fact_set_id,
                **audit_changes,
            ),
            "fact_set_id": fact_set_id,
            "page_id": page_id,
            "fact_ids": [new_id()],
            "unverified_count": 0,
        }
    )


def verdict_done(case_id: str) -> VerdictRunResult:
    verdict_run_id = new_id()
    return VerdictRunResult.model_validate(
        {
            "case_id": case_id,
            "status": "done",
            "error_code": None,
            "audit": audit_record(
                case_id,
                "verdict.suggested",
                actor="verdict:chat-main",
                ref=verdict_run_id,
            ),
            "verdict_run_id": verdict_run_id,
            "retriever_config": "r3",
            "verdict": "standard",
        }
    )


# --- The stage services ----------------------------------------------------------


@dataclass
class FakeStages:
    """Stands in for the stage services: each answers its command as it would.

    A case is redacted once and a page is classified once; every repeat gets
    the stored result (AD-6).
    """

    # What the stage does with a case it has no result for: "done" or "failed".
    redaction: str = "done"
    error_code: str = "redaction_failed"
    pages: int = 2
    # What the next calls do instead, first to last: "in_progress" (the stage
    # still works on it), "down" (no answer), "not_found" (no such case) or
    # "invalid" (the stage refuses the command itself).
    script: list[str] = field(default_factory=list)
    results: dict[str, RedactionResult] = field(default_factory=dict)
    calls: list[tuple[str, str | None, dict[str, str]]] = field(default_factory=list)
    # Classification (story 1.8). What the stage does with a page it has no
    # result for: "done" or "failed"; or, by page number, for some pages only.
    classification: str = "done"
    classification_error_code: str = "model_unavailable"
    failing_page_numbers: frozenset[int] = frozenset()
    # As `script`, for the next classify commands.
    classify_script: list[str] = field(default_factory=list)
    # Story 1.9. What the classifier reads on a page, by page number, as
    # (page type, medical or not, confidence); a lab report at 0.95 otherwise.
    readings: dict[int, tuple[str, bool, float]] = field(default_factory=dict)
    classifications: dict[tuple[str, str], ClassificationResult] = field(
        default_factory=dict
    )
    classify_calls: list[tuple[str, str, str, str | None, dict[str, str]]] = field(
        default_factory=list
    )

    async def redact_document(
        self,
        case_id: str,
        *,
        eval_run_id: str | None,
        trace_context: Mapping[str, str],
    ) -> RedactionResult:
        self.calls.append((case_id, eval_run_id, dict(trace_context)))
        self._follow(self.script, "That case could not be found.")
        if case_id not in self.results:
            self.results[case_id] = (
                redaction_done(
                    case_id,
                    [new_id() for _ in range(self.pages)],
                    eval_run_id=eval_run_id,
                )
                if self.redaction == "done"
                else redaction_failed(case_id, self.error_code, eval_run_id=eval_run_id)
            )
        return self.results[case_id]

    async def classify_page(
        self,
        case_id: str,
        page_id: str,
        contender: ClassifierContender,
        *,
        eval_run_id: str | None,
        trace_context: Mapping[str, str],
    ) -> ClassificationResult:
        self.classify_calls.append(
            (case_id, page_id, contender.value, eval_run_id, dict(trace_context))
        )
        self._follow(self.classify_script, "That page could not be found.")
        if contender is not ClassifierContender.LLM:
            # Story 4.2 builds the second contender; until then it is refused.
            raise DomainError(
                ErrorCode.VALIDATION_FAILED, "That classifier is not available."
            )
        redacted = self.results.get(case_id)
        if redacted is None or page_id not in redacted.page_ids:
            raise DomainError(ErrorCode.NOT_FOUND, "That page could not be found.")
        key = (case_id, page_id)
        if key not in self.classifications:
            page_number = redacted.page_ids.index(page_id) + 1
            failing = (
                page_number in self.failing_page_numbers
                if self.failing_page_numbers
                else self.classification != "done"
            )
            self.classifications[key] = (
                classification_failed(
                    case_id,
                    page_id,
                    self.classification_error_code,
                    eval_run_id=eval_run_id,
                )
                if failing
                else self._read(case_id, page_id, page_number, eval_run_id)
            )
        return self.classifications[key]

    def _read(
        self, case_id: str, page_id: str, page_number: int, eval_run_id: str | None
    ) -> ClassificationResult:
        if page_number not in self.readings:
            return classification_done(case_id, page_id, eval_run_id=eval_run_id)
        page_type, is_medical, confidence = self.readings[page_number]
        return classification_done(
            case_id,
            page_id,
            page_type=page_type,
            is_medical=is_medical,
            confidence=confidence,
            eval_run_id=eval_run_id,
        )

    @staticmethod
    def _follow(script: list[str], not_found_message: str) -> None:
        """Do what the script says for this call, if it says anything."""
        step = script.pop(0) if script else "answer"
        if step == "in_progress":
            raise DomainError(ErrorCode.IN_PROGRESS, "Still being worked on.")
        if step == "down":
            raise DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, "Not available.")
        if step == "invalid":
            raise DomainError(ErrorCode.VALIDATION_FAILED, "The request is not valid.")
        if step == "not_found":
            raise DomainError(ErrorCode.NOT_FOUND, not_found_message)


_REDACTION_PATH = re.compile(
    r"/v1\.0/invoke/intake/method/cases/(?P<case_id>[0-9a-f-]{36})/redaction"
)
_CLASSIFICATION_PATH = "/v1.0/invoke/classification/method/classifications"


@dataclass
class SidecarStandIn:
    """Stands in for `workflow`'s Dapr sidecar, with the stage services behind it.

    An `httpx` transport handler: `httpx.MockTransport(stand_in.handle)`. It
    answers the redaction command as `intake` does and the classify command
    as `classification` does, over HTTP and in the contracts' shapes, so the
    real client module is what the test runs.
    """

    stages: FakeStages = field(default_factory=FakeStages)
    requests: list[httpx.Request] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def redactions(self, case_id: str) -> int:
        """How often redaction was commanded for one case."""
        return sum(case_id in request.url.path for request in self.requests)

    def classify_commands(self, case_id: str) -> list[dict[str, Any]]:
        """The classify commands sent for one case, in the order they came."""
        commands = [
            json.loads(request.content)
            for request in self.requests
            if request.url.path == _CLASSIFICATION_PATH
        ]
        return [command for command in commands if command["case_id"] == case_id]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.requests.append(request)
        match = _REDACTION_PATH.fullmatch(request.url.path)
        classify = request.url.path == _CLASSIFICATION_PATH
        if (match is None and not classify) or request.method != "POST":
            # As the sidecar answers for an app or a method it cannot reach.
            return httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE"})
        command = json.loads(request.content)
        result: RedactionResult | ClassificationResult
        try:
            if match is not None:
                result = await self.stages.redact_document(
                    match["case_id"],
                    eval_run_id=command.get("eval_run_id"),
                    trace_context=dict(request.headers),
                )
            else:
                result = await self.stages.classify_page(
                    command["case_id"],
                    command["page_id"],
                    ClassifierContender(command["contender"]),
                    eval_run_id=command.get("eval_run_id"),
                    trace_context=dict(request.headers),
                )
        except DomainError as error:
            return httpx.Response(
                error.http_status,
                json=error.to_body(None).model_dump(mode="json"),
            )
        return httpx.Response(200, json=result.model_dump(mode="json"))
