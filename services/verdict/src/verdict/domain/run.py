"""Suggest a verdict for one case with one retriever row (AD-15, AD-10, AD-6).

The stage `workflow` commands once every page of a case is final, one run
per retriever configuration. The agent reads the case's facts, searches the
manual and reads rules through its three tools, and proposes reasons. On
row `r6`, whose retrieval plans its own queries, that loop is off: the run
makes one search per fact itself and the model composes its proposal from
what returned (`compose.py`). What
is stored is decided in `decide.py`, by code: the reasons the run can bear
out, the rules that refer a case, the verdict and the loading. It is a
suggestion: nothing here, and nothing anywhere, stores a decision.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from contracts.audit import AuditAction, AuditRecord, VerdictDetail
from contracts.enums import ActorKind, RetrieverConfig, StageStatus
from contracts.errors import NO_TRACE_ID, DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.retrieval import DEFAULT_TOP_K
from contracts.models.verdict import (
    AgentStepList,
    AgentStepQuery,
    RunStepQuery,
    VerdictRun,
    VerdictRunCommand,
    VerdictRunList,
    VerdictRunResult,
)
from verdict.domain.compose import searched_material
from verdict.domain.decide import (
    DEFAULT_CONFIDENCE_FLOOR,
    AnswerCutOff,
    InvalidModelOutput,
    KeptReasons,
    decide,
    read_proposal,
    stopped_at_the_step_limit,
    without_facts,
)
from verdict.domain.entities import AgentAnswer, KeyRow, RunKey, Suggestion
from verdict.domain.ports import (
    AgentFailed,
    FactReader,
    KeyRowGone,
    ModelCallFailed,
    ModelUnavailable,
    RuleLibrary,
    RunRepository,
    VerdictAgent,
)
from verdict.domain.toolbox import (
    DEFAULT_STEP_LIMIT,
    StepLimitReached,
    Toolbox,
    ToolFailed,
    ToolPorts,
)

logger = logging.getLogger(__name__)

IN_PROGRESS_MESSAGE = "The verdict run is still under way."
NOT_RECORDED_MESSAGE = "The verdict run could not be recorded. Please try again."
UNKNOWN_RUN_MESSAGE = "That verdict run could not be found."
MODEL_UNAVAILABLE_MESSAGE = "The model is not available. Please try again shortly."
UPSTREAM_UNAVAILABLE_MESSAGE = (
    "A service the verdict needs is not available. Please try again shortly."
)
RELEASED_MESSAGE = "The verdict run was given up before it ended. Please try again."

# How many runs or steps one read lists at most (VERDICT_RUN_LIST_LIMIT,
# VERDICT_STEP_LIST_LIMIT).
DEFAULT_RUN_LIST_LIMIT = 50
DEFAULT_STEP_LIST_LIMIT = 500

# The model's finish reason for an answer cut off at the token limit.
CUT_OFF = "length"

_NO_TRACE_CONTEXT: Mapping[str, str] = MappingProxyType({})


def utc_now() -> datetime:
    """The current time, in UTC."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RunPorts:
    """What a verdict run works with; the app factory or a test provides it."""

    repository: RunRepository
    facts: FactReader
    rules: RuleLibrary
    agent: VerdictAgent


