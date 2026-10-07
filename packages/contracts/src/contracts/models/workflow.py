"""Payloads of the operations `workflow` owns (AD-5, AD-8, AD-10)."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from contracts.audit import AuditRecord
from contracts.base import ContractModel, PageNumber, UtcDatetime
from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    Decision,
    DemoRole,
    PageStatus,
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


class StartCaseRequest(ContractModel):
    """Request of `POST /cases/{case_id}/start`; every field is optional (settings fill the gaps)."""

    classifier_contender: ClassifierContender | None = None
    retriever_configs: RetrieverConfigs | None = None
    stop_after: StopAfter | None = None
    eval_run_id: EvalRunId | None = None

    @model_validator(mode="after")
    def _configs_are_distinct(self) -> Self:
        if self.retriever_configs is not None:
            _require_distinct(self.retriever_configs)
        return self


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
    # AD-9: `web` passes the demo role on as the human actor.
    actor: DemoRole


class DecisionRecorded(ContractModel):
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
    """Response of `GET /cases/{case_id}/audit`: events in time order."""

    case_id: CaseId
    events: list[AuditRecord]


class PageQueueQuery(ContractModel):
    """Query of `GET /pages?status=`."""

    status: PageStatus


class QueuedPage(ContractModel):
    case_id: CaseId
    page_id: PageId
    page_number: PageNumber
    page_status: PageStatus


class PageQueue(ContractModel):
    """Response of `GET /pages?status=`: pages across cases, eval-run cases left out."""

    pages: list[QueuedPage]
