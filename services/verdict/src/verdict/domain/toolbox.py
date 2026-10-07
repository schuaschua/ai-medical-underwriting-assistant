"""The agent's three tools, as one run may use them (AD-15, security rules 15 and 16).

`list_facts`, `search_rules` and `read_rule`, and no fourth. The case, the
run and the retriever row are fixed here by the server: the model names none
of them. Every argument the model gives is checked against the contracts
before anything is done with it, and `read_rule` takes only a rule the run
has seen. Every call is one row of the step log, refused ones included.
Nothing here imports a framework: the adapter that runs the agent hands the
model's calls to `Toolbox.call`.
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import JsonValue, ValidationError

from contracts.enums import RetrieverConfig, StepOutcome, ToolName
from contracts.errors import DomainError, ErrorCode
from contracts.models.extraction import Fact
from contracts.models.retrieval import DEFAULT_TOP_K, MAX_QUERY_CHARS
from contracts.models.verdict import (
    AgentStep,
    ReadRuleArguments,
    SearchRulesArguments,
)
from verdict.domain.ports import RuleLibrary, RunRepository
from verdict.domain.state import RunState

logger = logging.getLogger(__name__)

DEFAULT_STEP_LIMIT = 30

# What a refused call tells the model: plain sentences, no internals.
INVALID_ARGUMENTS_MESSAGE = "The arguments are not valid for this tool."
UNKNOWN_TOOL_MESSAGE = "There is no such tool."
FACT_NOT_LISTED_MESSAGE = (
    "That fact_id is not a fact of this case. Call list_facts and use an id from it."
)
RULE_NOT_SEEN_MESSAGE = (
    "That rule may not be read: only a rule that search_rules returned in this "
    "run, or that a rule you have read refers to."
)
RULE_NOT_FOUND_MESSAGE = "The manual defines no rule of that id."
STEP_LIMIT_MESSAGE = "The run is at its limit: no further tool call is made."
# A character no text of the database may hold.
_NUL = "\x00"

# The codes a failed tool call may end a run with; anything else a tool's
# service answers is `upstream_unavailable`.
_FAILURE_CODES = frozenset(
    {ErrorCode.UPSTREAM_UNAVAILABLE, ErrorCode.RETRIEVER_NOT_AVAILABLE}
)


class StepLimitReached(Exception):
    """The run is at its step limit: the call was not made, and the run stops."""


class ToolFailed(Exception):
    """A tool could not do its work: the service behind it gave no answer. The run fails.

    `code` is the error code the run is stored under.
    """

    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class ToolPorts:
    rules: RuleLibrary
    repository: RunRepository


class _Refusal(Exception):
    """A call the tool does not make; the model is told why and the run goes on."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(code.value)
        self.code = code
        self.message = message


def storable(text: str) -> str:
    """A text as the database can hold it: without NUL characters and lone surrogates."""
    return text.replace(_NUL, "").encode("utf-8", "replace").decode("utf-8")


def _loggable(arguments: Mapping[str, object]) -> dict[str, JsonValue]:
    """The arguments of a call as the log keeps them: plain values the database can hold, and no text without end.

    The model wrote them, so nothing about them is taken on trust. Only
    strings, numbers, booleans and nulls are kept, at most ten of them; a
    string is cut at the longest query a search takes; a number that JSON
    has no form for (NaN, an infinity) is kept as its name. A nested value
    is left out.
    """
    kept: dict[str, JsonValue] = {}
    for name, value in list(arguments.items())[:10]:
        if not isinstance(name, str):
            continue
        name = storable(name)[:64]
        if isinstance(value, str):
            kept[name] = storable(value)[:MAX_QUERY_CHARS]
        elif isinstance(value, float) and not math.isfinite(value):
            kept[name] = str(value)
        elif value is None or isinstance(value, bool | int | float):
            kept[name] = value
    return kept