# AD-11: the ladder rows this build can run a verdict with: the ones
# `retrieval` can search. On `r1` the rules come from `fixed` chunks, which
# may hold several definitions or the start of one: a reason is checked
# against the rule's own definition inside the chunk
# (`effects.definition_of`), and a definition cut off before its rating
# bears out nothing. `r4` answers the `smart` chunks `r3` finds, in a
# reranker's order; `r5` answers the same `smart` chunks as `r2` and `r3`,
# from Azure AI Search, and so does `r6`, through that service's knowledge
# base.
# Named one by one: a row the contracts gain later is not run by the
# agent's default loop until it is put here.
RUNNABLE_RETRIEVER_CONFIGS: frozenset[RetrieverConfig] = frozenset(
    {
        RetrieverConfig.R1,
        RetrieverConfig.R2,
        RetrieverConfig.R3,
        RetrieverConfig.R4,
        RetrieverConfig.R5,
        RetrieverConfig.R6,
    }
)
# The rows `retrieval` answers only where it was given the chat deployment:
# its reranker (`r4`), and what the knowledge base plans with (`r6`).
RERANKER_RETRIEVER_CONFIGS: frozenset[RetrieverConfig] = frozenset({RetrieverConfig.R4})
CHAT_DEPLOYMENT_RETRIEVER_CONFIGS: frozenset[RetrieverConfig] = (
    RERANKER_RETRIEVER_CONFIGS | {RetrieverConfig.R6}
)
# The rows `retrieval` answers only where it was given a search service.
SEARCH_SERVICE_RETRIEVER_CONFIGS: frozenset[RetrieverConfig] = frozenset(
    {RetrieverConfig.R5, RetrieverConfig.R6}
)
# AD-15: the rows whose retrieval plans its own queries. On these the
# agent's search loop is off: one search per fact, made by the run, and one
# call of the model to compose the proposal, with no tool.
SERVICE_PLANNED_RETRIEVER_CONFIGS: frozenset[RetrieverConfig] = frozenset(
    {RetrieverConfig.R6}
)
# The rows a verdict may be commanded with unless the settings say otherwise
# (VERDICT_AVAILABLE_RETRIEVER_CONFIGS): the ones that need no search
# service and no chat deployment at `retrieval`. `workflow` and `retrieval` name the same rows,
# each in its own settings, and a test outside `services/` holds the three
# lists equal.
DEFAULT_AVAILABLE_RETRIEVER_CONFIGS: frozenset[RetrieverConfig] = (
    RUNNABLE_RETRIEVER_CONFIGS
    - SEARCH_SERVICE_RETRIEVER_CONFIGS
    - CHAT_DEPLOYMENT_RETRIEVER_CONFIGS
)


def _rows_in_words(configs: frozenset[RetrieverConfig]) -> str:
    """The rows as a message names them: `Rows r1, r2 and r3`, or `Only row r3`."""
    *others, last = sorted(config.value for config in configs)
    return f"Rows {', '.join(others)} and {last}" if others else f"Only row {last}"


def row_not_available_message(available: frozenset[RetrieverConfig]) -> str:
    """What a caller is told who commanded a run with a row this service does not run."""
    return (
        "A verdict cannot be suggested with that retrieval row here. "
        f"{_rows_in_words(available)} can be used for now."
    )


@dataclass(frozen=True, slots=True)
class RunOptions:
    """The settings a verdict run works with (VERDICT_*: see the settings)."""

    # AD-8: who suggested, as `verdict:<chat deployment name>`.
    actor: str
    deadline_seconds: float = 180.0
    stale_margin_seconds: float = 60.0
    # AD-15: the most tool calls one run may make.
    step_limit: int = DEFAULT_STEP_LIMIT
    # How long the agent may work in all. When it is spent the agent is
    # stopped and the case referred, as at the step limit. Safely inside
    # `deadline_seconds`, so that the stopping itself is stored in time.
    agent_budget_seconds: float = 150.0
    # AD-15: under this confidence a run refers, with `low_confidence`.
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR
    search_top_k: int = DEFAULT_TOP_K
    # AD-11: the ladder rows a verdict may be commanded with here.
    retriever_configs: frozenset[RetrieverConfig] = DEFAULT_AVAILABLE_RETRIEVER_CONFIGS


@dataclass(slots=True)
class _Run:
    """One verdict run under way: what it is for, and since when."""

    verdict_run_id: str
    key: RunKey
    actor: str
    eval_run_id: str | None
    trace_id: str
    started: float = field(default_factory=time.monotonic)


def _audit(run: _Run, action: AuditAction, occurred_at: datetime) -> AuditRecord:
    return AuditRecord(
        actor_kind=ActorKind.AI,
        actor=run.actor,
        action=action,
        occurred_at=occurred_at,
        case_id=run.key.case_id,
        # A verdict is about the case as a whole.
        page_id=None,
        # The owning record of a suggestion is its run.
        ref=run.verdict_run_id,
        # AD-15: a suggestion says which retriever row it was made with.
        detail=VerdictDetail(retriever_config=run.key.retriever_config)
        if action is AuditAction.VERDICT_SUGGESTED
        else None,
        trace_id=run.trace_id,
        eval_run_id=run.eval_run_id,
    )


def done_result(
    run: _Run, suggestion: Suggestion, occurred_at: datetime
) -> VerdictRunResult:
    """The result of a finished run, with its `verdict.suggested` record."""
    return VerdictRunResult(
        case_id=run.key.case_id,
        status=StageStatus.DONE,
        error_code=None,
        audit=_audit(run, AuditAction.VERDICT_SUGGESTED, occurred_at),
        verdict_run_id=run.verdict_run_id,
        retriever_config=run.key.retriever_config,
        verdict=suggestion.verdict,
    )


