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
    StubModel,
)
from fastapi.testclient import TestClient

from classification.adapters.http.app import create_app
from classification.adapters.http.routes import Dependencies, build_router
from classification.domain.entities import ClassificationKey
from classification.settings import Settings
from contracts.enums import ClassifierContender
from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.classification import ClassificationResult
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
    "body",
    [
        {"case_id": "not-an-id", "page_id": "x", "contender": "llm"},
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
