"""Story 1.13, against a real PostgreSQL and the Durable Task Scheduler emulator.

Run `docker compose up --detach --wait` first. No test here calls Azure.
Where `workflow` runs as a service it has a stand-in where its Dapr sidecar
would be: behind it `intake` answers the redaction command and
`classification` the classify command, in the contracts' shapes.
"""

import asyncio
import contextlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from workflow_fakes import (
    FakeEngine,
    FakeStages,
    SidecarStandIn,
    classification_done,
    redaction_done,
    redaction_failed,
    starting,
)
from workflow_local import connect, wait_for_case_status

from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    DemoRole,
    RetrieverConfig,
    StopAfter,
)
from contracts.ids import new_id
from contracts.models.workflow import (
    AuditTrail,
    CaseList,
    DecisionRequest,
    StartCaseRequest,
)
from workflow.adapters.db import (
    ONE_CASE_COMPLETED,
    ONE_CASE_STARTED,
    SqlCaseStore,
    build_database,
)
from workflow.adapters.http.app import create_app
from workflow.adapters.scheduler import build_client
from workflow.domain.case_list import read_case_list
from workflow.domain.cases import (
    fail_case,
    record_route,
    record_stage_result,
    settle_case_after_gate,
    start_case,
)
from workflow.domain.decisions import record_decision
from workflow.domain.entities import StartParameters
from workflow.domain.gate import Route
from workflow.domain.lifecycle import new_case
from workflow.domain.recording import Recording
from workflow.settings import Settings

pytestmark = pytest.mark.integration

FAST_RETRIES = {
    "activity_first_retry_seconds": 0.2,
    "activity_backoff_coefficient": 1.0,
    "stage_max_attempts": 4,
}
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
# What the classifier reads, by page number: a non-medical page it is sure of
# (the customer is asked) and a page it is not sure of (triage).
WAITING_READINGS = {1: ("invoice", False, 1.0), 2: ("lab_report", True, 0.6)}
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


def query(settings: Settings, statement: str, *parameters: object) -> list[Any]:
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def stored_events(settings: Settings, case_id: str) -> list[tuple[Any, ...]]:
    """The case's events as the table holds them, in the order they were written."""
    return query(
        settings,
        "SELECT action, actor_kind, actor, page_id::text, ref::text, detail, "
        "error_code, eval_run_id::text, trace_id "
        "FROM workflow.audit_event WHERE case_id = %s ORDER BY audit_event_seq",
        case_id,
    )


def stored_actions(settings: Settings, case_id: str) -> list[str]:
    return [str(row[0]) for row in stored_events(settings, case_id)]


def case_rows(settings: Settings) -> list[tuple[Any, ...]]:
    return query(
        settings, "SELECT case_id::text, case_status FROM workflow.case_status"
    )


def case_status(settings: Settings, case_id: str) -> str:
    (row,) = query(
        settings,
        "SELECT case_status FROM workflow.case_status WHERE case_id = %s",
        case_id,
    )
    return str(row[0])


@contextlib.contextmanager
def a_store(settings: Settings) -> Iterator[tuple[SqlCaseStore, asyncio.Runner]]:
    """The real store, as the service's role, with a loop to run its calls on."""
    with asyncio.Runner() as runner:
        database = build_database(settings)
        try:
            yield SqlCaseStore(database), runner
        finally:
            runner.run(database.dispose())


@pytest.fixture
def scheduler_client(local_scheduler: Settings) -> Iterator[DurableTaskSchedulerClient]:
    client = build_client(local_scheduler)
    try:
        yield client
    finally:
        client.close()


@contextlib.contextmanager
def workflow_service(
    settings: Settings, sidecar: httpx.AsyncBaseTransport
) -> Iterator[TestClient]:
    """The service as it really runs: its own role, its worker, the emulator."""
    with TestClient(
        create_app(settings.model_copy(update=FAST_RETRIES), sidecar=sidecar),
        raise_server_exceptions=False,
    ) as client:
        yield client


