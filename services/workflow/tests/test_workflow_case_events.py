"""Story 1.13: who started a case, that it was completed, and the underwriter's case list.

Unit tests, with stand-ins for the store and the engine (coding-style rule
23). The same rules are proven against PostgreSQL in
`test_workflow_case_events_integration.py`.
"""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from workflow_fakes import (
    TRACE_ID,
    FakeEngine,
    MemoryCaseStore,
    classification_done,
    redaction_done,
    redaction_failed,
    starting,
)

from contracts.audit import AuditAction, AuditRecord
from contracts.enums import (
    ActorKind,
    CaseStatus,
    ClassifierContender,
    DemoRole,
    RetrieverConfig,
    StopAfter,
)
from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import (
    AuditTrail,
    CaseList,
    DecisionRequest,
    StartCaseRequest,
)
from workflow.adapters.http.app import create_app
from workflow.adapters.http.routes import Dependencies
from workflow.domain.case_list import (
    DEFAULT_CASE_LIST_LIMIT,
    read_case_list,
    waiting_page_count,
)
from workflow.domain.cases import (
    read_audit_trail,
    record_route,
    record_stage_result,
    settle_case_after_gate,
    start_case,
    starting_role,
)
from workflow.domain.decisions import record_decision
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case
from workflow.domain.recording import (
    LIFECYCLE_ACTOR,
    Recording,
    case_completed_event,
    case_started_event,
)
from workflow.settings import Settings

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
DEFAULTS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


def start(
    case_id: str,
    store: MemoryCaseStore,
    engine: FakeEngine,
    actor: str | None = "customer",
    now: datetime = NOW,
    **options: Any,
) -> None:
    asyncio.run(
        start_case(
            case_id,
            StartCaseRequest(actor=actor, **options),
            store=store,
            engine=engine,
            defaults=DEFAULTS,
            trace_id=TRACE_ID,
            now=lambda: now,
        )
    )


def trail_of(case_id: str, store: MemoryCaseStore) -> list[AuditRecord]:
    return asyncio.run(read_audit_trail(case_id, store=store)).events


def actions(case_id: str, store: MemoryCaseStore) -> list[str]:
    return [event.action.value for event in trail_of(case_id, store)]


def gated(
    case_id: str, store: MemoryCaseStore, routes: list[Route], **parameters: Any
) -> list[str]:
    """A started case whose pages the gate has routed; returns the page ids."""
    case = new_case(case_id, replace(DEFAULTS, **parameters), NOW)
    page_ids = [new_id() for _ in routes]

    async def scenario() -> None:
        await store.start(*starting(case))
        await record_stage_result(redaction_done(case_id, page_ids), store=store)
        for page_id, route in zip(page_ids, routes, strict=True):
            classified = classification_done(case_id, page_id)
            await record_stage_result(classified, store=store)
            await record_route(
                case_id,
                page_id,
                classified.classification_id,
                route,
                0.9,
                store=store,
            )

    asyncio.run(scenario())
    return page_ids


def decide(
    case_id: str,
    page_id: str,
    decision: str,
    actor: str,
    store: MemoryCaseStore,
    at: datetime = NOW,
) -> None:
    asyncio.run(
        record_decision(
            case_id,
            page_id,
            DecisionRequest.model_validate({"decision": decision, "actor": actor}),
            store=store,
            engine=FakeEngine(),
            trace_id=TRACE_ID,
            now=lambda: at,
        )
    )


# --- The start ---------------------------------------------------------------------


@pytest.mark.parametrize("role", ["customer", "underwriter"])
def test_story_1_13_a_started_case_has_one_event_that_names_who_started_it(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, role: str
) -> None:
    start(case_id, store, engine, role)

    (started,) = trail_of(case_id, store)
    assert started.action is AuditAction.CASE_STARTED
    assert (started.actor_kind, started.actor) == (ActorKind.HUMAN, role)
    # Case-level: no page, and the case is its own reference.
    assert (started.page_id, started.ref, started.detail) == (None, case_id, None)
    assert started.occurred_at == NOW
    assert started.trace_id == TRACE_ID
    assert started.error_code is None


