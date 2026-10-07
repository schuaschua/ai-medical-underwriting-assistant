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
    [
        (CUSTOMER, "keep", "awaiting_triage"),
        (CUSTOMER, "discard", "discarded"),
        (UNDERWRITER, "accept", "extracting"),
        (UNDERWRITER, "deny", "denied"),
    ],
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


@pytest.mark.parametrize(
    "body",
    [
        # The browser cannot say who decided.
        {"decision": "keep", "actor": "underwriter"},
        {"decision": "keep", "actor": "workflow"},
        {"decision": "approve"},
        {},
    ],
)
def test_story_1_10_a_decision_that_is_not_valid_never_reaches_workflow(
    client: TestClient, sidecar: FakeSidecar, body: dict[str, str]
) -> None:
    response = client.post(
        decisions_path(new_id(), new_id()), json=body, headers=CUSTOMER
    )

    assert response.status_code == 422
    assert ErrorBody.model_validate(response.json()).error.code is (
        ErrorCode.VALIDATION_FAILED
    )
    assert sidecar.requests == []


def test_story_1_10_a_decision_needs_a_demo_role_and_well_formed_ids(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    case_id, page_id = new_id(), new_id()
    body = {"decision": "keep"}

    without_role = client.post(decisions_path(case_id, page_id), json=body)
    unknown_role = client.post(
        decisions_path(case_id, page_id), json=body, headers={"X-Demo-Role": "workflow"}
    )
    bad_case = client.post(decisions_path("1", page_id), json=body, headers=CUSTOMER)
    bad_page = client.post(decisions_path(case_id, "1"), json=body, headers=CUSTOMER)

    # AD-9: no call without a valid role; a service name is not one.
    for response in (without_role, unknown_role):
        assert response.status_code == 400
        assert ErrorBody.model_validate(response.json()).error.code is (
            ErrorCode.INVALID_ROLE
        )
    assert (bad_case.status_code, bad_page.status_code) == (422, 422)
    assert sidecar.requests == []


@pytest.mark.parametrize(
    ("role", "decision", "code", "status"),
    [
        # `web` has no rule of its own: it asks, and `workflow` refuses.
        (CUSTOMER, "accept", ErrorCode.ROLE_NOT_ALLOWED, 403),
        (UNDERWRITER, "keep", ErrorCode.ROLE_NOT_ALLOWED, 403),
        (CUSTOMER, "keep", ErrorCode.ACTOR_NOT_HUMAN, 403),
        (CUSTOMER, "discard", ErrorCode.NOT_AWAITING_DECISION, 409),
        (CUSTOMER, "discard", ErrorCode.NOT_FOUND, 404),
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


@pytest.mark.parametrize(
    "answer",
    [
        # The decision was stored but its event could not be raised.
        error(ErrorCode.UPSTREAM_UNAVAILABLE, "Saved, but the case was not told."),
        error(ErrorCode.INTERNAL_ERROR, "secret-workflow-detail"),
        # A code under another status than its own does not hold together.
        error(ErrorCode.NOT_AWAITING_DECISION, "secret-workflow-detail", 500),
        httpx.Response(200, json={"decision": "keep"}),
        httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE"}),
        httpx.ConnectError("secret-sidecar-detail"),
    ],
    ids=["not-told", "internal", "mismatch", "not-a-decision", "sidecar", "down"],
)
def test_story_1_10_a_decision_that_fails_is_502_so_the_caller_can_send_it_again(
    client: TestClient, sidecar: FakeSidecar, answer: httpx.Response | Exception
) -> None:
    sidecar.answer = answer

    response = client.post(
        decisions_path(new_id(), new_id()), json={"decision": "keep"}, headers=CUSTOMER
    )

    assert response.status_code == 502
    assert ErrorBody.model_validate(response.json()).error.code is (
        ErrorCode.UPSTREAM_UNAVAILABLE
    )
    assert "secret" not in response.text


# --- Classifications ----------------------------------------------------------------


@pytest.mark.parametrize("role", [CUSTOMER, UNDERWRITER])
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


def test_story_1_10_classifications_need_a_role_and_a_well_formed_case_id(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    assert client.get(f"/api/cases/{new_id()}/classifications").status_code == 400
    assert (
        client.get("/api/cases/1/classifications", headers=CUSTOMER).status_code == 422
    )
    assert sidecar.requests == []


@pytest.mark.parametrize(
    ("answer", "status", "code"),
    [
        (error(ErrorCode.NOT_FOUND, "That case could not be found."), 404, "not_found"),
        (error(ErrorCode.INTERNAL_ERROR, "secret-detail"), 502, "upstream_unavailable"),
        (httpx.Response(200, json={"case_id": "x"}), 502, "upstream_unavailable"),
        (httpx.ConnectError("secret-detail"), 502, "upstream_unavailable"),
    ],
    ids=["unknown-case", "internal", "not-a-list", "down"],
)
def test_story_1_10_classifications_that_cannot_be_read_are_answered_in_the_error_shape(
    client: TestClient,
    sidecar: FakeSidecar,
    answer: httpx.Response | Exception,
    status: int,
    code: str,
) -> None:
    sidecar.answer = answer

    response = client.get(f"/api/cases/{new_id()}/classifications", headers=CUSTOMER)

    assert response.status_code == status
    assert ErrorBody.model_validate(response.json()).error.code.value == code
    assert "secret" not in response.text
