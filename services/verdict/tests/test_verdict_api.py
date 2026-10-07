"""Stories 2.5 and 2.6: the routes of `verdict`, over HTTP, with in-memory stand-ins.

Unit tests: the run command, the three reads, the probes and the error
shape. No database, no model, no network: the agent is a stub whose script
names the tool calls it makes and the answer it gives.
"""

import itertools
from datetime import datetime
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from verdict_fakes import (
    ACTOR,
    DM_25,
    DM_50,
    TOB_25,
    TRACE_ID,
    TRACEPARENT,
    FakeFacts,
    FakeRules,
    MemoryRepository,
    StubAgent,
    fact,
    final_answer,
    reason,
)

from contracts.enums import RetrieverConfig, StageStatus
from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.extraction import Fact
from contracts.models.verdict import (
    VerdictRunResult,
)
from contracts.operations import get_operation
from verdict.adapters.http.app import run_options
from verdict.domain.toolbox import Toolbox
from verdict.settings import Settings

Call = tuple[str, dict[str, Any]]
LIST: Call = ("list_facts", {})


def search(fact_id: str, query: str = "SECRET-QUERY") -> Call:
    return ("search_rules", {"query": query, "fact_id": fact_id})


def read(rule_id: str) -> Call:
    return ("read_rule", {"rule_id": rule_id})


def script(agent: StubAgent, *calls: Call, answers: str | None = None) -> None:
    """Make the stub an agent that makes these tool calls and then gives this answer."""

    async def run(toolbox: Toolbox) -> str | None:
        for tool, arguments in calls:
            agent.answers.append(await toolbox.call(tool, arguments))
        return answers

    agent.script = run


def command(case_id: str, **changes: object) -> dict[str, object]:
    return {"case_id": case_id, "retriever_config": "r3", **changes}


def error_code(response: httpx2.Response) -> ErrorCode:
    return ErrorBody.model_validate(response.json()).error.code


def a_fact(facts: FakeFacts, case_id: str, *, verified: bool = True) -> Fact:
    made = fact(case_id, verified=verified)
    facts.facts.append(made)
    return made


def loaded_case(
    facts: FakeFacts, rules: FakeRules, agent: StubAgent, case_id: str
) -> tuple[Fact, Fact]:
    """A case with two facts that meet rules with debits of +50 and +25."""
    glucose, smoking = a_fact(facts, case_id), a_fact(facts, case_id)
    rules.by_query = {"glucose": [DM_50, DM_25], "smoking": [TOB_25]}
    script(
        agent,
        LIST,
        search(glucose.fact_id, "glucose"),
        read(DM_50),
        search(smoking.fact_id, "smoking"),
        read(TOB_25),
        answers=final_answer(
            reason(DM_50, glucose.fact_id),
            reason(TOB_25, smoking.fact_id, debit_pct=25),
            # The agent's own verdict word is whatever: it is never stored.
            verdict="decline",
        ),
    )
    return glucose, smoking


# --- POST /verdict-runs ------------------------------------------------------------------


def test_story_2_5_post_verdict_runs_answers_the_stored_result_in_the_contracts_shape(
    client: TestClient,
    facts: FakeFacts,
    rules: FakeRules,
    agent: StubAgent,
    repository: MemoryRepository,
    case_id: str,
    fixed_now: datetime,
) -> None:
    loaded_case(facts, rules, agent, case_id)

    response = client.post(
        "/verdict-runs",
        json=command(case_id),
        headers={"traceparent": TRACEPARENT, "tracestate": "k=v"},
    )

    assert response.status_code == 200
    result = VerdictRunResult.model_validate(response.json())
    assert (result.status, result.error_code) == (StageStatus.DONE, None)
    assert (result.case_id, result.retriever_config) == (case_id, RetrieverConfig.R3)
    # Worked out by the domain from the cited rules, not the agent's word.
    assert result.verdict is not None and result.verdict.value == "loaded"
    # AD-8: `verdict.suggested`, by the service and its model, about the
    # case as a whole, with the run as its owning record.
    audit = result.audit
    assert (audit.action.value, audit.actor, audit.actor_kind.value) == (
        "verdict.suggested",
        ACTOR,
        "ai",
    )
    assert (audit.case_id, audit.page_id, audit.ref) == (
        case_id,
        None,
        result.verdict_run_id,
    )
    assert (audit.trace_id, audit.occurred_at) == (TRACE_ID, fixed_now)
    # The stored result is what was answered, and the path is the contracts'.
    stored = repository.only_row()
    assert stored.verdict_run_id == result.verdict_run_id
    assert VerdictRunResult.model_validate_json(stored.result_json or "") == result
    operation = get_operation("run_verdict")
    assert (operation.path, operation.idempotency_key) == (
        "/verdict-runs",
        ("case_id", "retriever_config"),
    )
    # The caller's trace context goes on to `extraction` with the read.
    assert facts.trace_contexts[0] == {"traceparent": TRACEPARENT, "tracestate": "k=v"}
    # Ids and a verdict word only: nothing of a fact or a rule comes back.
    assert "SECRET" not in response.text


