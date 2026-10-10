"""Payloads of the operations `verdict` owns (AD-10, AD-15)."""

from typing import Annotated, ClassVar, Literal, Self

from pydantic import Field, JsonValue, StringConstraints, model_validator

from contracts.audit import AuditAction, VerdictDetail
from contracts.base import (
    Confidence,
    ContractModel,
    Milliseconds,
    Percent,
    UtcDatetime,
)
from contracts.enums import (
    ReasonEffect,
    RetrieverConfig,
    StageStatus,
    StepOutcome,
    SystemReason,
    ToolName,
    Verdict,
)
from contracts.errors import ErrorCode
from contracts.ids import CaseId, FactId, VerdictRunId
from contracts.models._stage import StageCommand, StageResult
from contracts.models.retrieval import MAX_QUERY_CHARS
from contracts.rules import RuleId

# AD-10: the exact label every verdict payload and the result screen carry.
SUGGESTION_LABEL: Literal["AI suggestion, not a decision"] = (
    "AI suggestion, not a decision"
)

# A step number is stored in a 4-byte integer column: a larger one names no
# step, and as a cursor it is refused, not handed to the database.
MAX_STEP_NUMBER = 2**31 - 1
StepNumber = Annotated[int, Field(ge=1, le=MAX_STEP_NUMBER)]

# The name of a tool the model asked for and the agent does not have. The
# model wrote it, so it is text and nothing more (security rules 14 and 15):
# never a `ToolName`, and never longer than this.
MAX_ASKED_TOOL_CHARS = 64
AskedTool = Annotated[str, StringConstraints(max_length=MAX_ASKED_TOOL_CHARS)]


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

    # Not blank, and no longer than a search takes (`SearchRequest.query`).
    query: Annotated[str, StringConstraints(pattern=r"\S", max_length=MAX_QUERY_CHARS)]
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
        detail = self.audit.detail
        if isinstance(detail, VerdictDetail) and (
            detail.retriever_config is not self.retriever_config
        ):
            raise ValueError("the audit record must name the run's retriever_config")
        return self


class VerdictRun(ContractModel):
    """One verdict run as read by `web`: always a suggestion, never a decision."""

    verdict_run_id: VerdictRunId
    case_id: CaseId
    retriever_config: RetrieverConfig
    status: StageStatus
    label: Literal["AI suggestion, not a decision"] = SUGGESTION_LABEL
    # Null until the run is done.
    verdict: Verdict | None
    # Set when, and only when, the verdict is `loaded`.
    loading_pct: Percent | None
    # The agent's own figure. Null until the run is done, and for a done run
    # in which the agent gave no answer: a case without facts, or a run the
    # step limit stopped.
    confidence: Confidence | None
    reasons: list[Reason]
    system_reasons: list[SystemReason]
    error_code: ErrorCode | None

    @model_validator(mode="after")
    def _shape_follows_status(self) -> Self:
        done = self.status is StageStatus.DONE
        if done != (self.verdict is not None):
            raise ValueError("verdict is set when, and only when, status is done")
        if not done and self.confidence is not None:
            raise ValueError("confidence is set only when status is done")
        if not done and (self.reasons or self.system_reasons):
            raise ValueError("only a done run has reasons")
        if (self.verdict is Verdict.REFER) != bool(self.system_reasons):
            raise ValueError(
                "system_reasons are set when, and only when, the verdict is refer"
            )
        if (self.status is StageStatus.FAILED) != (self.error_code is not None):
            raise ValueError("error_code is set when, and only when, status is failed")
        if (self.verdict is Verdict.LOADED) != (self.loading_pct is not None):
            raise ValueError(
                "loading_pct is set when, and only when, the verdict is loaded"
            )
        return self


class VerdictRunList(ContractModel):
    """Response of `GET /cases/{case_id}/verdict-runs`: the case's runs, oldest first.

    The answer is bounded: `has_more` says that the case has more runs than
    are listed.
    """

    case_id: CaseId
    verdict_runs: list[VerdictRun]
    has_more: bool


class AgentStep(ContractModel):
    """One tool call of the verdict agent, as logged in `verdict.agent_step`.

    A call to a tool the agent does not have is a step too (owner,
    2026-10-08): `tool` is then null, `asked_tool` is the name the model
    asked for, and the step is refused. Its arguments are not kept: they
    are of no known shape.
    """

    verdict_run_id: VerdictRunId
    case_id: CaseId
    step_no: StepNumber
    # One of the agent's three tools; null when, and only when, the model
    # asked for a tool that does not exist.
    tool: ToolName | None
    # Set when, and only when, `tool` is null: the name asked for, cut to
    # its bound. Untrusted text, to be shown as text.
    asked_tool: AskedTool | None = None
    arguments: dict[str, JsonValue]
    fact_id: FactId | None
    # The rules the call returned or read.
    rule_ids: list[RuleId]
    outcome: StepOutcome
    # Set when, and only when, the call was refused or failed: why, from the
    # error catalogue (`rule_not_seen` for a `read_rule` the run may not make).
    error_code: ErrorCode | None
    latency_ms: Milliseconds
    occurred_at: UtcDatetime

    @model_validator(mode="after")
    def _error_code_follows_outcome(self) -> Self:
        if (self.outcome is StepOutcome.DONE) != (self.error_code is None):
            raise ValueError(
                "error_code is set when, and only when, the call was refused or failed"
            )
        if self.outcome is not StepOutcome.DONE and self.rule_ids:
            raise ValueError("a call that was refused or failed returned no rules")
        return self

    @model_validator(mode="after")
    def _asked_tool_stands_in_for_no_tool(self) -> Self:
        if (self.tool is None) != (self.asked_tool is not None):
            raise ValueError("asked_tool is set when, and only when, tool is null")
        if self.tool is None and (
            self.outcome is not StepOutcome.REFUSED
            or self.arguments
            or self.fact_id is not None
        ):
            raise ValueError(
                "a call to a tool that does not exist is refused, and nothing "
                "of it is kept but the name asked for"
            )
        return self


class RunStepQuery(ContractModel):
    """Query of `GET /verdict-runs/{verdict_run_id}/steps`; every field is optional.

    `tool` and `rule_id` narrow the run's steps as they narrow a case's.
    `tool` names one of the three tools: a step that asked for a tool that
    does not exist is listed only when no tool is named.
    `after_step_no` is the cursor: the last step number seen, so that the
    steps beyond one answer's limit can be read.
    """

    tool: ToolName | None = None
    rule_id: RuleId | None = None
    after_step_no: StepNumber | None = None


class AgentStepQuery(ContractModel):
    """Query of `GET /cases/{case_id}/agent-steps?tool=&rule_id=`; every field is optional.

    The cursor names the last step seen. A case's steps span its runs, and a
    step number is one run's own, so the cursor is that step's run and its
    number, given together.
    """

    tool: ToolName | None = None
    rule_id: RuleId | None = None
    after_verdict_run_id: VerdictRunId | None = None
    after_step_no: StepNumber | None = None

    @model_validator(mode="after")
    def _cursor_is_whole(self) -> Self:
        if (self.after_verdict_run_id is None) != (self.after_step_no is None):
            raise ValueError(
                "after_verdict_run_id and after_step_no are given together"
            )
        return self


class AgentStepList(ContractModel):
    """Response of both agent log reads: the steps in the order they were made.

    Within a run by step number; across the runs of a case, run after run.
    The answer is bounded: `has_more` says that more steps exist than are
    listed; they are read by asking again with the last step listed as the
    cursor.
    """

    steps: list[AgentStep]
    has_more: bool
