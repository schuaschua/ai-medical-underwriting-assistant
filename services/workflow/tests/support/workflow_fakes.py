"""In-memory stand-ins for `workflow`'s store and engine, and builders of stage results.

Unit tests use them in place of PostgreSQL and the Durable Task Scheduler
(coding-style rule 23). They live with the tests, on pytest's `pythonpath`,
so the service package and its image hold no test code.
"""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from contracts.audit import AuditRecord
from contracts.enums import CaseStatus, PageStatus
from contracts.ids import new_id
from contracts.models.classification import ClassificationResult
from contracts.models.extraction import FactSetResult
from contracts.models.intake import RedactionResult
from contracts.models.verdict import VerdictRunResult
from contracts.models.workflow import AuditTrail, CaseProgress, PageProgress
from workflow.domain.entities import CaseRecord
from workflow.domain.ports import EngineState
from workflow.domain.recording import Recording, RecordOutcome
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
            if change.page_status not in PAGE_TRANSITIONS[page.page_status]:
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

    async def progress(self, case_id: str) -> CaseProgress | None:
        if self.fail:
            raise StoreDown
        case = self.cases.get(case_id)
        if case is None:
            return None
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
                PageProgress(page_id=page_id, page_number=number, page_status=status)
                for number, page_id, status in pages
            ],
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

    async def ensure_started(self, case: CaseRecord) -> EngineState:
        self.calls.append(case.case_id)
        if self.fail:
            raise StoreDown
        if case.case_id in self.instances:
            return self.existing
        self.instances[case.case_id] = case
        return EngineState.CREATED


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
    case_id: str, page_id: str, **audit_changes: Any
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
                "page_type": "lab_report",
                "is_medical": True,
                "confidence": 0.95,
                "reason": "A table of laboratory values.",
            },
        }
    )


def classification_failed(
    case_id: str, page_id: str, error_code: str = "model_unavailable"
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
