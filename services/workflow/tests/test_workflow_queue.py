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
from pydantic import ValidationError
from workflow_fakes import (
    FakeEngine,
    MemoryCaseStore,
    classification_done,
    redaction_done,
)

from contracts.decisions import STATUSES_AWAITING_A_DECISION
from contracts.enums import (
    ClassifierContender,
    PageStatus,
    QueuedBy,
    RetrieverConfig,
    StopAfter,
)
from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import DecisionRequest, PageQueue
from workflow.adapters.http.app import create_app
from workflow.adapters.http.routes import Dependencies
from workflow.domain.cases import (
    fail_case,
    record_route,
    record_stage_result,
    settle_case_after_gate,
)
from workflow.domain.decisions import record_decision
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case
from workflow.domain.queue import (
    DEFAULT_PAGE_QUEUE_LIMIT,
    is_queue,
    queued_by,
    read_page_queue,
)
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
        await store.start(new_case(case_id, parameters, NOW))
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
        await settle_case_after_gate(case_id, store=store, now=lambda: minutes(60))

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


# --- The rule ---------------------------------------------------------------------


def test_story_1_11_a_queue_is_a_status_in_which_a_page_waits_for_a_person() -> None:
    assert {status for status in PageStatus if is_queue(status)} == set(
        STATUSES_AWAITING_A_DECISION
    )
    assert is_queue(PageStatus.AWAITING_TRIAGE)
    assert not is_queue(PageStatus.EXTRACTING)


def test_story_1_11_a_page_in_triage_came_from_the_gate_unless_the_customer_kept_it() -> (
    None
):
    assert queued_by(PageStatus.AWAITING_TRIAGE, False) is QueuedBy.GATE
    assert queued_by(PageStatus.AWAITING_TRIAGE, True) is QueuedBy.CUSTOMER
    assert queued_by(PageStatus.AWAITING_CUSTOMER, False) is None


@pytest.mark.parametrize(
    "status", [status for status in PageStatus if not is_queue(status)]
)
def test_story_1_11_a_status_that_is_no_queue_is_refused_before_the_store_is_read(
    status: PageStatus,
) -> None:
    store = MemoryCaseStore(fail=True)

    with pytest.raises(DomainError) as refused:
        asyncio.run(read_page_queue(status, store=store))

    assert refused.value.code is ErrorCode.VALIDATION_FAILED


# --- The route --------------------------------------------------------------------


def test_story_1_11_the_queue_lists_the_waiting_pages_of_every_case_oldest_first(
    client: TestClient, store: MemoryCaseStore
) -> None:
    first_case, (unsure, sure_medical, asked) = routed_case(
        store, [(Route.TRIAGE, 3), (Route.EXTRACTION, 3), (Route.CUSTOMER, 3)]
    )
    second_case, (older,) = routed_case(store, [(Route.TRIAGE, 1)])
    decide(store, first_case, asked, "keep", "customer", minute=5)

    queue = queue_of(client)

    # By how long each page has waited in triage: the kept page came last.
    assert listed(queue) == [older, unsure, asked]
    assert queue.has_more is False
    assert [(page.case_id, page.page_number) for page in queue.pages] == [
        (second_case, 1),
        (first_case, 1),
        (first_case, 3),
    ]
    assert {page.page_status for page in queue.pages} == {PageStatus.AWAITING_TRIAGE}
    assert {page.classifier_contender for page in queue.pages} == {
        ClassifierContender.LLM
    }
    # How each page got there: the gate, or the customer who kept it.
    assert [page.queued_by for page in queue.pages] == [
        QueuedBy.GATE,
        QueuedBy.GATE,
        QueuedBy.CUSTOMER,
    ]
    assert sure_medical not in listed(queue)


def test_story_1_11_the_customers_queue_is_read_the_same_way(
    client: TestClient, store: MemoryCaseStore
) -> None:
    _, (asked, _unsure) = routed_case(store, [(Route.CUSTOMER, 1), (Route.TRIAGE, 1)])

    queue = queue_of(client, "awaiting_customer")

    assert listed(queue) == [asked]
    assert queue.pages[0].queued_by is None


def test_story_1_11_the_pages_of_an_eval_run_case_are_not_listed(
    client: TestClient, store: MemoryCaseStore
) -> None:
    _, (listed_page,) = routed_case(store, [(Route.TRIAGE, 1)])
    routed_case(store, [(Route.TRIAGE, 1)], replace(PARAMETERS, eval_run_id=new_id()))

    assert listed(queue_of(client)) == [listed_page]


def test_story_1_11_the_pages_of_a_failed_case_are_not_listed(
    client: TestClient, store: MemoryCaseStore
) -> None:
    _, (listed_page,) = routed_case(store, [(Route.TRIAGE, 1)])
    failed_case, _ = routed_case(store, [(Route.TRIAGE, 1)])
    asyncio.run(fail_case(failed_case, store=store))

    assert store.cases[failed_case].case_status.value == "failed"
    assert listed(queue_of(client)) == [listed_page]


def test_story_1_11_the_pages_of_a_completed_case_are_not_listed(
    client: TestClient, store: MemoryCaseStore
) -> None:
    _, (listed_page,) = routed_case(store, [(Route.TRIAGE, 1)])
    # A case told to stop after the gate is completed there, with its page
    # still in triage: nobody decides it.
    completed_case, _ = routed_case(
        store, [(Route.TRIAGE, 1)], replace(PARAMETERS, stop_after=StopAfter.GATE)
    )

    assert store.cases[completed_case].case_status.value == "completed"
    assert listed(queue_of(client)) == [listed_page]


def test_story_1_11_a_decided_page_leaves_the_queue(
    client: TestClient, store: MemoryCaseStore
) -> None:
    case_id, (accepted, denied, waiting) = routed_case(
        store, [(Route.TRIAGE, 1), (Route.TRIAGE, 2), (Route.TRIAGE, 3)]
    )

    decide(store, case_id, accepted, "accept", "underwriter", minute=4)
    decide(store, case_id, denied, "deny", "underwriter", minute=5)

    assert listed(queue_of(client)) == [waiting]


def test_story_1_11_an_empty_queue_is_an_empty_list(client: TestClient) -> None:
    queue = queue_of(client)

    assert (queue.pages, queue.has_more) == ([], False)


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
        "?status=",
        "?status=waiting",
        "?status=AWAITING_TRIAGE",
        # A page status, but not one a page waits in.
        "?status=extracting",
        "?status=denied",
        "?status=awaiting_triage&limit=5",
    ],
    ids=["missing", "empty", "unknown", "case", "extracting", "denied", "extra"],
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


def test_story_1_11_the_queue_takes_get_only(client: TestClient) -> None:
    response = client.post("/pages?status=awaiting_triage")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


# --- The setting ------------------------------------------------------------------


def test_story_1_11_the_queue_limit_is_a_setting_with_a_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().page_queue_limit == DEFAULT_PAGE_QUEUE_LIMIT == 100

    monkeypatch.setenv("WORKFLOW_PAGE_QUEUE_LIMIT", "25")

    assert Settings().page_queue_limit == 25


@pytest.mark.parametrize("value", ["0", "-1", "1001", "many"])
def test_story_1_11_a_queue_limit_out_of_bounds_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("WORKFLOW_PAGE_QUEUE_LIMIT", value)

    with pytest.raises(ValidationError):
        Settings()
