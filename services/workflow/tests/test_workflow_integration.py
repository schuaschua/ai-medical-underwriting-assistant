"""Story 1.6, against a real PostgreSQL and the Durable Task Scheduler emulator (compose.yaml).

Run `docker compose up --detach --wait` first. No test here calls Azure. Each
test has a database and a service role of its own; the scheduler tests share
the emulator's `aiuw-test` task hub and use case ids of their own.
"""

import asyncio
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from alembic import command
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import (
    OrchestrationQuery,
    OrchestrationState,
    OrchestrationStatus,
)
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy.exc import DBAPIError
from workflow_fakes import (
    BY_CUSTOMER,
    STARTED_BY,
    FakeStages,
    SidecarStandIn,
    classification_done,
    facts_done,
    redaction_done,
    redaction_failed,
    starting,
)
from workflow_local import after_the_start, connect

from contracts.enums import CaseStatus, ClassifierContender, RetrieverConfig
from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models._stage import StageResult
from contracts.models.workflow import AuditTrail, CaseProgress, CaseStarted
from workflow.adapters.db import (
    SqlCaseStore,
    SqlSchemaRevision,
    SqlTrailGuard,
    build_database,
)
from workflow.adapters.http.app import create_app
from workflow.adapters.migrations import (
    AUDIT_TRAIL_HAS_ROWS_MESSAGE,
    alembic_config,
    bundled_head,
)
from workflow.adapters.scheduler import (
    Activities,
    SchedulerEngine,
    build_client,
    build_worker,
)
from workflow.domain.cases import (
    read_audit_trail,
    read_progress,
    record_stage_result,
    start_case,
)
from workflow.domain.entities import StartParameters
from workflow.domain.lifecycle import new_case
from workflow.domain.ports import EngineState
from workflow.domain.recording import RecordOutcome
from workflow.settings import Settings

pytestmark = pytest.mark.integration

PARAMETERS = StartParameters(
    classifier_contender=ClassifierContender.LLM,
    retriever_configs=(RetrieverConfig.R3,),
    stop_after=None,
    eval_run_id=None,
)
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


# --- Helpers -------------------------------------------------------------------


def query(
    settings: Settings, statement: str, *parameters: object
) -> list[tuple[Any, ...]]:
    """Run one read with a connection of the test's own."""
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def audit_rows(settings: Settings, case_id: str) -> list[tuple[Any, ...]]:
    return after_the_start(
        query(
            settings,
            "SELECT action, page_id::text, ref::text, error_code, detail, actor "
            "FROM workflow.audit_event WHERE case_id = %s "
            "ORDER BY audit_event_seq",
            case_id,
        )
    )


def case_row(settings: Settings, case_id: str) -> tuple[Any, ...] | None:
    rows = query(
        settings,
        "SELECT case_status, redaction_status FROM workflow.case_status "
        "WHERE case_id = %s",
        case_id,
    )
    return rows[0] if rows else None


@contextmanager
def a_store(settings: Settings) -> Iterator[tuple[SqlCaseStore, asyncio.Runner]]:
    """The real store, as the service's role, with a loop to run its calls on."""
    with asyncio.Runner() as runner:
        database = build_database(settings)
        try:
            yield SqlCaseStore(database), runner
        finally:
            runner.run(database.dispose())


def started_case(store: SqlCaseStore, runner: asyncio.Runner) -> str:
    case_id = new_id()
    runner.run(store.start(*starting(new_case(case_id, PARAMETERS, NOW))))
    return case_id


def outcome_of(
    store: SqlCaseStore, runner: asyncio.Runner, result: StageResult
) -> RecordOutcome:
    return runner.run(record_stage_result(result, store=store))


def record(store: SqlCaseStore, runner: asyncio.Runner, result: StageResult) -> bool:
    """Record a result; say whether anything was written."""
    return outcome_of(store, runner, result) is RecordOutcome.RECORDED


