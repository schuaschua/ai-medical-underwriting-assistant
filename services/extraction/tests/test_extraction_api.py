"""Story 2.4: the routes of `extraction`, over HTTP, with in-memory stand-ins.

Unit tests: the extract command, the read, the probes and the error shape.
No database, no model, no network.
"""

import logging

import httpx2
import pytest
from extraction_fakes import (
    ACTOR,
    TRACE_ID,
    TRACEPARENT,
    FakePages,
    MemoryRepository,
    StubModel,
    answer,
    fact,
)
from fastapi.testclient import TestClient

from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.extraction import FactSetResult
from contracts.operations import get_operation
from extraction.adapters.http.app import create_app
from extraction.adapters.http.routes import Dependencies, build_router
from extraction.domain.entities import FactSetKey
from extraction.settings import Settings


def command(case_id: str, page_id: str, **changes: object) -> dict[str, object]:
    return {"case_id": case_id, "page_id": page_id, **changes}


def error_code(response: httpx2.Response) -> ErrorCode:
    return ErrorBody.model_validate(response.json()).error.code


# --- POST /fact-sets ---------------------------------------------------------------------


def test_story_2_4_post_fact_sets_answers_the_stored_result_in_the_contracts_shape(
    client: TestClient,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    page_id = pages.add(case_id)
    model.answers = [answer(fact(), fact("Resting heart rate 61 bpm", "61 bpm"))]

    response = client.post(
        "/fact-sets",
        json=command(case_id, page_id),
        headers={"traceparent": TRACEPARENT, "tracestate": "k=v"},
    )

    assert response.status_code == 200
    result = FactSetResult.model_validate(response.json())
    assert (result.status.value, result.error_code) == ("done", None)
    assert (result.case_id, result.page_id) == (case_id, page_id)
    assert (len(result.fact_ids), result.unverified_count) == (2, 1)
    assert (result.audit.action.value, result.audit.actor) == (
        "facts.extracted",
        ACTOR,
    )
    assert (result.audit.ref, result.audit.trace_id) == (result.fact_set_id, TRACE_ID)
    # The stored result is what was answered, and the path is the contracts'.
    assert repository.result_of(FactSetKey(case_id, page_id)) == result
    operation = get_operation("extract_facts")
    assert (operation.path, operation.idempotency_key) == (
        "/fact-sets",
        ("case_id", "page_id"),
    )
    # The caller's trace context goes on to `intake` with the reads.
    assert pages.trace_contexts[0] == {"traceparent": TRACEPARENT, "tracestate": "k=v"}
    # Ids and a small summary only: nothing of the page comes back.
    assert "SECRET" not in response.text
    assert "HbA1c" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"case_id": new_id(), "page_id": new_id(), "text": "SECRET-INPUT"},
    ],
)
def test_story_2_4_a_malformed_command_is_422_without_echoing_the_input(
    client: TestClient, body: dict[str, object]
) -> None:
    response = client.post("/fact-sets", json=body)

    assert (response.status_code, error_code(response)) == (
        422,
        ErrorCode.VALIDATION_FAILED,
    )
    assert "SECRET-INPUT" not in response.text


# --- GET /cases/{case_id}/facts ----------------------------------------------------------


def test_story_2_4_the_service_has_only_its_two_operations_and_the_probes(
    dependencies: Dependencies,
) -> None:
    routes = {
        (method, route.path)  # type: ignore[attr-defined]  # an API route
        for route in build_router(dependencies).routes
        for method in route.methods  # type: ignore[attr-defined]  # an API route
    }

    # No verdict, no search and no decision is made or asked for here.
    assert routes == {
        ("GET", "/health"),
        ("HEAD", "/health"),
        ("GET", "/ready"),
        ("HEAD", "/ready"),
        ("POST", "/fact-sets"),
        ("GET", "/cases/{case_id}/facts"),
    }


# --- Probes, headers and the error shape -------------------------------------------------


def test_story_2_4_an_unhandled_error_is_a_plain_500_without_detail(
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
            "/fact-sets",
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
