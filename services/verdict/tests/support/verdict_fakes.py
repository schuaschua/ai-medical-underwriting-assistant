"""In-memory stand-ins for what `verdict` works with, and the agent stub.

Unit tests use them in place of PostgreSQL, `extraction`, `retrieval` and
the model (coding-style rule 23). They live with the tests, on pytest's
`pythonpath`, so the service package and its image hold no test code. Every
fact and rule here is made up for the tests, and marked so that a test can
tell if any of it reached a log.
"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import httpx2
import psycopg

from contracts.enums import ChunkSet, RetrieverConfig, StageStatus, ToolName
from contracts.errors import HTTP_STATUS, DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.extraction import Fact, FactList
from contracts.models.retrieval import (
    RuleText,
    SearchItem,
    SearchRequest,
    SearchResponse,
)
from contracts.models.verdict import AgentStep, VerdictRun, VerdictRunResult
from verdict.domain.entities import AgentAnswer, KeyRow, RunKey, Suggestion
from verdict.domain.ports import ModelUnavailable
from verdict.domain.run import verdict_run
from verdict.domain.toolbox import Toolbox
from verdict.settings import Settings

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
DEPLOYMENT = "chat-test"
ACTOR = f"verdict:{DEPLOYMENT}"

# Rules of a made-up manual, worded as the real one words a definition.
DIABETES = "Type 2 diabetes mellitus"
HYPERTENSION = "Hypertension"
TOBACCO = "Tobacco use"
DM_25 = "UW-DM-001"
DM_50 = "UW-DM-002"
DM_DECLINE = "UW-DM-005"
HT_50 = "UW-HT-002"
TOB_25 = "UW-TOB-003"
PD_NONE = "UW-PD-001"
NOT_IN_MANUAL = "UW-XX-999"


def rule_text(rule_id: str, impairment: str, rating: str, *refers_to: str) -> str:
    """A definition paragraph as the manual prints one; SECRET-RULE marks it for the log tests."""
    see = "".join(f" If it applies, see rule {other}." for other in refers_to)
    return (
        f"Rule {rule_id}: {impairment} (section 2.4). Threshold: SECRET-RULE band. "
        f"Probable rating: {rating}.{see} Source of the threshold: a guideline."
    )


def rule(rule_id: str, impairment: str, rating: str, *refers_to: str) -> RuleText:
    return RuleText(
        rule_id=rule_id,
        chunk_id=f"smart-{rule_id}",
        chunk_set=ChunkSet.SMART,
        text=rule_text(rule_id, impairment, rating, *refers_to),
        manual_page=12,
        impairment=impairment,
        reference_rule_ids=list(refers_to),
    )


MANUAL: tuple[RuleText, ...] = (
    rule(DM_25, DIABETES, "a debit of +25 %"),
    rule(DM_50, DIABETES, "a debit of +50 %", HT_50),
    rule(DM_DECLINE, DIABETES, "decline as a postponement"),
    rule(HT_50, HYPERTENSION, "a debit of +50 %", TOB_25),
    rule(TOB_25, TOBACCO, "a debit of +25 %"),
    rule(PD_NONE, "Prediabetes", "no debit, +0 %"),
)


def fact(
    case_id: str, statement: str = "SECRET-FACT HbA1c 7.4 %", *, verified: bool = True
) -> Fact:
    """One stored fact of a case, as `extraction` answers it."""
    return Fact(
        fact_id=new_id(),
        case_id=case_id,
        page_id=new_id(),
        page_number=1,
        statement=statement,
        quote="SECRET-QUOTE 7.4 %",
        quote_verified=verified,
        quote_start=3 if verified else None,
        quote_end=9 if verified else None,
    )


def reason(
    rule_id: str,
    *fact_ids: str,
    effect: str = "debit",
    debit_pct: int | None = 50,
) -> dict[str, Any]:
    """One reason as the agent proposes it."""
    return {
        "rule_id": rule_id,
        "fact_ids": list(fact_ids),
        "effect": effect,
        "debit_pct": debit_pct if effect == "debit" else None,
    }


def final_answer(
    *reasons: dict[str, Any],
    confidence: float = 0.9,
    verdict: str = "standard",
    system_reasons: Sequence[str] = (),
) -> str:
    """The agent's final answer. Its verdict word is whatever: it is never stored."""
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": confidence,
            "reasons": list(reasons),
            "system_reasons": list(system_reasons),
        }
    )


class StoreDown(Exception):
    """What a failing stand-in raises. Its message must never reach a log or a body."""

    def __init__(self) -> None:
        super().__init__("secret-store-detail host=10.0.0.1 SELECT 1")


