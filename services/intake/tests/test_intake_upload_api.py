"""Story 1.5: `POST /cases` stores the original and records the case, or does neither."""

import logging
import re
from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.testclient import TestClient
from intake_fakes import (
    MemoryCaseRepository,
    MemoryOriginalStore,
)

from contracts.errors import ErrorBody
from contracts.ids import is_uuid7
from contracts.models.intake import CaseCreated
from contracts.upload import MAX_UPLOAD_BYTES
from intake.adapters.http.app import create_app
from intake.adapters.http.routes import Dependencies
from intake.settings import Settings

PDF = {"Content-Type": "application/pdf"}
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
# Text inside the synthetic PDF's first bytes and in the fakes' error message.
SECRET_FRAGMENTS = ("secret-store-detail", "10.0.0.1", "%PDF")


def error_of(response_json: object) -> tuple[str, str, str]:
    """Parse a body with the contracts model, so any other shape fails the test."""
    detail = ErrorBody.model_validate(response_json).error
    return detail.code.value, detail.message, detail.trace_id


def assert_nothing_stored(
    store: MemoryOriginalStore, repository: MemoryCaseRepository
) -> None:
    assert store.blobs == {}
    assert repository.cases == []
    assert repository.documents == []


def test_story_1_5_a_pdf_is_stored_and_the_case_recorded(
    client: TestClient,
    case_pdf: bytes,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    fixed_now: datetime,
) -> None:
    response = client.post("/cases", content=case_pdf, headers=PDF)

    assert response.status_code == 201
    created = CaseCreated.model_validate(response.json())
    assert is_uuid7(created.case_id)
    assert is_uuid7(created.document_id)
    assert created.case_id != created.document_id
    # The original, byte for byte, under the case's prefix.
    assert store.blobs == {f"{created.case_id}/{created.document_id}.pdf": case_pdf}
    (case,) = repository.cases
    (document,) = repository.documents
    assert (case.case_id, case.created_at) == (created.case_id, fixed_now)
    assert (document.document_id, document.case_id) == (
        created.document_id,
        created.case_id,
    )
    assert document.size_bytes == len(case_pdf)
    assert document.original_blob_name in store.blobs


def test_story_1_5_a_pdf_over_ten_megabytes_is_413_and_one_of_exactly_ten_is_accepted(
    client: TestClient, store: MemoryOriginalStore, repository: MemoryCaseRepository
) -> None:
    too_large = b"%PDF-1.7\n" + b"x" * MAX_UPLOAD_BYTES
    at_limit = b"%PDF-" + b"x" * (MAX_UPLOAD_BYTES - 5)

    response = client.post("/cases", content=too_large, headers=PDF)

    assert response.status_code == 413
    assert error_of(response.json())[0] == "file_too_large"
    assert_nothing_stored(store, repository)

    response = client.post("/cases", content=at_limit, headers=PDF)

    assert response.status_code == 201
    assert [len(blob) for blob in store.blobs.values()] == [MAX_UPLOAD_BYTES]


def chunks_over_the_limit() -> Iterator[bytes]:
    yield b"%PDF-1.7\n"
    for _ in range(11):
        yield b"x" * (1024 * 1024)


def test_story_1_5_a_body_with_no_declared_length_is_cut_off_at_the_limit(
    client: TestClient, store: MemoryOriginalStore, repository: MemoryCaseRepository
) -> None:
    # A generator body is sent chunked: there is no Content-Length to check.
    response = client.post("/cases", content=chunks_over_the_limit(), headers=PDF)

    assert response.status_code == 413
    assert error_of(response.json())[0] == "file_too_large"
    assert_nothing_stored(store, repository)


def test_story_1_5_content_that_is_no_pdf_is_415_and_nothing_is_stored(
    client: TestClient, store: MemoryOriginalStore, repository: MemoryCaseRepository
) -> None:
    for content in [
        b"plain text, renamed to report.pdf",
        b"PK\x03\x04 a zip",
        b"\x89PNG\r\n",
    ]:
        response = client.post("/cases", content=content, headers=PDF)

        assert response.status_code == 415
        assert error_of(response.json())[0] == "unsupported_file_type"
        assert_nothing_stored(store, repository)


def test_story_1_5_database_failure_removes_the_stored_original_and_logs_it_by_id(
    client: TestClient,
    case_pdf: bytes,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.fail = True

    with caplog.at_level(logging.INFO):
        response = client.post("/cases", content=case_pdf, headers=PDF)

    assert response.status_code == 502
    assert error_of(response.json())[0] == "upstream_unavailable"
    # The blob was written, then removed again: nothing is left behind.
    (blob_name,) = store.deleted
    assert_nothing_stored(store, repository)
    case_id, document_id = blob_name.removesuffix(".pdf").split("/")
    (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    line = record.getMessage()
    assert f"case_id={case_id} document_id={document_id}" in line
    assert "stage=record_case type=StoreDown original=removed" in line
    for fragment in SECRET_FRAGMENTS:
        assert fragment not in caplog.text


def test_story_1_5_logs_carry_ids_and_timings_never_content(
    client: TestClient, case_pdf: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        created = client.post(
            "/cases",
            content=case_pdf,
            headers={
                **PDF,
                "Content-Disposition": 'attachment; filename="jane-doe.pdf"',
            },
        ).json()

    (record,) = [r for r in caplog.records if r.name.startswith("intake.")]
    line = record.getMessage()
    assert re.fullmatch(
        rf"case created: case_id={created['case_id']} "
        rf"document_id={created['document_id']} "
        rf"size_bytes={len(case_pdf)} duration_ms=\d+",
        line,
    )
    assert "jane-doe" not in caplog.text
    assert "%PDF" not in caplog.text


# --- Probes -------------------------------------------------------------------


# --- The route table and the error shape --------------------------------------


def routes_of(app: FastAPI) -> set[tuple[str, str]]:
    """Every route the app really serves, with the methods it takes."""
    paths = get_openapi(title="intake", version="0", routes=app.routes)["paths"]
    return {
        (method.upper(), path) for path, methods in paths.items() for method in methods
    }


# GET and HEAD of a probe share one function, which only the schema builder minds.
@pytest.mark.filterwarnings("ignore:Duplicate Operation ID")
def test_story_1_5_no_route_returns_a_document_file(
    settings: Settings, dependencies: Dependencies
) -> None:
    app = create_app(settings, dependencies=dependencies)

    # The whole route table: two probes, the upload, and (story 1.7) the
    # redaction command with the reads of what it stores. The two file routes
    # answer with the redacted PDF, whole or one page of it (story 4.2);
    # nothing reads an original (AD-21).
    assert routes_of(app) == {
        ("GET", "/health"),
        ("HEAD", "/health"),
        ("GET", "/ready"),
        ("HEAD", "/ready"),
        ("POST", "/cases"),
        ("POST", "/cases/{case_id}/redaction"),
        ("GET", "/cases/{case_id}/pages"),
        ("GET", "/pages/{page_id}/text"),
        ("GET", "/pages/{page_id}/boxes"),
        ("GET", "/pages/{page_id}/thumbnail"),
        ("GET", "/documents/{document_id}/file"),
        ("GET", "/documents/{document_id}/pages/{page_number}/file"),
    }
