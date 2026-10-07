"""Story 1.6: an upload is safe to retry (the item deferred from story 1.5).

The browser sends an `Idempotency-Key` with each upload and the same key when
it retries; a repeat is answered with the case the first call created.
"""

import asyncio
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import httpx2
import psycopg
import pytest
from alembic import command
from azure.storage.blob import ContainerClient
from fastapi.testclient import TestClient
from intake_fakes import MemoryCaseRepository, MemoryOriginalStore

from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.models.intake import CaseCreated
from contracts.upload import KEY_REUSED_MESSAGE
from intake.adapters.db import SqlCaseRepository, build_database
from intake.adapters.http.app import create_app
from intake.adapters.http.routes import Dependencies
from intake.adapters.migrations import alembic_config
from intake.domain.entities import new_case_with_document
from intake.domain.ports import DuplicateUpload
from intake.domain.upload import create_case
from intake.settings import Settings

CASES_DIR = Path(__file__).resolve().parents[3] / "data" / "cases"
PDF = {"Content-Type": "application/pdf"}
KEY = "3f2b8a52-6c1d-4c43-9d0e-0a8f5a1b2c3d"
OTHER_KEY = "7d0c2f1e-55aa-4b7e-8c11-2e9d6f3a4b5c"


def upload(
    client: TestClient, content: bytes, key: str | None = KEY
) -> httpx2.Response:
    headers = dict(PDF)
    if key is not None:
        headers["Idempotency-Key"] = key
    # The test client answers with its own HTTP library's response (httpx2).
    response: httpx2.Response = client.post("/cases", content=content, headers=headers)
    return response


def error_code(response: httpx2.Response) -> str:
    return ErrorBody.model_validate(response.json()).error.code.value


# --- With stand-ins for the stores ----------------------------------------------