def failed_result(
    run: _Run, error_code: ErrorCode, occurred_at: datetime
) -> VerdictRunResult:
    """The result of a run that failed: no verdict, and the code that says why."""
    return VerdictRunResult(
        case_id=run.key.case_id,
        status=StageStatus.FAILED,
        error_code=error_code,
        audit=_audit(run, AuditAction.STAGE_FAILED, occurred_at),
        verdict_run_id=run.verdict_run_id,
        retriever_config=run.key.retriever_config,
        verdict=None,
    )


def verdict_run(
    verdict_run_id: str,
    key: RunKey,
    status: StageStatus,
    suggestion: Suggestion | None,
    error_code: ErrorCode | None,
) -> VerdictRun:
    """One run as it is read: a suggestion with its label, never a decision (AD-10).

    `suggestion` is what a done run stored; a running or failed run has none.
    """
    return VerdictRun(
        verdict_run_id=verdict_run_id,
        case_id=key.case_id,
        retriever_config=key.retriever_config,
        status=status,
        verdict=suggestion.verdict if suggestion is not None else None,
        loading_pct=suggestion.loading_pct if suggestion is not None else None,
        confidence=suggestion.confidence if suggestion is not None else None,
        reasons=list(suggestion.reasons) if suggestion is not None else [],
        system_reasons=list(suggestion.system_reasons)
        if suggestion is not None
        else [],
        error_code=error_code,
    )


async def run_verdict(
    command: VerdictRunCommand,
    *,
    ports: RunPorts,
    options: RunOptions,
    trace_id: str | None = None,
    trace_context: Mapping[str, str] = _NO_TRACE_CONTEXT,
    now: Callable[[], datetime] = utc_now,
) -> VerdictRunResult:
    """Run the verdict agent for one case and one retriever row; idempotent on that pair.

    A row this build cannot run is `retriever_not_available`: it leaves no
    key row, and is not worth sending again. Otherwise the key row is
    inserted as running before any work. A repeat while it runs is
    `in_progress`; a repeat after the end is answered with the stored result
    and calls neither the model nor another service. After
    `options.deadline_seconds`, for the facts, the whole of the agent's work
    and the storing together, a failed result is stored (`stage_timeout`). A
    key row still running `options.stale_margin_seconds` after its deadline
    is settled as failed by the next repeat, which does not take the work
    over: the run's steps are in the log under its id already.

    What a repeat of the command can mend is not stored as a failure. A
    request cancelled because the caller went away or the service is
    stopping gives the key row up, so the command sent again runs. So does
    a fault that passes: the model unavailable after the gateway's retries
    (`model_unavailable`), or `extraction` or `retrieval` giving no answer
    after the client's (`upstream_unavailable`); the caller is answered
    with that error and sends the command again, as for the other stages.
    The steps such a run logged stay in the log. When the agent's time
    budget is spent the agent is stopped and the case referred
    (`step_limit`), as at the step limit.
    """
    if command.retriever_config not in options.retriever_configs:
        raise DomainError(
            ErrorCode.RETRIEVER_NOT_AVAILABLE,
            row_not_available_message(options.retriever_configs),
        )
    key = RunKey(command.case_id, command.retriever_config)
    repository = ports.repository
    earlier = await repository.find(key)
    run = _Run(
        verdict_run_id=new_id(),
        key=key,
        actor=options.actor,
        eval_run_id=command.eval_run_id,
        trace_id=trace_id or NO_TRACE_ID,
    )
    if earlier is None:
        try:
            earlier = await repository.begin(run.verdict_run_id, key, now())
        except asyncio.CancelledError:
            # Cancelled while the key row was being written: it may be
            # there, with nobody to do its work. It is given up, if it is.
            await asyncio.shield(_release(run, "cancelled", repository))
            raise
    if earlier is not None:
        return await _repeat(earlier, run, ports, options, now)

    # One deadline for everything: the facts, every call of the agent and
    # of its tools, and the storing of the result.
    deadline = asyncio.timeout(options.deadline_seconds)
    try:
        async with deadline:
            return await _suggest(run, ports, options, trace_context, now)
    except asyncio.CancelledError:
        # The caller went away or the service is stopping, with the run
        # half done. The key row is given up, whatever happens to the
        # request from here on, so that the command sent again runs.
        await asyncio.shield(_release(run, "cancelled", repository))
        raise
    except Exception as error:  # noqa: BLE001 - whatever failed, the run is released or ends as failed with a code
        if isinstance(error, KeyRowGone):
            # Given up by a cancellation while the result was on its way:
            # as for any released row, the command sent again runs.
            logger.warning(
                "verdict run not stored: case_id=%s verdict_run_id=%s reason=key_row_gone",
                key.case_id,
                run.verdict_run_id,
            )
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, RELEASED_MESSAGE
            ) from None
        error_code, reason = _failure_of(error, deadline.expired())
        passing = _PASSING.get(error_code)
        if passing is not None:
            # Nothing is wrong with the case: the key row is given up and
            # the caller told to send the command again.
            await asyncio.shield(_release(run, reason, repository))
            raise DomainError(error_code, passing) from None
    # Settled whatever happens to the request from here on: a request
    # cancelled now must not leave the key row `running`.
    return await asyncio.shield(_fail(run, error_code, reason, repository, now))


