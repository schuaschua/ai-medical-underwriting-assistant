"""The verdict agent on Microsoft Agent Framework (spine AD-15, AD-16).

The framework runs the conversation: it sends the instructions and the task,
takes the model's tool calls, hands the tools' answers back and returns the
model's final answer. Everything that matters is held outside it:

- The model. The framework's chat client is given the gateway's own OpenAI
  client (`adapters/model.py`), so every chat completion it makes is the
  gateway's: retried, counted against the cap, traced, its tokens logged.
- The tools. There are exactly three, and each is `Toolbox.call` of the run
  (`domain/toolbox.py`): arguments checked against the contracts, the case
  and the retriever row fixed by the server, `read_rule` held to what the run
  has seen, every call logged as a step. A function middleware answers every
  tool call from the toolbox before the framework's own argument check, so a
  call with arguments that are not valid is a logged, refused step too. A
  call to a tool of another name never reaches that middleware: the
  framework answers it itself ("not found"). `AskedCalls` reads each answer
  of the model for such calls and has the toolbox log each as a refused
  step, in its place among the calls of that answer.
- The end. The step limit stops the framework's loop; a tool whose service
  gives no answer ends the run as failed.

The framework decides nothing that is stored: its final text goes to the
domain, which parses it (`domain/decide.py`).

On row `r6` there is no conversation to run (`compose`): the run has made
its searches itself, and the model is asked once, through the same gateway,
with another prompt, the same answer schema and no tool at all.
"""

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent_framework import (
    Agent,
    ChatContext,
    ChatMiddleware,
    ChatResponse,
    FunctionInvocationContext,
    FunctionMiddleware,
    FunctionTool,
    MiddlewareFailure,
    MiddlewareTermination,
)
from agent_framework.openai import OpenAIChatCompletionClient
from pydantic import BaseModel

from contracts.enums import ReasonEffect, SystemReason, ToolName, Verdict
from contracts.models.retrieval import MAX_QUERY_CHARS
from contracts.rules import RULE_ID_PATTERN
from verdict.adapters.model import ModelGateway, finish_reason_of, usage_of
from verdict.domain.entities import AgentAnswer
from verdict.domain.ports import AgentFailed, ModelCallFailed, ModelUnavailable
from verdict.domain.toolbox import StepLimitReached, Toolbox
from verdict.prompts import COMPOSE_VERDICT, SUGGEST_VERDICT, load_prompt

logger = logging.getLogger(__name__)

AGENT_NAME = "verdict"
# The one user message of a run. The case is not named in it: the tools know it.
TASK = "Propose the reasons for a verdict on this application. Start with list_facts."
FRAMEWORK_EVENT = "agent_framework_event"

# AD-15: the three tools as the model is shown them. Each description is
# what the model reads (coding-style rule 13). The schemas tell the model
# what to send; what is accepted is decided by the contracts' models in the
# toolbox, never by these.
TOOLS: tuple[tuple[ToolName, str, dict[str, object]], ...] = (
    (
        ToolName.LIST_FACTS,
        (
            "List the medical facts of this application. Each fact has a fact_id, "
            "a page_number, a one-line statement, the quote it rests on and "
            "whether that quote was verified. Takes no arguments."
        ),
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    ),
    (
        ToolName.SEARCH_RULES,
        (
            "Search the underwriting manual for the rules one fact may meet. "
            "Returns the best matching rules, each with its rule_ids, impairment, "
            "manual_page and text."
        ),
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Plain words about the fact: the measure, "
                    "its value and unit, or the finding. At most "
                    f"{MAX_QUERY_CHARS} characters.",
                },
                "fact_id": {
                    "type": "string",
                    "description": "The fact_id of the fact the search is "
                    "about, exactly as list_facts returned it.",
                },
            },
            "required": ["query", "fact_id"],
            "additionalProperties": False,
        },
    ),
    (
        ToolName.READ_RULE,
        (
            "Read the full text of one rule of the manual, with the rules it "
            "refers to. Only a rule that search_rules returned in this "
            "conversation, or that a rule you have read refers to, may be read."
        ),
        {
            "type": "object",
            "properties": {
                "rule_id": {
                    "type": "string",
                    "description": "The rule's id, for example UW-DM-002.",
                    "pattern": f"^{RULE_ID_PATTERN}$",
                },
            },
            "required": ["rule_id"],
            "additionalProperties": False,
        },
    ),
)

