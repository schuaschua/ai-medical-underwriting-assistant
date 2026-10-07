"""Story 1.8, against a real PostgreSQL.

Run `docker compose up --detach --wait` first. No test here calls Azure or a
model. The service runs as it really runs, with a transport where its Dapr
sidecar would be (behind it `intake` answers the page reads in the contracts'
shapes) and a transport where the chat deployment would be. The whole path,
with the real `intake` and the model stand-in, is tested beside the stand-ins
(`packages/` tests, story 1.8).
"""

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import httpx2
import psycopg
import pytest
from alembic import command
from classification_fakes import (
    DEPLOYMENT,
    PAGE_TEXT,
    REASON,
    IntakeSidecar,
    answer,
    completion,
)
from fastapi.testclient import TestClient

from classification.adapters.db import (
    SqlClassificationRepository,
    build_database,
)
from classification.adapters.http.app import create_app
from classification.adapters.migrations import (
    alembic_config,
    bundled_head,
)
from classification.domain.entities import ClassificationKey
from classification.settings import Settings
from contracts.enums import ClassifierContender
from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.classification import (
    Classification,
    ClassificationList,
    ClassificationResult,
)

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


def rows(settings: Settings) -> list[tuple[Any, ...]]:
    return query(
        settings,
        "SELECT case_id::text, page_id::text, contender, status, page_type, "
        "is_medical, confidence FROM classification.classification "
        "ORDER BY classification_id",
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
    return {"case_id": case_id, "page_id": page_id, "contender": "llm", **changes}


# --- Migrations and readiness -----------------------------------------------------------


def test_story_1_8_the_migration_keeps_everything_in_schema_classification(
    migrated_database: Settings,
) -> None:
    tables = query(
        migrated_database,
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
        "ORDER BY table_name",
    )

    # One schema, its own version table (AD-4), and no foreign key at all:
    # the case and the page are `intake`'s, held here as ids only.
    assert tables == [
        ("classification", "alembic_version"),
        ("classification", "classification"),
    ]
    assert query(
        migrated_database, "SELECT version_num FROM classification.alembic_version"
    ) == [(bundled_head(),)]
    assert query(
        migrated_database,
        "SELECT count(*) FROM information_schema.table_constraints "
        "WHERE table_schema = 'classification' AND constraint_type = 'FOREIGN KEY'",
    ) == [(0,)]


def test_story_1_8_readiness_fails_until_the_schema_is_at_the_bundled_head(
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
            "WHERE schema_name = 'classification'",
        ) == [(0,)]
        command.upgrade(alembic_config(empty_database), "head")
        after = client.get("/ready")
        # A revision this build does not know is not its head either.
        with connect(empty_database, autocommit=True) as connection:
            connection.execute(
                "UPDATE classification.alembic_version SET version_num = '9999'"
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
) -> Iterator[tuple[SqlClassificationRepository, asyncio.Runner]]:
    with asyncio.Runner() as runner:
        database = build_database(settings)
        try:
            yield SqlClassificationRepository(database), runner
        finally:
            runner.run(database.dispose())


def classification_of(classification_id: str, key: ClassificationKey) -> Classification:
    return Classification(
        classification_id=classification_id,
        case_id=key.case_id,
        page_id=key.page_id,
        contender=key.contender,
        page_type="invoice",  # type: ignore[arg-type]  # pydantic reads the enum's value
        is_medical=False,
        confidence=0.6,
        reason="A total due.",
    )


def test_story_1_8_begins_that_arrive_together_insert_one_row(
    migrated_database: Settings,
) -> None:
    key = ClassificationKey(new_id(), new_id(), ClassifierContender.LLM)

    def begin(_: int) -> bool:
        with a_repository(migrated_database) as (repository, runner):
            return runner.run(repository.begin(new_id(), key, NOW)) is None

    with ThreadPoolExecutor(max_workers=6) as pool:
        inserted = list(pool.map(begin, range(6)))

    # The unique key settles the race: one inserted it, five found it there.
    assert sorted(inserted) == [False] * 5 + [True]
    assert len(rows(migrated_database)) == 1


def test_story_1_8_a_result_is_stored_once_and_the_first_one_stands(
    migrated_database: Settings,
) -> None:
    key = ClassificationKey(new_id(), new_id(), ClassifierContender.LLM)
    classification_id = new_id()
    classification = classification_of(classification_id, key)
    with a_repository(migrated_database) as (repository, runner):
        runner.run(repository.begin(classification_id, key, NOW))

        first = runner.run(repository.finish(classification_id, "{}", classification))
        # A second end for the same row, as from a call that was superseded.
        second = runner.run(repository.finish(classification_id, '{"late": 1}', None))
        found = runner.run(repository.find(key))

    assert (first, second) == ("{}", "{}")
    assert found is not None
    assert (found.result_json, found.running) == ("{}", False)
    assert rows(migrated_database) == [
        (key.case_id, key.page_id, "llm", "done", "invoice", False, 0.6)
    ]
    assert query(
        migrated_database,
        "SELECT finished_at IS NOT NULL FROM classification.classification",
    ) == [(True,)]


# --- The service, on its database ---------------------------------------------------------------


def test_story_1_8_a_classified_page_is_stored_and_listed_by_the_real_service(
    migrated_database: Settings,
) -> None:
    sidecar, deployment = IntakeSidecar(), Deployment()
    case_id, eval_run_id = new_id(), new_id()
    first, second = sidecar.pages.add(case_id), sidecar.pages.add(case_id)

    with service(migrated_database, sidecar, deployment) as client:
        responses = [
            client.post(
                "/classifications",
                json=command_for(case_id, page_id, eval_run_id=eval_run_id),
            )
            for page_id in (first, second)
        ]
        listed = client.get(f"/cases/{case_id}/classifications")
        empty = client.get(f"/cases/{new_id()}/classifications")

    results = [
        ClassificationResult.model_validate(response.json()) for response in responses
    ]
    assert [response.status_code for response in responses] == [200, 200]
    for result, page_id in zip(results, (first, second), strict=True):
        assert result.classification is not None
        assert result.classification.model_dump(mode="json") == {
            "classification_id": result.classification_id,
            "case_id": case_id,
            "page_id": page_id,
            "contender": "llm",
            "page_type": "lab_report",
            "is_medical": True,
            "confidence": 1.0,
            "reason": REASON,
        }
        assert (result.audit.action.value, result.audit.actor) == (
            "page.classified",
            f"classification:{DEPLOYMENT}",
        )
        assert result.audit.eval_run_id == eval_run_id
    # Five runs of the model for each page.
    assert deployment.calls == 10
    assert rows(migrated_database) == [
        (case_id, first, "llm", "done", "lab_report", True, 1.0),
        (case_id, second, "llm", "done", "lab_report", True, 1.0),
    ]
    # The read lists what was stored, oldest first; another case has none.
    assert ClassificationList.model_validate(listed.json()).classifications == [
        result.classification for result in results
    ]
    assert empty.json()["classifications"] == []
    # The page text is in `intake`, never in this service's table.
    stored = query(
        migrated_database, "SELECT result FROM classification.classification"
    )
    assert all(PAGE_TEXT not in row[0] for row in stored)


def test_story_1_8_a_repeat_is_answered_from_the_database_without_a_model_call(
    migrated_database: Settings,
) -> None:
    sidecar, deployment = IntakeSidecar(), Deployment()
    case_id = new_id()
    page_id = sidecar.pages.add(case_id)

    with service(migrated_database, sidecar, deployment) as client:
        first = client.post("/classifications", json=command_for(case_id, page_id))
        calls, reads = deployment.calls, len(sidecar.requests)
    # Another instance of the service, as after a restart.
    with service(migrated_database, sidecar, deployment) as client:
        again = client.post("/classifications", json=command_for(case_id, page_id))

    assert again.status_code == 200
    assert again.json() == first.json()
    assert (deployment.calls, len(sidecar.requests)) == (calls, reads)
    assert len(rows(migrated_database)) == 1


def test_story_1_8_runs_that_differ_are_stored_with_their_agreement_rate(
    migrated_database: Settings,
) -> None:
    sidecar = IntakeSidecar()
    deployment = Deployment(
        answer("invoice", "A total due."),
        answer("other", "No heading."),
        answer("invoice", "An invoice number."),
        answer("invoice", "Amounts to pay."),
        answer("other", "A payslip."),
    )
    case_id = new_id()
    page_id = sidecar.pages.add(case_id)

    with service(migrated_database, sidecar, deployment) as client:
        body = client.post(
            "/classifications", json=command_for(case_id, page_id)
        ).json()

    assert body["classification"]["page_type"] == "invoice"
    assert body["classification"]["confidence"] == 0.6
    assert body["classification"]["is_medical"] is False
    assert rows(migrated_database) == [
        (case_id, page_id, "llm", "done", "invoice", False, 0.6)
    ]


@pytest.mark.parametrize(
    ("contents", "error_code", "calls"),
    [
        # One bad answer among the five.
        ((answer(), "Not JSON at all.", answer()), "invalid_model_output", 5),
        # 429 on the call and on each of its three retries, for every run.
        ((429,), "model_unavailable", 20),
    ],
    ids=["invalid-output", "model-unavailable"],
)
def test_story_1_8_a_failed_classification_is_stored_as_failed_and_lists_nothing(
    migrated_database: Settings,
    contents: tuple[Any, ...],
    error_code: str,
    calls: int,
) -> None:
    sidecar, deployment = IntakeSidecar(), Deployment(*contents)
    case_id = new_id()
    page_id = sidecar.pages.add(case_id)
    settings = migrated_database.model_copy(update={"model_retry_seconds": 0.01})

    with service(settings, sidecar, deployment) as client:
        response = client.post("/classifications", json=command_for(case_id, page_id))
        listed = client.get(f"/cases/{case_id}/classifications")
        again = client.post("/classifications", json=command_for(case_id, page_id))

    result = ClassificationResult.model_validate(response.json())
    assert (response.status_code, result.status.value) == (200, "failed")
    assert result.error_code is not None
    assert result.error_code.value == error_code
    assert result.classification is None
    assert (result.audit.action.value, result.audit.page_id) == (
        "stage.failed",
        page_id,
    )
    assert deployment.calls == calls
    assert listed.json()["classifications"] == []
    assert rows(migrated_database) == [
        (case_id, page_id, "llm", "failed", None, None, None)
    ]
    # The stored failure is the answer from then on: the model is not asked again.
    assert again.json() == response.json()
    assert deployment.calls == calls


def test_story_1_8_when_intake_is_down_for_the_page_no_row_is_left_and_the_repeat_works(
    migrated_database: Settings,
) -> None:
    sidecar, deployment = IntakeSidecar(), Deployment()
    case_id = new_id()
    page_id = sidecar.pages.add(case_id)
    answer_reads = sidecar.handle
    down = {"left": 1}

    def flaky(request: httpx.Request) -> httpx.Response:
        # The page list is answered; the first read of the page's text is not.
        if request.url.path.endswith("/text") and down["left"] > 0:
            down["left"] -= 1
            return httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE"})
        return answer_reads(request)

    app = create_app(
        migrated_database,
        sidecar=httpx.MockTransport(flaky),
        model=httpx2.MockTransport(deployment.handle),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        first = client.post("/classifications", json=command_for(case_id, page_id))
        rows_between = rows(migrated_database)
        again = client.post("/classifications", json=command_for(case_id, page_id))

    # 502, which `workflow` retries; the key row was released, and the
    # command sent again classified the page.
    assert first.status_code == 502
    assert ErrorBody.model_validate(first.json()).error.code is (
        ErrorCode.UPSTREAM_UNAVAILABLE
    )
    assert rows_between == []
    assert (again.status_code, again.json()["status"]) == (200, "done")
    assert rows(migrated_database) == [
        (case_id, page_id, "llm", "done", "lab_report", True, 1.0)
    ]


def test_story_1_8_the_stage_ends_at_its_deadline_with_a_stored_timeout(
    migrated_database: Settings,
) -> None:
    sidecar = IntakeSidecar()
    case_id = new_id()
    page_id = sidecar.pages.add(case_id)

    async def never(request: httpx2.Request) -> httpx2.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    settings = migrated_database.model_copy(update={"classify_deadline_seconds": 0.3})
    app = create_app(
        settings, sidecar=sidecar.transport(), model=httpx2.MockTransport(never)
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/classifications", json=command_for(case_id, page_id))

    assert response.status_code == 200
    assert response.json()["error_code"] == "stage_timeout"
    assert rows(migrated_database) == [
        (case_id, page_id, "llm", "failed", None, None, None)
    ]
