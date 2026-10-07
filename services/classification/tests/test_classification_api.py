"""Story 1.8: the routes of `classification`, over HTTP, with in-memory stand-ins.

Unit tests: the classify command, the read, the probes and the error shape.
No database, no model, no network.
"""

import asyncio
import logging

import pytest
from classification_fakes import (
    ACTOR,
    PAGE_TEXT,
    REASON,
    TRACE_ID,
    TRACEPARENT,
    FakePages,
    MemoryRepository,
    MemorySchemaRevision,
    StubModel,
    answer,
    unavailable,
)
from fastapi.testclient import TestClient

from classification.adapters.http.app import create_app
from classification.adapters.http.middleware import SECURITY_HEADERS
from classification.adapters.http.routes import Dependencies, build_router
from classification.domain.entities import ClassificationKey
from classification.settings import Settings
from contracts.enums import ClassifierContender
from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.classification import ClassificationList, ClassificationResult
from contracts.operations import get_operation


def command(case_id: str, page_id: str, **changes: object) -> dict[str, object]:
    return {"case_id": case_id, "page_id": page_id, "contender": "llm", **changes}


def error_code(response: object) -> ErrorCode:
    return ErrorBody.model_validate(response.json()).error.code  # type: ignore[attr-defined]  # an httpx response


# --- POST /classifications ---------------------------------------------------------------


def test_story_1_8_post_classifications_answers_the_stored_result_in_the_contracts_shape(
    client: TestClient, pages: FakePages, model: StubModel, case_id: str
) -> None:
    page_id = pages.add(case_id)
    operation = get_operation("classify_page")
    assert (operation.method.value, operation.path) == ("POST", "/classifications")

    response = client.post(
        operation.path,
        json=command(case_id, page_id),
        headers={"traceparent": TRACEPARENT},
    )

    assert response.status_code == 200
    result = ClassificationResult.model_validate(response.json())
    assert response.json()["classification"] == {
        "classification_id": result.classification_id,
        "case_id": case_id,
        "page_id": page_id,
        "contender": "llm",
        "page_type": "lab_report",
        "is_medical": True,
        "confidence": 1.0,
        "reason": REASON,
    }
    # The audit record `workflow` will write: the actor names the service and
    # the model deployment, in the trace of the request.
    assert response.json()["audit"] == {
        "actor_kind": "ai",
        "actor": ACTOR,
        "action": "page.classified",
        "occurred_at": "2026-10-07T12:00:00Z",
        "case_id": case_id,
        "page_id": page_id,
        "ref": result.classification_id,
        "detail": None,
        "trace_id": TRACE_ID,
        "eval_run_id": None,
        "error_code": None,
    }
    # AD-7: a classification never carries a route or a decision.
    assert set(response.json()) == {
        "case_id",
        "status",
        "error_code",
        "audit",
        "classification_id",
        "page_id",
        "contender",
        "classification",
    }
    # The trace context went on to `intake` with the reads.
    assert pages.trace_contexts == [{"traceparent": TRACEPARENT}] * 2
    assert model.calls == 5


def test_story_1_8_the_same_command_again_is_answered_from_the_store(
    client: TestClient, pages: FakePages, model: StubModel, case_id: str
) -> None:
    page_id = pages.add(case_id)
    first = client.post("/classifications", json=command(case_id, page_id))

    again = client.post("/classifications", json=command(case_id, page_id))

    assert again.status_code == 200
    assert again.json() == first.json()
    assert model.calls == 5


def test_story_1_8_a_command_while_the_first_is_running_is_409_in_progress(
    client: TestClient,
    pages: FakePages,
    repository: MemoryRepository,
    model: StubModel,
    case_id: str,
    fixed_now: object,
) -> None:
    page_id = pages.add(case_id)
    # The first command's key row, still running.
    key = ClassificationKey(case_id, page_id, ClassifierContender.LLM)
    asyncio.run(repository.begin(new_id(), key, fixed_now))  # type: ignore[arg-type]  # the fixture's datetime

    response = client.post("/classifications", json=command(case_id, page_id))

    assert response.status_code == 409
    assert error_code(response) is ErrorCode.IN_PROGRESS
    assert model.calls == 0


@pytest.mark.parametrize(
    ("answers", "code"),
    [
        (["not json"], "invalid_model_output"),
        ([unavailable()], "model_unavailable"),
    ],
)
def test_story_1_8_a_failed_classification_is_a_stored_result_not_an_error_status(
    client: TestClient,
    pages: FakePages,
    model: StubModel,
    case_id: str,
    answers: list[str | Exception],
    code: str,
) -> None:
    page_id = pages.add(case_id)
    model.answers = answers

    response = client.post("/classifications", json=command(case_id, page_id))

    # 200: `workflow` records the failed result, which fails the page and the
    # case. An error status would only be retried.
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["error_code"], body["classification"]) == (
        "failed",
        code,
        None,
    )
    assert (body["audit"]["action"], body["audit"]["page_id"]) == (
        "stage.failed",
        page_id,
    )
    listed = client.get(f"/cases/{case_id}/classifications").json()
    assert listed == {"case_id": case_id, "classifications": []}


def test_story_1_8_an_unknown_page_or_a_page_of_another_case_is_404_not_found(
    client: TestClient, pages: FakePages, repository: MemoryRepository, case_id: str
) -> None:
    pages.add(case_id)
    other_page = pages.add(new_id())

    for page_id in (new_id(), other_page):
        response = client.post("/classifications", json=command(case_id, page_id))
        assert response.status_code == 404
        assert error_code(response) is ErrorCode.NOT_FOUND
    assert repository.rows == {}


