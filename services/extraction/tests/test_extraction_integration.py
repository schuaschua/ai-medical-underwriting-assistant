"""Story 2.4, against a real PostgreSQL.

Run `docker compose up --detach --wait` first. No test here calls Azure or a
model. The service runs as it really runs, with a transport where its Dapr
sidecar would be (behind it `intake` answers the page reads in the contracts'
shapes) and a transport where the chat deployment would be. The whole path,
with the real `intake`, `workflow` and the model stand-in, is tested beside
the stand-ins (`packages/` tests, story 2.4).
"""

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import httpx2
import psycopg
import pytest
from alembic import command
from extraction_fakes import (
    DEPLOYMENT,
    PAGE_TEXT,
    QUOTE,
    STATEMENT,
    IntakeSidecar,
    answer,
    completion,
    fact,
)
from fastapi.testclient import TestClient

from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.extraction import Fact, FactList, FactSetResult
from contracts.text import normalise
from extraction.adapters.db import (
    SqlFactRepository,
    build_database,
)
from extraction.adapters.http.app import create_app
from extraction.adapters.migrations import (
    alembic_config,
    bundled_head,
)
from extraction.domain.entities import FactSetKey
from extraction.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def connect(settings: Settings, **options: Any) -> psycopg.Connection[Any]:
    return psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
        **options,
    )


def query(settings: Settings, statement: str, *parameters: object) -> list[Any]:
    with connect(settings) as connection:
        return connection.execute(statement, parameters).fetchall()


def fact_sets(settings: Settings) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT case_id::text, page_id::text, status FROM extraction.fact_set "
        "ORDER BY fact_set_id",
    )


def facts(settings: Settings) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT page_number, position, statement, quote, quote_verified, "
        "quote_start, quote_end FROM extraction.fact ORDER BY page_number, position",
    )


class Deployment:
    """Stands in for the chat deployment: answers every call from a list, the last again."""

    def __init__(self, *contents: Any) -> None:
        self.contents = list(contents) or [answer()]
        self.calls = 0

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        position = min(self.calls, len(self.contents) - 1)
        self.calls += 1
        given = self.contents[position]
        if isinstance(given, int):
            return httpx2.Response(given, headers={"retry-after": "0"}, json={})
        return httpx2.Response(200, json=completion(given))


