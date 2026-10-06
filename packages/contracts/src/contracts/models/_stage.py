"""What every stage command and stage result share (AD-6, AD-8)."""

from typing import ClassVar, Self

from pydantic import model_validator

from contracts.audit import AuditAction, AuditRecord
from contracts.base import ContractModel
from contracts.enums import StageStatus
from contracts.errors import ErrorCode
from contracts.ids import CaseId, EvalRunId


class StageCommand(ContractModel):
    """A command from `workflow` to a stage service: ids only, never content."""

    # The stage copies this into the audit record its result carries (AD-8).
    eval_run_id: EvalRunId | None = None


class StageResult(ContractModel):
    """A stored stage result: ids, a small summary and its one audit record."""

    # The audit action a done result of this stage carries; each stage sets its own.
    DONE_ACTION: ClassVar[AuditAction]

    case_id: CaseId
    # `done` or `failed`. A stage that is still running answers 409 `in_progress` (AD-6).
    status: StageStatus
    # Set when, and only when, the stage failed.
    error_code: ErrorCode | None
    audit: AuditRecord

    @model_validator(mode="after")
    def _result_is_consistent(self) -> Self:
        if self.status is StageStatus.RUNNING:
            raise ValueError("a stage result is done or failed, never running")
        failed = self.status is StageStatus.FAILED
        if failed != (self.error_code is not None):
            raise ValueError("error_code is set when, and only when, status is failed")
        expected = AuditAction.STAGE_FAILED if failed else self.DONE_ACTION
        if self.audit.action is not expected:
            raise ValueError(
                f"a {self.status.value} result carries a {expected.value} audit record"
            )
        if self.audit.case_id != self.case_id:
            raise ValueError("the audit record must be about the same case")
        return self
