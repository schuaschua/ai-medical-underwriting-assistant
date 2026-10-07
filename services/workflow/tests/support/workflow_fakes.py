"""In-memory stand-ins for `workflow`'s store and engine, and builders of stage results.

Unit tests use them in place of PostgreSQL and the Durable Task Scheduler
(coding-style rule 23). They live with the tests, on pytest's `pythonpath`,
so the service package and its image hold no test code.
"""

import asyncio
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import httpx
from durabletask import task

from contracts.audit import AuditAction, AuditRecord, RouteDetail
from contracts.decisions import STATUSES_AWAITING_A_DECISION
from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    Decision,
    DemoRole,
    PageStatus,
)
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.classification import ClassificationResult
from contracts.models.extraction import FactSetResult
from contracts.models.intake import RedactionResult
from contracts.models.verdict import VerdictRunResult
from contracts.models.workflow import (
    AuditTrail,
    CaseList,
    CaseProgress,
    CaseSummary,
    PageProgress,
    PageQueue,
    QueuedPage,
    StartCaseRequest,
)
from workflow.domain.case_list import waiting_page_count
from workflow.domain.case_status import case_status_after_gate, case_status_following
from workflow.domain.decisions import case_takes_decisions
from workflow.domain.entities import CaseRecord, PageDecision, SettledCase
from workflow.domain.ports import EngineState, Told
from workflow.domain.queue import queued_by
from workflow.domain.recording import (
    Decided,
    DecisionOutcome,
    Recording,
    RecordOutcome,
    case_completed_event,
    case_started_event,
)
from workflow.domain.transitions import (
    CASE_STATUSES_TAKING_RESULTS,
    CASE_TRANSITIONS,
    PAGE_TRANSITIONS,
    REDACTION_TRANSITIONS,
)

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
OCCURRED_AT = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


# Story 1.13: a case is started by a person. What `web` passes on with a
# start for the customer, as a request body and as the request itself.
STARTED_BY = {"actor": "customer"}
BY_CUSTOMER = StartCaseRequest(actor="customer")


def starting(
    case: CaseRecord, actor: DemoRole = DemoRole.CUSTOMER
) -> tuple[CaseRecord, AuditRecord]:
    """A case with its `case.started` event, as the start hands both to the store."""
    return case, case_started_event(
        case.case_id,
        actor,
        occurred_at=case.created_at,
        eval_run_id=case.parameters.eval_run_id,
        trace_id=TRACE_ID,
    )


def after_start(store: "MemoryCaseStore") -> list[tuple[datetime, Recording]]:
    """What the stand-in store recorded once its cases were started.

    Every case's trail begins with its one `case.started` event (story
    1.13). The tests of the earlier stories are about what follows it: this
    checks that each case's start is there, first and once, and leaves it out.
    """
    by_case: dict[str, list[AuditAction]] = {}
    for _, recording in store.events:
        by_case.setdefault(recording.audit.case_id, []).append(recording.audit.action)
    for actions in by_case.values():
        assert actions[0] is AuditAction.CASE_STARTED, actions
        assert actions.count(AuditAction.CASE_STARTED) == 1, actions
    return [
        (recorded_at, recording)
        for recorded_at, recording in store.events
        if recording.audit.action is not AuditAction.CASE_STARTED
    ]


class StoreDown(Exception):
    """What a failing stand-in raises. Its message must never reach a log or a body."""

    def __init__(self) -> None:
        super().__init__("secret-store-detail host=10.0.0.1 SELECT 1")


@dataclass
class MemoryPage:
    case_id: str
    page_number: int
    page_status: PageStatus
    # When the page got the status it has.
    updated_at: datetime = OCCURRED_AT