# Structured output: the model's final answer is this object and nothing
# else. It is the shape of the contracts' `VerdictOutput`. Nothing in it is
# stored as it is given: see `domain/decide.py`.
OUTPUT_SCHEMA_NAME = "verdict_output"
OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": [item.value for item in Verdict]},
        "confidence": {"type": "number"},
        "reasons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string"},
                    "fact_ids": {"type": "array", "items": {"type": "string"}},
                    "effect": {
                        "type": "string",
                        "enum": [item.value for item in ReasonEffect],
                    },
                    "debit_pct": {"type": ["integer", "null"]},
                },
                "required": ["rule_id", "fact_ids", "effect", "debit_pct"],
                "additionalProperties": False,
            },
        },
        "system_reasons": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [item.value for item in SystemReason],
            },
        },
    },
    "required": ["verdict", "confidence", "reasons", "system_reasons"],
    "additionalProperties": False,
}
RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": OUTPUT_SCHEMA_NAME,
        "strict": True,
        "schema": OUTPUT_SCHEMA,
    },
}

# How many turns more than the step limit the framework's own loop may
# take. The step limit is what stops a run: every turn that asks for a tool
# is at least one step, a tool that does not exist included. This only keeps
# the framework's own bound from ending a run first.
_TURNS_BEYOND_THE_STEP_LIMIT = 3


class FrameworkLogFilter(logging.Filter):
    """Reduces a record of the Agent Framework to a fixed code and an error type.

    The framework writes tool arguments, tool results and error texts into
    its messages, and those hold facts, queries and rule text. What is kept:
    where in the framework the record was made, and the type of the
    exception it carries (security rule 31).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        error = record.exc_info[0] if record.exc_info else None
        record.msg = (
            f"{FRAMEWORK_EVENT} at={record.module}:{record.lineno} "
            f"type={error.__qualname__ if error is not None else 'none'}"
        )
        record.args = None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


class FrameworkRecords(logging.Handler):
    """Carries the filter to every record of the framework, whichever of its loggers made it.

    A filter on a logger sees only the records made on that very logger, and
    the framework logs on `agent_framework` and on loggers below it
    (`agent_framework.openai`, `agent_framework._middleware`). A handler on
    `agent_framework` is handed the records of them all, before any handler
    further up, so the service's own handlers get the reduced record.
    Nothing is written here.
    """

    def __init__(self) -> None:
        super().__init__()
        self.addFilter(FrameworkLogFilter())

    def emit(self, record: logging.LogRecord) -> None:
        """The record goes on, reduced, to the handlers of the service."""


def quiet_the_framework() -> None:
    """Hold the framework's own logging to warnings, stripped of their text."""
    framework = logging.getLogger("agent_framework")
    framework.setLevel(logging.WARNING)
    if not any(isinstance(item, FrameworkRecords) for item in framework.handlers):
        framework.addHandler(FrameworkRecords())


quiet_the_framework()


def _plain(arguments: object) -> Mapping[str, object]:
    """A tool call's arguments as a plain mapping, whatever the framework holds them in."""
    if isinstance(arguments, BaseModel):
        return arguments.model_dump()
    if isinstance(arguments, Mapping):
        return {str(name): value for name, value in arguments.items()}
    # Not an object at all: the toolbox refuses it as it refuses any bad call.
    return {"arguments": None}


_TOOL_NAMES = frozenset(tool.value for tool in ToolName)


