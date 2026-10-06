"""Payloads of the operations `verdict` owns (AD-10, AD-15)."""

from typing import Annotated, ClassVar, Literal, Self

from pydantic import Field, JsonValue, model_validator

from contracts.audit import AuditAction
from contracts.base import (
    Confidence,
    ContractModel,
    Milliseconds,
    NonEmptyStr,
    Percent,
    UtcDatetime,
)
from contracts.enums import (
    ReasonEffect,
    RetrieverConfig,
    StageStatus,
    SystemReason,
    ToolName,
    Verdict,
)
from contracts.errors import ErrorCode
from contracts.ids import CaseId, FactId, VerdictRunId
from contracts.models._stage import StageCommand, StageResult
from contracts.rules import RuleId

# AD-10: the exact label every verdict payload and the result screen carry.
SUGGESTION_LABEL: Literal["AI suggestion, not a decision"] = (
    "AI suggestion, not a decision"
)

StepNumber = Annotated[int, Field(ge=1)]


class Reason(ContractModel):
    """One cited reason: a rule, the facts it was applied to, and its effect."""

    rule_id: RuleId
    fact_ids: Annotated[list[FactId], Field(min_length=1)]
    effect: ReasonEffect
    # Set when, and only when, the effect is `debit`.
    debit_pct: Percent | None

    @model_validator(mode="after")
    def _debit_pct_follows_effect(self) -> Self:
        if (self.effect is ReasonEffect.DEBIT) != (self.debit_pct is not None):
            raise ValueError("debit_pct is set when, and only when, effect is debit")
        return self


class VerdictOutput(ContractModel):
    """What the verdict agent must return; `verdict`'s domain code finishes it (AD-15)."""

    verdict: Verdict
    confidence: Confidence
    reasons: list[Reason]
    system_reasons: list[SystemReason]


class SearchRulesArguments(ContractModel):
    """Arguments of the agent tool `search_rules`."""

    query: NonEmptyStr
    fact_id: FactId


class ReadRuleArguments(ContractModel):
    """Arguments of the agent tool `read_rule`."""

    rule_id: RuleId


class VerdictRunCommand(StageCommand):
    """Request of `POST /verdict-runs`; key `case_id` + `retriever_config`."""

    case_id: CaseId
    retriever_config: RetrieverConfig


class VerdictRunResult(StageResult):
    DONE_ACTION: ClassVar[AuditAction] = AuditAction.VERDICT_SUGGESTED

    verdict_run_id: VerdictRunId
    retriever_config: RetrieverConfig
    # Null when the stage failed.
    verdict: Verdict | None

    @model_validator(mode="after")
    def _verdict_when_done(self) -> Self:
        if (self.status is StageStatus.DONE) != (self.verdict is not None):
            raise ValueError("verdict is set when, and only when, status is done")
        return self


class VerdictRun(ContractModel):
    """One verdict run as read by `web`: always a suggestion, never a decision."""

    verdict_run_id: VerdictRunId
    case_id: CaseId
    retriever_config: RetrieverConfig
    status: StageStatus
    label: Literal["AI suggestion, not a decision"] = SUGGESTION_LABEL
    # `verdict` and `confidence` are null until the run is done.
    verdict: Verdict | None
    # Set when, and only when, the verdict is `loaded`.
    loading_pct: Percent | None
    confidence: Confidence | None
    reasons: list[Reason]
    system_reasons: list[SystemReason]
    error_code: ErrorCode | None

    @model_validator(mode="after")
    def _shape_follows_status(self) -> Self:
        done = self.status is StageStatus.DONE
        if done != (self.verdict is not None) or done != (self.confidence is not None):
            raise ValueError(
                "verdict and confidence are set when, and only when, status is done"
            )
        if (self.status is StageStatus.FAILED) != (self.error_code is not None):
            raise ValueError("error_code is set when, and only when, status is failed")
        if (self.verdict is Verdict.LOADED) != (self.loading_pct is not None):
            raise ValueError(
                "loading_pct is set when, and only when, the verdict is loaded"
            )
        return self


class VerdictRunList(ContractModel):
    """Response of `GET /cases/{case_id}/verdict-runs`."""

    case_id: CaseId
    verdict_runs: list[VerdictRun]


class AgentStep(ContractModel):
    """One tool call of the verdict agent, as logged in `verdict.agent_step`."""

    verdict_run_id: VerdictRunId
    case_id: CaseId
    step_no: StepNumber
    tool: ToolName
    arguments: dict[str, JsonValue]
    fact_id: FactId | None
    # The rules the call returned or read.
    rule_ids: list[RuleId]
    latency_ms: Milliseconds
    occurred_at: UtcDatetime


class AgentStepQuery(ContractModel):
    """Query of `GET /cases/{case_id}/agent-steps?tool=&rule_id=`; both filters are optional."""

    tool: ToolName | None = None
    rule_id: RuleId | None = None


class AgentStepList(ContractModel):
    """Response of both agent log reads, in step order."""

    steps: list[AgentStep]
