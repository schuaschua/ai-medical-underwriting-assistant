"""Story 1.6: the routes of `workflow`, with in-memory stand-ins behind them."""

import asyncio
import logging
from dataclasses import replace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from workflow_fakes import (
    FakeEngine,
    MemoryCaseStore,
    MemorySchemaRevision,
    classification_done,
    redaction_done,
    redaction_failed,
)

from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import AuditTrail, CaseProgress, CaseStarted
from workflow.adapters.http.app import create_app
from workflow.adapters.http.routes import Dependencies, build_router
from workflow.domain.cases import record_stage_result
from workflow.settings import Settings

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


def error_of(response_json: object) -> tuple[str, str, str]:
    detail = ErrorBody.model_validate(response_json).error
    return detail.code.value, detail.message, detail.trace_id


# --- Start ---------------------------------------------------------------------


def test_story_1_6_start_answers_with_the_case_as_started(
    client: TestClient, case_id: str, store: MemoryCaseStore, engine: FakeEngine
) -> None:
    response = client.post(f"/cases/{case_id}/start")

    assert response.status_code == 200
    started = CaseStarted.model_validate(response.json())
    assert started.case_id == case_id
    assert started.case_status.value == "running"
    # No options were sent: the defaults from the settings apply.
    assert started.classifier_contender.value == "llm"
    assert [config.value for config in started.retriever_configs] == ["r3"]
    assert (started.stop_after, started.eval_run_id) == (None, None)
    assert list(engine.instances) == [case_id]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_story_1_6_start_twice_is_one_case_one_orchestration_and_the_same_answer(
    client: TestClient, case_id: str, store: MemoryCaseStore, engine: FakeEngine
) -> None:
    first = client.post(f"/cases/{case_id}/start", json={})
    second = client.post(f"/cases/{case_id}/start", json={})

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()
    assert len(store.cases) == 1
    assert list(engine.instances) == [case_id]
    assert store.events == []


def test_story_1_6_start_options_are_stored_with_the_case_and_returned(
    client: TestClient, case_id: str, store: MemoryCaseStore
) -> None:
    eval_run_id = new_id()
    options = {
        "classifier_contender": "doc-intelligence",
        "retriever_configs": ["r4", "r5"],
        "stop_after": "gate",
        "eval_run_id": eval_run_id,
    }

    response = client.post(f"/cases/{case_id}/start", json=options)

    assert response.status_code == 200
    assert response.json() == {"case_id": case_id, "case_status": "running", **options}
    parameters = store.cases[case_id].parameters
    assert parameters.classifier_contender.value == "doc-intelligence"
    assert [config.value for config in parameters.retriever_configs] == ["r4", "r5"]
    assert parameters.stop_after is not None
    assert parameters.eval_run_id == eval_run_id


@pytest.mark.parametrize(
    "options",
    [
        {"classifier_contender": "guess"},
        {"retriever_configs": []},
        {"retriever_configs": ["r3", "r3"]},
        {"retriever_configs": ["r9"]},
        {"stop_after": "redaction"},
        {"eval_run_id": "not-an-id"},
        {"unknown_field": 1},
    ],
    ids=[
        "contender",
        "no-configs",
        "repeated-config",
        "unknown-config",
        "stop-after",
        "eval-run-id",
        "unknown-field",
    ],
)
def test_story_1_6_an_invalid_start_option_is_422_and_starts_nothing(
    client: TestClient,
    case_id: str,
    store: MemoryCaseStore,
    engine: FakeEngine,
    options: dict[str, object],
) -> None:
    response = client.post(
        f"/cases/{case_id}/start", json=options, headers={"traceparent": TRACEPARENT}
    )

    assert response.status_code == 422
    code, message, trace_id = error_of(response.json())
    assert (code, trace_id) == ("validation_failed", TRACE_ID)
    # The framework's detail echoes the input; the message does not.
    assert message == "The request is not valid."
    assert store.cases == {}
    assert engine.calls == []


