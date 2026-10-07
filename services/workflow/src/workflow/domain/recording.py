"""The recording rule: what one stage result changes, and the audit event that says so (AD-8).

Every stage result, and later every human decision, becomes one `Recording`:
the status changes it causes and the one audit record that reports them. The
store writes a recording in a single transaction, or not at all.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from contracts.audit import AuditAction, AuditRecord
from contracts.enums import ActorKind, CaseStatus, PageStatus, StageStatus
from contracts.errors import NO_TRACE_ID, DomainError, ErrorCode
from contracts.models._stage import StageResult
from contracts.models.intake import RedactionResult


class RecordOutcome(StrEnum):
    """How the store left a recording."""

    RECORDED = "recorded"
    # The same audit event is already in the trail: nothing was written (AD-8).
    DUPLICATE = "duplicate"
    UNKNOWN_CASE = "unknown_case"
    UNKNOWN_PAGE = "unknown_page"
    # The result came late or out of order: the status it would set may not
    # follow the status the case or page has now. Nothing was written.
    OUT_OF_ORDER = "out_of_order"
    # The case has failed, and a failed case takes no further result.
    CASE_FAILED = "case_failed"
    # A redaction result for a case whose pages are tracked already.
    PAGES_ALREADY_TRACKED = "pages_already_tracked"


# What the lifecycle itself is called in an audit record, as `<app id>:<part>`.
LIFECYCLE_ACTOR = "workflow:case-lifecycle"
WRONG_PAGE_MESSAGE = "The result's audit record is about another page."


@dataclass(frozen=True, slots=True)
class NewPage:
    page_id: str
    page_number: int


@dataclass(frozen=True, slots=True)
class PageChange:
    page_id: str
    page_status: PageStatus
    # Set when the change may be made from this one status only, and not from
    # every status the transition table lists before `page_status`.
    only_from: PageStatus | None = None


@dataclass(frozen=True, slots=True)
class Recording:
    """One audit record and the status changes it reports."""

    audit: AuditRecord
    # Set for a failed stage; stored with the `stage.failed` event.
    error_code: ErrorCode | None = None
    case_status: CaseStatus | None = None
    redaction_status: StageStatus | None = None
    # Pages that begin to be tracked, each as `uploaded`.
    new_pages: tuple[NewPage, ...] = ()
    page_change: PageChange | None = None


# The page status a done stage result leaves its page in.
_PAGE_STATUS_WHEN_DONE: Mapping[AuditAction, PageStatus] = MappingProxyType(
    {
        AuditAction.PAGE_CLASSIFIED: PageStatus.CLASSIFIED,
        AuditAction.FACTS_EXTRACTED: PageStatus.EXTRACTED,
    }
)


def plan_recording(result: StageResult) -> Recording:
    """Work out what a stage result changes; the result's audit record reports it.

    A failed result fails the case, and its page if it names one. A done
    redaction starts the tracking of its pages; a done classification or
    extraction moves its page on. Nothing here routes a page: the gate is
    a rule of its own (AD-7).
    """
    audit = result.audit
    # A page stage's record is about that stage's page; a case-level stage's
    # record names no page. Anything else would move the wrong page.
    if audit.page_id != getattr(result, "page_id", None):
        raise DomainError(ErrorCode.VALIDATION_FAILED, WRONG_PAGE_MESSAGE)
    if result.status is StageStatus.FAILED:
        return Recording(
            audit=audit,
            error_code=result.error_code,
            case_status=CaseStatus.FAILED,
            redaction_status=StageStatus.FAILED
            if isinstance(result, RedactionResult)
            else None,
            page_change=PageChange(audit.page_id, PageStatus.FAILED)
            if audit.page_id is not None
            else None,
        )
    if isinstance(result, RedactionResult):
        return Recording(
            audit=audit,
            redaction_status=StageStatus.DONE,
            # The result lists the pages in document order, so a page's
            # number is its place in the list.
            new_pages=tuple(
                NewPage(page_id, number)
                for number, page_id in enumerate(result.page_ids, start=1)
            ),
        )
    page_status = _PAGE_STATUS_WHEN_DONE.get(audit.action)
    if page_status is not None and audit.page_id is not None:
        return Recording(
            audit=audit, page_change=PageChange(audit.page_id, page_status)
        )
    return Recording(audit=audit)


def lifecycle_failure(
    case_id: str,
    *,
    occurred_at: datetime,
    eval_run_id: str | None = None,
    trace_id: str | None = None,
) -> Recording:
    """What is recorded when a case's orchestration cannot go on: the case fails.

    The event is case-level and its reference is the case itself, so however
    often this is recorded for one case, the trail holds it once.
    """
    return Recording(
        audit=AuditRecord(
            actor_kind=ActorKind.AI,
            actor=LIFECYCLE_ACTOR,
            action=AuditAction.STAGE_FAILED,
            occurred_at=occurred_at,
            case_id=case_id,
            page_id=None,
            ref=case_id,
            detail=None,
            trace_id=trace_id or NO_TRACE_ID,
            eval_run_id=eval_run_id,
        ),
        error_code=ErrorCode.STAGE_FAILED,
        case_status=CaseStatus.FAILED,
    )
