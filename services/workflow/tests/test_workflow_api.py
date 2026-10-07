"""Story 1.6: the routes of `workflow`, with in-memory stand-ins behind them."""

import asyncio
from dataclasses import replace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from workflow_fakes import (
    STARTED_BY,
    FakeEngine,
    MemoryCaseStore,
    after_start,
    classification_done,
    redaction_done,
)

from contracts.errors import ErrorBody
from contracts.ids import new_id
from contracts.models.workflow import AuditTrail
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


def test_story_1_6_start_twice_is_one_case_one_orchestration_and_the_same_answer(
    client: TestClient, case_id: str, store: MemoryCaseStore, engine: FakeEngine
) -> None:
    first = client.post(f"/cases/{case_id}/start", json=STARTED_BY)
    second = client.post(f"/cases/{case_id}/start", json=STARTED_BY)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()
    assert len(store.cases) == 1
    assert list(engine.instances) == [case_id]
    assert after_start(store) == []


@pytest.mark.parametrize(
    "options",
    [
        {"retriever_configs": ["r9"]},
    ],
    ids=["unknown-config"],
)
def test_story_1_6_an_invalid_start_option_is_422_and_starts_nothing(
    client: TestClient,
    case_id: str,
    store: MemoryCaseStore,
    engine: FakeEngine,
    options: dict[str, object],
) -> None:
    response = client.post(
        f"/cases/{case_id}/start",
        json={**STARTED_BY, **options},
        headers={"traceparent": TRACEPARENT},
    )

    assert response.status_code == 422
    code, message, trace_id = error_of(response.json())
    assert (code, trace_id) == ("validation_failed", TRACE_ID)
    # The framework's detail echoes the input; the message does not.
    assert message == "The request is not valid."
    assert store.cases == {}
    assert engine.calls == []


# --- Progress and audit --------------------------------------------------------


def test_story_1_12_the_audit_route_lists_the_first_events_up_to_its_limit(
    settings: Settings, dependencies: Dependencies, case_id: str, store: MemoryCaseStore
) -> None:
    app = create_app(settings, dependencies=replace(dependencies, audit_trail_limit=2))
    page_ids = [new_id(), new_id()]
    with TestClient(app) as limited:
        limited.post(f"/cases/{case_id}/start", json=STARTED_BY)
        redacted = redaction_done(case_id, page_ids)
        asyncio.run(record_stage_result(redacted, store=store))

        # The start and the redaction.
        exact = AuditTrail.model_validate(limited.get(f"/cases/{case_id}/audit").json())
        asyncio.run(
            record_stage_result(classification_done(case_id, page_ids[0]), store=store)
        )
        bounded = AuditTrail.model_validate(
            limited.get(f"/cases/{case_id}/audit").json()
        )

    # Exactly as many as the limit: nothing more exists.
    assert (len(exact.events), exact.has_more) == (2, False)
    # One more than the limit: the first two, and a note that more exist.
    assert [event.action.value for event in bounded.events] == [
        "case.started",
        "document.redacted",
    ]
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
    # operation (story 1.10, AD-10), the read of a queue (story 1.11) and
    # the request for one more verdict run on a finished case (stories 2.5
    # and 2.6, AD-15), which carries no result: it only has a run made.
    # Stage results come from the orchestration's activities, never over
    # HTTP (AD-2).
    assert routes == [
        ("GET", "/cases"),
        ("GET", "/cases/{case_id}/audit"),
        ("GET", "/cases/{case_id}/progress"),
        ("GET", "/health"),
        ("GET", "/pages"),
        ("GET", "/ready"),
        ("HEAD", "/health"),
        ("HEAD", "/ready"),
        ("POST", "/cases/{case_id}/pages/{page_id}/decisions"),
        ("POST", "/cases/{case_id}/start"),
        ("POST", "/cases/{case_id}/verdict-runs"),
    ]
    with TestClient(create_app(settings, dependencies=dependencies)) as client:
        for method in ("put", "patch", "delete", "post"):
            response = client.request(method, f"/cases/{case_id}/audit")
            assert response.status_code == 405
            assert error_of(response.json())[0] == "method_not_allowed"
