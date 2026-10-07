"""Story 1.6, against a real PostgreSQL and the Durable Task Scheduler emulator (compose.yaml).

Run `docker compose up --detach --wait` first. No test here calls Azure. Each
test has a database and a service role of its own; the scheduler tests share
the emulator's `aiuw-test` task hub and use case ids of their own.
"""

import asyncio
import json
import logging
import secrets
import socket
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import main as alembic_command_line
from alembic.migration import MigrationContext
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import (
    OrchestrationQuery,
    OrchestrationState,
    OrchestrationStatus,
)
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import DBAPIError, OperationalError
from workflow_fakes import (
    FakeStages,
    SidecarStandIn,
    classification_done,
    classification_failed,
    facts_done,
    redaction_done,
    redaction_failed,
)
from workflow_local import as_service, connect

from contracts.enums import CaseStatus, ClassifierContender, RetrieverConfig
from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models._stage import StageResult
from contracts.models.workflow import AuditTrail, CaseProgress, CaseStarted
from workflow.adapters.db import (
    SqlCaseStore,
    SqlSchemaRevision,
    SqlTrailGuard,
    build_database,
    database_url,
    metadata,
)
from workflow.adapters.http.app import create_app
from workflow.adapters.local_role import ensure_local_service_role
from workflow.adapters.migrations import (
    AUDIT_TRAIL_HAS_ROWS_MESSAGE,
    NO_SERVICE_ROLE_MESSAGE,
    alembic_config,
    bundled_head,
    include_name,
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
from workflow.domain.recording import RecordOutcome, plan_recording
from workflow.settings import Settings, get_settings

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
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
    return query(
        settings,
        "SELECT action, page_id::text, ref::text, error_code, detail, actor "
        "FROM workflow.audit_event WHERE case_id = %s ORDER BY occurred_at, recorded_at",
        case_id,
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
    runner.run(store.start(new_case(case_id, PARAMETERS, NOW)))
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
    (story 1.7): a started case runs on to a done redaction.
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

    response = service.post(f"/cases/{case_id}/start")

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
        "case_status": "running",
    }
    assert [item.instance_id for item in instances_of(scheduler_client, case_id)] == [
        case_id
    ]
    # Story 1.7: the lifecycle now runs on through redaction, which is done.
    assert case_row(service_settings, case_id) == ("running", "done")


def test_story_1_6_starting_again_adds_no_orchestration_and_no_rows(
    service: TestClient,
    service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
) -> None:
    case_id = new_id()
    first = service.post(f"/cases/{case_id}/start", json={"stop_after": "gate"})
    # Once more straight away, and again after the orchestration has ended:
    # without the guard the scheduler would put a new run in a finished one's place.
    again_at_once = service.post(f"/cases/{case_id}/start", json={"stop_after": "gate"})
    before = completed(scheduler_client, case_id)
    rows_before = query(
        service_settings, "SELECT * FROM workflow.case_status ORDER BY case_id"
    )

    again_later = service.post(f"/cases/{case_id}/start", json={"stop_after": "gate"})

    for response in (first, again_at_once, again_later):
        assert response.status_code == 200
        assert response.json() == first.json()
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
    # one redaction (story 1.7), and no more.
    assert [row[0] for row in audit_rows(service_settings, case_id)] == [
        "document.redacted"
    ]


def test_story_1_6_starts_that_arrive_together_make_one_orchestration(
    service: TestClient,
    service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
) -> None:
    case_id = new_id()

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(
            pool.map(lambda _: service.post(f"/cases/{case_id}/start"), range(6))
        )

    assert {response.status_code for response in responses} == {200}
    assert len({response.text for response in responses}) == 1
    completed(scheduler_client, case_id)
    assert len(instances_of(scheduler_client, case_id)) == 1
    assert query(service_settings, "SELECT count(*) FROM workflow.case_status") == [
        (1,)
    ]


def test_story_1_6_start_options_are_stored_and_returned_by_the_real_service(
    service: TestClient, service_settings: Settings
) -> None:
    case_id, eval_run_id = new_id(), new_id()
    options = {
        "classifier_contender": "doc-intelligence",
        "retriever_configs": ["r5", "r4"],
        "stop_after": "gate",
        "eval_run_id": eval_run_id,
    }

    response = service.post(f"/cases/{case_id}/start", json=options)

    assert response.json() == {"case_id": case_id, "case_status": "running", **options}
    assert query(
        service_settings,
        "SELECT classifier_contender, retriever_configs, stop_after, "
        "eval_run_id::text FROM workflow.case_status WHERE case_id = %s",
        case_id,
    ) == [("doc-intelligence", ["r5", "r4"], "gate", eval_run_id)]
    assert service.post(f"/cases/{case_id}/start", json={"bad": 1}).status_code == 422


def test_story_1_6_progress_and_audit_are_read_from_the_real_service(
    service: TestClient, scheduler_client: DurableTaskSchedulerClient
) -> None:
    case_id = new_id()
    assert service.get(f"/cases/{case_id}/progress").status_code == 404
    assert service.get(f"/cases/{case_id}/audit").status_code == 404

    service.post(f"/cases/{case_id}/start")
    # Until the lifecycle has run as far as it goes, so the reads are of a
    # settled case.
    completed(scheduler_client, case_id)

    progress = CaseProgress.model_validate(
        service.get(f"/cases/{case_id}/progress").json()
    )
    trail = AuditTrail.model_validate(service.get(f"/cases/{case_id}/audit").json())
    assert progress.case_status is CaseStatus.RUNNING
    # Story 1.7: redaction is done, and its pages and its event are there.
    assert [page.page_status.value for page in progress.pages] == [
        "uploaded",
        "uploaded",
    ]
    assert [event.action.value for event in trail.events] == ["document.redacted"]


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


def test_story_1_6_the_orchestration_retries_an_activity_that_failed(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    settings = service_settings.model_copy(
        update={
            "activity_first_retry_seconds": 0.2,
            "activity_backoff_coefficient": 1.0,
        }
    )
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def on_loop(work: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(work, loop).result(30)

    database = build_database(settings)
    store = FlakyStore(SqlCaseStore(database), failures_left=2)
    worker = build_worker(settings, Activities(store, loop, 10.0, FakeStages()))
    case = new_case(new_id(), PARAMETERS, NOW)
    worker.start()  # type: ignore[no-untyped-call]  # the library's method has no return annotation
    try:
        on_loop(store.inner.start(case))
        assert on_loop(SchedulerEngine(scheduler_client).ensure_started(case)) is (
            EngineState.CREATED
        )
        state = completed(scheduler_client, case.case_id)
    finally:
        worker.stop()  # type: ignore[no-untyped-call]  # as above
        on_loop(database.dispose())
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()

    # Two failures surfaced to the orchestration, which tried again each time.
    assert state.runtime_status is OrchestrationStatus.COMPLETED
    assert store.calls == 3
    # What the engine kept of the failures holds no detail of them.
    history = scheduler_client.get_orchestration_history(case.case_id)
    assert "secret-database-detail" not in repr(history)


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
        response = client.post(f"/cases/{case_id}/start")

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
        assert client.post(f"/cases/{case_id}/start").status_code == 200
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
    assert trail.events == [result.audit]
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


def test_story_1_6_the_same_result_recorded_at_once_is_written_once(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        recording = plan_recording(redaction_done(case_id, [new_id(), new_id()]))

        async def both() -> list[RecordOutcome]:
            return await asyncio.gather(
                *(store.record(recording, NOW) for _ in range(4))
            )

        outcomes = runner.run(both())

    assert sorted(outcomes) == [RecordOutcome.DUPLICATE] * 3 + [RecordOutcome.RECORDED]
    assert len(audit_rows(service_settings, case_id)) == 1
    assert query(service_settings, "SELECT count(*) FROM workflow.page_status") == [
        (2,)
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


def test_story_1_6_a_failed_page_stage_fails_the_page_and_the_case(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        first, second = new_id(), new_id()
        record(store, runner, redaction_done(case_id, [first, second]))
        failed = classification_failed(case_id, second, "invalid_model_output")

        record(store, runner, failed)

    assert case_row(service_settings, case_id) == ("failed", "done")
    assert query(
        service_settings,
        "SELECT page_number, page_status FROM workflow.page_status ORDER BY page_number",
    ) == [(1, "uploaded"), (2, "failed")]
    assert audit_rows(service_settings, case_id)[-1][:4] == (
        "stage.failed",
        second,
        failed.audit.ref,
        "invalid_model_output",
    )


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


def test_story_1_6_every_status_change_has_exactly_one_event_and_events_are_in_time_order(
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

    # One event per recorded result, each exactly once, oldest first.
    assert trail.events == [result.audit for result in results]
    times = [event.occurred_at for event in trail.events]
    assert times == sorted(times)
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
            ("document.redacted", None, 1),
            ("page.classified", first, 1),
            ("page.classified", second, 1),
            ("facts.extracted", first, 1),
        ]
    )


def test_story_1_6_a_result_for_an_unknown_case_or_page_writes_nothing(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        with pytest.raises(DomainError) as unknown_case:
            record(store, runner, redaction_done(new_id(), [new_id()]))
        case_id = started_case(store, runner)
        other_case = started_case(store, runner)
        page_id = new_id()
        record(store, runner, redaction_done(other_case, [page_id]))
        # A page of another case is not this case's page.
        with pytest.raises(DomainError) as unknown_page:
            record(store, runner, classification_done(case_id, page_id))

    assert unknown_case.value.code is ErrorCode.NOT_FOUND
    assert unknown_page.value.code is ErrorCode.NOT_FOUND
    assert audit_rows(service_settings, case_id) == []
    assert query(service_settings, "SELECT page_status FROM workflow.page_status") == [
        ("uploaded",)
    ]


# --- The audit table is append-only ---------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE workflow.audit_event SET actor = 'underwriter'",
        "UPDATE workflow.audit_event SET occurred_at = now() WHERE action = 'stage.failed'",
        "DELETE FROM workflow.audit_event",
        "TRUNCATE workflow.audit_event",
        "DROP TABLE workflow.audit_event",
        "ALTER TABLE workflow.audit_event DISABLE TRIGGER ALL",
    ],
    ids=["update", "update-one", "delete", "truncate", "drop", "alter"],
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


def test_story_1_6_an_event_is_unique_on_case_page_action_and_ref_with_null_as_one_value(
    migrated_database: Settings, service_settings: Settings
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        result = redaction_failed(case_id)
        record(store, runner, result)

    # Past the service's own check, straight at the table: a second case-level
    # event (page_id null) with the same action and ref is refused.
    with (
        connect(service_settings) as connection,
        pytest.raises(psycopg.errors.UniqueViolation),
    ):
        connection.execute(
            "INSERT INTO workflow.audit_event (audit_event_id, actor_kind, actor, "
            "action, occurred_at, case_id, page_id, ref, trace_id, recorded_at) "
            "SELECT %s, actor_kind, actor, action, occurred_at, case_id, page_id, "
            "ref, trace_id, recorded_at FROM workflow.audit_event",
            (new_id(),),
        )

    assert len(audit_rows(service_settings, case_id)) == 1


# --- The schema and its migrations ----------------------------------------------


def ready(settings: Settings) -> int:
    # A new app each time: what a probe sees after the pipeline has migrated.
    # Built with stand-ins for the engine, so no worker starts.
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/health").status_code == 200
        return int(client.get("/ready").status_code)


def test_story_1_6_readiness_fails_until_the_schema_is_at_the_bundled_head(
    empty_database: Settings, local_scheduler: Settings
) -> None:
    config = alembic_config(empty_database)
    service = as_service(empty_database)

    # No migration has run: the schema does not even exist.
    assert ready(service) == 502

    command.upgrade(config, "head")
    assert ready(service) == 200

    command.downgrade(config, "base")
    assert ready(service) == 502
    assert (
        query(
            empty_database,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'workflow' AND table_name <> 'alembic_version'",
        )
        == []
    )

    command.upgrade(config, "head")
    assert ready(service) == 200


def test_story_1_6_migration_keeps_everything_in_schema_workflow(
    migrated_database: Settings,
) -> None:
    tables = query(
        migrated_database,
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY table_name",
    )

    # The version table too is in the service's own schema (AD-4).
    assert tables == [
        ("workflow", "alembic_version"),
        ("workflow", "audit_event"),
        ("workflow", "case_status"),
        ("workflow", "page_status"),
    ]
    assert query(
        migrated_database, "SELECT version_num FROM workflow.alembic_version"
    ) == [(bundled_head(),)]


def test_story_1_6_the_tables_in_code_match_the_migrated_database(
    migrated_database: Settings,
) -> None:
    engine = create_engine(database_url(migrated_database))
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_schemas": True,
                    "include_name": include_name,
                    "version_table": "alembic_version",
                    "version_table_schema": "workflow",
                    "compare_type": True,
                },
            )
            differences = compare_metadata(context, metadata)
    finally:
        engine.dispose()

    # Nothing to add, drop or alter: what the service writes is what exists.
    assert differences == []


def test_story_1_6_schema_comparison_looks_at_schema_workflow_only() -> None:
    assert include_name("workflow", "schema", {}) is True
    for other in ("intake", "public", None):
        assert include_name(other, "schema", {}) is False
    assert include_name("audit_event", "table", {"schema_name": "workflow"}) is True


def test_story_1_6_a_migration_run_must_name_the_service_role(
    empty_database: Settings,
) -> None:
    unnamed = empty_database.model_copy(update={"database_service_role": None})

    with pytest.raises(RuntimeError, match="WORKFLOW_DATABASE_SERVICE_ROLE") as raised:
        command.upgrade(alembic_config(unnamed), "head")

    assert str(raised.value) == NO_SERVICE_ROLE_MESSAGE
    # Nothing half-made is left behind: the tables went with the failed run.
    assert query(
        empty_database,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'workflow' AND table_name <> 'alembic_version'",
    ) == [(0,)]


def test_story_1_6_the_documented_alembic_command_migrates_the_database_named_in_the_environment(
    empty_database: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # README, 'Run locally': alembic -c services/workflow/alembic.ini upgrade head,
    # with the database chosen by WORKFLOW_DATABASE_* variables alone.
    assert empty_database.database_service_role is not None
    monkeypatch.setenv("WORKFLOW_DATABASE_HOST", empty_database.database_host)
    monkeypatch.setenv("WORKFLOW_DATABASE_PORT", str(empty_database.database_port))
    monkeypatch.setenv("WORKFLOW_DATABASE_NAME", empty_database.database_name)
    monkeypatch.setenv("WORKFLOW_DATABASE_USER", empty_database.database_user)
    monkeypatch.setenv(
        "WORKFLOW_DATABASE_SERVICE_ROLE", empty_database.database_service_role
    )
    monkeypatch.setenv("WORKFLOW_DATABASE_ENTRA_AUTH", "false")
    get_settings.cache_clear()
    try:
        alembic_command_line(argv=["-c", str(ALEMBIC_INI), "upgrade", "head"])
    finally:
        get_settings.cache_clear()

    assert query(
        empty_database, "SELECT version_num FROM workflow.alembic_version"
    ) == [(bundled_head(),)]
    assert ready(as_service(empty_database)) == 200


def test_story_1_6_the_local_service_role_is_created_once_and_only_locally(
    local_database: Settings,
) -> None:
    role = f"workflow_test_{secrets.token_hex(6)}"
    settings = local_database.model_copy(update={"database_service_role": role})
    try:
        assert ensure_local_service_role(settings) == role
        assert ensure_local_service_role(settings) == role
        assert query(
            local_database,
            "SELECT rolcanlogin, rolsuper, rolcreaterole FROM pg_roles WHERE rolname = %s",
            role,
        ) == [(True, False, False)]
    finally:
        with connect(local_database, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
            )

    with pytest.raises(ValueError, match="only for the local PostgreSQL"):
        ensure_local_service_role(
            settings.model_copy(update={"database_entra_auth": True})
        )
    with pytest.raises(ValueError, match="WORKFLOW_DATABASE_SERVICE_ROLE"):
        ensure_local_service_role(local_database)


# --- The database adapter -------------------------------------------------------


def test_story_1_6_ready_fails_when_the_database_is_unreachable(
    local_scheduler: Settings,
) -> None:
    # A port nothing listens on.
    settings = local_scheduler.model_copy(update={"database_port": 1})

    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/ready")

    assert response.status_code == 502
    assert "127.0.0.1" not in response.text


def test_story_1_6_readiness_reports_a_permission_error_as_such_not_as_unmigrated(
    migrated_database: Settings,
    local_scheduler: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A role that may sign in but has no rights on schema `workflow`.
    role = f"no_rights_{secrets.token_hex(4)}"
    with connect(migrated_database, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
    settings = migrated_database.model_copy(update={"database_user": role})
    try:
        status = ready(settings)
    finally:
        with connect(migrated_database, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    assert status == 502
    assert "schema revision unreadable: sqlstate=42501" in caplog.text
    assert "schema_revision=None" not in caplog.text
    assert role not in caplog.text


def test_story_1_6_a_statement_that_runs_too_long_is_ended_by_the_server(
    service_settings: Settings,
) -> None:
    settings = service_settings.model_copy(
        update={"database_statement_timeout_seconds": 1}
    )

    async def scenario() -> None:
        database = build_database(settings)
        try:
            async with database.connect() as connection:
                await connection.execute(select(func.pg_sleep(5)))
        finally:
            await database.dispose()

    started = time.monotonic()
    with pytest.raises(OperationalError) as raised:
        asyncio.run(scenario())

    assert isinstance(raised.value.orig, psycopg.errors.QueryCanceled)
    assert time.monotonic() - started < 4


def test_story_1_6_a_database_that_does_not_answer_is_given_up_on(
    local_scheduler: Settings,
) -> None:
    # A listening socket that never speaks the protocol.
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        settings = local_scheduler.model_copy(
            update={
                "database_port": silent.getsockname()[1],
                "database_connect_timeout_seconds": 2,
            }
        )
        started = time.monotonic()
        status = ready(settings)

    assert status == 502
    assert time.monotonic() - started < 8


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
        on_loop(store.inner.start(case))
        assert on_loop(engine.ensure_started(case)) is EngineState.CREATED
        state = completed(scheduler_client, case.case_id)
        # A repeat start now reports the case as it is.
        with caplog.at_level(logging.INFO):
            repeat = on_loop(
                start_case(
                    case.case_id,
                    None,
                    store=store.inner,
                    engine=engine,
                    defaults=PARAMETERS,
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


def test_story_1_6_an_error_no_retry_can_mend_is_not_retried_by_the_engine(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    settings = service_settings.model_copy(update=FAST_RETRIES)
    database = build_database(settings)
    store = FlakyStore(SqlCaseStore(database), failures_left=0)
    # The engine is given a case this database never stored.
    case = new_case(new_id(), PARAMETERS, NOW)

    with a_worker(settings, store) as (engine, on_loop):
        on_loop(engine.ensure_started(case))
        state = completed(scheduler_client, case.case_id)
        on_loop(database.dispose())

    # Asked once: `not_found` was answered, not raised for another try.
    assert store.calls == 1
    assert state.runtime_status is OrchestrationStatus.COMPLETED
    assert json.loads(state.serialized_output or "")["case_status"] == "failed"
    assert case_row(service_settings, case.case_id) is None


def test_story_1_6_a_repeat_start_of_a_case_whose_orchestration_was_terminated_says_failed(
    service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case_id = new_id()
    with a_store(service_settings) as (store, runner):
        engine = SchedulerEngine(scheduler_client)
        # No worker runs, so the orchestration is still waiting when an
        # operator terminates it.
        first = runner.run(
            start_case(case_id, None, store=store, engine=engine, defaults=PARAMETERS)
        )
        scheduler_client.terminate_orchestration(case_id)
        ended = completed(scheduler_client, case_id)

        with caplog.at_level(logging.INFO):
            repeat = runner.run(
                start_case(
                    case_id, None, store=store, engine=engine, defaults=PARAMETERS
                )
            )
            once_more = runner.run(
                start_case(
                    case_id, None, store=store, engine=engine, defaults=PARAMETERS
                )
            )

    assert first.case_status is CaseStatus.RUNNING
    assert ended.runtime_status is OrchestrationStatus.TERMINATED
    assert repeat.case_status is CaseStatus.FAILED
    assert once_more == repeat
    # Not started again in the dead one's place.
    (instance,) = instances_of(scheduler_client, case_id)
    assert instance.runtime_status is OrchestrationStatus.TERMINATED
    assert instance.created_at == ended.created_at
    assert case_row(service_settings, case_id) == ("failed", "running")
    assert [row[:4] for row in audit_rows(service_settings, case_id)] == [
        ("stage.failed", None, case_id, "stage_failed")
    ]
    assert f"case started: case_id={case_id} orchestration=dead case_status=failed" in (
        caplog.text
    )


class NoLookup:
    """The real scheduler client, except that it never finds an instance.

    This is the moment two starts share: each looked, neither found, both create.
    """

    def __init__(self, client: DurableTaskSchedulerClient) -> None:
        self._client = client
        self.creates = 0

    def get_orchestration_state(self, instance_id: str, **options: Any) -> None:
        return None

    def schedule_new_orchestration(self, orchestrator: str, **options: Any) -> str:
        self.creates += 1
        return self._client.schedule_new_orchestration(orchestrator, **options)


def test_story_1_6_the_scheduler_itself_refuses_a_second_create_running_or_completed(
    service_settings: Settings, scheduler_client: DurableTaskSchedulerClient
) -> None:
    case = new_case(new_id(), PARAMETERS, NOW)
    blind = NoLookup(scheduler_client)
    engine = SchedulerEngine(blind)  # type: ignore[arg-type]  # wraps the real client

    # No worker yet: the first run exists and has not finished.
    assert asyncio.run(engine.ensure_started(case)) is EngineState.CREATED
    (first,) = instances_of(scheduler_client, case.case_id)
    assert first.runtime_status in {
        OrchestrationStatus.PENDING,
        OrchestrationStatus.RUNNING,
    }
    # The create is sent again, and the scheduler refuses it.
    assert asyncio.run(engine.ensure_started(case)) is EngineState.ACTIVE
    assert blind.creates == 2
    (still,) = instances_of(scheduler_client, case.case_id)
    assert still.created_at == first.created_at

    database = build_database(service_settings)
    store = SqlCaseStore(database)
    with a_worker(service_settings, store) as (_, on_loop):
        on_loop(store.start(case))
        finished = completed(scheduler_client, case.case_id)
        on_loop(database.dispose())
    assert finished.runtime_status is OrchestrationStatus.COMPLETED

    # Again after the run completed: still refused, and not replaced.
    assert asyncio.run(engine.ensure_started(case)) is EngineState.ACTIVE
    assert blind.creates == 3
    (after,) = instances_of(scheduler_client, case.case_id)
    assert after.created_at == first.created_at
    assert after.runtime_status is OrchestrationStatus.COMPLETED


# --- Results that come late, twice or wrong ----------------------------------------


def snapshot(settings: Settings) -> tuple[list[tuple[Any, ...]], ...]:
    return tuple(
        query(settings, f"SELECT * FROM workflow.{table} ORDER BY 1")  # noqa: S608 - fixed table names
        for table in ("audit_event", "page_status", "case_status")
    )


def test_story_1_6_a_result_that_arrives_out_of_order_changes_nothing_in_the_database(
    migrated_database: Settings, service_settings: Settings
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        page_id = new_id()
        record(store, runner, redaction_done(case_id, [page_id]))
        record(store, runner, classification_done(case_id, page_id))
        set_page_status(migrated_database, page_id, "extracting")
        before = snapshot(service_settings)

        # A second classification, under another ref, long after the first;
        # and extraction's result for a page that... has it already.
        late = outcome_of(store, runner, classification_done(case_id, page_id))
        assert record(store, runner, facts_done(case_id, page_id)) is True
        after_extraction = snapshot(service_settings)
        twice = outcome_of(store, runner, facts_done(case_id, page_id))

    assert (late, twice) == (RecordOutcome.OUT_OF_ORDER, RecordOutcome.OUT_OF_ORDER)
    # No status moved and no audit row was written for either.
    assert len(before[0]) == 2
    assert snapshot(service_settings) == after_extraction
    assert len(after_extraction[0]) == 3


@pytest.mark.parametrize("final", ["extracted", "discarded", "denied", "failed"])
def test_story_1_6_a_result_for_a_final_page_changes_nothing_in_the_database(
    migrated_database: Settings, service_settings: Settings, final: str
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        page_id = new_id()
        record(store, runner, redaction_done(case_id, [page_id]))
        set_page_status(migrated_database, page_id, final)
        before = snapshot(service_settings)

        outcomes = [
            outcome_of(store, runner, result)
            for result in (
                classification_done(case_id, page_id),
                facts_done(case_id, page_id),
                classification_failed(case_id, page_id),
            )
        ]

    assert outcomes == [RecordOutcome.OUT_OF_ORDER] * 3
    # The failure did not fail the case either: the whole recording is refused.
    assert snapshot(service_settings) == before
    assert case_row(service_settings, case_id) == ("running", "done")


def test_story_1_6_a_result_for_a_failed_case_changes_nothing_in_the_database(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        first, second = new_id(), new_id()
        record(store, runner, redaction_done(case_id, [first, second]))
        record(store, runner, classification_failed(case_id, first))
        before = snapshot(service_settings)

        outcomes = [
            outcome_of(store, runner, result)
            for result in (
                classification_done(case_id, second),
                classification_failed(case_id, second),
            )
        ]

    assert outcomes == [RecordOutcome.CASE_FAILED] * 2
    assert snapshot(service_settings) == before
    assert case_row(service_settings, case_id) == ("failed", "done")


def test_story_1_6_a_second_redaction_result_is_reported_not_raised(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        pages = [new_id(), new_id()]
        record(store, runner, redaction_done(case_id, pages))
        before = snapshot(service_settings)

        # Under a new ref each time: the same pages, others, and a failure.
        outcomes = [
            outcome_of(store, runner, redaction_done(case_id, pages)),
            outcome_of(store, runner, redaction_done(case_id, [new_id()])),
            outcome_of(store, runner, redaction_failed(case_id)),
        ]

    assert outcomes == [
        RecordOutcome.PAGES_ALREADY_TRACKED,
        RecordOutcome.PAGES_ALREADY_TRACKED,
        # Redaction is done; it cannot fail afterwards.
        RecordOutcome.OUT_OF_ORDER,
    ]
    assert snapshot(service_settings) == before


def test_story_1_6_a_missing_detail_is_stored_as_sql_null(
    service_settings: Settings,
) -> None:
    with a_store(service_settings) as (store, runner):
        case_id = started_case(store, runner)
        record(store, runner, redaction_failed(case_id))

    assert query(
        service_settings,
        "SELECT detail IS NULL, jsonb_typeof(detail) FROM workflow.audit_event "
        "WHERE case_id = %s",
        case_id,
    ) == [(True, None)]


# --- Append-only, for every role ---------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE workflow.audit_event SET actor = 'underwriter'",
        "DELETE FROM workflow.audit_event",
        "TRUNCATE workflow.audit_event",
    ],
    ids=["update", "delete", "truncate"],
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