def test_story_1_13_the_start_event_takes_the_eval_run_of_its_case(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine
) -> None:
    eval_run_id = new_id()

    start(case_id, store, engine, "underwriter", eval_run_id=eval_run_id)

    (started,) = trail_of(case_id, store)
    assert started.eval_run_id == eval_run_id


@pytest.mark.parametrize("again", ["customer", "underwriter"])
def test_story_1_13_a_repeated_start_adds_no_event_and_the_first_actor_stands(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine, again: str
) -> None:
    start(case_id, store, engine, "customer")

    start(case_id, store, engine, again, now=NOW + timedelta(minutes=5))
    start(case_id, store, engine, again, stop_after=StopAfter.GATE)

    (started,) = trail_of(case_id, store)
    assert (started.actor, started.occurred_at) == ("customer", NOW)
    assert len(engine.instances) == 1


@pytest.mark.parametrize(
    "request_",
    [
        None,
        StartCaseRequest(),
        StartCaseRequest(stop_after=StopAfter.GATE),
        StartCaseRequest(actor="verdict"),
        StartCaseRequest(actor="workflow:case-lifecycle"),
        StartCaseRequest(actor="Customer"),
        StartCaseRequest(actor="robot"),
        StartCaseRequest(actor=""),
        StartCaseRequest(actor="  "),
    ],
)
def test_story_1_13_a_start_without_a_demo_role_is_refused_and_stores_nothing(
    case_id: str,
    store: MemoryCaseStore,
    engine: FakeEngine,
    request_: StartCaseRequest | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(DomainError) as refused:
        asyncio.run(
            start_case(
                case_id,
                request_,
                store=store,
                engine=engine,
                defaults=DEFAULTS,
                trace_id=None,
            )
        )

    assert refused.value.code is ErrorCode.ACTOR_NOT_HUMAN
    assert refused.value.http_status == 403
    assert (store.cases, store.events, engine.calls) == ({}, [], [])
    # security rule 31: the id and the code; never the caller's own text.
    assert f"case start refused: case_id={case_id} code=actor_not_human" in caplog.text
    if request_ is not None and request_.actor and request_.actor.strip():
        assert request_.actor not in caplog.text.replace(case_id, "")


def test_story_1_13_the_role_of_a_start_is_read_by_one_rule() -> None:
    assert starting_role(StartCaseRequest(actor="customer")) is DemoRole.CUSTOMER
    assert starting_role(StartCaseRequest(actor="underwriter")) is DemoRole.UNDERWRITER
    with pytest.raises(DomainError):
        starting_role(None)


def test_story_1_13_a_start_that_cannot_reach_the_engine_keeps_its_one_event(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine
) -> None:
    engine.fail = True
    with pytest.raises(DomainError) as unreachable:
        start(case_id, store, engine, "customer")
    engine.fail = False

    # The repeat finishes the job, here by the other role.
    start(case_id, store, engine, "underwriter")

    assert unreachable.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    (started,) = trail_of(case_id, store)
    assert started.actor == "customer"


# --- The completion ----------------------------------------------------------------


def test_story_1_13_the_last_decision_completes_the_case_with_its_event_last(
    case_id: str, store: MemoryCaseStore
) -> None:
    first, second = gated(case_id, store, [Route.CUSTOMER, Route.TRIAGE])
    asyncio.run(settle_case_after_gate(case_id, store=store, trace_id=None))
    decide(case_id, first, "discard", "customer", store)
    assert AuditAction.CASE_COMPLETED.value not in actions(case_id, store)
    later = NOW + timedelta(minutes=3)

    decide(case_id, second, "deny", "underwriter", store, at=later)

    assert store.cases[case_id].case_status is CaseStatus.COMPLETED
    trail = trail_of(case_id, store)
    assert trail[0].action is AuditAction.CASE_STARTED
    assert [event.action.value for event in trail[-2:]] == [
        "page.denied",
        "case.completed",
    ]
    completed = trail[-1]
    assert (completed.actor_kind, completed.actor) == (ActorKind.AI, LIFECYCLE_ACTOR)
    assert (completed.page_id, completed.ref, completed.detail) == (
        None,
        case_id,
        None,
    )
    assert completed.occurred_at == later
    assert completed.trace_id == TRACE_ID


def test_story_1_13_a_case_told_to_stop_after_the_gate_is_completed_with_its_event(
    case_id: str, store: MemoryCaseStore
) -> None:
    eval_run_id = new_id()
    gated(
        case_id,
        store,
        [Route.TRIAGE, Route.EXTRACTION],
        stop_after=StopAfter.GATE,
        eval_run_id=eval_run_id,
    )

    settled = asyncio.run(
        settle_case_after_gate(case_id, store=store, trace_id=TRACE_ID)
    )

    assert settled.case_status is CaseStatus.COMPLETED
    completed = trail_of(case_id, store)[-1]
    assert completed.action is AuditAction.CASE_COMPLETED
    assert completed.eval_run_id == eval_run_id
    assert completed.trace_id == TRACE_ID


def test_story_1_13_a_repeated_settle_or_decision_adds_no_second_completion(
    case_id: str, store: MemoryCaseStore
) -> None:
    (page_id,) = gated(case_id, store, [Route.CUSTOMER])
    asyncio.run(settle_case_after_gate(case_id, store=store, trace_id=None))
    decide(case_id, page_id, "discard", "customer", store)
    before = actions(case_id, store)

    decide(case_id, page_id, "discard", "customer", store)
    asyncio.run(settle_case_after_gate(case_id, store=store, trace_id=None))
    asyncio.run(settle_case_after_gate(case_id, store=store, trace_id=None))

    assert actions(case_id, store) == before
    assert before.count("case.completed") == 1
    assert before[-1] == "case.completed"


def test_story_1_13_a_recording_cannot_complete_a_case_past_its_event(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine
) -> None:
    start(case_id, store, engine)
    # No rule plans this: a case is completed where `case.completed` is written.
    completing = Recording(
        audit=redaction_done(case_id, [new_id()]).audit,
        case_status=CaseStatus.COMPLETED,
    )

    with pytest.raises(ValueError, match="does not complete a case"):
        asyncio.run(store.record(completing, NOW))

    assert store.cases[case_id].case_status is CaseStatus.RUNNING
    assert actions(case_id, store) == ["case.started"]


def test_story_1_13_a_case_that_waits_or_resumes_has_no_event_for_it(
    case_id: str, store: MemoryCaseStore
) -> None:
    first, _second = gated(case_id, store, [Route.TRIAGE, Route.EXTRACTION])
    asyncio.run(settle_case_after_gate(case_id, store=store, trace_id=None))
    assert store.cases[case_id].case_status is CaseStatus.AWAITING_HUMAN

    # Accepted: the case runs again, with a page still being extracted.
    decide(case_id, first, "accept", "underwriter", store)

    assert store.cases[case_id].case_status is CaseStatus.RUNNING
    case_level = [
        event.action.value
        for event in trail_of(case_id, store)
        if event.page_id is None
    ]
    assert case_level == ["case.started", "document.redacted"]


def test_story_1_13_a_failed_case_keeps_its_failure_and_is_never_completed(
    case_id: str, store: MemoryCaseStore, engine: FakeEngine
) -> None:
    start(case_id, store, engine)
    asyncio.run(record_stage_result(redaction_failed(case_id), store=store))

    asyncio.run(settle_case_after_gate(case_id, store=store, trace_id=None))

    assert store.cases[case_id].case_status is CaseStatus.FAILED
    assert actions(case_id, store) == ["case.started", "stage.failed"]


def test_story_1_13_the_two_events_are_built_to_the_contracts_rules() -> None:
    case_id, eval_run_id = new_id(), new_id()

    started = case_started_event(
        case_id, DemoRole.UNDERWRITER, occurred_at=NOW, eval_run_id=eval_run_id
    )
    completed = case_completed_event(case_id, occurred_at=NOW)

    assert (started.action, started.actor) == (AuditAction.CASE_STARTED, "underwriter")
    assert (completed.action, completed.actor) == (
        AuditAction.CASE_COMPLETED,
        "workflow:case-lifecycle",
    )
    assert (started.eval_run_id, completed.eval_run_id) == (eval_run_id, None)
    # No trace is known: the placeholder, never an empty value.
    assert started.trace_id == completed.trace_id == "0" * 32


# --- The case list -----------------------------------------------------------------


def test_story_1_13_the_list_is_newest_first_with_status_start_and_page_counts(
    store: MemoryCaseStore,
) -> None:
    oldest, middle, newest = new_id(), new_id(), new_id()
    for case_id, minutes in ((oldest, 0), (middle, 1), (newest, 2)):
        case = new_case(case_id, DEFAULTS, NOW + timedelta(minutes=minutes))
        asyncio.run(store.start(*starting(case)))
    # The oldest waits for people on two of three pages; the middle one failed.
    page_ids = [new_id() for _ in range(3)]

    async def scenario() -> None:
        await record_stage_result(redaction_done(oldest, page_ids), store=store)
        for page_id, route in zip(
            page_ids, [Route.CUSTOMER, Route.TRIAGE, Route.EXTRACTION], strict=True
        ):
            classified = classification_done(oldest, page_id)
            await record_stage_result(classified, store=store)
            await record_route(
                oldest, page_id, classified.classification_id, route, 0.9, store=store
            )
        await settle_case_after_gate(oldest, store=store, trace_id=None)
        await record_stage_result(redaction_failed(middle), store=store)

    asyncio.run(scenario())

    listed = asyncio.run(read_case_list(store=store))

    assert listed.has_more is False
    assert [
        (
            case.case_id,
            case.case_status.value,
            case.started_at,
            case.page_count,
            case.waiting_page_count,
        )
        for case in listed.cases
    ] == [
        (newest, "running", NOW + timedelta(minutes=2), 0, 0),
        (middle, "failed", NOW + timedelta(minutes=1), 0, 0),
        (oldest, "awaiting_human", NOW, 3, 2),
    ]


def test_story_1_13_an_eval_run_case_is_not_listed(store: MemoryCaseStore) -> None:
    shown, hidden = new_id(), new_id()
    asyncio.run(store.start(*starting(new_case(shown, DEFAULTS, NOW))))
    of_a_run = replace(DEFAULTS, eval_run_id=new_id())
    asyncio.run(store.start(*starting(new_case(hidden, of_a_run, NOW))))

    listed = asyncio.run(read_case_list(store=store))

    assert [case.case_id for case in listed.cases] == [shown]


def test_story_1_13_the_list_is_bounded_and_says_when_more_cases_exist(
    store: MemoryCaseStore,
) -> None:
    case_ids = [new_id() for _ in range(4)]
    for minutes, case_id in enumerate(case_ids):
        case = new_case(case_id, DEFAULTS, NOW + timedelta(minutes=minutes))
        asyncio.run(store.start(*starting(case)))

    bounded = asyncio.run(read_case_list(store=store, limit=3))
    exact = asyncio.run(read_case_list(store=store, limit=4))

    # The newest three, and a note that more exist.
    assert [case.case_id for case in bounded.cases] == case_ids[:0:-1]
    assert bounded.has_more is True
    assert (len(exact.cases), exact.has_more) == (4, False)


def test_story_1_13_no_cases_is_an_empty_list(store: MemoryCaseStore) -> None:
    listed = asyncio.run(read_case_list(store=store))

    assert (listed.cases, listed.has_more) == ([], False)


@pytest.mark.parametrize("limit", [0, -1])
def test_story_1_13_a_list_limit_under_one_is_refused(
    store: MemoryCaseStore, limit: int
) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        asyncio.run(read_case_list(store=store, limit=limit))


@pytest.mark.parametrize(
    ("case_status", "stop_after", "expected"),
    [
        (CaseStatus.AWAITING_HUMAN, None, 2),
        (CaseStatus.RUNNING, None, 2),
        # Nobody is asked about a page of a case that takes no decision.
        (CaseStatus.FAILED, None, 0),
        (CaseStatus.COMPLETED, None, 0),
        (CaseStatus.COMPLETED, StopAfter.GATE, 0),
        (CaseStatus.RUNNING, StopAfter.GATE, 0),
    ],
)
def test_story_1_13_only_pages_a_person_can_still_decide_count_as_waiting(
    case_status: CaseStatus, stop_after: StopAfter | None, expected: int
) -> None:
    assert waiting_page_count(case_status, stop_after, 2) == expected


# --- The routes --------------------------------------------------------------------


def error_of(response: Any) -> tuple[int, str]:
    body = ErrorBody.model_validate(response.json())
    return response.status_code, body.error.code.value


def test_story_1_13_the_start_route_records_the_actor_and_the_trace_of_the_request(
    client: TestClient, case_id: str
) -> None:
    response = client.post(
        f"/cases/{case_id}/start",
        json={"actor": "underwriter"},
        headers={"traceparent": TRACEPARENT},
    )

    assert response.status_code == 200
    trail = AuditTrail.model_validate(client.get(f"/cases/{case_id}/audit").json())
    (started,) = trail.events
    assert (started.action.value, started.actor) == ("case.started", "underwriter")
    assert started.trace_id == TRACE_ID


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"stop_after": "gate"},
        {"actor": "workflow"},
        {"actor": "ai"},
        # Blank too: who asks is `workflow`'s rule, not a matter of form.
        {"actor": ""},
        {"actor": "  "},
        {"actor": None},
    ],
)
def test_story_1_13_the_start_route_refuses_a_start_without_a_demo_role(
    client: TestClient,
    case_id: str,
    store: MemoryCaseStore,
    body: dict[str, str] | None,
) -> None:
    response = client.post(f"/cases/{case_id}/start", json=body)

    assert error_of(response) == (403, "actor_not_human")
    assert store.cases == {}
    assert client.get(f"/cases/{case_id}/audit").status_code == 404