def started(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    *,
    actor: DemoRole = DemoRole.CUSTOMER,
    at: datetime = NOW,
    parameters: StartParameters = PARAMETERS,
) -> str:
    case_id = new_id()
    runner.run(store.start(*starting(new_case(case_id, parameters, at), actor)))
    return case_id


def gated_case(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    routes: list[Route],
    parameters: StartParameters = PARAMETERS,
    at: datetime = NOW,
) -> tuple[str, list[str]]:
    """A started case whose pages the gate has routed, and that is not settled yet."""
    case_id = started(store, runner, parameters=parameters, at=at)
    page_ids = [new_id() for _ in routes]

    async def scenario() -> None:
        await record_stage_result(redaction_done(case_id, page_ids), store=store)
        for page_id, route in zip(page_ids, routes, strict=True):
            done = classification_done(case_id, page_id)
            await record_stage_result(done, store=store)
            await record_route(
                case_id, page_id, done.classification_id, route, 0.9, store=store
            )

    runner.run(scenario())
    return case_id, page_ids


def decide(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    case_id: str,
    page_id: str,
    decision: str,
    actor: str,
) -> None:
    runner.run(
        record_decision(
            case_id,
            page_id,
            DecisionRequest.model_validate({"decision": decision, "actor": actor}),
            store=store,
            engine=FakeEngine(),
            trace_id=TRACE_ID,
        )
    )


@contextlib.contextmanager
def audit_inserts_refused(migration_settings: Settings) -> Iterator[None]:
    """While this holds, the database takes no new audit event."""
    with connect(migration_settings, autocommit=True) as connection:
        connection.execute(
            "CREATE FUNCTION workflow.refuse_insert() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'audit insert refused by the test'; END $$"
        )
        connection.execute(
            "CREATE TRIGGER refuse_insert BEFORE INSERT ON workflow.audit_event "
            "FOR EACH ROW EXECUTE FUNCTION workflow.refuse_insert()"
        )
    try:
        yield
    finally:
        with connect(migration_settings, autocommit=True) as connection:
            connection.execute("DROP TRIGGER refuse_insert ON workflow.audit_event")
            connection.execute("DROP FUNCTION workflow.refuse_insert()")


# --- The whole way, through the service ---------------------------------------------


def test_story_1_13_a_customers_case_run_to_its_last_decision_starts_and_ends_its_trail(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=2, readings=WAITING_READINGS))
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        first_start = client.post(
            f"/cases/{case_id}/start",
            json={"actor": "customer"},
            headers={"traceparent": TRACEPARENT},
        )
        waiting = wait_for_case_status(client, case_id, "awaiting_human")
        listed_waiting = CaseList.model_validate(client.get("/cases").json())
        first, second = (page["page_id"] for page in waiting["pages"])
        # The same start again, by the other role and with another option.
        again = client.post(
            f"/cases/{case_id}/start",
            json={"actor": "underwriter", "stop_after": "gate"},
        )
        for page_id, decision, actor in (
            (first, "discard", "customer"),
            (second, "deny", "underwriter"),
        ):
            response = client.post(
                f"/cases/{case_id}/pages/{page_id}/decisions",
                json={"decision": decision, "actor": actor},
            )
            assert response.status_code == 200
        scheduler_client.wait_for_orchestration_completion(case_id, timeout=60)
        # The last decision once more, and the start once more.
        client.post(
            f"/cases/{case_id}/pages/{second}/decisions",
            json={"decision": "deny", "actor": "underwriter"},
        )
        client.post(f"/cases/{case_id}/start", json={"actor": "customer"})
        trail = AuditTrail.model_validate(client.get(f"/cases/{case_id}/audit").json())
        listed = CaseList.model_validate(client.get("/cases").json())

    assert first_start.status_code == again.status_code == 200
    # The repeat is answered with the case as it was first started.
    assert again.json()["stop_after"] is None
    actions = [event.action.value for event in trail.events]
    assert (actions[0], actions[-1]) == ("case.started", "case.completed")
    assert (actions.count("case.started"), actions.count("case.completed")) == (1, 1)
    # The last decision comes right before the completion it caused.
    assert actions[-2] == "page.denied"
    started_event, completed_event = trail.events[0], trail.events[-1]
    assert (started_event.actor_kind.value, started_event.actor) == (
        "human",
        "customer",
    )
    assert started_event.trace_id == TRACE_ID
    assert (completed_event.actor_kind.value, completed_event.actor) == (
        "ai",
        "workflow:case-lifecycle",
    )
    for event in (started_event, completed_event):
        assert (event.page_id, event.ref, event.detail, event.error_code) == (
            None,
            case_id,
            None,
            None,
        )
    # The list: the case while it waited, and with its status at the end.
    (while_waiting,) = listed_waiting.cases
    assert (
        while_waiting.case_id,
        while_waiting.case_status.value,
        while_waiting.page_count,
        while_waiting.waiting_page_count,
    ) == (case_id, "awaiting_human", 2, 2)
    (at_the_end,) = listed.cases
    assert (
        at_the_end.case_status.value,
        at_the_end.page_count,
        at_the_end.waiting_page_count,
    ) == ("completed", 2, 0)
    assert at_the_end.started_at == while_waiting.started_at
    assert at_the_end.started_at == started_event.occurred_at


