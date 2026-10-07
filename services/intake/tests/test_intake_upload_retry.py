"""Story 1.6: an upload is safe to retry (the item deferred from story 1.5).

The browser sends an `Idempotency-Key` with each upload and the same key when
it retries; a repeat is answered with the case the first call created.
"""

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx2
import psycopg
import pytest
from azure.storage.blob import ContainerClient
from fastapi.testclient import TestClient
from intake_fakes import MemoryCaseRepository, MemoryOriginalStore

from contracts.errors import ErrorBody
from contracts.models.intake import CaseCreated
from contracts.upload import KEY_REUSED_MESSAGE
from intake.adapters.http.app import create_app
from intake.domain.entities import new_case_with_document
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