@dataclass
class MemoryCaseStore:
    """Keeps to the store's contract: a recording is written whole, once, or not at all."""

    cases: dict[str, CaseRecord] = field(default_factory=dict)
    pages: dict[str, MemoryPage] = field(default_factory=dict)
    events: list[tuple[datetime, Recording]] = field(default_factory=list)
    decisions: list[PageDecision] = field(default_factory=list)
    # Story 2.4: the decisions an orchestration was told of, by decision id.
    told_decisions: dict[str, datetime] = field(default_factory=dict)
    # The mark cannot be written.
    fail_marks: bool = False
    fail: bool = False
    # The audit event cannot be written: everything before it is undone.
    fail_audit_insert: bool = False

    async def start(self, case: CaseRecord, started: AuditRecord) -> CaseRecord:
        if self.fail:
            raise StoreDown
        if case.case_id not in self.cases:
            # As the real store: the case and its `case.started` event, together.
            self.cases[case.case_id] = case
            self.events.append((case.created_at, Recording(audit=started)))
        return self.cases[case.case_id]

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
        if recording.case_status is CaseStatus.COMPLETED:
            # As the real store: a case is completed where its event is written.
            raise ValueError("a recording does not complete a case")
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
            self.pages[change.page_id].updated_at = recorded_at
        for new_page in recording.new_pages:
            self.pages[new_page.page_id] = MemoryPage(
                audit.case_id, new_page.page_number, PageStatus.UPLOADED, recorded_at
            )
        if recording.case_status is not None:
            case = replace(case, case_status=recording.case_status)
        if recording.redaction_status is not None:
            case = replace(case, redaction_status=recording.redaction_status)
        self.cases[audit.case_id] = case
        self.events.append((recorded_at, recording))
        if recording.follows_pages:
            # As the real store: after the result's own event, so that a
            # completion is the trail's last event.
            self._follow_pages(
                audit.case_id, recorded_at, at_the_gate=False, trace_id=audit.trace_id
            )
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

    def _follow_pages(
        self,
        case_id: str,
        moved_at: datetime,
        *,
        at_the_gate: bool,
        trace_id: str | None,
    ) -> SettledCase:
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
            if wanted is CaseStatus.COMPLETED:
                # As the real store: the event with the status, once.
                completed = case_completed_event(
                    case_id,
                    occurred_at=moved_at,
                    eval_run_id=case.parameters.eval_run_id,
                    trace_id=trace_id,
                )
                self.events.append((moved_at, Recording(audit=completed)))
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
        page.updated_at = recorded_at
        self.decisions.append(wanted)
        # The event takes the eval run of its case, as the real store sets it.
        stored_audit = audit.model_copy(
            update={"eval_run_id": case.parameters.eval_run_id}
        )
        self.events.append((recorded_at, replace(recording, audit=stored_audit)))
        # After the decision's event: a completion is the trail's last event.
        self._follow_pages(
            audit.case_id, recorded_at, at_the_gate=False, trace_id=audit.trace_id
        )
        return Decided(DecisionOutcome.RECORDED, wanted)

    async def settle_case(
        self, case_id: str, settled_at: datetime, trace_id: str | None
    ) -> SettledCase | None:
        if self.fail:
            raise StoreDown
        if case_id not in self.cases:
            return None
        return self._follow_pages(
            case_id, settled_at, at_the_gate=True, trace_id=trace_id
        )

    async def mark_decision_told(self, decision_id: str, told_at: datetime) -> None:
        if self.fail or self.fail_marks:
            raise StoreDown
        self.told_decisions.setdefault(decision_id, told_at)

    async def decisions_not_told(
        self, decided_before: datetime, limit: int
    ) -> list[PageDecision]:
        if self.fail:
            raise StoreDown
        untold = sorted(
            (
                decision
                for decision in self.decisions
                if decision.decision_id not in self.told_decisions
                # As the real store: a failed case has no lifecycle to tell.
                and self.cases[decision.case_id].case_status is not CaseStatus.FAILED
                and decision.occurred_at < decided_before
            ),
            key=lambda decision: (decision.occurred_at, decision.decision_id),
        )
        return untold[:limit]

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

    async def audit_trail(self, case_id: str, limit: int) -> AuditTrail | None:
        if self.fail:
            raise StoreDown
        if case_id not in self.cases:
            return None
        # As the real store: in the order written, which is the order of
        # the list, whatever time each event names or was recorded at. Each
        # carries the error code stored with it, and is built with the
        # contracts' validation, as the real store builds it.
        events = [
            AuditRecord.model_validate(
                {
                    **recording.audit.model_dump(),
                    "error_code": recording.error_code,
                }
            )
            for _, recording in self.events
            if recording.audit.case_id == case_id
        ]
        return AuditTrail(
            case_id=case_id, events=events[:limit], has_more=len(events) > limit
        )

    async def queue(self, status: PageStatus, limit: int) -> PageQueue:
        if self.fail:
            raise StoreDown
        kept = {
            decision.page_id
            for decision in self.decisions
            if decision.decision is Decision.KEEP
        }
        waiting = sorted(
            (page.updated_at, page.case_id, page.page_number, page_id)
            for page_id, page in self.pages.items()
            if page.page_status is status
            and self.cases[page.case_id].parameters.eval_run_id is None
            and case_takes_decisions(
                self.cases[page.case_id].case_status,
                self.cases[page.case_id].parameters.stop_after,
            )
        )
        return PageQueue(
            pages=[
                QueuedPage(
                    case_id=case_id,
                    page_id=page_id,
                    page_number=page_number,
                    page_status=status,
                    classifier_contender=self.cases[
                        case_id
                    ].parameters.classifier_contender,
                    queued_by=queued_by(status, page_id in kept),
                )
                for _, case_id, page_number, page_id in waiting[:limit]
            ],
            has_more=len(waiting) > limit,
        )

    async def case_list(self, limit: int) -> CaseList:
        if self.fail:
            raise StoreDown
        newest_first = sorted(
            (
                case
                for case in self.cases.values()
                if case.parameters.eval_run_id is None
            ),
            key=lambda case: (case.created_at, case.case_id),
            reverse=True,
        )

        def summary(case: CaseRecord) -> CaseSummary:
            statuses = [
                page.page_status
                for page in self.pages.values()
                if page.case_id == case.case_id
            ]
            return CaseSummary(
                case_id=case.case_id,
                case_status=case.case_status,
                started_at=case.created_at,
                page_count=len(statuses),
                waiting_page_count=waiting_page_count(
                    case.case_status,
                    case.parameters.stop_after,
                    sum(status in STATUSES_AWAITING_A_DECISION for status in statuses),
                ),
            )

        return CaseList(
            cases=[summary(case) for case in newest_first[:limit]],
            has_more=len(newest_first) > limit,
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
    # Only the next this many attempts to tell it fail.
    failing_events: int = 0
    # Story 2.4: what the engine finds when it is asked to tell a decision:
    # an orchestration to tell, or one that has ended, is missing or is dead.
    orchestration: Told = Told.TOLD
    # How often it was asked to tell a decision, reached or not.
    tell_attempts: int = 0

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
    ) -> Told:
        self.tell_attempts += 1
        if self.fail_events:
            raise StoreDown
        if self.failing_events > 0:
            self.failing_events -= 1
            raise StoreDown
        if self.orchestration is Told.TOLD:
            self.told.append((case_id, page_id, awaited, decision))
        return self.orchestration


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