def test_story_1_8_the_contender_that_is_not_built_is_422_validation_failed(
    client: TestClient, pages: FakePages, model: StubModel, case_id: str
) -> None:
    page_id = pages.add(case_id)

    response = client.post(
        "/classifications", json=command(case_id, page_id, contender="doc-intelligence")
    )

    assert response.status_code == 422
    assert error_code(response) is ErrorCode.VALIDATION_FAILED
    assert model.calls == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"case_id": "not-an-id", "page_id": "x", "contender": "llm"},
        {"case_id": new_id(), "page_id": new_id(), "contender": "guess"},
        {"case_id": new_id(), "page_id": new_id()},
        # A command carries ids only (AD-6): content is refused.
        {
            "case_id": new_id(),
            "page_id": new_id(),
            "contender": "llm",
            "text": PAGE_TEXT,
        },
    ],
)
def test_story_1_8_a_malformed_command_is_422_without_echoing_the_input(
    client: TestClient, model: StubModel, body: dict[str, object]
) -> None:
    response = client.post("/classifications", json=body)

    assert response.status_code == 422
    assert error_code(response) is ErrorCode.VALIDATION_FAILED
    assert "SECRET" not in response.text
    assert "not-an-id" not in response.text
    assert model.calls == 0


# --- GET /cases/{case_id}/classifications ----------------------------------------------


def test_story_1_8_get_classifications_lists_the_cases_stored_classifications(
    client: TestClient, pages: FakePages, model: StubModel, case_id: str
) -> None:
    operation = get_operation("list_classifications")
    path = operation.path.format(case_id=case_id)
    first, second = pages.add(case_id), pages.add(case_id)
    model.answers = [answer("invoice", "a total due")]
    stored = [
        client.post("/classifications", json=command(case_id, page_id)).json()
        for page_id in (first, second)
    ]

    response = client.get(path)

    assert response.status_code == 200
    listed = ClassificationList.model_validate(response.json())
    assert listed.case_id == case_id
    assert response.json()["classifications"] == [
        item["classification"] for item in stored
    ]
    assert [item.page_id for item in listed.classifications] == [first, second]
    assert {item.is_medical for item in listed.classifications} == {False}


def test_story_1_8_a_case_with_no_classification_lists_none(client: TestClient) -> None:
    case_id = new_id()

    response = client.get(f"/cases/{case_id}/classifications")

    assert response.status_code == 200
    assert response.json() == {"case_id": case_id, "classifications": []}


def test_story_1_8_a_case_id_that_is_not_an_id_is_refused(client: TestClient) -> None:
    response = client.get("/cases/not-an-id/classifications")

    assert response.status_code == 422
    assert error_code(response) is ErrorCode.VALIDATION_FAILED


def test_story_1_8_the_service_has_no_route_that_routes_or_decides(
    dependencies: Dependencies,
) -> None:
    paths = {
        getattr(route, "path", None) for route in build_router(dependencies).routes
    }

    # The two operations of the contracts and the two probes: nothing else.
    assert paths == {
        "/health",
        "/ready",
        "/classifications",
        "/cases/{case_id}/classifications",
    }


# --- Probes, headers and the error shape -------------------------------------------------


def test_story_1_8_health_answers_while_the_process_runs(client: TestClient) -> None:
    for method in ("GET", "HEAD"):
        assert client.request(method, "/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_story_1_8_ready_only_when_the_schema_is_at_the_bundled_head(
    client: TestClient,
    schema_revision: MemorySchemaRevision,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert client.get("/ready").status_code == 200

    # azure.md rule 22: an older schema, none at all, or no database.
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


def test_story_1_8_every_response_carries_the_security_headers(
    client: TestClient,
) -> None:
    for response in (
        client.get("/health"),
        client.get("/nowhere"),
        client.post("/classifications", json={}),
    ):
        for name, value in SECURITY_HEADERS:
            assert response.headers[name] == value


def test_story_1_8_framework_errors_leave_in_the_error_shape(
    client: TestClient,
) -> None:
    missing = client.get("/nowhere")
    wrong_method = client.delete("/classifications")

    assert (missing.status_code, error_code(missing)) == (404, ErrorCode.NOT_FOUND)
    assert (wrong_method.status_code, error_code(wrong_method)) == (
        405,
        ErrorCode.METHOD_NOT_ALLOWED,
    )
    assert ErrorBody.model_validate(missing.json()).error.trace_id == "0" * 32


def test_story_1_8_an_unhandled_error_is_a_plain_500_without_detail(
    settings: Settings,
    dependencies: Dependencies,
    pages: FakePages,
    repository: MemoryRepository,
    case_id: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_id = pages.add(case_id)

    async def broken(key: object) -> None:
        raise RuntimeError("SECRET detail of the failure")

    monkeypatch.setattr(repository, "find", broken)
    app = create_app(settings, dependencies=dependencies)

    with (
        TestClient(app, raise_server_exceptions=False) as client,
        caplog.at_level(logging.ERROR),
    ):
        response = client.post(
            "/classifications",
            json=command(case_id, page_id),
            headers={"traceparent": TRACEPARENT},
        )

    assert response.status_code == 500
    body = ErrorBody.model_validate(response.json()).error
    assert (body.code, body.trace_id) == (ErrorCode.INTERNAL_ERROR, TRACE_ID)
    assert "SECRET" not in response.text
    # The error's type and where it was raised, never its message.
    assert "unhandled error: type=RuntimeError" in caplog.text
    assert "SECRET" not in caplog.text
