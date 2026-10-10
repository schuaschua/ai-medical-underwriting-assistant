"""What a verdict run needs from the outside world; adapters provide it."""

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from contracts.enums import RetrieverConfig, ToolName
from contracts.errors import ErrorCode
from contracts.models.extraction import Fact
from contracts.models.retrieval import RuleText, SearchResponse
from contracts.models.verdict import AgentStep, VerdictRun
from verdict.domain.entities import AgentAnswer, KeyRow, RunKey, Suggestion

if TYPE_CHECKING:
    from verdict.domain.toolbox import Toolbox


class ModelUnavailable(Exception):
    """AD-16: the model gateway gave up after its retries."""


class ModelCallFailed(Exception):
    """The model refused the call, and would refuse it again.

    `reason` is a short code for the log, never a message of the model's
    endpoint: such a message could hold what was sent to it.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AgentFailed(Exception):
    """The agent's run failed for a reason that is neither the model's nor a tool's.

    `kind` is the type of what the agent's framework raised, for the log:
    its message can hold what was sent to the model, and is not kept.
    """

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class KeyRowGone(Exception):
    """A result was to be stored and its key row is no longer there: it was released meanwhile."""


class VerdictAgent(Protocol):
    """The verdict agent: the chat deployment with the run's three tools (AD-15, AD-16)."""

    async def run(self, toolbox: "Toolbox") -> AgentAnswer | None:
        """Let the agent work one run through with the tools of `toolbox`; return its final answer.

        The answer is not looked at here: the caller parses it. None when
        the run ended without one, because the toolbox stopped it at its
        step limit. An error a tool raised is raised on, as it is. Raises
        `ModelUnavailable` when the model could not be had,
        `ModelCallFailed` when it refused a call, and `AgentFailed` for any
        other failure of the run.
        """
        ...

    async def compose(self, toolbox: "Toolbox", material: str) -> AgentAnswer:
        """AD-15, row `r6`: ask the model once, with no tool, for its proposal from `material`.

        `material` is the case's facts and the rules the run's own searches
        returned (`domain/compose.py`). The answer is not looked at here.
        `toolbox` names the run for the log; none of its tools is offered.
        Raises `ModelUnavailable` and `ModelCallFailed` as `run` does.
        """
        ...


class FactReader(Protocol):
    """The read of `extraction` (AD-3): the case's facts, each with its checked quote."""

    async def facts_of_case(
        self, case_id: str, trace_context: Mapping[str, str]
    ) -> list[Fact]:
        """The case's stored facts, in page order; empty for a case with none.

        Raises `upstream_unavailable` when `extraction` could not be had.
        """
        ...


class RuleLibrary(Protocol):
    """The two reads of `retrieval` (AD-3, AD-11): the search and the rule read."""

    async def search(
        self,
        query: str,
        retriever_config: RetrieverConfig,
        top_k: int,
        trace_context: Mapping[str, str],
    ) -> SearchResponse:
        """Search the manual with one ladder row.

        Raises `upstream_unavailable` when `retrieval` could not be had, and
        `retriever_not_available` when it cannot search with that row.
        """
        ...

    async def read(
        self,
        rule_id: str,
        retriever_config: RetrieverConfig,
        trace_context: Mapping[str, str],
    ) -> RuleText | None:
        """The text that defines a rule; None when the manual defines no such rule.

        Raises as `search` does.
        """
        ...


class RunRepository(Protocol):
    """The key row of a run, what a done run stores, and the agent's step log (AD-6, AD-15)."""

    async def find(self, key: RunKey) -> KeyRow | None:
        """The key row of an earlier command with this key, if there is one."""
        ...

    async def begin(
        self, verdict_run_id: str, key: RunKey, started_at: datetime
    ) -> KeyRow | None:
        """Insert the key row as running, before any work.

        Returns None when this call inserted it; otherwise the row that was
        there already, and nothing is changed.
        """
        ...

    async def finish(
        self,
        verdict_run_id: str,
        result_json: str,
        suggestion: Suggestion | None,
        error_code: ErrorCode | None,
    ) -> str:
        """Store the result, and the suggestion of a done run with its reasons, in one transaction.

        `suggestion` is None for a failed result, which names its
        `error_code` instead. Nothing is written unless the key row is still
        running. Returns the result the row holds afterwards: this one, or
        the one stored before it. Raises `KeyRowGone` when the row is no
        longer there.
        """
        ...

    async def release(self, verdict_run_id: str) -> None:
        """Remove the key row if it is still running, so the command can be run again.

        A row that holds a result is left as it is. The steps the run
        logged stay: the log is never taken back.
        """
        ...

    async def append_step(self, step: AgentStep) -> None:
        """Add one tool call to the log. The log is only ever added to (AD-15)."""
        ...

    async def runs_of_case(self, case_id: str, limit: int) -> list[VerdictRun]:
        """The case's runs, oldest first, at most `limit`: running, done and failed ones."""
        ...

    async def run_exists(self, verdict_run_id: str) -> bool:
        """Whether a run of that id is stored."""
        ...

    async def steps_of_run(
        self,
        verdict_run_id: str,
        tool: ToolName | None,
        rule_id: str | None,
        after_step_no: int | None,
        limit: int,
    ) -> list[AgentStep]:
        """The run's steps by step number, at most `limit`.

        With `tool`, only that tool's calls; with `rule_id`, only calls that
        returned or read that rule; with `after_step_no`, only steps of a
        higher number.
        """
        ...

    async def steps_of_case(
        self,
        case_id: str,
        tool: ToolName | None,
        rule_id: str | None,
        after: tuple[str, int] | None,
        limit: int,
    ) -> list[AgentStep]:
        """The steps of every run of the case, in the order they were logged, at most `limit`.

        With `tool`, only that tool's calls; with `rule_id`, only calls that
        returned or read that rule; with `after` (a run and a step number of
        it), only steps logged after that one, and none when the case has no
        such step.
        """
        ...