@pytest.mark.parametrize("body", [{"actor": 1}, {"actor": ["customer"]}])
def test_story_1_13_a_start_whose_actor_is_no_text_is_not_valid(
    client: TestClient, case_id: str, store: MemoryCaseStore, body: dict[str, Any]
) -> None:
    response = client.post(f"/cases/{case_id}/start", json=body)

    assert error_of(response) == (422, "validation_failed")
    assert store.cases == {}


def test_story_1_13_the_list_route_answers_with_the_cases_up_to_its_setting(
    settings: Settings, dependencies: Dependencies, store: MemoryCaseStore
) -> None:
    app = create_app(settings, dependencies=replace(dependencies, case_list_limit=2))
    case_ids = [new_id() for _ in range(3)]
    with TestClient(app) as limited:
        assert CaseList.model_validate(limited.get("/cases").json()) == CaseList(
            cases=[], has_more=False
        )
        for minutes, case_id in enumerate(case_ids):
            case = new_case(case_id, DEFAULTS, NOW + timedelta(minutes=minutes))
            asyncio.run(store.start(*starting(case)))
        response = limited.get("/cases")

    assert response.status_code == 200
    listed = CaseList.model_validate(response.json())
    assert [case.case_id for case in listed.cases] == [case_ids[2], case_ids[1]]
    assert listed.has_more is True
    # The fields of the contract and no other: nothing of a page or a person.
    assert set(response.json()["cases"][0]) == {
        "case_id",
        "case_status",
        "started_at",
        "page_count",
        "waiting_page_count",
    }


def test_story_1_13_the_list_route_takes_only_a_read(client: TestClient) -> None:
    for method in ("post", "put", "patch", "delete"):
        assert error_of(client.request(method, "/cases")) == (
            405,
            "method_not_allowed",
        )


def test_story_1_13_the_list_limit_is_a_setting_with_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().case_list_limit == DEFAULT_CASE_LIST_LIMIT == 100
    monkeypatch.setenv("WORKFLOW_CASE_LIST_LIMIT", "25")
    assert Settings().case_list_limit == 25
    for value in ("0", "-1", "1001", "many"):
        monkeypatch.setenv("WORKFLOW_CASE_LIST_LIMIT", value)
        with pytest.raises(ValidationError):
            Settings()