# The faults that pass, and what the caller is told: the run is not stored
# as failed for them.
_PASSING: Mapping[ErrorCode, str] = MappingProxyType(
    {
        ErrorCode.MODEL_UNAVAILABLE: MODEL_UNAVAILABLE_MESSAGE,
        ErrorCode.UPSTREAM_UNAVAILABLE: UPSTREAM_UNAVAILABLE_MESSAGE,
    }
)


def _failure_of(error: Exception, deadline_passed: bool) -> tuple[ErrorCode, str]:
    """The error code a failure is stored under, and a reason for the log.

    security rule 31: the reason is a code or the error's type, never its
    message, which could hold a fact, a rule's text or the model's answer.
    """
    if isinstance(error, TimeoutError) and deadline_passed:
        return ErrorCode.STAGE_TIMEOUT, "deadline"
    if isinstance(error, AnswerCutOff):
        # The catalogue's code for an answer that is not valid; the log says
        # that it was cut off at the token limit, which a setting mends.
        return ErrorCode.INVALID_MODEL_OUTPUT, "answer_cut_off"
    if isinstance(error, InvalidModelOutput):
        return ErrorCode.INVALID_MODEL_OUTPUT, "invalid_model_output"
    if isinstance(error, ModelUnavailable):
        return ErrorCode.MODEL_UNAVAILABLE, "model_unavailable"
    if isinstance(error, ModelCallFailed):
        return ErrorCode.STAGE_FAILED, error.reason
    if isinstance(error, AgentFailed):
        # What the framework raised, by its type.
        return ErrorCode.STAGE_FAILED, f"agent:{error.kind}"
    if isinstance(error, ToolFailed):
        return error.code, f"tool:{error.code.value}"
    if isinstance(error, DomainError):
        if error.code is ErrorCode.UPSTREAM_UNAVAILABLE:
            # `extraction` could not be had for the facts.
            return ErrorCode.UPSTREAM_UNAVAILABLE, "facts:upstream_unavailable"
        return ErrorCode.STAGE_FAILED, error.code.value
    return ErrorCode.STAGE_FAILED, type(error).__qualname__


async def _repeat(
    earlier: KeyRow,
    run: _Run,
    ports: RunPorts,
    options: RunOptions,
    now: Callable[[], datetime],
) -> VerdictRunResult:
    """Answer a command whose key row exists already (AD-6)."""
    key = earlier.key
    if earlier.result_json is not None:
        logger.info(
            "verdict run repeated: case_id=%s retriever_config=%s verdict_run_id=%s",
            key.case_id,
            key.retriever_config.value,
            earlier.verdict_run_id,
        )
        return VerdictRunResult.model_validate_json(earlier.result_json)
    limit = timedelta(seconds=options.deadline_seconds + options.stale_margin_seconds)
    if now() - earlier.started_at <= limit:
        raise DomainError(ErrorCode.IN_PROGRESS, IN_PROGRESS_MESSAGE)
    # Nothing is working on it any more. It fails, as it would have at its
    # deadline, under the id its key row was given.
    stale = _Run(
        verdict_run_id=earlier.verdict_run_id,
        key=key,
        actor=run.actor,
        eval_run_id=run.eval_run_id,
        trace_id=run.trace_id,
    )
    return await asyncio.shield(
        _fail(stale, ErrorCode.STAGE_TIMEOUT, "stale", ports.repository, now)
    )


