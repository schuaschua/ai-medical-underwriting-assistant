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
    verdict_done,
)
from workflow_local import connect, wait_for_case_status

from contracts.enums import (
    ClassifierContender,
    DemoRole,
    RetrieverConfig,
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
from workflow.domain.recording import RecordOutcome
from workflow.domain.verdicts import complete_case
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


def suggest_and_complete(
    store: SqlCaseStore,
    runner: asyncio.Runner,
    case_id: str,
    eval_run_id: str | None = None,
) -> str:
    """The lifecycle's last steps (stories 2.5 and 2.6): the verdict run is recorded, then the case completed."""

    async def scenario() -> str:
        run = verdict_done(case_id, eval_run_id=eval_run_id)
        assert await record_stage_result(run, store=store) is RecordOutcome.RECORDED
        settled = await complete_case(case_id, store=store, trace_id=TRACE_ID)
        return settled.case_status.value

    return runner.run(scenario())


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
    # The last decision made every page final; the case then got its
    # verdict run, and the completion follows that (stories 2.5 and 2.6).
    assert actions[-3:] == ["page.denied", "verdict.suggested", "case.completed"]
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


def test_story_1_13_the_service_refuses_a_start_without_a_demo_role_and_stores_no_case(
    service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
) -> None:
    case_id = new_id()

    with workflow_service(service_settings, SidecarStandIn().transport()) as client:
        response = client.post(f"/cases/{case_id}/start", json={})
        unknown = client.get(f"/cases/{case_id}/audit")

    assert (response.status_code, response.json()["error"]["code"]) == (
        403,
        "actor_not_human",
    )
    assert unknown.status_code == 404
    assert case_rows(service_settings) == []
    assert stored_events(service_settings, case_id) == []
    assert scheduler_client.get_orchestration_state(case_id) is None


# --- The start, in the store --------------------------------------------------------


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
        assert suggest_and_complete(store, runner, case_id) == "completed"
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
        suggest_and_complete(store, runner, completed)
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
