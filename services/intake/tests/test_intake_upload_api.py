"""Story 1.5: `POST /cases` stores the original and records the case, or does neither."""

import asyncio
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
    MemorySchemaRevision,
)

from contracts.errors import NO_TRACE_ID, ErrorBody
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


def test_story_1_5_two_uploads_of_one_file_are_two_cases(
    client: TestClient, case_pdf: bytes, store: MemoryOriginalStore
) -> None:
    first = client.post("/cases", content=case_pdf, headers=PDF).json()
    second = client.post("/cases", content=case_pdf, headers=PDF).json()

    assert first["case_id"] != second["case_id"]
    assert len(store.blobs) == 2


def test_story_1_5_a_pdf_over_ten_megabytes_is_413_and_nothing_is_stored(
    client: TestClient, store: MemoryOriginalStore, repository: MemoryCaseRepository
) -> None:
    too_large = b"%PDF-1.7\n" + b"x" * MAX_UPLOAD_BYTES

    response = client.post("/cases", content=too_large, headers=PDF)

    assert response.status_code == 413
    assert error_of(response.json())[0] == "file_too_large"
    assert_nothing_stored(store, repository)


def test_story_1_5_a_pdf_of_exactly_ten_megabytes_is_accepted(
    client: TestClient, store: MemoryOriginalStore
) -> None:
    at_limit = b"%PDF-" + b"x" * (MAX_UPLOAD_BYTES - 5)

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


@pytest.mark.parametrize(
    "content",
    [b"plain text, renamed to report.pdf", b"PK\x03\x04 a zip", b"\x89PNG\r\n"],
)
def test_story_1_5_content_that_is_no_pdf_is_415_and_nothing_is_stored(
    client: TestClient,
    content: bytes,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
) -> None:
    # The declared type says PDF; the content decides.
    response = client.post("/cases", content=content, headers=PDF)

    assert response.status_code == 415
    assert error_of(response.json())[0] == "unsupported_file_type"
    assert_nothing_stored(store, repository)


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/json", "multipart/form-data", None]
)
def test_story_1_5_a_body_not_declared_as_pdf_is_415(
    client: TestClient,
    case_pdf: bytes,
    content_type: str | None,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
) -> None:
    headers = {} if content_type is None else {"Content-Type": content_type}

    response = client.post("/cases", content=case_pdf, headers=headers)

    assert response.status_code == 415
    assert error_of(response.json())[0] == "unsupported_media_type"
    assert_nothing_stored(store, repository)


def test_story_1_5_a_declared_type_with_parameters_is_still_a_pdf(
    client: TestClient, case_pdf: bytes
) -> None:
    response = client.post(
        "/cases",
        content=case_pdf,
        headers={"Content-Type": "Application/PDF; name=x"},
    )

    assert response.status_code == 201


def test_story_1_5_an_empty_body_is_422_and_nothing_is_stored(
    client: TestClient, store: MemoryOriginalStore, repository: MemoryCaseRepository
) -> None:
    response = client.post(
        "/cases", content=b"", headers={**PDF, "traceparent": TRACEPARENT}
    )

    assert response.status_code == 422
    code, _, trace_id = error_of(response.json())
    assert (code, trace_id) == ("validation_failed", TRACE_ID)
    assert_nothing_stored(store, repository)