async def _suggest(
    run: _Run,
    ports: RunPorts,
    options: RunOptions,
    trace_context: Mapping[str, str],
    now: Callable[[], datetime],
) -> VerdictRunResult:
    key = run.key
    # Read once, when the run begins: every tool call of the run sees these facts.
    facts = await ports.facts.facts_of_case(key.case_id, trace_context)
    kept: KeptReasons | None = None
    steps = 0
    if not facts:
        # Every page was discarded or denied, or none held a fact. No rule
        # can match nothing: the agent is not asked.
        suggestion = without_facts()
    else:
        toolbox = Toolbox(
            verdict_run_id=run.verdict_run_id,
            case_id=key.case_id,
            retriever_config=key.retriever_config,
            facts=facts,
            ports=ToolPorts(rules=ports.rules, repository=ports.repository),
            trace_context=trace_context,
            now=now,
            step_limit=options.step_limit,
            search_top_k=options.search_top_k,
            searches_count_as_reads=key.retriever_config
            in SERVICE_PLANNED_RETRIEVER_CONFIGS,
        )
        suggestion, kept = await _work(toolbox, ports.agent, options)
        steps = toolbox.state.steps
    result = done_result(run, suggestion, now())
    stored = VerdictRunResult.model_validate_json(
        await _store_done(run, result, suggestion, ports.repository)
    )
    if stored != result:
        # Settled as failed by another call first: that result stands, and
        # this suggestion was not stored.
        logger.warning(
            "verdict run superseded: case_id=%s retriever_config=%s verdict_run_id=%s",
            key.case_id,
            key.retriever_config.value,
            run.verdict_run_id,
        )
        return stored
    # Ids, codes, counts and timings: never a fact, a query or a rule's text.
    logger.info(
        "verdict suggested: case_id=%s retriever_config=%s verdict_run_id=%s "
        "verdict=%s loading_pct=%s system_reasons=%s facts=%d steps=%d reasons=%d "
        "reasons_dropped=%d dropped_by=%s duration_ms=%d",
        key.case_id,
        key.retriever_config.value,
        run.verdict_run_id,
        suggestion.verdict.value,
        suggestion.loading_pct,
        ",".join(reason.value for reason in suggestion.system_reasons) or "none",
        len(facts),
        steps,
        len(suggestion.reasons),
        suggestion.dropped,
        ",".join(f"{cause}:{count}" for cause, count in sorted(kept.dropped.items()))
        if kept is not None and kept.dropped
        else "none",
        int((time.monotonic() - run.started) * 1000),
    )
    return stored


async def _work(
    toolbox: Toolbox, agent: VerdictAgent, options: RunOptions
) -> tuple[Suggestion, KeptReasons | None]:
    """Let the agent work the run through, and decide what is stored of it.

    On a row whose retrieval plans its own queries (`r6`) the run makes the
    searches, one per fact, and the agent composes from them in one call
    without a tool. The step limit, the time budget and everything decided
    afterwards are the same on every row.
    """
    answer: AgentAnswer | None
    try:
        # AD-15: the agent's work has a time budget of its own, inside the
        # stage deadline. When it is spent the agent is stopped where it is.
        async with asyncio.timeout(options.agent_budget_seconds) as budget:
            if toolbox.retriever_config in SERVICE_PLANNED_RETRIEVER_CONFIGS:
                answer = await agent.compose(toolbox, await searched_material(toolbox))
            else:
                answer = await agent.run(toolbox)
    except StepLimitReached:
        answer = None
    except TimeoutError:
        if not budget.expired():
            raise
        # Out of time is the step limit by another measure: the case is
        # referred, not failed.
        toolbox.state.step_limit_reached = True
        answer = None
    if toolbox.failure is not None:
        # A tool's service gave no answer: whatever the agent made of that,
        # the run failed there.
        raise toolbox.failure
    if toolbox.state.step_limit_reached:
        # AD-15: the limit stopped the run. Whatever the agent may still
        # have said, it is not an answer the run was allowed to reach.
        return stopped_at_the_step_limit(), None
    if answer is None:
        # No answer and no limit: the agent ended without saying anything.
        raise InvalidModelOutput
    if answer.finish_reason == CUT_OFF:
        raise AnswerCutOff
    return decide(
        read_proposal(answer.text),
        toolbox.state,
        confidence_floor=options.confidence_floor,
    )