class ToolGuard(FunctionMiddleware):
    """Answers every tool call of one run from its toolbox, and ends the run when the toolbox says so.

    It runs before the framework checks a call's arguments, and it never
    hands a call on to the framework's own invocation: the toolbox is the
    one place a tool is run, so every call is validated, counted and logged
    there, and only there.

    The framework hands it only the calls to the three tools. The calls of
    the model's last answer to a tool that does not exist are noted here
    (`asked`), and each is logged by the toolbox before the call that came
    after it, so that step numbers are the order the model asked in.
    """

    def __init__(self, toolbox: Toolbox) -> None:
        self._toolbox = toolbox
        # What ended the run in a tool, if anything did.
        self.error: BaseException | None = None
        # The calls of the model's last answer not yet dealt with, in its
        # order: the call's id, and the name asked for if no tool has it.
        self._asked: list[tuple[str | None, str | None]] = []

    def asked(self, response: object) -> None:
        """Note the tool calls of one answer of the model."""
        self._asked = [
            (
                getattr(content, "call_id", None),
                None if content.name in _TOOL_NAMES else _text(content.name),
            )
            for message in getattr(response, "messages", None) or ()
            for content in getattr(message, "contents", None) or ()
            if getattr(content, "type", None) == "function_call"
            and not getattr(content, "informational_only", False)
        ]

    async def refuse_unknown_tools(self, before: str | None = None) -> None:
        """Log the noted calls to a tool that does not exist: those before the call `before`, or all of them.

        The toolbox counts each against the step limit. At the limit it
        says so in its state and the next call goes on to be refused there
        too, as the calls of the three tools beyond the limit are.
        """
        if before is not None and all(call_id != before for call_id, _ in self._asked):
            return
        while self._asked:
            call_id, unknown = self._asked.pop(0)
            if before is not None and call_id == before:
                return
            if unknown is None:
                continue
            try:
                # Without its arguments: they are of no known shape.
                await self._toolbox.call(unknown, {})
            except StepLimitReached:
                continue
            except Exception as error:  # noqa: BLE001 - a step that could not be logged ends the run
                self.error = error
                raise MiddlewareFailure("step not logged") from None

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        call_id = context.metadata.get("call_id")
        await self.refuse_unknown_tools(
            before=call_id if isinstance(call_id, str) else None
        )
        try:
            answer = await self._toolbox.call(
                context.function.name, _plain(context.arguments)
            )
        except StepLimitReached:
            # AD-15: the run stops here. The loop ends without an answer.
            raise MiddlewareTermination("step limit") from None
        except Exception as error:  # noqa: BLE001 - whatever ended the call, the run ends with it
            # A tool's service gave no answer, or a step could not be
            # logged. The run must not go on as if the call had been made:
            # anything but this would be turned into a tool result.
            self.error = error
            raise MiddlewareFailure("tool failed") from None
        context.result = json.dumps(answer)


def _text(name: object) -> str:
    """The name of a tool the model asked for, as text; the toolbox bounds it."""
    return name if isinstance(name, str) else ""


class AskedCalls(ChatMiddleware):
    """Sees every answer of the model, so that a call to a tool that does not exist is a step.

    Before the model is asked again, the calls of its last answer that the
    framework answered itself are logged, those after the last call to a
    real tool among them. If that took the run to its step limit, the model
    is not asked again: the loop ends without an answer, as it does when a
    real tool's call meets the limit.
    """

    def __init__(self, guard: ToolGuard, toolbox: Toolbox) -> None:
        self._guard = guard
        self._toolbox = toolbox

    async def process(
        self, context: ChatContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        await self._guard.refuse_unknown_tools()
        if self._toolbox.state.step_limit_reached:
            # AD-15: the run stops here, without another call of the model.
            context.result = ChatResponse(messages=[])
            return
        await call_next()
        self._guard.asked(context.result)


def _tools(toolbox: Toolbox) -> list[FunctionTool]:
    """The run's three tools, as the framework declares them to the model."""

    def tool(name: ToolName) -> Callable[..., Awaitable[str]]:
        async def call(**arguments: Any) -> str:
            # `ToolGuard` answers every call before this is reached. Should
            # the framework ever invoke a tool itself, it is the same call.
            return json.dumps(await toolbox.call(name.value, arguments))

        return call

    return [
        FunctionTool(
            name=name.value,
            description=description,
            func=tool(name),
            input_model=schema,
            approval_mode="never_require",
        )
        for name, description, schema in TOOLS
    ]


_FINISH_REASON = re.compile(r"[a-z_]{1,32}")


def _finish_reason_of(response: Any) -> str:
    """Why the model stopped, as a code: never the model's own text beyond a plain word."""
    reason = getattr(response, "finish_reason", None)
    if reason is None:
        return "none"
    reason = getattr(reason, "value", reason)
    return (
        reason
        if isinstance(reason, str) and _FINISH_REASON.fullmatch(reason)
        else "other"
    )


def _token_counts(response: Any) -> tuple[int, int]:
    """The input and output tokens of a whole run, as the framework added them up; 0 where it names none."""
    usage = getattr(response, "usage_details", None)
    if not isinstance(usage, Mapping):
        return 0, 0
    counts = (usage.get("input_token_count"), usage.get("output_token_count"))
    first, second = (count if isinstance(count, int) else 0 for count in counts)
    return first, second


def _model_error(error: BaseException) -> ModelUnavailable | ModelCallFailed | None:
    """The gateway's own error inside what the framework raised, if that is what it is."""
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ModelUnavailable | ModelCallFailed):
            return current
        pending += [
            current.__cause__,
            current.__context__,
            getattr(current, "inner_exception", None),
        ]
    return None