def test_story_1_13_a_case_that_stops_after_the_gate_ends_its_trail_with_the_completion(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(pages=2, readings=WAITING_READINGS))
    case_id, eval_run_id = new_id(), new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(
            f"/cases/{case_id}/start",
            json={
                "actor": "underwriter",
                "stop_after": "gate",
                "eval_run_id": eval_run_id,
            },
        )
        wait_for_case_status(client, case_id, "completed")
        scheduler_client.wait_for_orchestration_completion(case_id, timeout=60)
        trail = AuditTrail.model_validate(client.get(f"/cases/{case_id}/audit").json())
        listed = CaseList.model_validate(client.get("/cases").json())

    actions = [event.action.value for event in trail.events]
    assert (actions[0], actions[-1]) == ("case.started", "case.completed")
    assert actions.count("case.completed") == 1
    assert trail.events[0].actor == "underwriter"
    assert {event.eval_run_id for event in trail.events} == {eval_run_id}
    # An eval-run case is not the underwriter's to open.
    assert listed == CaseList(cases=[], has_more=False)


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"stop_after": "gate"},
        {"actor": "verdict"},
        {"actor": "robot"},
        {"actor": ""},
        {"actor": "  "},
    ],
)
def test_story_1_13_the_service_refuses_a_start_without_a_demo_role_and_stores_no_case(
    service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    body: dict[str, str] | None,
) -> None:
    case_id = new_id()

    with workflow_service(service_settings, SidecarStandIn().transport()) as client:
        response = client.post(f"/cases/{case_id}/start", json=body)
        unknown = client.get(f"/cases/{case_id}/audit")

    assert (response.status_code, response.json()["error"]["code"]) == (
        403,
        "actor_not_human",
    )
    assert unknown.status_code == 404
    assert case_rows(service_settings) == []
    assert stored_events(service_settings, case_id) == []
    assert scheduler_client.get_orchestration_state(case_id) is None