async def _store_done(
    run: _Run,
    result: VerdictRunResult,
    suggestion: Suggestion,
    repository: RunRepository,
) -> str:
    """Store a done result, trying once more if the first attempt fails.

    The agent's work is done by now, and a store that fails once often
    works the next moment. A second failure is raised: the run then ends as
    failed, if that at least can be stored.
    """
    try:
        return await repository.finish(
            run.verdict_run_id, result.model_dump_json(), suggestion, None
        )
    except Exception as error:  # noqa: BLE001 - whatever failed, it is tried once more
        logger.warning(
            "verdict run store retried: case_id=%s verdict_run_id=%s type=%s",
            run.key.case_id,
            run.verdict_run_id,
            type(error).__qualname__,
        )
    return await repository.finish(
        run.verdict_run_id, result.model_dump_json(), suggestion, None
    )


async def _release(run: _Run, reason: str, repository: RunRepository) -> None:
    """Give the key row up, so that the same command sent again does the work."""
    key = run.key
    try:
        await repository.release(run.verdict_run_id)
        released = True
    except Exception as error:  # noqa: BLE001 - the row then stays `running`; a later repeat settles it
        released = False
        reason = f"{reason}:{type(error).__qualname__}"
    logger.warning(
        "verdict run released: case_id=%s retriever_config=%s verdict_run_id=%s "
        "reason=%s released=%s duration_ms=%d",
        key.case_id,
        key.retriever_config.value,
        run.verdict_run_id,
        reason,
        str(released).lower(),
        int((time.monotonic() - run.started) * 1000),
    )


async def _fail(
    run: _Run,
    error_code: ErrorCode,
    reason: str,
    repository: RunRepository,
    now: Callable[[], datetime],
) -> VerdictRunResult:
    """End a run as failed: store the result that says why, and no reason."""
    key = run.key
    result = failed_result(run, error_code, now())
    try:
        stored = VerdictRunResult.model_validate_json(
            await repository.finish(
                run.verdict_run_id, result.model_dump_json(), None, error_code
            )
        )
    except Exception as error:
        # The key row stays `running`; a later repeat settles it.
        logger.error(
            "verdict run failed: case_id=%s retriever_config=%s verdict_run_id=%s "
            "error_code=%s reason=%s stored=false type=%s",
            key.case_id,
            key.retriever_config.value,
            run.verdict_run_id,
            error_code.value,
            reason,
            type(error).__qualname__,
        )
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, NOT_RECORDED_MESSAGE
        ) from error
    logger.error(
        "verdict run failed: case_id=%s retriever_config=%s verdict_run_id=%s "
        "error_code=%s reason=%s stored=%s duration_ms=%d",
        key.case_id,
        key.retriever_config.value,
        run.verdict_run_id,
        error_code.value,
        reason,
        stored.status.value,
        int((time.monotonic() - run.started) * 1000),
    )
    return stored


def _bounded[T](rows: list[T], limit: int) -> tuple[list[T], bool]:
    """The first `limit` rows, and whether there were more."""
    return rows[:limit], len(rows) > limit


async def list_verdict_runs(
    case_id: str, *, repository: RunRepository, limit: int = DEFAULT_RUN_LIST_LIMIT
) -> VerdictRunList:
    """The case's verdict runs, oldest first, at most `limit`; an empty list for a case with none."""
    runs, has_more = _bounded(await repository.runs_of_case(case_id, limit + 1), limit)
    return VerdictRunList(case_id=case_id, verdict_runs=runs, has_more=has_more)


async def list_run_steps(
    verdict_run_id: str,
    query: RunStepQuery,
    *,
    repository: RunRepository,
    limit: int = DEFAULT_STEP_LIST_LIMIT,
) -> AgentStepList:
    """One run's tool calls in order, at most `limit`, from the cursor on; only matching ones with a filter.

    `not_found` for a run that is not stored.
    """
    if not await repository.run_exists(verdict_run_id):
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_RUN_MESSAGE)
    steps, has_more = _bounded(
        await repository.steps_of_run(
            verdict_run_id, query.tool, query.rule_id, query.after_step_no, limit + 1
        ),
        limit,
    )
    return AgentStepList(steps=steps, has_more=has_more)


async def list_case_agent_steps(
    case_id: str,
    query: AgentStepQuery,
    *,
    repository: RunRepository,
    limit: int = DEFAULT_STEP_LIST_LIMIT,
) -> AgentStepList:
    """The tool calls of every run of a case in order, at most `limit`, from the cursor on; only matching ones with a filter."""
    after = (
        (query.after_verdict_run_id, query.after_step_no)
        if query.after_verdict_run_id is not None and query.after_step_no is not None
        else None
    )
    steps, has_more = _bounded(
        await repository.steps_of_case(
            case_id, query.tool, query.rule_id, after, limit + 1
        ),
        limit,
    )
    return AgentStepList(steps=steps, has_more=has_more)