def facts_failed(
    case_id: str,
    page_id: str,
    error_code: str = "invalid_model_output",
    **audit_changes: Any,
) -> FactSetResult:
    fact_set_id = new_id()
    return FactSetResult.model_validate(
        {
            "case_id": case_id,
            "status": "failed",
            "error_code": error_code,
            "audit": audit_record(
                case_id,
                "stage.failed",
                actor="extraction:chat-main",
                page_id=page_id,
                ref=fact_set_id,
                **audit_changes,
            ),
            "fact_set_id": fact_set_id,
            "page_id": page_id,
            "fact_ids": [],
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

    A case is redacted once, and a page is classified once and extracted
    once; every repeat gets the stored result (AD-6).
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
    # Extraction (story 2.4). What the stage does with a page it has no fact
    # set for: "done" or "failed"; or, by page number, for some pages only.
    extraction: str = "done"
    extraction_error_code: str = "invalid_model_output"
    failing_extraction_page_numbers: frozenset[int] = frozenset()
    # As `script`, for the next extract commands.
    extract_script: list[str] = field(default_factory=list)
    # While set, an extract command does not answer until it is released: a
    # page stays `extracting` for as long as a test needs it to.
    extraction_hold: threading.Event | None = None
    fact_sets: dict[tuple[str, str], FactSetResult] = field(default_factory=dict)
    extract_calls: list[tuple[str, str, str | None, dict[str, str]]] = field(
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

    async def extract_facts(
        self,
        case_id: str,
        page_id: str,
        *,
        eval_run_id: str | None,
        trace_context: Mapping[str, str],
    ) -> FactSetResult:
        self.extract_calls.append((case_id, page_id, eval_run_id, dict(trace_context)))
        self._follow(self.extract_script, "That page could not be found.")
        redacted = self.results.get(case_id)
        if redacted is None or page_id not in redacted.page_ids:
            raise DomainError(ErrorCode.NOT_FOUND, "That page could not be found.")
        hold = self.extraction_hold
        while hold is not None and not hold.is_set():
            # Not a wait for time to pass: the other tasks get their turn.
            await asyncio.sleep(0.01)
        key = (case_id, page_id)
        if key not in self.fact_sets:
            page_number = redacted.page_ids.index(page_id) + 1
            failing = (
                page_number in self.failing_extraction_page_numbers
                if self.failing_extraction_page_numbers
                else self.extraction != "done"
            )
            self.fact_sets[key] = (
                facts_failed(
                    case_id,
                    page_id,
                    self.extraction_error_code,
                    eval_run_id=eval_run_id,
                )
                if failing
                else facts_done(case_id, page_id, eval_run_id=eval_run_id)
            )
        return self.fact_sets[key]

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
_EXTRACTION_PATH = "/v1.0/invoke/extraction/method/fact-sets"


@dataclass
class SidecarStandIn:
    """Stands in for `workflow`'s Dapr sidecar, with the stage services behind it.

    An `httpx` transport handler: `httpx.MockTransport(stand_in.handle)`. It
    answers the redaction command as `intake` does, the classify command as
    `classification` does and the extract command as `extraction` does, over HTTP and in the contracts' shapes, so the
    real client module is what the test runs.
    """

    stages: FakeStages = field(default_factory=FakeStages)
    requests: list[httpx.Request] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def redactions(self, case_id: str) -> int:
        """How often redaction was commanded for one case."""
        return sum(
            _REDACTION_PATH.fullmatch(request.url.path) is not None
            and case_id in request.url.path
            for request in self.requests
        )

    def classify_commands(self, case_id: str) -> list[dict[str, Any]]:
        """The classify commands sent for one case, in the order they came."""
        commands = [
            json.loads(request.content)
            for request in self.requests
            if request.url.path == _CLASSIFICATION_PATH
        ]
        return [command for command in commands if command["case_id"] == case_id]

    def extract_commands(self, case_id: str) -> list[dict[str, Any]]:
        """The extract commands sent for one case, in the order they came."""
        commands = [
            json.loads(request.content)
            for request in self.requests
            if request.url.path == _EXTRACTION_PATH
        ]
        return [command for command in commands if command["case_id"] == case_id]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.requests.append(request)
        match = _REDACTION_PATH.fullmatch(request.url.path)
        classify = request.url.path == _CLASSIFICATION_PATH
        extract = request.url.path == _EXTRACTION_PATH
        if (match is None and not classify and not extract) or request.method != "POST":
            # As the sidecar answers for an app or a method it cannot reach.
            return httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE"})
        command = json.loads(request.content)
        result: RedactionResult | ClassificationResult | FactSetResult
        try:
            if match is not None:
                result = await self.stages.redact_document(
                    match["case_id"],
                    eval_run_id=command.get("eval_run_id"),
                    trace_context=dict(request.headers),
                )
            elif extract:
                result = await self.stages.extract_facts(
                    command["case_id"],
                    command["page_id"],
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


# --- The orchestrator, stepped by hand ------------------------------------------------

# Story 2.4: what a done extraction's activity answers the orchestrator with.
EXTRACTED = {"outcome": "ok", "case_status": "running"}
EXTRACTION_FAILED = {"outcome": "ok", "case_status": "failed"}
_EXTRACT_FACTS = "extract_facts"


def activity_task(
    context: object, activity: str, options: Mapping[str, Any]
) -> task.CompletableTask[Any]:
    """A task of the engine's own kind for an activity a stand-in context was asked for.

    The context keeps it, with the activity's name and input, as
    `activity_tasks`: the orchestrator looks at the tasks themselves once it
    waits for several things side by side (story 2.4), so a test finishes a
    task to answer it.
    """
    created = task.CompletableTask[Any]()
    kept: list[tuple[str, Any, task.CompletableTask[Any]]] = (
        context.__dict__.setdefault("activity_tasks", [])
    )
    kept.append((activity, options.get("input"), created))
    return created


def pending_extractions(context: object) -> list[tuple[str, task.CompletableTask[Any]]]:
    """The extractions the orchestrator asked for and has no answer to yet, as (page id, task)."""
    return [
        (given["page_id"], asked)
        for activity, given, asked in context.__dict__.get("activity_tasks", [])
        if activity == _EXTRACT_FACTS and not asked.is_complete
    ]


def finish_extractions(
    steps: Any,
    context: object,
    answers: Mapping[str, object] | None = None,
) -> Any:
    """Answer every extraction the orchestrator waits for, as the engine would; return its result.

    Each is answered as done, or with what `answers` names for its page id
    (an exception there fails the task, as when every retry failed). Goes on
    for as long as answers bring new extractions. Returns None if the
    orchestrator then still waits for something else: a person's decision.
    """
    while True:
        pending = pending_extractions(context)
        if not pending:
            return None
        for page_id, asked in pending:
            answer = (answers or {}).get(page_id, EXTRACTED)
            if isinstance(answer, Exception):
                asked.fail("failed", answer)
            else:
                asked.complete(answer)
            try:
                # The engine resumes the orchestrator with the task that finished.
                steps.send(asked)
            except StopIteration as done:
                return done.value