@dataclass
class MemoryRepository:
    """Keeps to the repository's contract: one row per key, settled once, a log only added to."""

    rows: dict[RunKey, KeyRow] = field(default_factory=dict)
    suggestions: dict[str, Suggestion] = field(default_factory=dict)
    error_codes: dict[str, ErrorCode] = field(default_factory=dict)
    steps: list[AgentStep] = field(default_factory=list)
    fail_finish: bool = False
    # Only the next this many attempts to store a result fail.
    failing_finishes: int = 0
    fail_release: bool = False
    fail_append: bool = False
    finishes: int = 0
    released: list[str] = field(default_factory=list)

    async def find(self, key: RunKey) -> KeyRow | None:
        return self.rows.get(key)

    async def begin(
        self, verdict_run_id: str, key: RunKey, started_at: datetime
    ) -> KeyRow | None:
        existing = self.rows.get(key)
        if existing is not None:
            return existing
        self.rows[key] = KeyRow(verdict_run_id, key, started_at, None)
        return None

    def _row(self, verdict_run_id: str) -> KeyRow | None:
        return next(
            (row for row in self.rows.values() if row.verdict_run_id == verdict_run_id),
            None,
        )

    async def finish(
        self,
        verdict_run_id: str,
        result_json: str,
        suggestion: Suggestion | None,
        error_code: ErrorCode | None,
    ) -> str:
        self.finishes += 1
        if self.fail_finish:
            raise StoreDown
        if self.failing_finishes > 0:
            self.failing_finishes -= 1
            raise StoreDown
        row = self._row(verdict_run_id)
        if row is None:
            raise AssertionError("finish without a key row")
        if row.result_json is not None:
            return row.result_json
        self.rows[row.key] = KeyRow(
            row.verdict_run_id, row.key, row.started_at, result_json
        )
        if suggestion is not None:
            self.suggestions[verdict_run_id] = suggestion
        if error_code is not None:
            self.error_codes[verdict_run_id] = error_code
        return result_json

    async def release(self, verdict_run_id: str) -> None:
        if self.fail_release:
            raise StoreDown
        row = self._row(verdict_run_id)
        if row is not None and row.running:
            del self.rows[row.key]
            self.released.append(verdict_run_id)

    async def append_step(self, step: AgentStep) -> None:
        if self.fail_append:
            raise StoreDown
        self.steps.append(step)

    def _run(self, row: KeyRow) -> VerdictRun:
        status = StageStatus.RUNNING
        if row.result_json is not None:
            status = VerdictRunResult.model_validate_json(row.result_json).status
        return verdict_run(
            row.verdict_run_id,
            row.key,
            status,
            self.suggestions.get(row.verdict_run_id),
            self.error_codes.get(row.verdict_run_id),
        )

    async def runs_of_case(self, case_id: str, limit: int) -> list[VerdictRun]:
        rows = [row for row in self.rows.values() if row.key.case_id == case_id]
        return [self._run(row) for row in rows][:limit]

    async def run_exists(self, verdict_run_id: str) -> bool:
        return self._row(verdict_run_id) is not None

    async def steps_of_run(
        self,
        verdict_run_id: str,
        tool: ToolName | None,
        rule_id: str | None,
        after_step_no: int | None,
        limit: int,
    ) -> list[AgentStep]:
        steps = [
            step
            for step in self.steps
            if step.verdict_run_id == verdict_run_id
            and (tool is None or step.tool is tool)
            and (rule_id is None or rule_id in step.rule_ids)
            and (after_step_no is None or step.step_no > after_step_no)
        ]
        return sorted(steps, key=lambda step: step.step_no)[:limit]

    async def steps_of_case(
        self,
        case_id: str,
        tool: ToolName | None,
        rule_id: str | None,
        after: tuple[str, int] | None,
        limit: int,
    ) -> list[AgentStep]:
        steps = [step for step in self.steps if step.case_id == case_id]
        if after is not None:
            places = [
                place
                for place, step in enumerate(steps)
                if (step.verdict_run_id, step.step_no) == after
            ]
            steps = steps[places[0] + 1 :] if places else []
        return [
            step
            for step in steps
            if (tool is None or step.tool is tool)
            and (rule_id is None or rule_id in step.rule_ids)
        ][:limit]

    # What the tests look at.

    def only_row(self) -> KeyRow:
        (row,) = self.rows.values()
        return row

    def stored_run(self) -> VerdictRun:
        return self._run(self.only_row())


def unavailable() -> DomainError:
    return DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, "secret-upstream-detail")


@dataclass
class FakeFacts:
    """Stands in for `extraction`: the case's facts, or no answer."""

    facts: list[Fact] = field(default_factory=list)
    fail: bool = False
    calls: int = 0
    trace_contexts: list[Mapping[str, str]] = field(default_factory=list)

    async def facts_of_case(
        self, case_id: str, trace_context: Mapping[str, str]
    ) -> list[Fact]:
        self.calls += 1
        self.trace_contexts.append(trace_context)
        if self.fail:
            raise unavailable()
        return [item for item in self.facts if item.case_id == case_id]


