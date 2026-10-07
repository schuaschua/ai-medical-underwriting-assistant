"""Story 1.13: `web` passes the demo role on with a start, and serves the underwriter's case list.

The Dapr sidecar is a fake here: a transport that records what `web` sent and
answers as `workflow` would.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import CaseList
from web.adapters.http.app import create_app
from web.settings import Settings

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
INVOKE = "http://127.0.0.1:3500/v1.0/invoke"


def summary(case_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "case_status": "awaiting_human",
        "started_at": "2026-10-07T09:00:00Z",
        "page_count": 3,
        "waiting_page_count": 1,
        **changes,
    }


def refusal(code: ErrorCode, message: str) -> httpx.Response:
    failure = DomainError(code, message)
    return httpx.Response(
        failure.http_status, json=failure.to_body(TRACE_ID).model_dump(mode="json")
    )


@dataclass
class FakeSidecar:
    """Stands in for the Dapr sidecar and, behind it, `workflow`."""

    # What `GET /cases` answers.
    listed: dict[str, Any] | httpx.Response | Exception = field(
        default_factory=lambda: {"cases": [], "has_more": False}
    )
    requests: list[httpx.Request] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = await request.aread()
        if request.method == "GET" and request.url.path.endswith("/method/cases"):
            if isinstance(self.listed, Exception):
                raise self.listed
            if isinstance(self.listed, httpx.Response):
                return self.listed
            return httpx.Response(200, json=self.listed)
        case_id = request.url.path.split("/")[-2]
        options = json.loads(body) if body else {}
        if options.pop("actor", None) not in ("customer", "underwriter"):
            return refusal(ErrorCode.ACTOR_NOT_HUMAN, "Only a person may start a case.")
        return httpx.Response(
            200,
            json={
                "case_id": case_id,
                "case_status": "running",
                "classifier_contender": "llm",
                "retriever_configs": ["r3"],
                "stop_after": None,
                "eval_run_id": None,
                **options,
            },
        )


@pytest.fixture
def sidecar() -> FakeSidecar:
    return FakeSidecar()


@pytest.fixture
def client(settings: Settings, sidecar: FakeSidecar) -> Iterator[TestClient]:
    app = create_app(settings, sidecar=httpx.MockTransport(sidecar))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def error_of(response: Any) -> tuple[int, str]:
    return response.status_code, ErrorBody.model_validate(
        response.json()
    ).error.code.value


# --- The start names who asked -------------------------------------------------------


@pytest.mark.parametrize(("headers", "role"), [(UNDERWRITER, "underwriter")])
def test_story_1_13_a_start_is_passed_on_with_the_requests_demo_role_as_its_actor(
    client: TestClient, sidecar: FakeSidecar, headers: dict[str, str], role: str
) -> None:
    case_id = new_id()

    response = client.post(f"/api/cases/{case_id}/start", headers=headers)

    assert response.status_code == 200
    (start_call,) = sidecar.requests
    assert str(start_call.url) == f"{INVOKE}/workflow/method/cases/{case_id}/start"
    assert json.loads(start_call.content) == {"actor": role}
    # The role travels in the body; internal services never read the header.
    assert "x-demo-role" not in start_call.headers
    # The answer is the case as started: it names no actor.
    assert "actor" not in response.json()


def test_story_1_13_a_browser_cannot_say_who_started_the_case(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    bodies: list[dict[str, object]] = [
        {"actor": "underwriter"},
        {"actor": "customer"},
        {"actor": "workflow:case-lifecycle"},
        {"actor": None},
        {"actor": "underwriter", "stop_after": "gate"},
    ]
    for headers in (CUSTOMER, UNDERWRITER):
        for body in bodies:
            response = client.post(
                f"/api/cases/{new_id()}/start", json=body, headers=headers
            )
            # The body `web` reads has no such field: the start never reaches `workflow`.
            assert error_of(response) == (422, "validation_failed")
    assert sidecar.requests == []


# --- The case list -------------------------------------------------------------------


def test_story_1_13_the_underwriter_reads_the_case_list_as_workflow_answers_it(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    newest, older = new_id(), new_id()
    sidecar.listed = {
        "cases": [
            summary(newest, case_status="running", page_count=0, waiting_page_count=0),
            summary(older, case_status="completed", waiting_page_count=0),
        ],
        "has_more": True,
    }

    response = client.get(
        "/api/cases", headers={**UNDERWRITER, "traceparent": TRACEPARENT}
    )

    assert response.status_code == 200
    assert response.json() == sidecar.listed
    listed = CaseList.model_validate(response.json())
    assert [case.case_id for case in listed.cases] == [newest, older]
    assert listed.has_more is True
    (call,) = sidecar.requests
    assert (call.method, str(call.url)) == ("GET", f"{INVOKE}/workflow/method/cases")
    assert call.headers["traceparent"] == TRACEPARENT
    assert "x-demo-role" not in call.headers
    assert response.headers["cache-control"] == "no-store"


def test_story_1_13_the_customer_is_refused_the_case_list(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    response = client.get("/api/cases", headers=CUSTOMER)

    assert error_of(response) == (403, "role_not_allowed")
    # Refused by `web` itself: `workflow` is never asked.
    assert sidecar.requests == []