def test_story_1_6_a_repeated_upload_with_the_same_key_returns_the_first_case(
    client: TestClient,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    case_pdf: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = upload(client, case_pdf)
    with caplog.at_level(logging.INFO):
        second = upload(client, case_pdf)

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json() == first.json()
    created = CaseCreated.model_validate(first.json())
    # One case, one document, one original.
    assert [case.case_id for case in repository.cases] == [created.case_id]
    assert [item.document_id for item in repository.documents] == [created.document_id]
    assert list(store.blobs) == [f"{created.case_id}/{created.document_id}.pdf"]
    assert store.deleted == []
    # The repeat is logged by its ids; the key itself is never logged.
    assert f"upload repeated: case_id={created.case_id}" in caplog.text
    assert KEY not in caplog.text


def test_story_1_6_uploads_without_a_key_or_with_different_keys_are_separate_cases(
    client: TestClient, repository: MemoryCaseRepository, case_pdf: bytes
) -> None:
    answers = [
        upload(client, case_pdf, None),
        upload(client, case_pdf, None),
        upload(client, case_pdf, KEY),
        upload(client, case_pdf, OTHER_KEY),
    ]

    assert {answer.status_code for answer in answers} == {201}
    assert len({answer.json()["case_id"] for answer in answers}) == 4
    assert [item.idempotency_key for item in repository.documents] == [
        None,
        None,
        KEY,
        OTHER_KEY,
    ]


def test_story_1_6_a_key_reused_for_a_different_file_is_422_and_stores_nothing(
    client: TestClient,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    case_pdf: bytes,
) -> None:
    upload(client, case_pdf)
    other_file = (CASES_DIR / "case-002.pdf").read_bytes()

    response = upload(client, other_file)

    assert response.status_code == 422
    detail = ErrorBody.model_validate(response.json()).error
    assert (detail.code.value, detail.message) == (
        "validation_failed",
        KEY_REUSED_MESSAGE,
    )
    assert len(repository.documents) == 1
    assert len(store.blobs) == 1


@pytest.mark.parametrize(
    "key", ["short", "x" * 65, "has spaces in it, and more", "key;drop-table-1234567"]
)
def test_story_1_6_a_malformed_key_is_422_before_anything_is_stored(
    client: TestClient,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    case_pdf: bytes,
    key: str,
) -> None:
    response = upload(client, case_pdf, key)

    assert response.status_code == 422
    assert error_code(response) == "validation_failed"
    assert key not in response.text
    assert (store.blobs, repository.documents) == ({}, [])


def test_story_1_6_an_upload_that_loses_the_race_for_its_key_yields_to_the_winner(
    client: TestClient,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    case_pdf: bytes,
    fixed_now: datetime,
) -> None:
    # Another call with the same key and file records its case while this one
    # is storing its original.
    winner_case, winner_document = new_case_with_document(case_pdf, fixed_now, KEY)
    repository.racing = (winner_case, winner_document)

    response = upload(client, case_pdf)

    assert response.status_code == 201
    assert response.json() == {
        "case_id": winner_case.case_id,
        "document_id": winner_document.document_id,
    }
    # One case; the loser's original has no row and was removed again.
    assert repository.documents == [winner_document]
    assert store.blobs == {}
    assert len(store.deleted) == 1


def test_story_1_6_losing_the_race_to_a_different_file_is_still_refused(
    client: TestClient,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    case_pdf: bytes,
    fixed_now: datetime,
) -> None:
    other_file = (CASES_DIR / "case-002.pdf").read_bytes()
    repository.racing = new_case_with_document(other_file, fixed_now, KEY)

    response = upload(client, case_pdf)

    assert response.status_code == 422
    assert store.blobs == {}
    assert len(repository.documents) == 1


def test_story_1_6_when_an_earlier_upload_cannot_be_looked_up_nothing_is_stored(
    client: TestClient,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    case_pdf: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.fail_find = True

    with caplog.at_level(logging.ERROR):
        response = upload(client, case_pdf)

    assert response.status_code == 502
    assert error_code(response) == "upstream_unavailable"
    assert (store.blobs, repository.documents) == ({}, [])
    assert "upload failed: stage=find_earlier type=StoreDown" in caplog.text
    assert "secret-store-detail" not in caplog.text
    # A file uploaded without a key needs no such lookup.
    assert upload(client, case_pdf, None).status_code == 201


def test_story_1_6_a_retry_after_a_failed_attempt_creates_the_case(
    client: TestClient,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    case_pdf: bytes,
) -> None:
    repository.fail = True
    assert upload(client, case_pdf).status_code == 502
    assert (store.blobs, repository.documents) == ({}, [])

    repository.fail = False
    response = upload(client, case_pdf)

    assert response.status_code == 201
    assert len(repository.documents) == 1
    assert len(store.blobs) == 1


def test_story_1_6_a_retry_after_an_answer_that_was_lost_finds_the_recorded_case(
    client: TestClient,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    case_pdf: bytes,
) -> None:
    # The timeout that story 1.5 left open: the rows are stored, the answer is not.
    repository.commit_then_fail = True
    first = upload(client, case_pdf)
    repository.commit_then_fail = False

    second = upload(client, case_pdf)

    assert second.status_code == 201
    assert len(repository.documents) == 1
    assert len(store.blobs) == 1
    if first.status_code == 201:
        assert second.json() == first.json()


def lost_race(repository: MemoryCaseRepository, case_pdf: bytes, now: datetime) -> None:
    """Another call with the same key records its case during this one's upload."""
    repository.racing = new_case_with_document(case_pdf, now, KEY)


@pytest.mark.parametrize(
    ("after_the_race", "stage"),
    [("fail", "stage=find_earlier type=StoreDown"), ("empty", None)],
    ids=["lookup-fails", "lookup-finds-nothing"],
)
def test_story_1_6_losing_the_race_and_then_not_finding_the_winner_is_502_and_cleans_up(
    client: TestClient,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    case_pdf: bytes,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
    after_the_race: str,
    stage: str | None,
) -> None:
    lost_race(repository, case_pdf, fixed_now)
    # The first lookup (before storing) finds nothing; the one after the race
    # fails, or comes back empty.
    repository.find_script = ["empty", after_the_race]

    with caplog.at_level(logging.ERROR):
        response = upload(client, case_pdf)

    assert response.status_code == 502
    assert error_code(response) == "upstream_unavailable"
    assert repository.finds == 2
    # This call's original has no row and is gone again; the winner's case stands.
    assert store.blobs == {}
    assert len(store.deleted) == 1
    assert len(repository.documents) == 1
    if stage is not None:
        assert stage in caplog.text
    assert "secret-store-detail" not in caplog.text


def test_story_1_6_the_lookup_and_the_store_share_one_deadline(
    settings: Settings,
    dependencies: Dependencies,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    case_pdf: bytes,
) -> None:
    # The lookup never answers: the deadline ends it, and nothing is stored.
    repository.find_script = ["hang"]
    quick = replace(dependencies, upload_deadline_seconds=0.2)
    started = time.monotonic()

    with TestClient(
        create_app(settings, dependencies=quick), raise_server_exceptions=False
    ) as quick_client:
        response = upload(quick_client, case_pdf)

    assert response.status_code == 502
    assert error_code(response) == "upstream_unavailable"
    assert time.monotonic() - started < 5
    assert (store.blobs, repository.documents) == ({}, [])


def test_story_1_6_a_lookup_that_hangs_after_the_race_ends_at_the_same_deadline(
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    case_pdf: bytes,
    fixed_now: datetime,
) -> None:
    lost_race(repository, case_pdf, fixed_now)
    repository.find_script = ["empty", "hang"]

    async def scenario() -> float:
        started = time.monotonic()
        with pytest.raises(DomainError) as raised:
            await create_case(
                case_pdf,
                store=store,
                repository=repository,
                deadline_seconds=0.3,
                idempotency_key=KEY,
            )
        assert raised.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
        return time.monotonic() - started

    # One deadline for the whole call, not a fresh one for each lookup.
    assert asyncio.run(scenario()) < 2
    assert store.blobs == {}
    assert len(repository.documents) == 1


def test_story_1_6_the_clean_up_after_a_lost_race_is_not_cancelled_with_the_request(
    repository: MemoryCaseRepository, case_pdf: bytes, fixed_now: datetime
) -> None:
    class SlowDelete(MemoryOriginalStore):
        """Its delete takes a moment, and says when it has begun."""

        deleting: asyncio.Event

        async def delete(self, blob_name: str) -> None:
            self.deleting.set()
            await asyncio.sleep(0.05)
            await super().delete(blob_name)

    slow = SlowDelete()
    lost_race(repository, case_pdf, fixed_now)

    async def scenario() -> None:
        slow.deleting = asyncio.Event()
        call = asyncio.ensure_future(
            create_case(
                case_pdf, store=slow, repository=repository, idempotency_key=KEY
            )
        )
        await slow.deleting.wait()
        # The caller goes away while the losing original is being removed.
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        await asyncio.sleep(0.2)

    asyncio.run(scenario())

    # The removal went through all the same: no original without a row.
    assert slow.blobs == {}
    assert len(slow.deleted) == 1


# --- Against a real PostgreSQL and the blob emulator ------------------------------


def documents(settings: Settings) -> list[tuple[object, ...]]:
    with psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
    ) as connection:
        return connection.execute(
            "SELECT case_id::text, document_id::text, sha256, idempotency_key "
            "FROM intake.document ORDER BY created_at"
        ).fetchall()


@pytest.mark.integration
def test_story_1_6_a_retried_upload_is_one_case_one_row_and_one_original(
    migrated_database: Settings, originals: ContainerClient, case_pdf: bytes
) -> None:
    with TestClient(
        create_app(migrated_database), raise_server_exceptions=False
    ) as client:
        first = upload(client, case_pdf)
        second = upload(client, case_pdf)
        different_file = upload(client, (CASES_DIR / "case-003.pdf").read_bytes())

    created = CaseCreated.model_validate(first.json())
    assert second.status_code == 201
    assert second.json() == first.json()
    assert different_file.status_code == 422
    assert documents(migrated_database) == [
        (
            created.case_id,
            created.document_id,
            hashlib.sha256(case_pdf).hexdigest(),
            KEY,
        )
    ]
    assert list(originals.list_blob_names()) == [
        f"{created.case_id}/{created.document_id}.pdf"
    ]


@pytest.mark.integration
def test_story_1_6_uploads_with_one_key_that_arrive_together_make_one_case(
    migrated_database: Settings, originals: ContainerClient, case_pdf: bytes
) -> None:
    with (
        TestClient(
            create_app(migrated_database), raise_server_exceptions=False
        ) as client,
        ThreadPoolExecutor(max_workers=5) as pool,
    ):
        answers = list(pool.map(lambda _: upload(client, case_pdf), range(5)))

    # The database's unique rule settles the race: every call names one case.
    assert {answer.status_code for answer in answers} == {201}
    assert len({answer.text for answer in answers}) == 1
    assert len(documents(migrated_database)) == 1
    assert len(list(originals.list_blob_names())) == 1


@pytest.mark.integration
def test_story_1_6_the_key_column_is_added_without_touching_existing_documents(
    empty_database: Settings, originals: ContainerClient, case_pdf: bytes
) -> None:
    config = alembic_config(empty_database)
    command.upgrade(config, "0001")
    with psycopg.connect(
        host=empty_database.database_host,
        port=empty_database.database_port,
        dbname=empty_database.database_name,
        user=empty_database.database_user,
        autocommit=True,
    ) as connection:
        # Two documents from before the change, as story 1.5 wrote them.
        for number in (1, 2):
            connection.execute(
                'INSERT INTO intake."case" (case_id, created_at) '
                "VALUES (gen_random_uuid(), now())"
            )
            connection.execute(
                "INSERT INTO intake.document (document_id, case_id, "
                "original_blob_name, size_bytes, sha256, created_at) "
                'SELECT gen_random_uuid(), case_id, %s, 1, %s, now() FROM intake."case" '
                "WHERE case_id NOT IN (SELECT case_id FROM intake.document)",
                (f"old/{number}.pdf", "0" * 64),
            )

    # Expand only (coding-style rule 29): the old rows stay, each without a key.
    command.upgrade(config, "head")

    assert [row[3] for row in documents(empty_database)] == [None, None]
    with TestClient(
        create_app(empty_database), raise_server_exceptions=False
    ) as client:
        assert client.get("/ready").status_code == 200
        assert upload(client, case_pdf).status_code == 201
        assert upload(client, case_pdf, None).status_code == 201
    assert [row[3] for row in documents(empty_database)] == [None, None, KEY, None]

    command.downgrade(config, "0001")
    with psycopg.connect(
        host=empty_database.database_host,
        port=empty_database.database_port,
        dbname=empty_database.database_name,
        user=empty_database.database_user,
    ) as connection:
        columns = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'intake' AND table_name = 'document'"
        ).fetchall()
    assert ("idempotency_key",) not in columns