@dataclass
class FakeRules:
    """Stands in for `retrieval`: a search answers the rules named for its query, a read one rule."""

    manual: dict[str, RuleText] = field(
        default_factory=lambda: {item.rule_id: item for item in MANUAL}
    )
    # The rules a search returns, by query; a query not named here returns `default`.
    by_query: dict[str, list[str]] = field(default_factory=dict)
    default: list[str] = field(default_factory=list)
    fail_search: bool = False
    fail_read: bool = False
    row_not_available: bool = False
    searches: list[tuple[str, RetrieverConfig, int]] = field(default_factory=list)
    reads: list[tuple[str, RetrieverConfig]] = field(default_factory=list)

    async def search(
        self,
        query: str,
        retriever_config: RetrieverConfig,
        top_k: int,
        trace_context: Mapping[str, str],
    ) -> SearchResponse:
        self.searches.append((query, retriever_config, top_k))
        if self.row_not_available:
            raise DomainError(ErrorCode.RETRIEVER_NOT_AVAILABLE, "secret-row-detail")
        if self.fail_search:
            raise unavailable()
        rule_ids = self.by_query.get(query, self.default)
        return SearchResponse(
            retriever_config=retriever_config,
            latency_ms=3,
            items=[
                SearchItem(
                    chunk_id=self.manual[rule_id].chunk_id,
                    rule_ids=[rule_id],
                    rank=rank,
                    score=round(1 / (rank + 1), 3),
                    text=self.manual[rule_id].text,
                    manual_page=self.manual[rule_id].manual_page,
                    impairment=self.manual[rule_id].impairment,
                )
                for rank, rule_id in enumerate(rule_ids, start=1)
            ],
        )

    async def read(
        self,
        rule_id: str,
        retriever_config: RetrieverConfig,
        trace_context: Mapping[str, str],
    ) -> RuleText | None:
        self.reads.append((rule_id, retriever_config))
        if self.fail_read:
            raise unavailable()
        return self.manual.get(rule_id)


Script = Callable[[Toolbox], Awaitable[str | None]]


@dataclass
class StubAgent:
    """The agent stub: a test says which tools it calls and what it answers in the end.

    `script` is the agent's whole run. It gets the toolbox, as the real
    agent's adapter does, and returns the final answer's text, or None for
    no answer.
    """

    script: Script | None = None
    error: Exception | None = None
    delay_seconds: float = 0.0
    finish_reason: str = "stop"
    runs: int = 0
    # What each tool call answered the agent, in order.
    answers: list[dict[str, object]] = field(default_factory=list)
    started: asyncio.Event = field(default_factory=asyncio.Event)

    async def run(self, toolbox: Toolbox) -> AgentAnswer | None:
        self.runs += 1
        self.started.set()
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        if self.script is None:
            raise AssertionError("the agent was run without a script")
        text = await self.script(toolbox)
        return AgentAnswer(text, self.finish_reason) if text is not None else None


def model_unavailable() -> ModelUnavailable:
    return ModelUnavailable()


@dataclass
class MemorySchemaRevision:
    revision: str | None
    fail: bool = False

    async def current(self) -> str | None:
        if self.fail:
            raise StoreDown
        return self.revision


# --- The chat deployment, as a transport ------------------------------------------

# One scripted turn of the model: its tool calls, its final text, an HTTP
# status in place of an answer, or a whole chat completion as a mapping.
Turn = list[dict[str, Any]] | str | int | dict[str, Any]


def tool_call(name: str, arguments: object = None) -> dict[str, Any]:
    """One tool call as the model names it; `arguments` as an object, or the raw text."""
    text = arguments if isinstance(arguments, str) else json.dumps(arguments or {})
    return {
        "id": f"call_{new_id()}",
        "type": "function",
        "function": {"name": name, "arguments": text},
    }


def list_facts_call() -> dict[str, Any]:
    return tool_call(ToolName.LIST_FACTS.value)


def search_call(fact_id: str, query: str = "SECRET-QUERY glucose") -> dict[str, Any]:
    return tool_call(ToolName.SEARCH_RULES.value, {"query": query, "fact_id": fact_id})


def read_call(rule_id: str) -> dict[str, Any]:
    return tool_call(ToolName.READ_RULE.value, {"rule_id": rule_id})


def completion(
    message: dict[str, Any],
    finish_reason: object = "stop",
    usage: tuple[int, int] | None = (11, 7),
) -> dict[str, Any]:
    """A chat completion as the deployment answers it, around one message."""
    body: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": DEPLOYMENT,
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
    }
    if usage is not None:
        body["usage"] = {
            "prompt_tokens": usage[0],
            "completion_tokens": usage[1],
            "total_tokens": sum(usage),
        }
    return body


