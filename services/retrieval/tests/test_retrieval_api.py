"""Story 2.2: the HTTP face of `retrieval`: its probes, and how every error leaves it.

The search and the rule read (story 2.3) are tested in `test_retrieval_search.py`.
"""

import logging

import httpx2
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from retrieval_fakes import MemorySchemaRevision

from contracts.errors import ErrorBody, ErrorCode
from retrieval.adapters.http.app import create_app
from retrieval.adapters.http.middleware import SECURITY_HEADERS
from retrieval.adapters.http.routes import Dependencies, build_router
from retrieval.settings import Settings

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


def error_code(response: httpx2.Response) -> ErrorCode:
    return ErrorBody.model_validate(response.json()).error.code


def test_story_2_2_health_answers_while_the_process_runs(client: TestClient) -> None:
    for method in ("GET", "HEAD"):
        assert client.request(method, "/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_story_2_2_ready_only_when_the_schema_is_at_the_bundled_head(
    client: TestClient,
    schema_revision: MemorySchemaRevision,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert client.get("/ready").status_code == 200

    # azure.md rule 22: a schema that is behind, none at all, or no database.
    for revision in ("0000", None):
        schema_revision.revision = revision
        response = client.get("/ready")
        assert response.status_code == 502
        assert error_code(response) is ErrorCode.UPSTREAM_UNAVAILABLE
    schema_revision.fail = True
    with caplog.at_level(logging.WARNING):
        response = client.get("/ready")
    assert response.status_code == 502
    assert "not ready: database type=StoreDown" in caplog.text
    assert "secret-store-detail" not in caplog.text
    assert "secret-store-detail" not in response.text


def test_story_2_2_the_service_has_its_probes_and_the_two_reads_and_no_ingestion_route(
    dependencies: Dependencies,
) -> None:
    paths = {
        getattr(route, "path", None) for route in build_router(dependencies).routes
    }

    # The search and the rule read came with story 2.3; the ingestion is no route.
    assert paths == {"/health", "/ready", "/searches", "/rules/{rule_id}"}


def test_story_2_2_every_response_carries_the_security_headers(
    client: TestClient,
) -> None:
    for response in (client.get("/health"), client.get("/nowhere")):
        for name, value in SECURITY_HEADERS:
            assert response.headers[name] == value


def test_story_2_2_framework_errors_leave_in_the_error_shape(
    client: TestClient,
) -> None:
    missing = client.get("/chunks")
    wrong_method = client.delete("/health")

    assert (missing.status_code, error_code(missing)) == (404, ErrorCode.NOT_FOUND)
    assert (wrong_method.status_code, error_code(wrong_method)) == (
        405,
        ErrorCode.METHOD_NOT_ALLOWED,
    )
    assert ErrorBody.model_validate(missing.json()).error.trace_id == "0" * 32


def test_story_2_2_an_unhandled_error_is_a_plain_500_without_detail(
    settings: Settings,
    dependencies: Dependencies,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(settings, dependencies=dependencies)
    broken = APIRouter()

    @broken.get("/broken")
    async def fail() -> None:
        raise RuntimeError("SECRET detail of the failure")

    @broken.post("/takes-a-number")
    async def number(value: int) -> int:
        return value

    app.include_router(broken)

    with (
        TestClient(app, raise_server_exceptions=False) as client,
        caplog.at_level(logging.ERROR),
    ):
        response = client.get("/broken", headers={"traceparent": TRACEPARENT})
        invalid = client.post("/takes-a-number?value=SECRET-INPUT")

    assert response.status_code == 500
    body = ErrorBody.model_validate(response.json()).error
    assert (body.code, body.trace_id) == (ErrorCode.INTERNAL_ERROR, TRACE_ID)
    assert "SECRET" not in response.text
    # The error's type and where it was raised, never its message.
    assert "unhandled error: type=RuntimeError" in caplog.text
    assert "SECRET" not in caplog.text
    # A request the framework refuses does not have its input echoed.
    assert (invalid.status_code, error_code(invalid)) == (
        422,
        ErrorCode.VALIDATION_FAILED,
    )
    assert "SECRET" not in invalid.text