@pytest.mark.parametrize(
    "bad_id",
    [
        "abc",
        "019a0000-0000-4000-8000-000000000001",
        "019A0000-0000-7000-8000-000000000001",
    ],
    ids=["not-a-uuid", "uuid-v4", "upper-case"],
)
def test_story_1_6_a_case_id_that_is_not_a_uuid7_is_422(
    client: TestClient, engine: FakeEngine, bad_id: str
) -> None:
    for response in (
        client.post(f"/cases/{bad_id}/start"),
        client.get(f"/cases/{bad_id}/progress"),
        client.get(f"/cases/{bad_id}/audit"),
    ):
        assert response.status_code == 422
        assert error_of(response.json())[0] == "validation_failed"
    assert engine.calls == []


def test_story_1_6_a_start_that_cannot_reach_the_engine_is_502_in_the_error_shape(
    client: TestClient,
    case_id: str,
    engine: FakeEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine.fail = True

    with caplog.at_level(logging.ERROR):
        response = client.post(f"/cases/{case_id}/start")

    assert response.status_code == 502
    assert error_of(response.json())[0] == "upstream_unavailable"
    assert "secret-store-detail" not in response.text
    assert "secret-store-detail" not in caplog.text


def test_story_1_6_a_database_failure_is_a_plain_500(
    client: TestClient,
    case_id: str,
    store: MemoryCaseStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store.fail = True

    with caplog.at_level(logging.ERROR):
        responses = [
            client.post(f"/cases/{case_id}/start"),
            client.get(f"/cases/{case_id}/progress"),
            client.get(f"/cases/{case_id}/audit"),
        ]

    for response in responses:
        assert response.status_code == 500
        assert error_of(response.json())[0] == "internal_error"
        assert "secret-store-detail" not in response.text
    # security rule 31: the type and where, never the message.
    assert "type=StoreDown" in caplog.text
    assert "secret-store-detail" not in caplog.text


# --- Progress and audit --------------------------------------------------------


def test_story_1_6_progress_of_a_started_case_has_its_status_and_no_pages_yet(
    client: TestClient, case_id: str
) -> None:
    client.post(f"/cases/{case_id}/start")

    response = client.get(f"/cases/{case_id}/progress")

    assert response.status_code == 200
    assert response.json() == {
        "case_id": case_id,
        "case_status": "running",
        "redaction_status": "running",
        "pages": [],
        # Story 1.9: the failure reason, null while nothing has failed.
        "error_code": None,
    }


def test_story_1_6_progress_lists_the_pages_once_they_exist(
    client: TestClient,
    case_id: str,
    store: MemoryCaseStore,
    dependencies: Dependencies,
) -> None:
    client.post(f"/cases/{case_id}/start")
    pages = [new_id(), new_id()]
    asyncio.run(record_stage_result(redaction_done(case_id, pages), store=store))

    progress = CaseProgress.model_validate(
        client.get(f"/cases/{case_id}/progress").json()
    )

    assert progress.redaction_status.value == "done"
    assert [(page.page_id, page.page_number) for page in progress.pages] == [
        (pages[0], 1),
        (pages[1], 2),
    ]
    assert {page.page_status.value for page in progress.pages} == {"uploaded"}


def test_story_1_6_audit_lists_the_recorded_events(
    client: TestClient, case_id: str, store: MemoryCaseStore
) -> None:
    client.post(f"/cases/{case_id}/start")
    assert client.get(f"/cases/{case_id}/audit").json() == {
        "case_id": case_id,
        "events": [],
        "has_more": False,
    }
    result = redaction_failed(case_id)
    asyncio.run(record_stage_result(result, store=store))

    trail = AuditTrail.model_validate(client.get(f"/cases/{case_id}/audit").json())

    # Story 1.12: the event is the stage's record, with the failure's code.
    assert trail.events == [
        result.audit.model_copy(update={"error_code": ErrorCode.REDACTION_FAILED})
    ]
    assert trail.events[0].action.value == "stage.failed"
    assert trail.has_more is False


def test_story_1_12_the_audit_route_lists_the_first_events_up_to_its_limit(
    settings: Settings, dependencies: Dependencies, case_id: str, store: MemoryCaseStore
) -> None:
    app = create_app(settings, dependencies=replace(dependencies, audit_trail_limit=2))
    page_ids = [new_id(), new_id()]
    with TestClient(app) as limited:
        limited.post(f"/cases/{case_id}/start")
        redacted = redaction_done(case_id, page_ids)
        asyncio.run(record_stage_result(redacted, store=store))
        first = classification_done(case_id, page_ids[0])
        asyncio.run(record_stage_result(first, store=store))

        exact = AuditTrail.model_validate(limited.get(f"/cases/{case_id}/audit").json())
        asyncio.run(
            record_stage_result(classification_done(case_id, page_ids[1]), store=store)
        )
        bounded = AuditTrail.model_validate(
            limited.get(f"/cases/{case_id}/audit").json()
        )

    # Exactly as many as the limit: nothing more exists.
    assert (len(exact.events), exact.has_more) == (2, False)
    # One more than the limit: the first two, and a note that more exist.
    assert [event.action.value for event in bounded.events] == [
        "document.redacted",
        "page.classified",
    ]
    assert bounded.events[1].page_id == page_ids[0]
    assert bounded.has_more is True


def test_story_1_6_progress_and_audit_of_an_unknown_case_are_404(
    client: TestClient, case_id: str
) -> None:
    for path in (f"/cases/{case_id}/progress", f"/cases/{case_id}/audit"):
        response = client.get(path, headers={"traceparent": TRACEPARENT})

        assert response.status_code == 404
        assert error_of(response.json()) == (
            "not_found",
            "That case could not be found.",
            TRACE_ID,
        )


def test_story_1_6_no_route_takes_a_stage_result_or_changes_the_audit_trail(
    settings: Settings, dependencies: Dependencies, case_id: str
) -> None:
    routes = sorted(
        (method, route.path)
        for route in build_router(dependencies).routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    )

    # The whole surface: two probes, start, two reads, the one decision
    # operation (story 1.10, AD-10) and the read of a queue (story 1.11).
    # Stage results come from the orchestration's activities, never over
    # HTTP (AD-2).
    assert routes == [
        ("GET", "/cases/{case_id}/audit"),
        ("GET", "/cases/{case_id}/progress"),
        ("GET", "/health"),
        ("GET", "/pages"),
        ("GET", "/ready"),
        ("HEAD", "/health"),
        ("HEAD", "/ready"),
        ("POST", "/cases/{case_id}/pages/{page_id}/decisions"),
        ("POST", "/cases/{case_id}/start"),
    ]
    with TestClient(create_app(settings, dependencies=dependencies)) as client:
        for method in ("put", "patch", "delete", "post"):
            response = client.request(method, f"/cases/{case_id}/audit")
            assert response.status_code == 405
            assert error_of(response.json())[0] == "method_not_allowed"


# --- Probes --------------------------------------------------------------------


def test_story_1_6_health_answers_without_the_database(
    client: TestClient, schema_revision: MemorySchemaRevision
) -> None:
    schema_revision.fail = True

    assert client.get("/health").json() == {"status": "ok"}
    assert client.head("/health").status_code == 200


def test_story_1_6_ready_only_when_the_schema_is_at_the_bundled_head(
    client: TestClient,
    schema_revision: MemorySchemaRevision,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert client.get("/ready").json() == {"status": "ok"}

    for revision in (None, "0000"):
        schema_revision.revision = revision
        response = client.get("/ready")
        assert response.status_code == 502
        assert error_of(response.json())[:2] == (
            "upstream_unavailable",
            "The service is not ready.",
        )

    schema_revision.fail = True
    with caplog.at_level(logging.WARNING):
        response = client.get("/ready")
    assert response.status_code == 502
    assert "not ready: database type=StoreDown" in caplog.text
    assert "secret-store-detail" not in caplog.text


def test_story_1_6_an_unknown_path_is_404_in_the_error_shape(
    client: TestClient,
) -> None:
    response = client.get("/cases")

    assert response.status_code == 404
    assert error_of(response.json())[0] == "not_found"