def test_story_1_5_storage_failure_is_a_5xx_and_leaves_no_rows(
    client: TestClient,
    case_pdf: bytes,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store.fail_put = True

    with caplog.at_level(logging.INFO):
        response = client.post("/cases", content=case_pdf, headers=PDF)

    assert response.status_code == 502
    assert error_of(response.json())[0] == "upstream_unavailable"
    assert_nothing_stored(store, repository)
    assert "stage=store_original type=StoreDown" in caplog.text
    for fragment in SECRET_FRAGMENTS:
        assert fragment not in response.text
        assert fragment not in caplog.text


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


def test_story_1_5_an_original_that_cannot_be_removed_is_left_unreferenced_and_logged(
    client: TestClient,
    case_pdf: bytes,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository.fail = True
    store.fail_delete = True

    response = client.post("/cases", content=case_pdf, headers=PDF)

    assert response.status_code == 502
    # The blob stays, but no row points to it.
    (blob_name,) = store.blobs
    assert repository.documents == []
    case_id = blob_name.split("/")[0]
    assert f"case_id={case_id}" in caplog.text
    assert "original=left_unreferenced" in caplog.text


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


def test_story_1_5_health_answers_without_touching_the_database(
    client: TestClient, schema_revision: MemorySchemaRevision
) -> None:
    schema_revision.fail = True

    assert client.get("/health").json() == {"status": "ok"}
    assert client.head("/health").status_code == 200


def test_story_1_5_ready_when_the_schema_is_at_the_bundled_head(
    client: TestClient,
) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("revision", [None, "0000", "9999"])
def test_story_1_5_not_ready_when_the_schema_revision_is_not_the_head(
    client: TestClient,
    schema_revision: MemorySchemaRevision,
    revision: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    schema_revision.revision = revision

    response = client.get("/ready")

    assert response.status_code == 502
    assert error_of(response.json())[0] == "upstream_unavailable"
    assert f"schema_revision={revision} head_revision=0001" in caplog.text


def test_story_1_5_not_ready_when_the_database_cannot_be_reached(
    client: TestClient,
    schema_revision: MemorySchemaRevision,
    caplog: pytest.LogCaptureFixture,
) -> None:
    schema_revision.fail = True

    response = client.get("/ready")

    assert response.status_code == 502
    assert "not ready: database type=StoreDown" in caplog.text
    assert "secret-store-detail" not in caplog.text
    assert "secret-store-detail" not in response.text


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

    # The whole route table: two probes and the upload. Nothing reads a file.
    assert routes_of(app) == {
        ("GET", "/health"),
        ("HEAD", "/health"),
        ("GET", "/ready"),
        ("HEAD", "/ready"),
        ("POST", "/cases"),
    }


def test_story_1_5_asking_for_the_original_is_404(
    client: TestClient, case_pdf: bytes
) -> None:
    created = client.post("/cases", content=case_pdf, headers=PDF).json()
    case_id, document_id = created["case_id"], created["document_id"]

    for path in (
        f"/cases/{case_id}",
        f"/cases/{case_id}/original",
        f"/documents/{document_id}",
        f"/documents/{document_id}/file",
        f"/documents/{document_id}/original",
        f"/originals/{case_id}/{document_id}.pdf",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert error_of(response.json())[0] == "not_found"
        assert b"%PDF" not in response.content


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE"])
def test_story_1_5_wrong_method_on_cases_is_405_in_the_error_shape(
    client: TestClient, method: str
) -> None:
    response = client.request(method, "/cases")

    assert response.status_code == 405
    code, _, trace_id = error_of(response.json())
    assert code == "method_not_allowed"
    assert trace_id == NO_TRACE_ID


def test_story_1_5_every_response_carries_the_security_headers(
    client: TestClient,
) -> None:
    for response in (client.get("/health"), client.get("/nope")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert "strict-transport-security" in response.headers
        assert response.headers["referrer-policy"] == "same-origin"


def test_story_1_5_no_interactive_api_pages_are_served(client: TestClient) -> None:
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404


def test_story_1_5_a_declared_length_over_the_limit_is_refused_before_the_body_is_read(
    client: TestClient, store: MemoryOriginalStore
) -> None:
    # Declared sizes are checked before the body is read.
    response = client.post(
        "/cases",
        content=b"",
        headers={**PDF, "Content-Length": str(MAX_UPLOAD_BYTES + 1)},
    )

    assert response.status_code == 413
    assert store.blobs == {}


@pytest.mark.parametrize(
    "value", ["-1", "+5", "1.5", "ten", "1e3", "9" * 5000, "\u00b2"]
)
def test_story_1_5_a_malformed_content_length_is_422_not_500(
    settings: Settings,
    dependencies: Dependencies,
    store: MemoryOriginalStore,
    value: str,
) -> None:
    app = create_app(settings, dependencies=dependencies)
    answer: dict[str, int] = {}

    async def call() -> None:
        # Straight to the app: an HTTP client would refuse to send such a header.
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/cases",
            "raw_path": b"/cases",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"content-type", b"application/pdf"),
                (b"content-length", value.encode("latin-1")),
            ],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8001),
        }

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b"%PDF-1.7", "more_body": False}

        async def send(message: dict[str, object]) -> None:
            if message["type"] == "http.response.start":
                answer["status"] = int(str(message["status"]))

        await app(scope, receive, send)  # type: ignore[arg-type]  # plain dicts stand in for the ASGI types

    asyncio.run(call())

    assert answer["status"] == 422
    assert store.blobs == {}


@pytest.mark.parametrize("declared", [1, 7, 9, 5000])
def test_story_1_5_a_body_that_differs_from_its_declared_length_is_422(
    client: TestClient,
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    declared: int,
) -> None:
    response = client.post(
        "/cases", content=b"%PDF-1.7", headers={**PDF, "Content-Length": str(declared)}
    )

    assert response.status_code == 422
    assert error_of(response.json())[0] == "validation_failed"
    assert_nothing_stored(store, repository)
