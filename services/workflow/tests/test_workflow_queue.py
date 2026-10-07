"""Story 1.11: the cross-case queue of pages that wait for a person.

Unit tests: the rule of what a queue is, the route with its query validation,
and what the queue leaves out, with the in-memory store. No database, no
scheduler, no network.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from fastapi.testclient import TestClient
from workflow_fakes import (
    FakeEngine,
    MemoryCaseStore,
    classification_done,
    redaction_done,
    starting,
)

from contracts.enums import (
    ClassifierContender,
    RetrieverConfig,
)
from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import DecisionRequest, PageQueue
from workflow.adapters.http.app import create_app
from workflow.adapters.http.routes import Dependencies
from workflow.domain.cases import (
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.decisions import record_decision
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case
from workflow.settings import Settings

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


def minutes(count: int) -> datetime:
    return NOW + timedelta(minutes=count)


def routed_case(
    store: MemoryCaseStore,
    routes: list[tuple[Route, int]],
    parameters: StartParameters = PARAMETERS,
) -> tuple[str, list[str]]:
    """A started case whose pages the gate routed, each at its own minute; settled."""
    case_id = new_id()
    page_ids = [new_id() for _ in routes]

    async def scenario() -> None:
        await store.start(*starting(new_case(case_id, parameters, NOW)))
        await record_stage_result(
            redaction_done(case_id, page_ids), store=store, now=lambda: NOW
        )
        for page_id, (route, minute) in zip(page_ids, routes, strict=True):
            done = classification_done(case_id, page_id)
            await record_stage_result(done, store=store, now=lambda: NOW)
            await record_route(
                case_id,
                page_id,
                done.classification_id,
                route,
                0.9,
                store=store,
                now=partial(minutes, minute),
            )
        await settle_case_after_gate(
            case_id, store=store, trace_id=None, now=lambda: minutes(60)
        )

    asyncio.run(scenario())
    return case_id, page_ids


def decide(
    store: MemoryCaseStore,
    case_id: str,
    page_id: str,
    decision: str,
    actor: str,
    minute: int,
) -> None:
    asyncio.run(
        record_decision(
            case_id,
            page_id,
            DecisionRequest.model_validate({"decision": decision, "actor": actor}),
            store=store,
            engine=FakeEngine(),
            now=lambda: minutes(minute),
        )
    )


def queue_of(client: TestClient, status: str = "awaiting_triage") -> PageQueue:
    response = client.get("/pages", params={"status": status})
    assert response.status_code == 200
    return PageQueue.model_validate(response.json())


def listed(queue: PageQueue) -> list[str]:
    return [page.page_id for page in queue.pages]


# --- The route --------------------------------------------------------------------


def test_story_1_11_a_decided_page_leaves_the_queue(
    client: TestClient, store: MemoryCaseStore
) -> None:
    case_id, (accepted, denied, waiting) = routed_case(
        store, [(Route.TRIAGE, 1), (Route.TRIAGE, 2), (Route.TRIAGE, 3)]
    )

    decide(store, case_id, accepted, "accept", "underwriter", minute=4)
    decide(store, case_id, denied, "deny", "underwriter", minute=5)

    assert listed(queue_of(client)) == [waiting]


def test_story_1_11_the_queue_is_bounded_and_says_when_more_pages_wait(
    settings: Settings, dependencies: Dependencies, store: MemoryCaseStore
) -> None:
    _, pages = routed_case(store, [(Route.TRIAGE, minute) for minute in (1, 2, 3)])
    app = create_app(settings, dependencies=replace(dependencies, page_queue_limit=2))

    with TestClient(app) as client:
        bounded = queue_of(client)
    with TestClient(
        create_app(settings, dependencies=replace(dependencies, page_queue_limit=3))
    ) as client:
        whole = queue_of(client)

    assert (listed(bounded), bounded.has_more) == (pages[:2], True)
    assert (listed(whole), whole.has_more) == (pages, False)


@pytest.mark.parametrize(
    "query",
    [
        "",
        # A page status, but not one a page waits in.
        "?status=extracting",
    ],
    ids=["missing", "extracting"],
)
def test_story_1_11_a_missing_or_unknown_status_is_422_and_echoes_nothing(
    client: TestClient, store: MemoryCaseStore, query: str
) -> None:
    routed_case(store, [(Route.TRIAGE, 1)])

    response = client.get(f"/pages{query}", headers={"traceparent": TRACEPARENT})

    assert response.status_code == 422
    detail = ErrorBody.model_validate(response.json()).error
    assert (detail.code, detail.trace_id) == (ErrorCode.VALIDATION_FAILED, TRACE_ID)
    assert "waiting" not in response.text and "limit" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