def turn_completion(turn: Turn) -> dict[str, Any]:
    """The chat completion of one scripted turn that is an answer."""
    if isinstance(turn, dict):
        return turn
    if isinstance(turn, str):
        return completion({"role": "assistant", "content": turn})
    if isinstance(turn, list):
        return completion(
            {"role": "assistant", "content": None, "tool_calls": turn}, "tool_calls"
        )
    raise AssertionError("a status is not an answer")


@dataclass
class ScriptedModel:
    """Stands in for the chat deployment: the model stub of the agent's tests.

    Answers each chat completion with the next turn of its script, and the
    last turn again once the script has run out. An `int` turn is answered
    as that HTTP status, with a message that must never reach a log.
    """

    turns: list[Turn] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.requests)

    def bodies(self) -> list[dict[str, Any]]:
        """What was sent, call by call."""
        return [json.loads(request.content) for request in self.requests]

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        position = min(len(self.requests), len(self.turns) - 1)
        self.requests.append(request)
        turn = self.turns[position]
        if isinstance(turn, int):
            return httpx2.Response(
                turn,
                headers=self.headers,
                json={"error": {"code": "x", "message": "SECRET-ENDPOINT-MESSAGE"}},
            )
        return httpx2.Response(200, json=turn_completion(turn))

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)


# --- The Dapr sidecar, with `extraction` and `retrieval` behind it ----------------

_FACTS_PATH = re.compile(r"/v1\.0/invoke/extraction/method/cases/([^/]+)/facts")
_SEARCHES_PATH = "/v1.0/invoke/retrieval/method/searches"
_RULE_PATH = re.compile(r"/v1\.0/invoke/retrieval/method/rules/([^/]+)")


def error_body(code: ErrorCode, message: str = "SECRET-UPSTREAM-MESSAGE") -> Any:
    return DomainError(code, message).to_body(None).model_dump(mode="json")


@dataclass
class UpstreamSidecar:
    """Stands in for the Dapr sidecar: `extraction`'s facts and `retrieval`'s two reads.

    Answers in the contracts' shapes, as the two services do. `rules` is
    the made-up manual and what a search returns from it.
    """

    facts: list[Fact] = field(default_factory=list)
    rules: FakeRules = field(default_factory=FakeRules)
    # Paths answered with this status instead, by the service's app id.
    down: dict[str, int] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def paths(self) -> list[tuple[str, str]]:
        return [(request.method, request.url.path) for request in self.requests]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        for app_id, status in self.down.items():
            if path.startswith(f"/v1.0/invoke/{app_id}/"):
                return httpx.Response(
                    status, json={"errorCode": "ERR_DIRECT_INVOKE", "message": "x"}
                )
        facts_of = _FACTS_PATH.fullmatch(path)
        if facts_of and request.method == "GET":
            case_id = facts_of.group(1)
            listed = FactList(
                case_id=case_id,
                facts=[item for item in self.facts if item.case_id == case_id],
            )
            return httpx.Response(200, json=listed.model_dump(mode="json"))
        if path == _SEARCHES_PATH and request.method == "POST":
            asked = SearchRequest.model_validate_json(request.content)
            if asked.retriever_config is not RetrieverConfig.R3:
                code = ErrorCode.RETRIEVER_NOT_AVAILABLE
                return httpx.Response(HTTP_STATUS[code], json=error_body(code))
            found = await self.rules.search(
                asked.query, asked.retriever_config, asked.top_k, {}
            )
            return httpx.Response(200, json=found.model_dump(mode="json"))
        rule_of = _RULE_PATH.fullmatch(path)
        if rule_of and request.method == "GET":
            defined = self.rules.manual.get(rule_of.group(1))
            self.rules.reads.append(
                (
                    rule_of.group(1),
                    RetrieverConfig(request.url.params["retriever_config"]),
                )
            )
            if defined is None:
                code = ErrorCode.NOT_FOUND
                return httpx.Response(HTTP_STATUS[code], json=error_body(code))
            return httpx.Response(200, json=defined.model_dump(mode="json"))
        return httpx.Response(404, json={"errorCode": "ERR_DIRECT_INVOKE"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


# --- The local PostgreSQL, seen as one role or another ----------------------------


def connect(settings: Settings, **options: Any) -> psycopg.Connection[Any]:
    """A connection of the test's own, as the role the settings name."""
    return psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
        **options,
    )


def as_service(settings: Settings) -> Settings:
    """The settings the service itself runs with: signed in as its own role."""
    if settings.database_service_role is None:
        raise ValueError("the settings name no service role")
    return settings.model_copy(
        update={
            "database_user": settings.database_service_role,
            "database_service_role": None,
        }
    )