@pytest.mark.integration
def test_story_1_6_the_repository_reports_a_second_document_with_one_key_as_a_duplicate(
    migrated_database: Settings, case_pdf: bytes, fixed_now: datetime
) -> None:
    # The real repository, called twice in a row: no threads, no timing.
    first = new_case_with_document(case_pdf, fixed_now, KEY)
    second = new_case_with_document(case_pdf, fixed_now, KEY)
    unkeyed = [new_case_with_document(case_pdf, fixed_now) for _ in range(2)]

    async def scenario() -> None:
        database = build_database(migrated_database)
        repository = SqlCaseRepository(database)
        try:
            await repository.add(*first)
            with pytest.raises(DuplicateUpload):
                await repository.add(*second)
            # Documents without a key never collide.
            for pair in unkeyed:
                await repository.add(*pair)
            found = await repository.find_by_idempotency_key(KEY)
            assert found is not None
            assert found.document_id == first[1].document_id
        finally:
            await database.dispose()

    asyncio.run(scenario())

    # The refused insert left neither of its rows: one case and one document
    # for the key, and the two without one.
    rows = documents(migrated_database)
    assert [row[3] for row in rows].count(KEY) == 1
    assert len(rows) == 3
    with psycopg.connect(
        host=migrated_database.database_host,
        port=migrated_database.database_port,
        dbname=migrated_database.database_name,
        user=migrated_database.database_user,
    ) as connection:
        cases = connection.execute('SELECT case_id::text FROM intake."case"').fetchall()
    assert len(cases) == 3
    assert (second[0].case_id,) not in cases
