"""Story 2.4: the routes of `extraction`, over HTTP, with in-memory stand-ins.

Unit tests: the extract command, the read, the probes and the error shape.
No database, no model, no network.
"""

import logging

import httpx2
import pytest
from extraction_fakes import (
    ACTOR,
    PAGE_TEXT,
    TRACE_ID,
    TRACEPARENT,
    FakePages,
    MemoryRepository,
    MemorySchemaRevision,
    StubModel,
    answer,
    fact,
    unavailable,
)
from fastapi.testclient import TestClient

from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.extraction import FactList, FactSetResult
from contracts.operations import get_operation
from contracts.text import normalise
from extraction.adapters.http.app import create_app
from extraction.adapters.http.middleware import SECURITY_HEADERS
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


def test_story_2_4_the_same_command_again_is_answered_from_the_store(
    client: TestClient, pages: FakePages, model: StubModel, case_id: str
) -> None:
    page_id = pages.add(case_id)

    first = client.post("/fact-sets", json=command(case_id, page_id))
    again = client.post("/fact-sets", json=command(case_id, page_id))

    assert (first.status_code, again.status_code) == (200, 200)
    assert again.json() == first.json()
    assert model.calls == 1


def test_story_2_4_a_failed_extraction_is_a_stored_result_not_an_error_status(
    client: TestClient, pages: FakePages, model: StubModel, case_id: str
) -> None:
    bad_page, down_page = pages.add(case_id), pages.add(case_id)
    model.answers = ["prose, not the object asked for", unavailable()]

    invalid = client.post("/fact-sets", json=command(case_id, bad_page))
    down = client.post("/fact-sets", json=command(case_id, down_page))

    for response, code in (
        (invalid, ErrorCode.INVALID_MODEL_OUTPUT),
        (down, ErrorCode.MODEL_UNAVAILABLE),
    ):
        # 200: it is the stored result, and `workflow` records it.
        assert response.status_code == 200
        result = FactSetResult.model_validate(response.json())
        assert (result.status.value, result.error_code) == ("failed", code)
        assert result.audit.action.value == "stage.failed"
        assert result.fact_ids == []
    assert client.get(f"/cases/{case_id}/facts").json()["facts"] == []


def test_story_2_4_an_unknown_page_or_a_page_of_another_case_is_404_not_found(
    client: TestClient, pages: FakePages, case_id: str
) -> None:
    pages.add(case_id)
    other_page = pages.add(new_id())

    for page_id in (new_id(), other_page):
        response = client.post("/fact-sets", json=command(case_id, page_id))
        assert (response.status_code, error_code(response)) == (
            404,
            ErrorCode.NOT_FOUND,
        )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"case_id": "not-an-id", "page_id": "SECRET-INPUT"},
        {"case_id": new_id()},
        {"case_id": new_id(), "page_id": new_id(), "contender": "llm"},
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


def test_story_2_4_get_facts_lists_the_cases_facts_in_page_order(
    client: TestClient, pages: FakePages, model: StubModel, case_id: str
) -> None:
    first_page, second_page = pages.add(case_id), pages.add(case_id)
    other_case = new_id()
    other_page = pages.add(other_case)
    model.answers = [
        answer(fact("Second page"), fact("Second page, not on it", "nowhere")),
        answer(fact("First page")),
        answer(fact("Another case")),
    ]
    for case, page in (
        (case_id, second_page),
        (case_id, first_page),
        (other_case, other_page),
    ):
        assert client.post("/fact-sets", json=command(case, page)).status_code == 200

    response = client.get(f"/cases/{case_id}/facts")

    assert response.status_code == 200
    listed = FactList.model_validate(response.json())
    assert listed.case_id == case_id
    assert [(item.page_number, item.statement) for item in listed.facts] == [
        (1, "First page"),
        (2, "Second page"),
        (2, "Second page, not on it"),
    ]
    assert [item.page_id for item in listed.facts] == [
        first_page,
        second_page,
        second_page,
    ]
    verified, _, unverified = listed.facts
    # A verified fact's offsets select its quote on the page.
    assert verified.quote_start is not None and verified.quote_end is not None
    assert normalise(PAGE_TEXT[verified.quote_start : verified.quote_end]) == (
        normalise(verified.quote)
    )
    # An unverified one is listed, flagged, with no offsets.
    assert unverified.model_dump(
        include={"quote_verified", "quote_start", "quote_end"}
    ) == {
        "quote_verified": False,
        "quote_start": None,
        "quote_end": None,
    }
    assert get_operation("list_facts").path == "/cases/{case_id}/facts"


def test_story_2_4_a_case_with_no_facts_lists_none_and_is_not_a_404(
    client: TestClient,
) -> None:
    case_id = new_id()

    response = client.get(f"/cases/{case_id}/facts")

    assert response.status_code == 200
    assert response.json() == {"case_id": case_id, "facts": []}


def test_story_2_4_a_case_id_that_is_not_an_id_is_refused(client: TestClient) -> None:
    response = client.get("/cases/not-an-id/facts")

    assert (response.status_code, error_code(response)) == (
        422,
        ErrorCode.VALIDATION_FAILED,
    )


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


def test_story_2_4_health_answers_while_the_process_runs(client: TestClient) -> None:
    for method in ("GET", "HEAD"):
        assert client.request(method, "/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_story_2_4_ready_only_when_the_schema_is_at_the_bundled_head(
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


def test_story_2_4_every_response_carries_the_security_headers(
    client: TestClient,
) -> None:
    for response in (
        client.get("/health"),
        client.get("/nowhere"),
        client.post("/fact-sets", json={}),
    ):
        for name, value in SECURITY_HEADERS:
            assert response.headers[name] == value


def test_story_2_4_framework_errors_leave_in_the_error_shape(
    client: TestClient,
) -> None:
    missing = client.get("/nowhere")
    wrong_method = client.delete("/fact-sets")

    assert (missing.status_code, error_code(missing)) == (404, ErrorCode.NOT_FOUND)
    assert (wrong_method.status_code, error_code(wrong_method)) == (
        405,
        ErrorCode.METHOD_NOT_ALLOWED,
    )
    assert ErrorBody.model_validate(missing.json()).error.trace_id == "0" * 32


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