class Toolbox:
    """The three tools of one run, and that run's state and step log.

    `facts` is the case's facts as `extraction` held them when the run
    began: `list_facts` answers them, so every tool call of a run sees the
    same facts.

    On row `r6` no model calls the tools: the run itself lists the facts
    and makes one search per fact through `call` (`domain/compose.py`), so
    that each is checked, counted against the step limit and logged like
    any tool call.
    """

    def __init__(
        self,
        *,
        verdict_run_id: str,
        case_id: str,
        retriever_config: RetrieverConfig,
        facts: Sequence[Fact],
        ports: ToolPorts,
        trace_context: Mapping[str, str],
        now: Callable[[], datetime],
        step_limit: int = DEFAULT_STEP_LIMIT,
        search_top_k: int = DEFAULT_TOP_K,
        searches_count_as_reads: bool = False,
    ) -> None:
        self.verdict_run_id = verdict_run_id
        self.case_id = case_id
        self.retriever_config = retriever_config
        self.state = RunState()
        # The error that ended the run in a tool, if one did.
        self.failure: ToolFailed | None = None
        self._facts = tuple(facts)
        self._fact_ids = frozenset(fact.fact_id for fact in facts)
        self._ports = ports
        self._trace_context = trace_context
        self._now = now
        self._step_limit = step_limit
        self._search_top_k = search_top_k
        # AD-15, row `r6`: the run reads no rule, so a rule counts as read
        # when a search of the run returned its chunk.
        self._searches_count_as_reads = searches_count_as_reads

    @property
    def facts(self) -> tuple[Fact, ...]:
        """The case's facts as they were when the run began, in page order."""
        return self._facts

    @property
    def steps_left(self) -> int:
        """How many more tool calls the run may make before its step limit."""
        return max(0, self._step_limit - self.state.steps)

    async def stop_at_the_limit(self, tool: ToolName) -> None:
        """Stop a run that knows it cannot make the calls it needs within its steps (AD-15).

        Row `r6` knows how many searches it will make before the first of
        them. The stop is one row of the log, refused with `step_limit`,
        as a call beyond the limit is; then `StepLimitReached`.
        """
        self.state.steps += 1
        self.state.step_limit_reached = True
        await self._log(
            self.state.steps,
            tool,
            {},
            None,
            [],
            StepOutcome.REFUSED,
            ErrorCode.STEP_LIMIT,
            time.monotonic(),
        )
        raise StepLimitReached

    def failed(self, code: ErrorCode) -> "ToolFailed":
        """Note that the run ends in a tool, with this code; the error to raise."""
        self.failure = ToolFailed(code)
        return self.failure

    async def call(
        self, tool: str, arguments: Mapping[str, object]
    ) -> dict[str, object]:
        """Make one tool call for the model, and log it as one step.

        Answers what the model is told: the tool's result, or why the call
        was refused. `StepLimitReached` when the run is at its limit: the
        call is then not made, and logged as refused with `step_limit`.
        `ToolFailed` when the service behind the tool gave no answer: the
        step is logged as failed and the run ends. A call cancelled or
        failing half way is logged as failed too.
        """
        state = self.state
        try:
            name = ToolName(tool)
        except ValueError:
            # No fourth tool (security rule 16). It is not a step: the log
            # holds the calls of the three tools.
            logger.warning(
                "unknown tool refused: case_id=%s verdict_run_id=%s",
                self.case_id,
                self.verdict_run_id,
            )
            return {
                "refused": ErrorCode.VALIDATION_FAILED.value,
                "message": UNKNOWN_TOOL_MESSAGE,
            }
        state.steps += 1
        step_no = state.steps
        started = time.monotonic()
        logged: dict[str, JsonValue] = _loggable(arguments)
        if step_no > self._step_limit:
            # AD-15: the call is not made. It is logged all the same, as
            # refused, so the log shows where the run was stopped.
            state.step_limit_reached = True
            await self._log(
                step_no,
                name,
                {} if name is ToolName.LIST_FACTS else logged,
                None,
                [],
                StepOutcome.REFUSED,
                ErrorCode.STEP_LIMIT,
                started,
            )
            raise StepLimitReached
        fact_id: str | None = None
        rule_ids: list[str] = []
        outcome = StepOutcome.DONE
        error_code: ErrorCode | None = None
        answer: dict[str, object]
        try:
            if name is ToolName.LIST_FACTS:
                # The tool takes no argument: whatever was sent is not kept.
                logged = {}
                answer = self._list_facts()
            elif name is ToolName.SEARCH_RULES:
                search = self._valid(SearchRulesArguments, arguments)
                logged = {"query": storable(search.query), "fact_id": search.fact_id}
                if logged["query"] != search.query:
                    # A NUL or half a character: no search takes it, and no text
                    # of the database holds it.
                    raise _Refusal(
                        ErrorCode.VALIDATION_FAILED, INVALID_ARGUMENTS_MESSAGE
                    )
                if search.fact_id not in self._fact_ids:
                    raise _Refusal(ErrorCode.VALIDATION_FAILED, FACT_NOT_LISTED_MESSAGE)
                fact_id = search.fact_id
                answer, rule_ids = await self._search_rules(search)
                state.facts_searched.add(fact_id)
            else:
                read = self._valid(ReadRuleArguments, arguments)
                logged = {"rule_id": read.rule_id}
                answer, rule_ids = await self._read_rule(read)
        except _Refusal as refusal:
            outcome, error_code = StepOutcome.REFUSED, refusal.code
            answer = {"refused": refusal.code.value, "message": refusal.message}
        except DomainError as error:
            # The service behind the tool gave no answer, after the
            # client's own retries. The call is logged, and the run ends.
            code = (
                error.code
                if error.code in _FAILURE_CODES
                else ErrorCode.UPSTREAM_UNAVAILABLE
            )
            self.failure = ToolFailed(code)
            await self._log(
                step_no, name, logged, fact_id, [], StepOutcome.FAILED, code, started
            )
            raise self.failure from None
        except BaseException as stopped:
            # Cancelled half way (the run's deadline or its time budget, or
            # the caller gone), or failed in a way no tool expects. The call
            # was begun, so the log has its row; then the error goes on as
            # it was.
            code = (
                ErrorCode.STAGE_TIMEOUT
                if isinstance(stopped, asyncio.CancelledError)
                else ErrorCode.INTERNAL_ERROR
            )
            await self._log_what_was_stopped(
                step_no, name, logged, fact_id, code, started
            )
            raise
        await self._log(
            step_no, name, logged, fact_id, rule_ids, outcome, error_code, started
        )
        return answer

    async def _log_what_was_stopped(
        self,
        step_no: int,
        tool: ToolName,
        arguments: dict[str, JsonValue],
        fact_id: str | None,
        code: ErrorCode,
        started: float,
    ) -> None:
        """Log a call that was stopped half way, whatever happens to the request; a log that fails here changes nothing."""
        try:
            await asyncio.shield(
                self._log(
                    step_no,
                    tool,
                    arguments,
                    fact_id,
                    [],
                    StepOutcome.FAILED,
                    code,
                    started,
                )
            )
        except BaseException as error:  # noqa: BLE001 - the error that stopped the call is the one that counts
            logger.warning(
                "agent step not logged: case_id=%s verdict_run_id=%s step_no=%d type=%s",
                self.case_id,
                self.verdict_run_id,
                step_no,
                type(error).__qualname__,
            )

    @staticmethod
    def _valid[M: (SearchRulesArguments, ReadRuleArguments)](
        model: type[M], arguments: Mapping[str, object]
    ) -> M:
        """The model's arguments as the contracts take them (security rule 15); refused otherwise."""
        try:
            return model.model_validate(dict(arguments))
        except ValidationError:
            raise _Refusal(
                ErrorCode.VALIDATION_FAILED, INVALID_ARGUMENTS_MESSAGE
            ) from None

    def _list_facts(self) -> dict[str, object]:
        self.state.listed(self._facts)
        return {
            "facts": [
                {
                    "fact_id": fact.fact_id,
                    "page_number": fact.page_number,
                    "statement": fact.statement,
                    "quote": fact.quote,
                    "quote_verified": fact.quote_verified,
                }
                for fact in self._facts
            ]
        }

    async def _search_rules(
        self, arguments: SearchRulesArguments
    ) -> tuple[dict[str, object], list[str]]:
        found = await self._ports.rules.search(
            arguments.query,
            self.retriever_config,
            self._search_top_k,
            self._trace_context,
        )
        rule_ids = self.state.found(found.items, as_read=self._searches_count_as_reads)
        return {
            "fact_id": arguments.fact_id,
            "rules": [
                {
                    "rule_ids": list(item.rule_ids),
                    "rank": item.rank,
                    "impairment": item.impairment,
                    "manual_page": item.manual_page,
                    "text": item.text,
                }
                for item in found.items
            ],
        }, rule_ids

    async def _read_rule(
        self, arguments: ReadRuleArguments
    ) -> tuple[dict[str, object], list[str]]:
        if not self.state.may_read(arguments.rule_id):
            raise _Refusal(ErrorCode.RULE_NOT_SEEN, RULE_NOT_SEEN_MESSAGE)
        rule = await self._ports.rules.read(
            arguments.rule_id, self.retriever_config, self._trace_context
        )
        if rule is None or rule.rule_id != arguments.rule_id:
            # Referred to by a rule, and not defined in the manual.
            raise _Refusal(ErrorCode.NOT_FOUND, RULE_NOT_FOUND_MESSAGE)
        self.state.was_read(rule)
        return {
            "rule_id": rule.rule_id,
            "impairment": rule.impairment,
            "manual_page": rule.manual_page,
            "text": rule.text,
            "reference_rule_ids": list(rule.reference_rule_ids),
        }, [rule.rule_id]

    async def _log(
        self,
        step_no: int,
        tool: ToolName,
        arguments: dict[str, JsonValue],
        fact_id: str | None,
        rule_ids: list[str],
        outcome: StepOutcome,
        error_code: ErrorCode | None,
        started: float,
    ) -> None:
        latency_ms = int((time.monotonic() - started) * 1000)
        await self._ports.repository.append_step(
            AgentStep(
                verdict_run_id=self.verdict_run_id,
                case_id=self.case_id,
                step_no=step_no,
                tool=tool,
                arguments=arguments,
                fact_id=fact_id,
                rule_ids=rule_ids,
                outcome=outcome,
                error_code=error_code,
                latency_ms=latency_ms,
                occurred_at=self._now(),
            )
        )
        # security rule 31: ids, codes, counts and timings; never the query,
        # a fact or a rule's text.
        logger.info(
            "agent step: case_id=%s verdict_run_id=%s step_no=%d tool=%s "
            "outcome=%s error_code=%s rules=%d latency_ms=%d",
            self.case_id,
            self.verdict_run_id,
            step_no,
            tool.value,
            outcome.value,
            error_code.value if error_code is not None else None,
            len(rule_ids),
            latency_ms,
        )
