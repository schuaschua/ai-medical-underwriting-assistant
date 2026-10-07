"""Story 1.10: `web` passes a decision on to `workflow` and reads the classifications.

The Dapr sidecar is a fake here: a transport that records what `web` sent and
answers as `workflow` and `classification` would.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from contracts.decisions import decision_rule
from contracts.enums import Decision
from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.classification import ClassificationList
from contracts.models.workflow import DecisionRecorded
from web.adapters.http.app import create_app
from web.settings import Settings

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
INVOKE = "http://127.0.0.1:3500/v1.0/invoke"
OCCURRED_AT = "2026-10-07T09:00:00Z"


def error(code: ErrorCode, message: str, status: int | None = None) -> httpx.Response:
    failure = DomainError(code, message)
    return httpx.Response(
        status or failure.http_status,
        json=failure.to_body(TRACE_ID).model_dump(mode="json"),
    )


@dataclass
class FakeSidecar:
    """Stands in for the Dapr sidecar and, behind it, `workflow` and `classification`."""

    # An answer that replaces the usual one, for every call.
    answer: httpx.Response | Exception | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    bodies: list[Any] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = await request.aread()
        self.bodies.append(json.loads(body) if body else None)
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer is not None:
            return self.answer
        parts = request.url.path.split("/")
        if parts[-1] == "classifications":
            case_id = parts[-2]
            return httpx.Response(
                200,
                json={
                    "case_id": case_id,
                    "classifications": [
                        {
                            "classification_id": new_id(),
                            "case_id": case_id,
                            "page_id": new_id(),
                            "contender": "llm",
                            "page_type": "other",
                            "is_medical": False,
                            "confidence": 0.96,
                            "reason": "A page with no medical content.",
                        }
                    ],
                },
            )
        # A decision: answered as `workflow` does for the role's own decisions.
        sent = self.bodies[-1]
        return httpx.Response(
            200,
            json={
                "decision_id": new_id(),
                "case_id": parts[-4],
                "page_id": parts[-2],
                "decision": sent["decision"],
                "actor": sent["actor"],
                "page_status": decision_rule(Decision(sent["decision"])).leaves.value,
                "occurred_at": OCCURRED_AT,
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


def decisions_path(case_id: str, page_id: str) -> str:
    return f"/api/cases/{case_id}/pages/{page_id}/decisions"


# --- Decisions --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "decision", "page_status"),
    [(CUSTOMER, "keep", "awaiting_triage"), (UNDERWRITER, "accept", "extracting")],
)
def test_story_1_10_a_decision_is_passed_on_with_the_requests_role_as_the_actor(
    client: TestClient,
    sidecar: FakeSidecar,
    role: dict[str, str],
    decision: str,
    page_status: str,
) -> None:
    case_id, page_id = new_id(), new_id()

    response = client.post(
        decisions_path(case_id, page_id),
        json={"decision": decision},
        headers={**role, "traceparent": TRACEPARENT},
    )

    assert response.status_code == 200
    recorded = DecisionRecorded.model_validate(response.json())
    assert (recorded.case_id, recorded.page_id) == (case_id, page_id)
    assert recorded.actor.value == role["X-Demo-Role"]
    assert recorded.page_status.value == page_status
    # One call, to `workflow`'s one decision operation, by app id (AD-3, AD-10).
    (call,) = sidecar.requests
    assert (call.method, str(call.url)) == (
        "POST",
        f"{INVOKE}/workflow/method/cases/{case_id}/pages/{page_id}/decisions",
    )
    # The actor is the demo role of the request, and nothing else is added.
    assert sidecar.bodies == [{"decision": decision, "actor": role["X-Demo-Role"]}]
    assert call.headers["traceparent"] == TRACEPARENT
    assert response.headers["cache-control"] == "no-store"


def test_story_1_10_a_decision_that_is_not_valid_never_reaches_workflow(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    for body in [
        {"decision": "keep", "actor": "underwriter"},
        {"decision": "keep", "actor": "workflow"},
        {"decision": "approve"},
        {},
    ]:
        response = client.post(
            decisions_path(new_id(), new_id()), json=body, headers=CUSTOMER
        )

        assert response.status_code == 422
        assert ErrorBody.model_validate(response.json()).error.code is (
            ErrorCode.VALIDATION_FAILED
        )
        assert sidecar.requests == []


@pytest.mark.parametrize(
    ("role", "decision", "code", "status"),
    [
        (CUSTOMER, "accept", ErrorCode.ROLE_NOT_ALLOWED, 403),
        (CUSTOMER, "discard", ErrorCode.NOT_AWAITING_DECISION, 409),
    ],
)
def test_story_1_10_workflows_refusal_of_a_decision_is_passed_on(
    client: TestClient,
    sidecar: FakeSidecar,
    role: dict[str, str],
    decision: str,
    code: ErrorCode,
    status: int,
) -> None:
    sidecar.answer = error(code, "As workflow worded it.")

    response = client.post(
        decisions_path(new_id(), new_id()), json={"decision": decision}, headers=role
    )

    assert response.status_code == status
    detail = ErrorBody.model_validate(response.json()).error
    assert (detail.code, detail.message) == (code, "As workflow worded it.")
    # It was asked: the refusal is `workflow`'s, not `web`'s.
    assert len(sidecar.requests) == 1


# --- Classifications ----------------------------------------------------------------


@pytest.mark.parametrize("role", [CUSTOMER])
def test_story_1_10_classifications_are_read_from_classification_by_either_role(
    client: TestClient, sidecar: FakeSidecar, role: dict[str, str]
) -> None:
    case_id = new_id()

    response = client.get(
        f"/api/cases/{case_id}/classifications",
        headers={**role, "traceparent": TRACEPARENT},
    )

    assert response.status_code == 200
    listed = ClassificationList.model_validate(response.json())
    assert listed.case_id == case_id
    # The type and the confidence are the server's: a 0-to-1 number, which
    # the SPA words as a percentage.
    (classification,) = listed.classifications
    assert (classification.page_type.value, classification.confidence) == (
        "other",
        0.96,
    )
    (call,) = sidecar.requests
    assert (call.method, str(call.url)) == (
        "GET",
        f"{INVOKE}/classification/method/cases/{case_id}/classifications",
    )
    assert call.headers["traceparent"] == TRACEPARENT
    assert response.headers["cache-control"] == "no-store"