def test_story_1_13_a_failed_case_keeps_its_failure_and_has_no_completion(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    sidecar = SidecarStandIn(FakeStages(redaction="failed"))
    case_id = new_id()

    with workflow_service(service_settings, sidecar.transport()) as client:
        client.post(f"/cases/{case_id}/start", json={"actor": "customer"})
        wait_for_case_status(client, case_id, "failed")
        scheduler_client.wait_for_orchestration_completion(case_id, timeout=60)
        listed = CaseList.model_validate(client.get("/cases").json())

    assert stored_actions(service_settings, case_id) == ["case.started", "stage.failed"]
    # A failed case is in the list all the same: that is how it is found.
    assert [(case.case_id, case.case_status.value) for case in listed.cases] == [
        (case_id, "failed")
    ]


# --- The start, in the store --------------------------------------------------------


def test_story_1_13_the_real_store_writes_the_start_event_with_the_case_once(
    service_settings: Settings,
) -> None:
    eval_run_id = new_id()
    of_a_run = StartParameters(
        classifier_contender=ClassifierContender.LLM,
        retriever_configs=(RetrieverConfig.R3,),
        stop_after=None,
        eval_run_id=eval_run_id,
    )
    with a_store(service_settings) as (store, runner):
        case_id = new_id()
        case = new_case(case_id, of_a_run, NOW)
        first = runner.run(store.start(*starting(case, DemoRole.UNDERWRITER)))
        # Again: by the same role, by the other one, and later.
        same = runner.run(store.start(*starting(case, DemoRole.UNDERWRITER)))
        later = new_case(case_id, PARAMETERS, NOW + timedelta(hours=1))
        other = runner.run(store.start(*starting(later, DemoRole.CUSTOMER)))

    assert first == same == other
    assert stored_events(service_settings, case_id) == [
        (
            "case.started",
            "human",
            "underwriter",
            None,
            case_id,
            None,
            None,
            eval_run_id,
            TRACE_ID,
        )
    ]
    (recorded_at,) = query(
        service_settings,
        "SELECT recorded_at, occurred_at FROM workflow.audit_event WHERE case_id = %s",
        case_id,
    )
    assert tuple(recorded_at) == (NOW, NOW)


def test_story_1_13_starts_at_once_by_both_roles_leave_one_case_and_one_event(
    service_settings: Settings,
) -> None:
    case_id = new_id()
    roles = [DemoRole.CUSTOMER, DemoRole.UNDERWRITER] * 4

    def start_as(role: DemoRole) -> str:
        # Each on a connection and a loop of its own, as separate requests are.
        with a_store(service_settings) as (store, runner):
            runner.run(
                start_case(
                    case_id,
                    StartCaseRequest(actor=role.value),
                    store=store,
                    engine=FakeEngine(),
                    defaults=PARAMETERS,
                    trace_id=None,
                )
            )
        return role.value

    with ThreadPoolExecutor(max_workers=len(roles)) as pool:
        list(pool.map(start_as, roles))

    events = stored_events(service_settings, case_id)
    assert [row[0] for row in events] == ["case.started"]
    assert events[0][2] in {"customer", "underwriter"}
    assert len(case_rows(service_settings)) == 1


def test_story_1_13_when_the_start_event_cannot_be_written_no_case_is_stored(
    migrated_database: Settings, service_settings: Settings
) -> None:
    case_id = new_id()
    with a_store(service_settings) as (store, runner):
        with audit_inserts_refused(migrated_database), pytest.raises(DBAPIError):
            runner.run(store.start(*starting(new_case(case_id, PARAMETERS, NOW))))
        during = case_rows(service_settings)
        # The retry, once the insert works again.
        runner.run(store.start(*starting(new_case(case_id, PARAMETERS, NOW))))

    assert during == []
    assert case_rows(service_settings) == [(case_id, "running")]
    assert stored_actions(service_settings, case_id) == ["case.started"]


def test_story_1_13_the_database_takes_one_start_and_one_completion_per_case(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id, (page_id,) = gated_case(store, runner, [Route.CUSTOMER])
        decide(store, runner, case_id, page_id, "discard", "customer")
        other, _ = gated_case(store, runner, [Route.CUSTOMER])
    before = stored_events(service_settings, case_id)
    assert [row[0] for row in before].count("case.completed") == 1

    # Written past the code, by the service's own role, each under another
    # reference, so the event's own unique constraint does not catch it.
    insert = (
        "INSERT INTO workflow.audit_event (audit_event_id, actor_kind, actor, "
        "action, occurred_at, case_id, page_id, ref, trace_id, recorded_at) "
        "VALUES (%s, %s, %s, %s, now(), %s, NULL, %s, %s, now())"
    )
    for kind, actor, action, index in (
        ("human", "underwriter", "case.started", ONE_CASE_STARTED),
        ("ai", "workflow:case-lifecycle", "case.completed", ONE_CASE_COMPLETED),
    ):
        with (
            connect(service_settings, autocommit=True) as connection,
            pytest.raises(psycopg.errors.UniqueViolation) as refused,
        ):
            connection.execute(
                insert, (new_id(), kind, actor, action, case_id, new_id(), "0" * 32)
            )
        assert refused.value.diag.constraint_name == index

    assert stored_events(service_settings, case_id) == before
    # The rule is per case: another case, not yet completed, takes its own.
    with connect(service_settings, autocommit=True) as connection:
        connection.execute(
            insert,
            (
                new_id(),
                "ai",
                "workflow:case-lifecycle",
                "case.completed",
                other,
                other,
                "0" * 32,
            ),
        )
    assert stored_actions(service_settings, other)[-1] == "case.completed"


def test_story_1_13_the_real_store_refuses_a_recording_that_completes_a_case(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started(store, runner)
        completing = Recording(
            audit=redaction_done(case_id, [new_id()]).audit,
            case_status=CaseStatus.COMPLETED,
        )
        with pytest.raises(ValueError, match="does not complete a case"):
            runner.run(store.record(completing, NOW))

    # Neither the status nor an event: nothing completes a case past its event.
    assert case_status(service_settings, case_id) == "running"
    assert stored_actions(service_settings, case_id) == ["case.started"]


# --- The completion, in the store ---------------------------------------------------


def test_story_1_13_an_eval_run_case_completed_by_its_last_decision_keeps_its_run_id(
    service_settings: Settings,
) -> None:
    eval_run_id = new_id()
    of_a_run = StartParameters(
        classifier_contender=ClassifierContender.LLM,
        retriever_configs=(RetrieverConfig.R3,),
        # Not told to stop after the gate: its pages are decided like any other's.
        stop_after=None,
        eval_run_id=eval_run_id,
    )
    with a_store(service_settings) as (store, runner):
        case_id, (first, second) = gated_case(
            store, runner, [Route.CUSTOMER, Route.TRIAGE], of_a_run
        )
        runner.run(settle_case_after_gate(case_id, store=store, trace_id=None))
        decide(store, runner, case_id, first, "discard", "customer")
        decide(store, runner, case_id, second, "deny", "underwriter")

    events = stored_events(service_settings, case_id)
    assert case_status(service_settings, case_id) == "completed"
    assert [row[0] for row in events][-2:] == ["page.denied", "case.completed"]
    # The completion, like the decision before it, carries the case's run id.
    assert (events[-1][7], events[-2][7]) == (eval_run_id, eval_run_id)


def test_story_1_13_the_real_store_completes_a_case_with_its_event_after_the_last_decision(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id, (first, second) = gated_case(
            store, runner, [Route.CUSTOMER, Route.TRIAGE]
        )
        runner.run(settle_case_after_gate(case_id, store=store, trace_id=None))
        decide(store, runner, case_id, first, "discard", "customer")
        before_the_last = stored_actions(service_settings, case_id)
        decide(store, runner, case_id, second, "deny", "underwriter")
        after_the_last = stored_events(service_settings, case_id)
        # Repeated: the decision, and the settle after the gate, late.
        decide(store, runner, case_id, second, "deny", "underwriter")
        runner.run(settle_case_after_gate(case_id, store=store, trace_id=None))
        runner.run(settle_case_after_gate(case_id, store=store, trace_id=None))

    assert "case.completed" not in before_the_last
    assert [row[0] for row in after_the_last][-2:] == ["page.denied", "case.completed"]
    assert after_the_last[-1] == (
        "case.completed",
        "ai",
        "workflow:case-lifecycle",
        None,
        case_id,
        None,
        None,
        None,
        # The trace of the decision that completed the case.
        TRACE_ID,
    )
    assert stored_events(service_settings, case_id) == after_the_last
    assert case_status(service_settings, case_id) == "completed"


def test_story_1_13_the_real_store_completes_a_case_stopped_after_the_gate_once(
    service_settings: Settings,
) -> None:
    eval_run_id = new_id()
    stopping = StartParameters(
        classifier_contender=ClassifierContender.LLM,
        retriever_configs=(RetrieverConfig.R3,),
        stop_after=StopAfter.GATE,
        eval_run_id=eval_run_id,
    )
    with a_store(service_settings) as (store, runner):
        case_id, _pages = gated_case(
            store, runner, [Route.TRIAGE, Route.EXTRACTION], stopping
        )
        first = runner.run(
            settle_case_after_gate(case_id, store=store, trace_id=TRACE_ID)
        )
        again = runner.run(settle_case_after_gate(case_id, store=store, trace_id=None))

    assert first.case_status.value == again.case_status.value == "completed"
    events = stored_events(service_settings, case_id)
    assert [row[0] for row in events].count("case.completed") == 1
    assert events[-1][0] == "case.completed"
    assert (events[-1][7], events[-1][8]) == (eval_run_id, TRACE_ID)


def test_story_1_13_settles_at_once_complete_a_case_with_one_event(
    service_settings: Settings,
) -> None:
    stopping = StartParameters(
        classifier_contender=ClassifierContender.LLM,
        retriever_configs=(RetrieverConfig.R3,),
        stop_after=StopAfter.GATE,
        eval_run_id=None,
    )
    with a_store(service_settings) as (store, runner):
        case_id, _pages = gated_case(store, runner, [Route.TRIAGE], stopping)

    def settle(_: int) -> str:
        with a_store(service_settings) as (own_store, own_runner):
            settled = own_runner.run(
                settle_case_after_gate(case_id, store=own_store, trace_id=None)
            )
        return settled.case_status.value

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(settle, range(6)))

    assert set(statuses) == {"completed"}
    assert stored_actions(service_settings, case_id).count("case.completed") == 1


def test_story_1_13_when_the_completion_event_cannot_be_written_the_case_is_not_completed(
    migrated_database: Settings, service_settings: Settings
) -> None:
    stopping = StartParameters(
        classifier_contender=ClassifierContender.LLM,
        retriever_configs=(RetrieverConfig.R3,),
        stop_after=StopAfter.GATE,
        eval_run_id=None,
    )
    with a_store(service_settings) as (store, runner):
        case_id, _pages = gated_case(store, runner, [Route.TRIAGE], stopping)
        with audit_inserts_refused(migrated_database), pytest.raises(DBAPIError):
            runner.run(settle_case_after_gate(case_id, store=store, trace_id=None))
        during = case_status(service_settings, case_id)
        # The activity runs again, once the insert works.
        runner.run(settle_case_after_gate(case_id, store=store, trace_id=None))

    # The status and its event are written together, or neither.
    assert during == "running"
    assert case_status(service_settings, case_id) == "completed"
    assert stored_actions(service_settings, case_id)[-1] == "case.completed"


def test_story_1_13_the_real_store_writes_no_event_for_waiting_resuming_or_failing(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id, (first, _second) = gated_case(
            store, runner, [Route.TRIAGE, Route.EXTRACTION]
        )
        runner.run(settle_case_after_gate(case_id, store=store, trace_id=None))
        waiting = case_status(service_settings, case_id)
        decide(store, runner, case_id, first, "accept", "underwriter")
        resumed = case_status(service_settings, case_id)
        # The lifecycle cannot go on: the case fails.
        runner.run(fail_case(case_id, store=store))
        runner.run(settle_case_after_gate(case_id, store=store, trace_id=None))

    assert (waiting, resumed) == ("awaiting_human", "running")
    assert case_status(service_settings, case_id) == "failed"
    case_level = [
        row[0] for row in stored_events(service_settings, case_id) if row[3] is None
    ]
    # No `case.completed`, and nothing for the wait or the resume.
    assert case_level == ["case.started", "document.redacted", "stage.failed"]


# --- The case list, in the store ----------------------------------------------------


def test_story_1_13_the_real_store_lists_cases_newest_first_with_their_page_counts(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        oldest, _ = gated_case(
            store, runner, [Route.CUSTOMER, Route.TRIAGE, Route.EXTRACTION]
        )
        runner.run(settle_case_after_gate(oldest, store=store, trace_id=None))
        failed = started(store, runner, at=NOW + timedelta(minutes=1))
        runner.run(record_stage_result(redaction_failed(failed), store=store))
        # A page of a failed case stays where it was; nobody is asked about it.
        failed_waiting, _ = gated_case(
            store, runner, [Route.TRIAGE], at=NOW + timedelta(minutes=2)
        )
        runner.run(fail_case(failed_waiting, store=store))
        completed, (page_id,) = gated_case(
            store, runner, [Route.CUSTOMER], at=NOW + timedelta(minutes=3)
        )
        decide(store, runner, completed, page_id, "discard", "customer")
        newest = started(
            store, runner, actor=DemoRole.UNDERWRITER, at=NOW + timedelta(minutes=4)
        )
        listed = runner.run(read_case_list(store=store))

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
        (newest, "running", NOW + timedelta(minutes=4), 0, 0),
        (completed, "completed", NOW + timedelta(minutes=3), 1, 0),
        (failed_waiting, "failed", NOW + timedelta(minutes=2), 1, 0),
        (failed, "failed", NOW + timedelta(minutes=1), 0, 0),
        (oldest, "awaiting_human", NOW, 3, 2),
    ]


def test_story_1_13_a_case_told_to_stop_after_the_gate_is_listed_with_no_waiting_page(
    service_settings: Settings,
) -> None:
    # Not of an eval run, so it is listed; told to stop after the gate, so
    # nobody is asked about its page, also before the settle completes it.
    stopping = StartParameters(
        classifier_contender=ClassifierContender.LLM,
        retriever_configs=(RetrieverConfig.R3,),
        stop_after=StopAfter.GATE,
        eval_run_id=None,
    )
    with a_store(service_settings) as (store, runner):
        case_id, _pages = gated_case(store, runner, [Route.TRIAGE], stopping)
        listed = runner.run(read_case_list(store=store))

    (case,) = listed.cases
    assert (
        case.case_id,
        case.case_status.value,
        case.page_count,
        case.waiting_page_count,
    ) == (case_id, "running", 1, 0)


def test_story_1_13_the_real_store_leaves_eval_run_cases_out_of_the_list(
    service_settings: Settings,
) -> None:
    of_a_run = StartParameters(
        classifier_contender=ClassifierContender.LLM,
        retriever_configs=(RetrieverConfig.R3,),
        stop_after=None,
        eval_run_id=new_id(),
    )
    with a_store(service_settings) as (store, runner):
        empty = runner.run(read_case_list(store=store))
        shown = started(store, runner)
        started(store, runner, parameters=of_a_run, at=NOW + timedelta(minutes=1))
        listed = runner.run(read_case_list(store=store))

    assert empty == CaseList(cases=[], has_more=False)
    assert [case.case_id for case in listed.cases] == [shown]
    assert listed.has_more is False


def test_story_1_13_the_real_store_bounds_the_list_and_says_when_more_exist(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_ids = [
            started(store, runner, at=NOW + timedelta(minutes=minutes))
            for minutes in range(4)
        ]
        # Two cases started at the same instant: the id settles the order.
        twins = sorted(
            started(store, runner, at=NOW + timedelta(minutes=10)) for _ in range(2)
        )
        bounded = runner.run(read_case_list(store=store, limit=3))
        exact = runner.run(read_case_list(store=store, limit=6))

    assert [case.case_id for case in bounded.cases] == [
        twins[1],
        twins[0],
        case_ids[3],
    ]
    assert bounded.has_more is True
    assert (len(exact.cases), exact.has_more) == (6, False)
    assert [case.case_id for case in exact.cases][3:] == case_ids[2::-1]


def test_story_1_13_the_service_lists_no_more_cases_than_its_setting_allows(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_ids = [
            started(store, runner, at=NOW + timedelta(minutes=minutes))
            for minutes in range(3)
        ]
    limited = service_settings.model_copy(update={"case_list_limit": 2})

    with workflow_service(limited, SidecarStandIn().transport()) as client:
        answer: dict[str, Any] = client.get("/cases").json()

    assert [case["case_id"] for case in answer["cases"]] == [case_ids[2], case_ids[1]]
    assert answer["has_more"] is True
    assert answer["cases"][0]["started_at"] == "2026-10-07T09:02:00Z"