@contextmanager
def service(
    settings: Settings, sidecar: IntakeSidecar, deployment: Deployment
) -> Iterator[TestClient]:
    """The service as it really runs, on this test's database."""
    app = create_app(
        settings,
        sidecar=sidecar.transport(),
        model=httpx2.MockTransport(deployment.handle),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def command_for(case_id: str, page_id: str, **changes: Any) -> dict[str, Any]:
    return {"case_id": case_id, "page_id": page_id, **changes}


# --- Migrations and readiness -----------------------------------------------------------


def test_story_2_4_the_migration_keeps_everything_in_schema_extraction(
    migrated_database: Settings,
) -> None:
    tables = query(
        migrated_database,
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY table_name",
    )

    # One schema and its own version table (AD-4).
    assert tables == [
        ("extraction", "alembic_version"),
        ("extraction", "fact"),
        ("extraction", "fact_set"),
    ]
    assert query(
        migrated_database, "SELECT version_num FROM extraction.alembic_version"
    ) == [(bundled_head(),)]
    # The one foreign key stays inside the schema: a fact belongs to its fact
    # set. The case and the page are `intake`'s, held here as ids only.
    assert query(
        migrated_database,
        "SELECT tc.table_name, ccu.table_schema, ccu.table_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.constraint_column_usage ccu "
        "ON ccu.constraint_name = tc.constraint_name "
        "WHERE tc.table_schema = 'extraction' AND tc.constraint_type = 'FOREIGN KEY'",
    ) == [("fact", "extraction", "fact_set")]


def test_story_2_4_readiness_fails_until_the_schema_is_at_the_bundled_head(
    empty_database: Settings,
) -> None:
    sidecar, deployment = IntakeSidecar(), Deployment()

    with service(empty_database, sidecar, deployment) as client:
        # Liveness asks the process only.
        assert client.get("/health").status_code == 200
        before = client.get("/ready")
        # The service never migrates at start-up: the pipeline does.
        assert query(
            empty_database,
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name = 'extraction'",
        ) == [(0,)]
        command.upgrade(alembic_config(empty_database), "head")
        after = client.get("/ready")
        # A revision this build does not know is not its head either.
        with connect(empty_database, autocommit=True) as connection:
            connection.execute(
                "UPDATE extraction.alembic_version SET version_num = '9999'"
            )
        unknown = client.get("/ready")

    assert before.status_code == 502
    assert ErrorBody.model_validate(before.json()).error.code is (
        ErrorCode.UPSTREAM_UNAVAILABLE
    )
    assert after.status_code == 200
    assert unknown.status_code == 502


# --- The repository -------------------------------------------------------------------------


@contextmanager
def a_repository(
    settings: Settings,
) -> Iterator[tuple[SqlFactRepository, asyncio.Runner]]:
    with asyncio.Runner() as runner:
        database = build_database(settings)
        try:
            yield SqlFactRepository(database), runner
        finally:
            runner.run(database.dispose())


def a_fact(key: FactSetKey, page_number: int = 1, **changes: Any) -> Fact:
    values: dict[str, Any] = {
        "fact_id": new_id(),
        "case_id": key.case_id,
        "page_id": key.page_id,
        "page_number": page_number,
        "statement": STATEMENT,
        "quote": QUOTE,
        "quote_verified": True,
        "quote_start": 4,
        "quote_end": 15,
        **changes,
    }
    return Fact.model_validate(values)


def test_story_2_4_begins_that_arrive_together_insert_one_row(
    migrated_database: Settings,
) -> None:
    key = FactSetKey(new_id(), new_id())

    def begin(_: int) -> bool:
        with a_repository(migrated_database) as (repository, runner):
            return runner.run(repository.begin(new_id(), key, NOW)) is None

    with ThreadPoolExecutor(max_workers=6) as pool:
        inserted = list(pool.map(begin, range(6)))

    assert inserted.count(True) == 1
    assert len(fact_sets(migrated_database)) == 1


def test_story_2_4_a_running_row_is_taken_over_once_and_a_settled_one_never(
    migrated_database: Settings,
) -> None:
    key = FactSetKey(new_id(), new_id())
    fact_set_id = new_id()
    later = NOW + timedelta(hours=1)

    with a_repository(migrated_database) as (repository, runner):
        runner.run(repository.begin(fact_set_id, key, NOW))
        stale = runner.run(repository.find(key))
        assert stale is not None
        # Two repeats found the same stale row: one of them gets it.
        won = runner.run(repository.take_over(stale, later))
        lost = runner.run(repository.take_over(stale, later))
        taken = runner.run(repository.find(key))
        assert taken is not None
        runner.run(repository.finish(fact_set_id, "{}", []))
        settled = runner.run(repository.take_over(taken, later + timedelta(hours=1)))

    assert (won, lost, settled) == (True, False, False)
    # The same row, under the id it was given, begun anew.
    assert (taken.fact_set_id, taken.started_at, taken.running) == (
        fact_set_id,
        later,
        True,
    )
    assert fact_sets(migrated_database) == [(key.case_id, key.page_id, "done")]


def test_story_2_4_a_stale_row_is_taken_over_by_the_real_service_and_the_page_extracted(
    migrated_database: Settings,
) -> None:
    sidecar = IntakeSidecar()
    case_id = new_id()
    page_id = sidecar.pages.add(case_id)
    fact_set_id = new_id()
    # Left `running` by a process that died an hour ago.
    with a_repository(migrated_database) as (repository, runner):
        runner.run(
            repository.begin(
                fact_set_id,
                FactSetKey(case_id, page_id),
                datetime.now(UTC) - timedelta(hours=1),
            )
        )
    deployment = Deployment()

    with service(migrated_database, sidecar, deployment) as client:
        response = client.post("/fact-sets", json=command_for(case_id, page_id))

    result = FactSetResult.model_validate(response.json())
    assert (result.status.value, result.fact_set_id) == ("done", fact_set_id)
    assert deployment.calls == 1
    assert len(facts(migrated_database)) == 1


def test_story_2_4_a_result_and_its_facts_are_stored_together_once_and_the_first_stands(
    migrated_database: Settings,
) -> None:
    key = FactSetKey(new_id(), new_id())
    fact_set_id = new_id()
    stored_facts = [
        a_fact(key, statement="first"),
        a_fact(
            key,
            statement="second",
            quote_verified=False,
            quote_start=None,
            quote_end=None,
        ),
    ]

    with a_repository(migrated_database) as (repository, runner):
        runner.run(repository.begin(fact_set_id, key, NOW))
        first = runner.run(repository.finish(fact_set_id, '{"n": 1}', stored_facts))
        # A late finish, done or failed, changes nothing and adds no fact.
        late = runner.run(
            repository.finish(fact_set_id, '{"n": 2}', [a_fact(key, statement="late")])
        )
        failed_late = runner.run(repository.finish(fact_set_id, '{"n": 3}', None))
        listed = runner.run(repository.of_case(key.case_id))
        row = runner.run(repository.find(key))

    assert (first, late, failed_late) == ('{"n": 1}', '{"n": 1}', '{"n": 1}')
    assert listed == stored_facts
    assert row is not None and (row.result_json, row.running) == ('{"n": 1}', False)
    assert fact_sets(migrated_database) == [(key.case_id, key.page_id, "done")]
    assert [item[:3] for item in facts(migrated_database)] == [
        (1, 0, "first"),
        (1, 1, "second"),
    ]


def test_story_2_4_the_database_takes_offsets_only_with_a_verified_quote(
    migrated_database: Settings,
) -> None:
    key = FactSetKey(new_id(), new_id())
    fact_set_id = new_id()
    with a_repository(migrated_database) as (repository, runner):
        runner.run(repository.begin(fact_set_id, key, NOW))

    insert = (
        "INSERT INTO extraction.fact (fact_id, fact_set_id, case_id, page_id, "
        "page_number, position, statement, quote, quote_verified, quote_start, "
        "quote_end) VALUES (%s, %s, %s, %s, 1, %s, 's', 'q', %s, %s, %s)"
    )
    for position, (verified, start, end) in enumerate(
        [(False, 1, 2), (True, None, None), (True, 5, 5)]
    ):
        with (
            connect(migrated_database) as connection,
            pytest.raises(psycopg.errors.CheckViolation),
        ):
            connection.execute(
                insert,
                (
                    new_id(),
                    fact_set_id,
                    key.case_id,
                    key.page_id,
                    position,
                    verified,
                    start,
                    end,
                ),
            )


# --- The real service ---------------------------------------------------------------------


def test_story_2_4_a_pages_facts_are_stored_and_listed_by_the_real_service(
    migrated_database: Settings,
) -> None:
    sidecar = IntakeSidecar()
    case_id = new_id()
    first_page = sidecar.pages.add(case_id, "Application form\nHeight\n165 cm")
    second_page = sidecar.pages.add(case_id)
    deployment = Deployment(
        answer(
            fact(),
            fact(
                "Fasting plasma glucose 142 mg/dL", "fasting plasma glucose 142 mg/dl"
            ),
            fact("Name: [Person]", "Patient name [Person]"),
            fact("Resting heart rate 61 bpm", "Resting heart rate 61 bpm"),
        ),
        answer(fact("Height 165 cm", "Height 165 cm")),
    )

    with service(migrated_database, sidecar, deployment) as client:
        second = client.post("/fact-sets", json=command_for(case_id, second_page))
        first = client.post("/fact-sets", json=command_for(case_id, first_page))
        listed = client.get(f"/cases/{case_id}/facts")
        ready = client.get("/ready")

    assert (second.status_code, first.status_code, ready.status_code) == (200, 200, 200)
    result = FactSetResult.model_validate(second.json())
    assert (result.status.value, len(result.fact_ids), result.unverified_count) == (
        "done",
        3,
        1,
    )
    assert result.audit.actor == f"extraction:{DEPLOYMENT}"
    facts_listed = FactList.model_validate(listed.json()).facts
    # Page order, then the order stored; the masked value is nowhere.
    assert [(item.page_number, item.statement) for item in facts_listed] == [
        (1, "Height 165 cm"),
        (2, STATEMENT),
        (2, "Fasting plasma glucose 142 mg/dL"),
        (2, "Resting heart rate 61 bpm"),
    ]
    texts = {1: "Application form\nHeight\n165 cm", 2: PAGE_TEXT}
    for item in facts_listed[:3]:
        assert item.quote_verified
        assert item.quote_start is not None and item.quote_end is not None
        quoted = texts[item.page_number][item.quote_start : item.quote_end]
        assert normalise(quoted) == normalise(item.quote)
    assert facts_listed[3].quote_verified is False
    assert (facts_listed[3].quote_start, facts_listed[3].quote_end) == (None, None)
    assert [item.fact_id for item in facts_listed[1:]] == result.fact_ids
    assert "[Person]" not in listed.text
    # One read of the text per page, and one model call per page.
    assert deployment.calls == 2


def test_story_2_4_a_repeat_is_answered_from_the_database_without_a_model_call(
    migrated_database: Settings,
) -> None:
    sidecar = IntakeSidecar()
    case_id = new_id()
    page_id = sidecar.pages.add(case_id)
    deployment = Deployment()

    with service(migrated_database, sidecar, deployment) as client:
        first = client.post("/fact-sets", json=command_for(case_id, page_id))
        reads = len(sidecar.requests)
    # Another process, as after a restart.
    with service(migrated_database, sidecar, deployment) as client:
        again = client.post("/fact-sets", json=command_for(case_id, page_id))

    assert again.json() == first.json()
    assert deployment.calls == 1
    assert len(sidecar.requests) == reads
    assert len(facts(migrated_database)) == 1


@pytest.mark.parametrize(
    ("contents", "error_code", "calls"),
    [
        (("not the object that was asked for",), "invalid_model_output", 1),
        ((429,), "model_unavailable", 2),
    ],
)
def test_story_2_4_a_failed_extraction_is_stored_as_failed_with_no_fact(
    contents: tuple[Any, ...],
    error_code: str,
    calls: int,
    migrated_database: Settings,
) -> None:
    sidecar = IntakeSidecar()
    case_id = new_id()
    page_id = sidecar.pages.add(case_id)
    deployment = Deployment(*contents)
    settings = migrated_database.model_copy(
        update={"model_max_retries": 1, "model_retry_seconds": 0.001}
    )

    with service(settings, sidecar, deployment) as client:
        response = client.post("/fact-sets", json=command_for(case_id, page_id))
        listed = client.get(f"/cases/{case_id}/facts")

    assert response.status_code == 200
    result = FactSetResult.model_validate(response.json())
    assert (result.status.value, result.error_code) == ("failed", error_code)
    assert deployment.calls == calls
    assert fact_sets(migrated_database) == [(case_id, page_id, "failed")]
    assert listed.json() == {"case_id": case_id, "facts": []}


def test_story_2_4_when_intake_is_down_for_the_page_no_row_is_left_and_the_repeat_works(
    migrated_database: Settings,
) -> None:
    sidecar = IntakeSidecar()
    case_id = new_id()
    page_id = sidecar.pages.add(case_id)
    deployment = Deployment()
    text_reads = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal text_reads
        if request.url.path.endswith("/text"):
            text_reads += 1
            if text_reads == 1:
                # As the sidecar answers when `intake` cannot be reached.
                return httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE"})
        return sidecar.handle(request)

    app = create_app(
        migrated_database,
        sidecar=httpx.MockTransport(flaky),
        model=httpx2.MockTransport(deployment.handle),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        first = client.post("/fact-sets", json=command_for(case_id, page_id))
        rows_after_failure = fact_sets(migrated_database)
        again = client.post("/fact-sets", json=command_for(case_id, page_id))

    assert first.status_code == 502
    assert ErrorBody.model_validate(first.json()).error.code is (
        ErrorCode.UPSTREAM_UNAVAILABLE
    )
    # The key row was given up, so the command sent again reads the page.
    assert rows_after_failure == []
    assert FactSetResult.model_validate(again.json()).status.value == "done"
    assert deployment.calls == 1