@pytest.mark.parametrize("row", ["r6"])
def test_story_2_5_a_row_that_is_not_built_is_409_retriever_not_available(
    client: TestClient,
    facts: FakeFacts,
    agent: StubAgent,
    repository: MemoryRepository,
    case_id: str,
    row: str,
    settings: Settings,
) -> None:
    a_fact(facts, case_id)

    response = client.post("/verdict-runs", json=command(case_id, retriever_config=row))

    # Refused, and not worth sending again: no key row, no work.
    assert (response.status_code, error_code(response)) == (
        409,
        ErrorCode.RETRIEVER_NOT_AVAILABLE,
    )
    assert (repository.rows, agent.runs, facts.calls) == ({}, 0, 0)
    assert client.get(f"/cases/{case_id}/verdict-runs").json()["verdict_runs"] == []
    # Stories 3.3 and 3.7: `r5` needs a search service at `retrieval` and
    # `r4` a reranker. Each is refused the same way unless the settings
    # name it, and a row this build cannot run is no setting at all.
    for needs_more in ("r4", "r5"):
        other = client.post(
            "/verdict-runs", json=command(case_id, retriever_config=needs_more)
        )
        assert (other.status_code, error_code(other)) == (
            409,
            ErrorCode.RETRIEVER_NOT_AVAILABLE,
        )
        assert "Rows r1, r2 and r3 can be used" in other.json()["error"]["message"]
    named = settings.model_copy(
        update={"available_retriever_configs": ["r1", "r2", "r3", "r4", "r5"]}
    )
    for config in (RetrieverConfig.R4, RetrieverConfig.R5):
        assert config in run_options(named).retriever_configs
        assert config not in run_options(settings).retriever_configs
    with pytest.raises(ValidationError):
        Settings(available_retriever_configs=[RetrieverConfig.R3, RetrieverConfig(row)])


@pytest.mark.parametrize(
    "body",
    [
        # The caller names no run, no step limit and no verdict.
        {"case_id": new_id(), "retriever_config": "r3", "text": "SECRET-INPUT"},
    ],
)
def test_story_2_5_a_malformed_command_is_422_without_echoing_the_input(
    client: TestClient, repository: MemoryRepository, body: dict[str, object]
) -> None:
    response = client.post("/verdict-runs", json=body)

    assert (response.status_code, error_code(response)) == (
        422,
        ErrorCode.VALIDATION_FAILED,
    )
    assert "SECRET-INPUT" not in response.text
    assert repository.rows == {}


# --- GET /verdict-runs/{verdict_run_id}/steps and GET /cases/{case_id}/agent-steps --------


def test_story_2_5_no_run_and_no_step_can_be_changed_or_removed_over_http(
    client: TestClient,
) -> None:
    run_id, case_id = new_id(), new_id()

    for method, path in itertools.product(
        ("PUT", "PATCH", "DELETE"),
        (
            "/verdict-runs",
            f"/verdict-runs/{run_id}/steps",
            f"/cases/{case_id}/verdict-runs",
            f"/cases/{case_id}/agent-steps",
        ),
    ):
        response = client.request(method, path)
        assert (response.status_code, error_code(response)) == (
            405,
            ErrorCode.METHOD_NOT_ALLOWED,
        ), path