class FrameworkVerdictAgent:
    """The verdict agent: Microsoft Agent Framework on the gateway's client."""

    def __init__(self, gateway: ModelGateway, *, step_limit: int) -> None:
        self._gateway = gateway
        self._deployment = gateway.deployment
        self._chat: OpenAIChatCompletionClient[Any] = OpenAIChatCompletionClient(
            model=gateway.deployment,
            # AD-16: the gateway's own client. The framework is given no
            # endpoint, key or credential, so it can build no other.
            async_client=gateway.client,
            function_invocation_configuration={
                "max_iterations": step_limit + _TURNS_BEYOND_THE_STEP_LIMIT,
                # In the order the model named them, so that step numbers
                # are the order of the calls.
                "allow_concurrent_invocation": False,
                # Exception texts never reach the model.
                "include_detailed_errors": False,
            },
        )
        self._instructions = load_prompt(SUGGEST_VERDICT)
        self._compose_instructions = load_prompt(COMPOSE_VERDICT)

    async def compose(self, toolbox: Toolbox, material: str) -> AgentAnswer:
        """Row `r6`: one chat completion with no tool; the model's proposal from what the run searched.

        AD-16: through the gateway, like every call of the model. The
        request names no tool, so the model can neither search nor read:
        the search service has planned and run this row's queries already.
        """
        started = time.monotonic()
        completion = await self._gateway.complete(
            {
                "messages": [
                    {"role": "system", "content": self._compose_instructions},
                    # Data, never instructions: the facts and the rules found.
                    {"role": "user", "content": material},
                ],
                "response_format": RESPONSE_FORMAT,
            }
        )
        try:
            text = completion.choices[0].message.content
        except (AttributeError, IndexError, TypeError):
            text = None
        input_tokens, output_tokens = usage_of(completion)
        finish_reason = finish_reason_of(completion)
        # Ids, counts and timings: what the call cost, never what was said.
        logger.info(
            "agent composed: case_id=%s verdict_run_id=%s deployment=%s steps=%d "
            "input_tokens=%d output_tokens=%d finish_reason=%s duration_ms=%d",
            toolbox.case_id,
            toolbox.verdict_run_id,
            self._deployment,
            toolbox.state.steps,
            input_tokens,
            output_tokens,
            finish_reason,
            int((time.monotonic() - started) * 1000),
        )
        return AgentAnswer(text if isinstance(text, str) else "", finish_reason)

    async def run(self, toolbox: Toolbox) -> AgentAnswer | None:
        """One run of the agent with the tools of `toolbox`; its final answer, or None at the step limit."""
        started = time.monotonic()
        guard = ToolGuard(toolbox)
        agent = Agent(
            client=self._chat,
            instructions=self._instructions,
            name=AGENT_NAME,
            # AD-15, security rule 16: these three, and nothing else: no
            # other Foundry tool, no connection, no context provider.
            tools=_tools(toolbox),
            middleware=[guard, AskedCalls(guard, toolbox)],
            default_options={"response_format": RESPONSE_FORMAT},
        )
        try:
            response = await agent.run(TASK)
            # The loop ended after a batch of calls (the step limit): the
            # calls to a tool that does not exist among its last are steps too.
            await guard.refuse_unknown_tools()
        except Exception as error:  # noqa: BLE001 - whatever the framework raised, it is told apart below
            if guard.error is not None:
                # What the tool raised, as it raised it: the domain knows it.
                raise guard.error from None
            model_error = _model_error(error)
            if model_error is not None:
                raise model_error from None
            # security rule 31: the type only.
            raise AgentFailed(type(error).__qualname__) from None
        if guard.error is not None:
            raise guard.error
        input_tokens, output_tokens = _token_counts(response)
        finish_reason = _finish_reason_of(response)
        # Ids, counts and timings: what the run cost, never what was said.
        logger.info(
            "agent run: case_id=%s verdict_run_id=%s deployment=%s steps=%d "
            "input_tokens=%d output_tokens=%d finish_reason=%s stopped_at_limit=%s "
            "duration_ms=%d",
            toolbox.case_id,
            toolbox.verdict_run_id,
            self._deployment,
            toolbox.state.steps,
            input_tokens,
            output_tokens,
            finish_reason,
            str(toolbox.state.step_limit_reached).lower(),
            int((time.monotonic() - started) * 1000),
        )
        if toolbox.state.step_limit_reached:
            return None
        text = response.text
        return AgentAnswer(text if isinstance(text, str) else "", finish_reason)
