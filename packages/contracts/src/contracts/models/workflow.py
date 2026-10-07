"""Payloads of the operations `workflow` owns (AD-5, AD-8, AD-10)."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from contracts.audit import AuditRecord
from contracts.base import (
    ContractModel,
    Count,
    NonEmptyStr,
    PageNumber,
    UtcDatetime,
)
from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    Decision,
    DemoRole,
    PageStatus,
    QueuedBy,
    RetrieverConfig,
    StageStatus,
    StopAfter,
)
from contracts.errors import ErrorCode
from contracts.ids import CaseId, DecisionId, EvalRunId, PageId

RetrieverConfigs = Annotated[list[RetrieverConfig], Field(min_length=1)]


def _require_distinct(configs: list[RetrieverConfig]) -> None:
    # AD-15: a verdict run is keyed by case and retriever_config, so a repeat is meaningless.
    if len(set(configs)) != len(configs):
        raise ValueError("retriever_configs must not repeat a value")


class StartCaseOptions(ContractModel):
    """What a case may be started with; every option is optional (settings fill the gaps).

    Also the body of `web`'s `POST /api/cases/{case_id}/start`. It names no
    actor: `web` passes the request's demo role on as the actor (AD-9), so a
    browser cannot say who started the case.
    """

    classifier_contender: ClassifierContender | None = None
    retriever_configs: RetrieverConfigs | None = None
    stop_after: StopAfter | None = None
    eval_run_id: EvalRunId | None = None

    @model_validator(mode="after")
    def _configs_are_distinct(self) -> Self:
        if self.retriever_configs is not None:
            _require_distinct(self.retriever_configs)
        return self


class StartCaseRequest(StartCaseOptions):
    """Request of `POST /cases/{case_id}/start`: the options, and who asks.

    `workflow` refuses a start that names no demo role.
    """

    # AD-9: `web` passes the demo role on as the human actor who asked for
    # the start. Any text is taken here, blank too, and the field may be
    # left out, so that an actor that is missing, blank or no demo role
    # reaches `workflow`'s domain rule and is refused there as
    # `actor_not_human` (AD-10), and not as a request that is merely not valid.
    actor: str | None = None


class CaseStarted(ContractModel):
    """Response of `POST /cases/{case_id}/start`: the case as it was actually started."""

    case_id: CaseId
    case_status: CaseStatus
    classifier_contender: ClassifierContender
    retriever_configs: RetrieverConfigs
    stop_after: StopAfter | None
    eval_run_id: EvalRunId | None

    @model_validator(mode="after")
    def _configs_are_distinct(self) -> Self:
        _require_distinct(self.retriever_configs)
        return self


class DecisionRequest(ContractModel):
    """Request of `POST /cases/{case_id}/pages/{page_id}/decisions`."""

    decision: Decision
    # AD-9: `web` passes the demo role on as the human actor. Any text is
    # taken here, so that an actor that is no demo role reaches `workflow`'s
    # domain rule and is refused there as `actor_not_human` (AD-10), and not
    # as a request that is merely not valid.
    actor: NonEmptyStr


class DecisionRecorded(ContractModel):
    """Response of `POST /cases/{case_id}/pages/{page_id}/decisions`: the stored decision.

    `page_status` is the status the decision left the page in. A repeat of a
    decision is answered with the one stored the first time.
    """

    decision_id: DecisionId
    case_id: CaseId
    page_id: PageId
    decision: Decision
    actor: DemoRole
    page_status: PageStatus
    occurred_at: UtcDatetime


class VerdictRunRequest(ContractModel):
    """Request of `POST /cases/{case_id}/verdict-runs` (the Compare toggle, AD-11)."""

    retriever_config: RetrieverConfig


class VerdictRunRequested(ContractModel):
    """The requested run's state; the run itself is read from `verdict`."""

    case_id: CaseId
    retriever_config: RetrieverConfig
    status: StageStatus


class PageProgress(ContractModel):
    page_id: PageId
    page_number: PageNumber
    page_status: PageStatus
    # Set when a stage failed on this page: the code of its `stage.failed` event.
    error_code: ErrorCode | None = None


class CaseProgress(ContractModel):
    """Response of `GET /cases/{case_id}/progress`."""

    case_id: CaseId
    case_status: CaseStatus
    redaction_status: StageStatus
    # Empty until redaction is done.
    pages: list[PageProgress]
    # Set when a stage failed: the code of the case's first `stage.failed` event.
    error_code: ErrorCode | None = None


class AuditTrail(ContractModel):
    """Response of `GET /cases/{case_id}/audit`: the case's events, oldest first.

    The order is the one `workflow` recorded the events in, so a cause never
    comes after its effect, whatever the clocks of the services that set
    `occurred_at` say. The answer is bounded: it holds the first events, and
    `has_more` says that the case has more than are listed.
    """

    case_id: CaseId
    events: list[AuditRecord]
    has_more: bool


class PageQueueQuery(ContractModel):
    """Query of `GET /pages?status=`."""

    status: PageStatus


class QueuedPage(ContractModel):
    case_id: CaseId
    page_id: PageId
    page_number: PageNumber
    page_status: PageStatus
    # What the case was started with: the classifier whose reading the gate used.
    classifier_contender: ClassifierContender
    # For a page that waits for triage: how it got there. Null in any other queue.
    queued_by: QueuedBy | None = None


class PageQueue(ContractModel):
    """Response of `GET /pages?status=`: the pages across cases that wait in that status.

    Oldest waiting first. Pages of a case that belongs to an eval run, that is
    failed or completed, or that was started with `stop_after: gate` are left
    out. The answer is bounded: `has_more` says that more pages wait than are
    listed.
    """

    pages: list[QueuedPage]
    has_more: bool


class CaseSummary(ContractModel):
    """One case in the underwriter's list."""

    case_id: CaseId
    case_status: CaseStatus
    # When the case was first started.
    started_at: UtcDatetime
    # The pages `workflow` tracks for the case; none until redaction is done.
    page_count: Count
    # How many of them wait for a person, the customer or the underwriter.
    waiting_page_count: Count

    @model_validator(mode="after")
    def _waiting_pages_are_among_the_pages(self) -> Self:
        if self.waiting_page_count > self.page_count:
            raise ValueError("waiting_page_count must not exceed page_count")
        return self


class CaseList(ContractModel):
    """Response of `GET /cases`: the cases, newest started first.

    Cases that belong to an eval run are left out. The answer is bounded:
    `has_more` says that more cases exist than are listed.
    """

    cases: list[CaseSummary]
    has_more: bool
