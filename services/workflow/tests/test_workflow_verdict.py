"""Stories 2.5 and 2.6: the verdict stage in the lifecycle, the step that completes a case, and one more run on a finished case.

Unit tests, with in-memory stand-ins: no database, no scheduler, no network.
The orchestrators are stepped by hand, as the engine would step them. The
same rules are proven against PostgreSQL and the scheduler emulator in
`test_workflow_verdict_integration.py`.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from workflow_fakes import (
    TRACE_ID,
    FakeEngine,
    FakeStages,
    MemoryCaseStore,
    SidecarStandIn,
    classification_done,
    facts_done,
    redaction_done,
    starting,
    verdict_done,
)

from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    RetrieverConfig,
)
from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import (
    DecisionRequest,
)
from contracts.operations import get_operation
from workflow.adapters.dapr import StageClient, build_http_client
from workflow.domain.cases import (
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.decisions import (
    record_decision,
)
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case
from workflow.domain.recording import RecordOutcome
from workflow.settings import Settings

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
# What makes a page final, by the way the gate sent it: an extraction, or a
# person's decision.
LAST_DECISION = {
    Route.CUSTOMER: ("discard", "customer"),
    Route.TRIAGE: ("deny", "underwriter"),
}


def gated_case(
    store: MemoryCaseStore,
    case_id: str,
    routes: list[Route],
    parameters: StartParameters = PARAMETERS,
) -> list[str]:
    """A started case whose pages the gate has routed and settled; its page ids, in page order."""
    page_ids = [new_id() for _ in routes]

    async def scenario() -> None:
        await store.start(*starting(new_case(case_id, parameters, NOW)))
        await record_stage_result(redaction_done(case_id, page_ids), store=store)
        classified = [classification_done(case_id, page_id) for page_id in page_ids]
        for done in classified:
            await record_stage_result(done, store=store)
        for page_id, route, done in zip(page_ids, routes, classified, strict=True):
            await record_route(
                case_id, page_id, done.classification_id, route, 0.9, store=store
            )
        await settle_case_after_gate(case_id, store=store, trace_id=None)

    asyncio.run(scenario())
    return page_ids


def decide(
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    page_id: str,
    decision: str,
    actor: str,
) -> Any:
    return asyncio.run(
        record_decision(
            case_id,
            page_id,
            DecisionRequest.model_validate({"decision": decision, "actor": actor}),
            store=store,
            engine=engine,
            trace_id=TRACE_ID,
            now=lambda: NOW,
        )
    )


def final_case(
    store: MemoryCaseStore,
    case_id: str,
    routes: list[Route] | None = None,
    parameters: StartParameters = PARAMETERS,
) -> list[str]:
    """A case whose every page is final: extracted, discarded or denied, by its route."""
    routes = routes or [Route.EXTRACTION]
    page_ids = gated_case(store, case_id, routes, parameters)
    for page_id, route in zip(page_ids, routes, strict=True):
        if route is Route.EXTRACTION:
            record(store, facts_done(case_id, page_id))
        else:
            decide(store, FakeEngine(), case_id, page_id, *LAST_DECISION[route])
    return page_ids


def record(
    store: MemoryCaseStore, result: Any, asked_afterwards: bool = False
) -> RecordOutcome:
    return asyncio.run(
        record_stage_result(
            result, store=store, asked_afterwards=asked_afterwards, now=lambda: NOW
        )
    )


# --- The client module: `verdict` through the Dapr sidecar ------------------------------


def stage_call(
    sidecar: SidecarStandIn,
    case_id: str,
    retriever_config: RetrieverConfig = RetrieverConfig.R3,
    eval_run_id: str | None = None,
) -> Any:
    async def scenario() -> Any:
        settings = Settings(applicationinsights_connection_string=None)
        client = StageClient(build_http_client(settings, sidecar.transport()), settings)
        try:
            return await client.run_verdict(
                case_id,
                retriever_config,
                eval_run_id=eval_run_id,
                trace_context={"traceparent": TRACEPARENT},
            )
        finally:
            await client.aclose()

    return asyncio.run(scenario())


def test_story_2_5_a_run_is_commanded_through_the_sidecar_by_app_id_with_ids_only(
    case_id: str,
) -> None:
    sidecar = SidecarStandIn(FakeStages())
    eval_run_id = new_id()

    result = stage_call(sidecar, case_id, eval_run_id=eval_run_id)

    assert result == sidecar.stages.verdict_results[(case_id, "r3")]
    (request,) = sidecar.requests
    operation = get_operation("run_verdict")
    assert operation.path == "/verdict-runs"
    assert (request.method, request.url.path) == (
        "POST",
        "/v1.0/invoke/verdict/method/verdict-runs",
    )
    # AD-6: ids only; the stage reads the facts and the manual itself.
    assert json.loads(request.content) == {
        "case_id": case_id,
        "retriever_config": "r3",
        "eval_run_id": eval_run_id,
    }
    assert sidecar.verdict_commands(case_id) == [json.loads(request.content)]
    # The call belongs to the trace of the activity that makes it.
    assert TRACE_ID in request.headers["traceparent"]


def test_story_2_5_a_result_about_another_configuration_or_case_is_never_recorded(
    case_id: str,
) -> None:
    for wrong in (verdict_done(case_id, "r4"), verdict_done(new_id())):

        def answers_about_another(
            request: httpx.Request, result: Any = wrong
        ) -> httpx.Response:
            return httpx.Response(200, json=result.model_dump(mode="json"))

        sidecar = SidecarStandIn()
        sidecar.handle = answers_about_another  # type: ignore[method-assign,assignment]  # this test's stage answers wrongly

        with pytest.raises(DomainError) as raised:
            stage_call(sidecar, case_id)

        # Recording it would count another row's run, or another case's, as this one's.
        assert raised.value.code is ErrorCode.VALIDATION_FAILED


# --- The request for one more run: the operation and its route ---------------------------


def post_run(client: TestClient, case_id: str, body: object = None) -> Any:
    return client.post(
        f"/cases/{case_id}/verdict-runs",
        json={"retriever_config": "r3"} if body is None else body,
        headers={"traceparent": TRACEPARENT},
    )


def error_of(response: Any) -> tuple[int, str, str]:
    detail = ErrorBody.model_validate(response.json()).error
    return response.status_code, detail.code.value, detail.trace_id


@pytest.mark.parametrize(
    "routes",
    [
        [Route.CUSTOMER],
        # One page of several is enough.
        [Route.EXTRACTION, Route.EXTRACTION, Route.TRIAGE],
    ],
    ids=["awaiting-customer", "one-of-three"],
)
def test_story_2_6_a_run_asked_for_while_a_page_is_not_final_is_409_and_nothing_is_scheduled(
    client: TestClient,
    store: MemoryCaseStore,
    engine: FakeEngine,
    case_id: str,
    routes: list[Route],
    caplog: pytest.LogCaptureFixture,
) -> None:
    page_ids = gated_case(store, case_id, routes)
    if len(routes) > 1:
        for page_id in page_ids[:2]:
            record(store, facts_done(case_id, page_id))
    before = (list(store.events), dict(store.cases))

    with caplog.at_level(logging.INFO, logger="workflow.domain.verdicts"):
        response = post_run(client, case_id)

    assert error_of(response) == (409, "pages_not_terminal", TRACE_ID)
    assert engine.verdict_run_requests == []
    assert engine.verdict_runs == {}
    assert (list(store.events), dict(store.cases)) == before
    # security rule 31: ids and codes.
    assert (
        f"verdict run refused: case_id={case_id} retriever_config=r3 "
        "code=pages_not_terminal"
    ) in caplog.text


def test_story_2_6_a_run_asked_for_while_the_cases_own_runs_are_under_way_is_409(
    client: TestClient, store: MemoryCaseStore, engine: FakeEngine, case_id: str
) -> None:
    # Every page is final and the case is not completed yet: the lifecycle
    # is making its own runs. One more run is for a finished case.
    final_case(store, case_id)
    assert store.cases[case_id].case_status is CaseStatus.RUNNING

    response = post_run(client, case_id)

    assert error_of(response) == (409, "pages_not_terminal", TRACE_ID)
    assert "still under way" in response.json()["error"]["message"]
    assert engine.verdict_run_requests == []