def set_page_status(admin: Settings, page_id: str, status: str) -> None:
    """Put a page where a later story's step (the gate, a decision) will put it."""
    with connect(admin, autocommit=True) as connection:
        connection.execute(
            "UPDATE workflow.page_status SET page_status = %s WHERE page_id = %s",
            (status, page_id),
        )


@pytest.fixture
def scheduler_client(local_scheduler: Settings) -> Iterator[DurableTaskSchedulerClient]:
    """A client of the test's own, to look at what the service did in the scheduler."""
    client = build_client(local_scheduler)
    try:
        yield client
    finally:
        client.close()


def instances_of(
    client: DurableTaskSchedulerClient, case_id: str
) -> list[OrchestrationState]:
    return client.get_all_orchestration_states(
        OrchestrationQuery(instance_id_prefix=case_id, fetch_inputs_and_outputs=True)
    )


def completed(client: DurableTaskSchedulerClient, case_id: str) -> OrchestrationState:
    state = client.wait_for_orchestration_completion(case_id, timeout=30)
    assert state is not None
    return state


@pytest.fixture
def service(
    service_settings: Settings, local_scheduler: Settings
) -> Iterator[TestClient]:
    """The service as it really runs: its own role, its worker, the emulator.

    Its Dapr sidecar is a stand-in behind which `intake` redacts every case
    (story 1.7), `classification` classifies every page (story 1.8) and
    `extraction` extracts every page the gate sends on (story 2.4): a
    started case runs on to its end.
    """
    with TestClient(
        create_app(service_settings, sidecar=SidecarStandIn().transport()),
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client


# --- One orchestration per case --------------------------------------------------


def test_story_1_6_a_started_case_has_exactly_one_orchestration_named_by_its_case_id(
    service: TestClient,
    service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
) -> None:
    case_id = new_id()

    response = service.post(f"/cases/{case_id}/start", json=STARTED_BY)

    assert response.status_code == 200
    started = CaseStarted.model_validate(response.json())
    assert (started.case_id, started.case_status) == (case_id, CaseStatus.RUNNING)
    state = completed(scheduler_client, case_id)
    # AD-5: the instance id is the case id.
    assert state.instance_id == case_id
    assert state.name == "case_lifecycle"
    assert state.runtime_status is OrchestrationStatus.COMPLETED
    # Ids and the start parameters went in; the confirmed status came out.
    assert json.loads(state.serialized_input or "") == response.json()
    assert json.loads(state.serialized_output or "") == {
        "case_id": case_id,
        "case_status": "completed",
    }
    assert [item.instance_id for item in instances_of(scheduler_client, case_id)] == [
        case_id
    ]
    # Story 1.7: the lifecycle runs on through redaction, which is done; and
    # since story 2.4 to the end: the pages are extracted, the case completed.
    assert case_row(service_settings, case_id) == ("completed", "done")


def test_story_1_6_starting_again_adds_no_orchestration_and_no_rows(
    service: TestClient,
    service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
) -> None:
    case_id = new_id()
    # Started with nothing asked for: every page of the stand-in's case goes
    # on to extraction and is extracted, so the case is `running` while its
    # lifecycle runs and `completed` after it (story 2.4).
    first = service.post(f"/cases/{case_id}/start", json=STARTED_BY)
    # Once more straight away, and again after the orchestration has ended:
    # without the guard the scheduler would put a new run in a finished one's place.
    again_at_once = service.post(f"/cases/{case_id}/start", json=STARTED_BY)
    before = completed(scheduler_client, case_id)
    rows_before = query(
        service_settings, "SELECT * FROM workflow.case_status ORDER BY case_id"
    )

    again_later = service.post(
        f"/cases/{case_id}/start", json={**STARTED_BY, "stop_after": "gate"}
    )

    for response in (first, again_at_once, again_later):
        assert response.status_code == 200
    # A repeat answers with what the case was started with, and the status
    # it has by now: nothing a repeat asks for is taken.
    assert {**again_at_once.json(), "case_status": "running"} == first.json()
    assert again_later.json() == {**first.json(), "case_status": "completed"}
    assert first.json()["case_status"] == "running"
    assert first.json()["stop_after"] is None
    (after,) = instances_of(scheduler_client, case_id)
    assert after.instance_id == case_id
    # The same run, not a new one in its place.
    assert after.created_at == before.created_at
    assert after.runtime_status is OrchestrationStatus.COMPLETED
    assert (
        query(service_settings, "SELECT * FROM workflow.case_status ORDER BY case_id")
        == rows_before
    )
    assert len(rows_before) == 1
    # Nothing was run a second time: the trail holds the one event of the
    # one redaction (story 1.7), one for each of its two pages'
    # classification (story 1.8), one for each page's route (story 1.9),
    # one for each page's extraction (story 2.4), the one of the case's
    # verdict run and, after it, the one completion (stories 2.5 and 2.6),
    # and no more.
    assert [row[0] for row in audit_rows(service_settings, case_id)] == [
        "document.redacted",
        "page.classified",
        "page.classified",
        "page.routed",
        "page.routed",
        "facts.extracted",
        "facts.extracted",
        "verdict.suggested",
        "case.completed",
    ]


def test_story_1_6_progress_and_audit_are_read_from_the_real_service(
    service: TestClient, scheduler_client: DurableTaskSchedulerClient
) -> None:
    case_id = new_id()
    assert service.get(f"/cases/{case_id}/progress").status_code == 404
    assert service.get(f"/cases/{case_id}/audit").status_code == 404

    service.post(f"/cases/{case_id}/start", json=STARTED_BY)
    # Until the lifecycle has run as far as it goes, so the reads are of a
    # settled case.
    completed(scheduler_client, case_id)

    progress = CaseProgress.model_validate(
        service.get(f"/cases/{case_id}/progress").json()
    )
    trail = AuditTrail.model_validate(service.get(f"/cases/{case_id}/audit").json())
    assert progress.case_status is CaseStatus.COMPLETED
    # Story 1.7: redaction is done, and its pages and its event are there.
    # Story 1.8: each page is classified, with an event of its own.
    # Story 1.9: the gate sends each on, here to extraction, with its event.
    # Story 2.4: each is extracted, with its event. Stories 2.5 and 2.6:
    # the case then gets its verdict run, and is completed after it.
    assert [page.page_status.value for page in progress.pages] == [
        "extracted",
        "extracted",
    ]
    # Story 1.13: the start is the trail's first event.
    assert trail.events[0].action.value == "case.started"
    assert [event.action.value for event in trail.events[-2:]] == [
        "verdict.suggested",
        "case.completed",
    ]
    assert sorted(event.action.value for event in trail.events[1:-2]) == [
        "document.redacted",
        "facts.extracted",
        "facts.extracted",
        "page.classified",
        "page.classified",
        "page.routed",
        "page.routed",
    ]


@dataclass
class FlakyStore:
    """The real store, whose first reads of a case's status fail."""

    inner: SqlCaseStore
    failures_left: int
    calls: int = 0

    async def status(self, case_id: str) -> CaseStatus | None:
        self.calls += 1
        if self.failures_left > 0:
            self.failures_left -= 1
            raise ConnectionError("secret-database-detail")
        return await self.inner.status(case_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def test_story_1_6_a_start_while_the_scheduler_is_down_is_502_and_a_repeat_finishes_it(
    service_settings: Settings,
    local_scheduler: Settings,
    scheduler_client: DurableTaskSchedulerClient,
) -> None:
    case_id = new_id()
    # A port nothing listens on.
    down = service_settings.model_copy(
        update={
            "scheduler_endpoint": "http://127.0.0.1:1",
            "scheduler_timeout_seconds": 2.0,
        }
    )

    with TestClient(create_app(down), raise_server_exceptions=False) as client:
        response = client.post(f"/cases/{case_id}/start", json=STARTED_BY)

    assert response.status_code == 502
    assert ErrorBody.model_validate(response.json()).error.code is (
        ErrorCode.UPSTREAM_UNAVAILABLE
    )
    assert "127.0.0.1" not in response.text
    assert instances_of(scheduler_client, case_id) == []

    with TestClient(
        create_app(service_settings, sidecar=SidecarStandIn().transport()),
        raise_server_exceptions=False,
    ) as client:
        assert (
            client.post(f"/cases/{case_id}/start", json=STARTED_BY).status_code == 200
        )
        # While the service is still up: its worker runs the orchestration.
        assert completed(scheduler_client, case_id).instance_id == case_id

    assert query(service_settings, "SELECT count(*) FROM workflow.case_status") == [
        (1,)
    ]


# --- The recording path, as the service's role ---------------------------------


def test_story_1_6_a_done_result_is_recorded_with_its_status_change_and_one_event(
    service_settings: Settings,
) -> None:
    pages = [new_id(), new_id(), new_id()]
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        result = redaction_done(case_id, pages)

        assert record(store, runner, result) is True

        progress = runner.run(read_progress(case_id, store=store))
        trail = runner.run(read_audit_trail(case_id, store=store))

    assert case_row(service_settings, case_id) == ("running", "done")
    assert [
        (page.page_id, page.page_number, page.page_status.value)
        for page in progress.pages
    ] == [
        (pages[0], 1, "uploaded"),
        (pages[1], 2, "uploaded"),
        (pages[2], 3, "uploaded"),
    ]
    # The event read back is the record that came in, field for field.
    assert [event.action.value for event in trail.events] == [
        "case.started",
        "document.redacted",
    ]
    assert trail.events[1] == result.audit
    assert audit_rows(service_settings, case_id) == [
        (
            "document.redacted",
            None,
            result.audit.ref,
            None,
            # Counts per category, never the values (AD-8).
            {"Person": 2, "PhoneNumber": 1},
            "intake:azure-ai-language",
        )
    ]


def test_story_1_6_recording_a_result_twice_adds_no_row_and_changes_no_status(
    migrated_database: Settings, service_settings: Settings
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        page_id = new_id()
        record(store, runner, redaction_done(case_id, [page_id]))
        classified = classification_done(case_id, page_id)
        assert record(store, runner, classified) is True
        # The page then moves on (here: to extraction, and its facts are extracted).
        set_page_status(migrated_database, page_id, "extracting")
        assert record(store, runner, facts_done(case_id, page_id)) is True
        snapshot = (
            query(service_settings, "SELECT * FROM workflow.audit_event ORDER BY 1"),
            query(service_settings, "SELECT * FROM workflow.page_status ORDER BY 1"),
            query(service_settings, "SELECT * FROM workflow.case_status ORDER BY 1"),
        )

        # The classification activity runs again, long after.
        assert record(store, runner, classified) is False

    assert snapshot == (
        query(service_settings, "SELECT * FROM workflow.audit_event ORDER BY 1"),
        query(service_settings, "SELECT * FROM workflow.page_status ORDER BY 1"),
        query(service_settings, "SELECT * FROM workflow.case_status ORDER BY 1"),
    )
    # The retry did not take the page back to `classified`.
    assert query(service_settings, "SELECT page_status FROM workflow.page_status") == [
        ("extracted",)
    ]


def test_story_1_6_a_failed_result_fails_the_case_with_one_stage_failed_row(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        result = redaction_failed(case_id, "stage_timeout")

        assert record(store, runner, result) is True
        assert record(store, runner, result) is False

    assert case_row(service_settings, case_id) == ("failed", "failed")
    assert audit_rows(service_settings, case_id) == [
        (
            "stage.failed",
            None,
            result.audit.ref,
            "stage_timeout",
            None,
            "intake:azure-ai-language",
        )
    ]
    assert query(service_settings, "SELECT count(*) FROM workflow.page_status") == [
        (0,)
    ]


@contextmanager
def audit_inserts_failing(admin: Settings) -> Iterator[None]:
    """While this is open, every insert into the audit table fails in the database."""
    with connect(admin, autocommit=True) as connection:
        connection.execute(
            "CREATE FUNCTION workflow.refuse_insert() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'audit insert refused by the test'; END $$"
        )
        connection.execute(
            "CREATE TRIGGER refuse_insert BEFORE INSERT ON workflow.audit_event "
            "FOR EACH ROW EXECUTE FUNCTION workflow.refuse_insert()"
        )
    try:
        yield
    finally:
        with connect(admin, autocommit=True) as connection:
            connection.execute("DROP TRIGGER refuse_insert ON workflow.audit_event")
            connection.execute("DROP FUNCTION workflow.refuse_insert()")


def test_story_1_6_when_the_audit_insert_fails_the_status_change_is_rolled_back(
    migrated_database: Settings, service_settings: Settings
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        pages = [new_id(), new_id()]
        done = redaction_done(case_id, pages)
        failed = redaction_failed(case_id)

        with audit_inserts_failing(migrated_database):
            # The error is raised, so the activity fails and is tried again.
            with pytest.raises(DBAPIError):
                record(store, runner, done)
            with pytest.raises(DBAPIError):
                record(store, runner, failed)

            # Neither the pages, nor the redaction status, nor the case status.
            assert case_row(service_settings, case_id) == ("running", "running")
            assert query(
                service_settings, "SELECT count(*) FROM workflow.page_status"
            ) == [(0,)]
            assert audit_rows(service_settings, case_id) == []

        # The retry, once the insert works again.
        assert record(store, runner, done) is True

    assert case_row(service_settings, case_id) == ("running", "done")
    assert query(service_settings, "SELECT count(*) FROM workflow.page_status") == [
        (2,)
    ]
    assert len(audit_rows(service_settings, case_id)) == 1


def test_story_1_6_every_status_change_has_exactly_one_event_and_events_are_in_recorded_order(
    migrated_database: Settings, service_settings: Settings
) -> None:
    def at(minute: int) -> datetime:
        return NOW + timedelta(minutes=minute)

    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        first, second = new_id(), new_id()
        results: list[StageResult] = [
            redaction_done(case_id, [first, second], occurred_at=at(1)),
            classification_done(case_id, first, occurred_at=at(2)),
            classification_done(case_id, second, occurred_at=at(3)),
            facts_done(case_id, first, occurred_at=at(4)),
        ]
        # The two classifications are recorded later-first, and every result
        # twice, as retried activities would.
        for result in [results[0], results[2], results[1]]:
            assert record(store, runner, result) is True
        set_page_status(migrated_database, first, "extracting")
        assert record(store, runner, results[3]) is True
        for result in results:
            assert record(store, runner, result) is False

        trail = runner.run(read_audit_trail(case_id, store=store))
        progress = runner.run(read_progress(case_id, store=store))

    # One event per recorded result, each exactly once, in the order they
    # were recorded (story 1.12), whatever time each names.
    recorded = [results[0], results[2], results[1], results[3]]
    assert trail.events[0].action.value == "case.started"
    assert trail.events[1:] == [result.audit for result in recorded]
    assert trail.has_more is False
    # Every status the case and its pages now have is reported by one event:
    # the redaction (pages tracked), and the last stage each page went through.
    assert {page.page_id: page.page_status.value for page in progress.pages} == {
        first: "extracted",
        second: "classified",
    }
    by_subject = query(
        service_settings,
        "SELECT action, page_id::text, count(*) FROM workflow.audit_event "
        "WHERE case_id = %s GROUP BY action, page_id ORDER BY action, page_id",
        case_id,
    )
    assert sorted(by_subject) == sorted(
        [
            ("case.started", None, 1),
            ("document.redacted", None, 1),
            ("page.classified", first, 1),
            ("page.classified", second, 1),
            ("facts.extracted", first, 1),
        ]
    )


# --- The audit table is append-only ---------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE workflow.audit_event SET actor = 'underwriter'",
        "DELETE FROM workflow.audit_event",
    ],
    ids=["update", "delete"],
)
def test_story_1_6_the_service_role_cannot_change_or_remove_an_audit_event(
    service_settings: Settings, statement: str
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        record(store, runner, redaction_failed(case_id))
    before = audit_rows(service_settings, case_id)

    with (
        connect(service_settings) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute(statement)

    assert audit_rows(service_settings, case_id) == before
    assert len(before) == 1


def test_story_1_6_the_service_role_has_exactly_the_rights_the_migration_gives(
    migrated_database: Settings, service_settings: Settings
) -> None:
    role = service_settings.database_user
    granted = query(
        migrated_database,
        "SELECT table_name, string_agg(privilege_type, ',' ORDER BY privilege_type) "
        "FROM information_schema.role_table_grants "
        "WHERE grantee = %s GROUP BY table_name ORDER BY table_name",
        role,
    )

    assert granted == [
        ("alembic_version", "SELECT"),
        # AD-8: no UPDATE, no DELETE, no TRUNCATE.
        ("audit_event", "INSERT,SELECT"),
        ("case_status", "INSERT,SELECT,UPDATE"),
        # AD-5: the mark that a decision was told is added and read (story 2.4).
        ("decision_told", "INSERT,SELECT"),
        # AD-10: a decision is added and read, never changed (story 1.10).
        ("human_decision", "INSERT,SELECT"),
        ("page_status", "INSERT,SELECT,UPDATE"),
    ]
    # Only migrations change the schema or its revision.
    with (
        connect(service_settings) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute("CREATE TABLE workflow.extra (id int)")
    with (
        connect(service_settings) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute("UPDATE workflow.alembic_version SET version_num = '0000'")
    assert query(
        migrated_database, "SELECT rolsuper FROM pg_roles WHERE rolname = %s", role
    ) == [(False,)]


# --- The schema and its migrations ----------------------------------------------


def ready(settings: Settings) -> int:
    # A new app each time: what a probe sees after the pipeline has migrated.
    # Built with stand-ins for the engine, so no worker starts.
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/health").status_code == 200
        return int(client.get("/ready").status_code)


# --- A case whose orchestration cannot go on ---------------------------------------


@contextmanager
def a_worker(settings: Settings, store: Any) -> Iterator[tuple[SchedulerEngine, Any]]:
    """A worker of the test's own around `store`, and the engine that starts cases."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def on_loop(work: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(work, loop).result(30)

    client = build_client(settings)
    worker = build_worker(settings, Activities(store, loop, 10.0, FakeStages()))
    worker.start()  # type: ignore[no-untyped-call]  # the library's method has no return annotation
    try:
        yield SchedulerEngine(client), on_loop
    finally:
        worker.stop()  # type: ignore[no-untyped-call]  # as above
        client.close()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


FAST_RETRIES = {
    "activity_first_retry_seconds": 0.2,
    "activity_backoff_coefficient": 1.0,
    "activity_max_attempts": 3,
}


def test_story_1_6_when_an_activity_fails_on_every_retry_the_case_is_marked_failed(
    service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = service_settings.model_copy(update=FAST_RETRIES)
    database = build_database(settings)
    # The first step fails every time; marking the case failed works.
    store = FlakyStore(SqlCaseStore(database), failures_left=10**6)
    case = new_case(new_id(), PARAMETERS, NOW)

    with a_worker(settings, store) as (engine, on_loop):
        on_loop(store.inner.start(*starting(case)))
        assert on_loop(engine.ensure_started(case)) is EngineState.CREATED
        state = completed(scheduler_client, case.case_id)
        # A repeat start now reports the case as it is.
        with caplog.at_level(logging.INFO):
            repeat = on_loop(
                start_case(
                    case.case_id,
                    BY_CUSTOMER,
                    store=store.inner,
                    engine=engine,
                    defaults=PARAMETERS,
                    trace_id=None,
                )
            )
        on_loop(database.dispose())

    # Tried as often as the policy says, then no more.
    assert store.calls == 3
    # The orchestration ended in an orderly way, and the case is not left running.
    assert state.runtime_status is OrchestrationStatus.COMPLETED
    assert json.loads(state.serialized_output or "") == {
        "case_id": case.case_id,
        "case_status": "failed",
    }
    assert case_row(service_settings, case.case_id) == ("failed", "running")
    # One case-level event, written with the status change.
    assert audit_rows(service_settings, case.case_id) == [
        (
            "stage.failed",
            None,
            case.case_id,
            "stage_failed",
            None,
            "workflow:case-lifecycle",
        )
    ]
    assert repeat.case_status is CaseStatus.FAILED
    assert (
        f"case started: case_id={case.case_id} orchestration=completed case_status=failed"
        in (caplog.text)
    )
    assert "secret-database-detail" not in caplog.text


# --- Results that come late, twice or wrong ----------------------------------------


def snapshot(settings: Settings) -> tuple[list[tuple[Any, ...]], ...]:
    return tuple(
        query(settings, f"SELECT * FROM workflow.{table} ORDER BY 1")  # noqa: S608 - fixed table names
        for table in ("audit_event", "page_status", "case_status")
    )


# --- Append-only, for every role ---------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "TRUNCATE workflow.audit_event",
    ],
    ids=["truncate"],
)
def test_story_1_6_not_even_the_tables_owner_can_change_or_remove_an_audit_event(
    migrated_database: Settings, service_settings: Settings, statement: str
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        record(store, runner, redaction_failed(case_id))

    # The migration role owns the table (and here is a superuser as well).
    with (
        connect(migrated_database) as connection,
        pytest.raises(psycopg.errors.RestrictViolation, match="append-only"),
    ):
        connection.execute(statement)

    assert len(audit_rows(migrated_database, case_id)) == 1


def test_story_1_6_a_downgrade_is_refused_while_the_audit_trail_holds_events(
    migrated_database: Settings, service_settings: Settings
) -> None:
    config = alembic_config(migrated_database)
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        record(store, runner, redaction_failed(case_id))

    for target in ("0001", "base"):
        with pytest.raises(RuntimeError) as raised:
            command.downgrade(config, target)
        assert str(raised.value) == AUDIT_TRAIL_HAS_ROWS_MESSAGE

    # Neither the guard nor the table went: the event is there and protected.
    assert query(
        migrated_database, "SELECT version_num FROM workflow.alembic_version"
    ) == [(bundled_head(),)]
    assert len(audit_rows(migrated_database, case_id)) == 1
    with (
        connect(migrated_database) as connection,
        pytest.raises(psycopg.errors.RestrictViolation),
    ):
        connection.execute("DELETE FROM workflow.audit_event")


def test_story_1_6_not_ready_when_connected_as_a_role_that_could_change_the_trail(
    migrated_database: Settings,
    service_settings: Settings,
    local_scheduler: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def can_change(settings: Settings) -> tuple[bool, str | None]:
        database = build_database(settings)
        try:
            return (
                await SqlTrailGuard(database).role_can_change_trail(),
                await SqlSchemaRevision(database).current(),
            )
        finally:
            await database.dispose()

    # The schema is at the head for both; only the role differs.
    assert asyncio.run(can_change(service_settings)) == (False, bundled_head())
    assert asyncio.run(can_change(migrated_database)) == (True, bundled_head())

    assert ready(service_settings) == 200
    with caplog.at_level(logging.WARNING):
        # The service pointed at the owner's role by mistake.
        assert ready(migrated_database) == 502
    assert "not ready: code=audit_trail_writable" in caplog.text

    # A role that is not the owner but was granted UPDATE by hand.
    with connect(migrated_database, autocommit=True) as connection:
        connection.execute(
            sql.SQL("GRANT UPDATE ON workflow.audit_event TO {}").format(
                sql.Identifier(service_settings.database_user)
            )
        )
    assert ready(service_settings) == 502
