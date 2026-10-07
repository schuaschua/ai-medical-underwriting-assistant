"""Story 1.6: `web` starts a case in `workflow` and reads its progress and audit trail.

The Dapr sidecar is a fake here: a transport that records what `web` sent and
answers as `intake` and `workflow` would.
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient

from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import AuditTrail, CaseProgress, CaseStarted
from web.adapters.http.app import create_app
from web.settings import Settings

CASE_PDF = Path(__file__).resolve().parents[3] / "data" / "cases" / "case-001.pdf"
CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
KEY = "3f2b8a52-6c1d-4c43-9d0e-0a8f5a1b2c3d"
INVOKE = "http://127.0.0.1:3500/v1.0/invoke"


@dataclass
class FakeSidecar:
    """Stands in for the Dapr sidecar and, behind it, `intake` and `workflow`."""

    case_id: str = field(default_factory=new_id)
    document_id: str = field(default_factory=new_id)
    # The cases `workflow` has been asked to start, with what each runs with.
    started: dict[str, dict[str, object]] = field(default_factory=dict)
    # Story 1.13: the actor each case was first started by.
    started_by: dict[str, str] = field(default_factory=dict)
    # Answers that replace the usual one, by "<METHOD> <last path part>".
    answers: dict[str, httpx.Response | Exception] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = await request.aread()
        key = f"{request.method} {request.url.path.rsplit('/', 1)[-1]}"
        if key in self.answers:
            answer = self.answers[key]
            if isinstance(answer, Exception):
                raise answer
            return answer
        if key == "POST cases":
            return httpx.Response(
                201, json={"case_id": self.case_id, "document_id": self.document_id}
            )
        case_id = request.url.path.split("/")[-2]
        if key == "POST start":
            options = json.loads(body) if body else {}
            # Story 1.13: as `workflow` does, a start that names no demo
            # role is refused, and the first actor is the one kept.
            actor = options.pop("actor", None)
            if actor not in ("customer", "underwriter"):
                return error(ErrorCode.ACTOR_NOT_HUMAN, "Only a person may start.")
            self.started_by.setdefault(case_id, actor)
            case = self.started.setdefault(
                case_id,
                {
                    "case_id": case_id,
                    "case_status": "running",
                    "classifier_contender": "llm",
                    "retriever_configs": ["r3"],
                    "stop_after": None,
                    "eval_run_id": None,
                    **options,
                },
            )
            return httpx.Response(200, json=case)
        if case_id not in self.started:
            return error(ErrorCode.NOT_FOUND, "That case could not be found.")
        if key == "GET progress":
            return httpx.Response(
                200,
                json={
                    "case_id": case_id,
                    "case_status": "running",
                    "redaction_status": "running",
                    "pages": [],
                },
            )
        return httpx.Response(
            200, json={"case_id": case_id, "events": [], "has_more": False}
        )


def error(code: ErrorCode, message: str, status: int | None = None) -> httpx.Response:
    failure = DomainError(code, message)
    return httpx.Response(
        status or failure.http_status,
        json=failure.to_body(TRACE_ID).model_dump(mode="json"),
    )


@pytest.fixture
def sidecar() -> FakeSidecar:
    return FakeSidecar()


@pytest.fixture
def client(settings: Settings, sidecar: FakeSidecar) -> Iterator[TestClient]:
    app = create_app(settings, sidecar=httpx.MockTransport(sidecar))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def error_of(response: httpx2.Response) -> tuple[str, str]:
    detail = ErrorBody.model_validate(response.json()).error
    return detail.code.value, detail.message


# --- Upload, then start ----------------------------------------------------------


def test_story_1_6_an_upload_is_followed_by_a_start_of_that_case_in_workflow(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    uploaded = client.post(
        "/api/cases",
        content=CASE_PDF.read_bytes(),
        headers={**CUSTOMER, "Content-Type": "application/pdf"},
    ).json()

    response = client.post(
        f"/api/cases/{uploaded['case_id']}/start",
        headers={**CUSTOMER, "traceparent": TRACEPARENT},
    )

    assert response.status_code == 200
    started = CaseStarted.model_validate(response.json())
    assert (started.case_id, started.case_status.value) == (sidecar.case_id, "running")
    # AD-2: `web` asked `intake` to create the case, then `workflow` to start it.
    upload_call, start_call = sidecar.requests
    assert str(upload_call.url) == f"{INVOKE}/intake/method/cases"
    assert start_call.method == "POST"
    assert str(start_call.url) == (
        f"{INVOKE}/workflow/method/cases/{sidecar.case_id}/start"
    )
    # No option was given: none is sent, and `workflow` applies its defaults.
    # Story 1.13: the start names the demo role that asked for it.
    assert json.loads(start_call.content) == {"actor": "customer"}
    assert start_call.headers["traceparent"] == TRACEPARENT
    # The role is `web`'s business; internal services never read the header.
    assert "x-demo-role" not in start_call.headers
    assert response.headers["cache-control"] == "no-store"


def test_story_1_6_the_customer_may_not_set_a_start_option(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    for options in [
        {"classifier_contender": "doc-intelligence"},
        {"retriever_configs": ["r4"]},
        {"stop_after": "gate"},
        {"eval_run_id": "019a0000-0000-7000-8000-000000000009"},
    ]:
        case_id = new_id()

        response = client.post(
            f"/api/cases/{case_id}/start", json=options, headers=CUSTOMER
        )

        assert response.status_code == 403
        assert error_of(response) == (
            "role_not_allowed",
            "Start options are not open to your role.",
        )
        assert sidecar.requests == []


def test_story_1_6_starting_twice_gives_the_same_answer(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    case_id = new_id()

    first = client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)
    second = client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()
    assert list(sidecar.started) == [case_id]


def test_story_1_6_the_underwriter_may_start_a_case_with_or_without_options(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    plain = client.post(f"/api/cases/{new_id()}/start", headers=UNDERWRITER)
    with_options = client.post(
        f"/api/cases/{new_id()}/start", json={"stop_after": "gate"}, headers=UNDERWRITER
    )

    assert (plain.status_code, with_options.status_code) == (200, 200)
    assert with_options.json()["stop_after"] == "gate"


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ConnectError("secret-address 10.0.0.1"),
        httpx.Response(200, json={"case_id": "not-a-case"}),
    ],
    ids=["refused", "wrong-shape"],
)
def test_story_1_6_a_start_that_fails_is_502_so_the_caller_can_try_again(
    client: TestClient,
    sidecar: FakeSidecar,
    answer: httpx.Response | Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case_id = new_id()
    sidecar.answers["POST start"] = answer

    with caplog.at_level(logging.ERROR, logger="web.adapters.dapr"):
        response = client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)

    assert response.status_code == 502
    assert error_of(response) == (
        "upstream_unavailable",
        "The service is not available right now. Please try again.",
    )
    assert "secret-address" not in response.text
    # security rule 31: service, operation and a type or status; no message.
    assert "service call failed: service=workflow operation=start_case" in caplog.text
    assert "secret-address" not in caplog.text

    # The retry: the same call, once `workflow` answers again.
    del sidecar.answers["POST start"]
    assert (
        client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER).status_code == 200
    )


# --- The idempotency key of an upload ---------------------------------------------


def test_story_1_6_the_upload_idempotency_key_is_passed_on_to_intake(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    headers = {**CUSTOMER, "Content-Type": "application/pdf"}

    with_key = client.post(
        "/api/cases",
        content=CASE_PDF.read_bytes(),
        headers={**headers, "Idempotency-Key": KEY},
    )
    without_key = client.post(
        "/api/cases", content=CASE_PDF.read_bytes(), headers=headers
    )

    assert (with_key.status_code, without_key.status_code) == (201, 201)
    first, second = sidecar.requests
    assert first.headers["idempotency-key"] == KEY
    assert "idempotency-key" not in second.headers


# --- Progress and audit -----------------------------------------------------------


@pytest.mark.parametrize("role", [CUSTOMER])
def test_story_1_6_progress_is_read_from_workflow_by_either_role(
    client: TestClient, sidecar: FakeSidecar, role: dict[str, str]
) -> None:
    case_id = new_id()
    client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)

    response = client.get(
        f"/api/cases/{case_id}/progress", headers={**role, "traceparent": TRACEPARENT}
    )

    assert response.status_code == 200
    progress = CaseProgress.model_validate(response.json())
    assert (progress.case_id, progress.case_status.value) == (case_id, "running")
    assert progress.pages == []
    call = sidecar.requests[-1]
    assert (call.method, str(call.url)) == (
        "GET",
        f"{INVOKE}/workflow/method/cases/{case_id}/progress",
    )
    assert call.headers["traceparent"] == TRACEPARENT
    assert response.headers["cache-control"] == "no-store"


def test_story_1_6_the_audit_trail_is_read_from_workflow_by_the_underwriter_only(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    case_id = new_id()
    client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)
    calls = len(sidecar.requests)

    refused = client.get(f"/api/cases/{case_id}/audit", headers=CUSTOMER)
    assert refused.status_code == 403
    assert error_of(refused)[0] == "role_not_allowed"
    assert client.get(f"/api/cases/{case_id}/audit").status_code == 400
    # Neither call reached `workflow`.
    assert len(sidecar.requests) == calls

    response = client.get(f"/api/cases/{case_id}/audit", headers=UNDERWRITER)

    assert response.status_code == 200
    trail = AuditTrail.model_validate(response.json())
    assert (trail.case_id, trail.events) == (case_id, [])
    assert str(sidecar.requests[-1].url) == (
        f"{INVOKE}/workflow/method/cases/{case_id}/audit"
    )


def test_story_1_12_the_trail_passes_through_with_its_error_code_and_its_more_flag(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    case_id, page_id = new_id(), new_id()
    client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)

    def event(action: str, actor: str, **changes: object) -> dict[str, object]:
        return {
            "actor_kind": "ai",
            "actor": actor,
            "action": action,
            "occurred_at": "2026-10-07T09:00:00Z",
            "case_id": case_id,
            "page_id": page_id,
            "ref": new_id(),
            "detail": None,
            "trace_id": TRACE_ID,
            "eval_run_id": None,
            "error_code": None,
            **changes,
        }

    answered = {
        "case_id": case_id,
        "events": [
            event(
                "document.redacted",
                "intake:azure-ai-language",
                page_id=None,
                detail={"Person": 2, "PhoneNumber": 1},
            ),
            # Named an earlier time than the event before it: `web` keeps
            # the order `workflow` answered in.
            event(
                "page.routed",
                "workflow:gate",
                occurred_at="2026-10-07T08:59:00Z",
                detail={"route": "awaiting_triage", "threshold": 0.9},
            ),
            event(
                "stage.failed",
                "classification:chat-main",
                error_code="model_unavailable",
            ),
        ],
        "has_more": True,
    }
    sidecar.answers["GET audit"] = httpx.Response(200, json=answered)

    response = client.get(f"/api/cases/{case_id}/audit", headers=UNDERWRITER)

    assert response.status_code == 200
    assert response.json() == answered
    trail = AuditTrail.model_validate(response.json())
    assert trail.has_more is True
    assert [event.error_code for event in trail.events] == [
        None,
        None,
        ErrorCode.MODEL_UNAVAILABLE,
    ]
    assert response.headers["cache-control"] == "no-store"
