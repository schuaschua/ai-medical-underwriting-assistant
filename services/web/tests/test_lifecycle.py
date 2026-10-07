"""Story 1.6: `web` starts a case in `workflow` and reads its progress and audit trail.

The Dapr sidecar is a fake here: a transport that records what `web` sent and
answers as `intake` and `workflow` would.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Iterator
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


def test_story_1_6_the_upload_itself_starts_nothing(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    # The caller starts the case with a call of its own, so a start that fails
    # can be tried again without sending the file again.
    client.post(
        "/api/cases",
        content=CASE_PDF.read_bytes(),
        headers={**CUSTOMER, "Content-Type": "application/pdf"},
    )

    assert [request.url.path.rsplit("/", 1)[-1] for request in sidecar.requests] == [
        "cases"
    ]
    assert sidecar.started == {}


def test_story_1_6_start_options_are_passed_on_as_given(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    case_id, eval_run_id = new_id(), new_id()
    options = {
        "classifier_contender": "doc-intelligence",
        "retriever_configs": ["r4", "r5"],
        "stop_after": "gate",
        "eval_run_id": eval_run_id,
    }

    response = client.post(
        f"/api/cases/{case_id}/start", json=options, headers=UNDERWRITER
    )

    assert response.status_code == 200
    assert response.json() == {"case_id": case_id, "case_status": "running", **options}
    assert json.loads(sidecar.requests[0].content) == {
        **options,
        "actor": "underwriter",
    }


@pytest.mark.parametrize(
    "options",
    [
        {"classifier_contender": "doc-intelligence"},
        {"retriever_configs": ["r4"]},
        {"stop_after": "gate"},
        {"eval_run_id": "019a0000-0000-7000-8000-000000000009"},
    ],
    ids=["classifier_contender", "retriever_configs", "stop_after", "eval_run_id"],
)
def test_story_1_6_the_customer_may_not_set_a_start_option(
    client: TestClient, sidecar: FakeSidecar, options: dict[str, object]
) -> None:
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


@pytest.mark.parametrize(
    "body", [None, {}, {"stop_after": None, "eval_run_id": None}], ids=str
)
def test_story_1_6_a_customer_start_without_options_is_let_through(
    client: TestClient, sidecar: FakeSidecar, body: dict[str, object] | None
) -> None:
    case_id = new_id()

    response = client.post(f"/api/cases/{case_id}/start", json=body, headers=CUSTOMER)

    assert response.status_code == 200
    # Nothing is passed on as an option, not even an explicit "not set":
    # only who asked (story 1.13).
    assert json.loads(sidecar.requests[0].content) == {"actor": "customer"}


def test_story_1_6_starting_twice_gives_the_same_answer(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    case_id = new_id()

    first = client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)
    second = client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()
    assert list(sidecar.started) == [case_id]


@pytest.mark.parametrize(
    "options",
    [{"classifier_contender": "guess"}, {"retriever_configs": []}, {"extra": True}],
)
def test_story_1_6_an_invalid_start_option_is_422_and_never_reaches_workflow(
    client: TestClient, sidecar: FakeSidecar, options: dict[str, object]
) -> None:
    response = client.post(
        f"/api/cases/{new_id()}/start", json=options, headers=UNDERWRITER
    )

    assert response.status_code == 422
    assert error_of(response)[0] == "validation_failed"
    assert sidecar.requests == []


@pytest.mark.parametrize("headers", [{}, {"X-Demo-Role": "admin"}])
def test_story_1_6_a_start_needs_a_demo_role(
    client: TestClient, sidecar: FakeSidecar, headers: dict[str, str]
) -> None:
    response = client.post(f"/api/cases/{new_id()}/start", headers=headers)

    assert response.status_code == 400
    assert error_of(response)[0] == "invalid_role"
    assert sidecar.requests == []


def test_story_1_6_the_underwriter_may_start_a_case_with_or_without_options(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    plain = client.post(f"/api/cases/{new_id()}/start", headers=UNDERWRITER)
    with_options = client.post(
        f"/api/cases/{new_id()}/start", json={"stop_after": "gate"}, headers=UNDERWRITER
    )

    assert (plain.status_code, with_options.status_code) == (200, 200)
    assert with_options.json()["stop_after"] == "gate"


class Trickle(httpx.AsyncByteStream):
    """An answer that never ends but never pauses for long either."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            await asyncio.sleep(0.02)
            yield b" "


def test_story_1_6_a_call_to_workflow_has_one_deadline_shorter_than_the_browsers(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # The order of the three deadlines: workflow 10 s < web 20 s < browser 30 s.
    deadline = Settings().lifecycle_timeout_seconds
    assert deadline == 20.0
    assert 10.0 < deadline < 30.0

    async def trickling(request: httpx.Request) -> httpx.Response:
        # Each phase stays within a per-phase timeout; only a deadline for
        # the whole call ends this.
        return httpx.Response(200, stream=Trickle())

    quick = settings.model_copy(update={"lifecycle_timeout_seconds": 0.3})
    app = create_app(quick, sidecar=httpx.MockTransport(trickling))
    started = time.monotonic()
    with (
        TestClient(app, raise_server_exceptions=False) as client,
        caplog.at_level(logging.ERROR, logger="web.adapters.dapr"),
    ):
        responses = [
            client.post(f"/api/cases/{new_id()}/start", headers=CUSTOMER),
            client.get(f"/api/cases/{new_id()}/progress", headers=CUSTOMER),
            client.get(f"/api/cases/{new_id()}/audit", headers=UNDERWRITER),
        ]

    for response in responses:
        assert response.status_code == 502
        assert error_of(response)[0] == "upstream_unavailable"
    assert time.monotonic() - started < 5
    assert "operation=start_case type=TimeoutError" in caplog.text


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ConnectError("secret-address 10.0.0.1"),
        httpx.ReadTimeout("secret-address 10.0.0.1"),
        httpx.Response(500, text="ERR_DIRECT_INVOKE secret-address"),
        httpx.Response(200, json={"case_id": "not-a-case"}),
        httpx.Response(200, text="not json"),
    ],
    ids=["refused", "timeout", "sidecar-500", "wrong-shape", "not-json"],
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


def test_story_1_6_an_error_of_workflows_own_is_not_passed_on_as_the_callers_fault(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    # `not_found` is not something a start may say; and a code under the
    # wrong status is no answer to trust.
    for answer in (
        error(ErrorCode.NOT_FOUND, "Said by workflow."),
        error(ErrorCode.INTERNAL_ERROR, "Said by workflow."),
        error(ErrorCode.VALIDATION_FAILED, "Said by workflow.", status=500),
    ):
        sidecar.answers["POST start"] = answer

        response = client.post(f"/api/cases/{new_id()}/start", headers=CUSTOMER)

        assert response.status_code == 502
        assert "Said by workflow." not in response.text


def test_story_1_6_workflows_refusal_of_the_start_options_is_passed_on(
    client: TestClient, sidecar: FakeSidecar, caplog: pytest.LogCaptureFixture
) -> None:
    sidecar.answers["POST start"] = error(
        ErrorCode.VALIDATION_FAILED, "The request is not valid."
    )

    with caplog.at_level(logging.INFO, logger="web.adapters.dapr"):
        response = client.post(f"/api/cases/{new_id()}/start", headers=CUSTOMER)

    assert response.status_code == 422
    assert error_of(response) == ("validation_failed", "The request is not valid.")
    (record,) = [r for r in caplog.records if r.name == "web.adapters.dapr"]
    assert record.levelno == logging.INFO
    assert record.getMessage() == (
        "request refused: service=workflow operation=start_case status=422 "
        "code=validation_failed"
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


@pytest.mark.parametrize("key", ["short", "x" * 65, "spaces are not allowed here"])
def test_story_1_6_a_malformed_idempotency_key_is_422_and_never_reaches_intake(
    client: TestClient, sidecar: FakeSidecar, key: str
) -> None:
    response = client.post(
        "/api/cases",
        content=CASE_PDF.read_bytes(),
        headers={**CUSTOMER, "Content-Type": "application/pdf", "Idempotency-Key": key},
    )

    assert response.status_code == 422
    assert error_of(response)[0] == "validation_failed"
    assert key not in response.text
    assert sidecar.requests == []


def test_story_1_6_intakes_refusal_of_a_reused_key_is_passed_on(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    message = "This upload key was already used for a different file."
    sidecar.answers["POST cases"] = error(ErrorCode.VALIDATION_FAILED, message)

    response = client.post(
        "/api/cases",
        content=CASE_PDF.read_bytes(),
        headers={**CUSTOMER, "Content-Type": "application/pdf", "Idempotency-Key": KEY},
    )

    assert response.status_code == 422
    assert error_of(response) == ("validation_failed", message)


# --- Progress and audit -----------------------------------------------------------


@pytest.mark.parametrize("role", [CUSTOMER, UNDERWRITER])
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


@pytest.mark.parametrize("role", [CUSTOMER, UNDERWRITER])
def test_story_1_9_routed_page_statuses_pass_through_as_workflow_answered(
    client: TestClient, sidecar: FakeSidecar, role: dict[str, str]
) -> None:
    case_id = new_id()
    client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)
    statuses = ["extracting", "awaiting_customer", "awaiting_triage"]
    answered = {
        "case_id": case_id,
        "case_status": "awaiting_human",
        "redaction_status": "done",
        "pages": [
            {
                "page_id": new_id(),
                "page_number": number,
                "page_status": status,
                "error_code": None,
            }
            for number, status in enumerate(statuses, start=1)
        ],
        "error_code": None,
    }
    sidecar.answers["GET progress"] = httpx.Response(200, json=answered)

    response = client.get(f"/api/cases/{case_id}/progress", headers=role)

    # `web` computes no route and no status: what `workflow` said is what the
    # SPA gets, field for field, with nothing about the gate added.
    assert response.status_code == 200
    assert response.json() == answered


def test_story_1_9_the_failure_reason_passes_through_on_the_case_and_the_page(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    case_id = new_id()
    client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)
    answered = {
        "case_id": case_id,
        "case_status": "failed",
        "redaction_status": "done",
        "pages": [
            {
                "page_id": new_id(),
                "page_number": 1,
                "page_status": "classified",
                "error_code": None,
            },
            {
                "page_id": new_id(),
                "page_number": 2,
                "page_status": "failed",
                "error_code": "invalid_model_output",
            },
        ],
        "error_code": "invalid_model_output",
    }
    sidecar.answers["GET progress"] = httpx.Response(200, json=answered)

    response = client.get(f"/api/cases/{case_id}/progress", headers=CUSTOMER)

    assert response.json() == answered
    # An answer of an older `workflow`, without the field, reads as "nothing failed".
    older = {**answered, "pages": []}
    del older["error_code"]
    sidecar.answers["GET progress"] = httpx.Response(200, json=older)
    assert (
        client.get(f"/api/cases/{case_id}/progress", headers=CUSTOMER).json()[
            "error_code"
        ]
        is None
    )
    # A code that is not in the catalogue is not passed on.
    sidecar.answers["GET progress"] = httpx.Response(
        200, json={**answered, "error_code": "it broke"}
    )
    refused = client.get(f"/api/cases/{case_id}/progress", headers=CUSTOMER)
    assert refused.status_code == 502
    assert "it broke" not in refused.text


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


def test_story_1_12_the_trail_of_an_unknown_case_is_404_and_a_malformed_id_is_refused(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    unknown = client.get(f"/api/cases/{new_id()}/audit", headers=UNDERWRITER)
    calls = len(sidecar.requests)
    malformed = client.get("/api/cases/not-a-case-id/audit", headers=UNDERWRITER)

    assert unknown.status_code == 404
    assert error_of(unknown)[0] == "not_found"
    assert malformed.status_code == 422
    assert error_of(malformed)[0] == "validation_failed"
    # The malformed id never reached `workflow`.
    assert len(sidecar.requests) == calls


def test_story_1_12_a_trail_that_is_not_the_contracts_shape_is_not_passed_on(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    case_id = new_id()
    client.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)
    # As an older `workflow` would answer: without the flag.
    sidecar.answers["GET audit"] = httpx.Response(
        200, json={"case_id": case_id, "events": []}
    )

    response = client.get(f"/api/cases/{case_id}/audit", headers=UNDERWRITER)

    assert response.status_code == 502
    assert error_of(response)[0] == "upstream_unavailable"


def test_story_1_6_progress_and_audit_of_an_unknown_case_are_404(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    case_id = new_id()

    progress = client.get(f"/api/cases/{case_id}/progress", headers=CUSTOMER)
    audit = client.get(f"/api/cases/{case_id}/audit", headers=UNDERWRITER)

    for response in (progress, audit):
        assert response.status_code == 404
        assert error_of(response) == ("not_found", "That case could not be found.")


@pytest.mark.parametrize(
    "bad_id", ["abc", "019a0000-0000-4000-8000-000000000001", "..%2F..%2Fhealth"]
)
def test_story_1_6_a_case_id_that_is_not_a_uuid7_never_reaches_workflow(
    client: TestClient, sidecar: FakeSidecar, bad_id: str
) -> None:
    responses = [
        client.post(f"/api/cases/{bad_id}/start", headers=CUSTOMER),
        client.get(f"/api/cases/{bad_id}/progress", headers=CUSTOMER),
        client.get(f"/api/cases/{bad_id}/audit", headers=UNDERWRITER),
    ]

    for response in responses:
        assert response.status_code in {404, 422}
        assert error_of(response)[0] in {"not_found", "validation_failed"}
    # The id goes into the path of the call to `workflow`: only a real id may.
    assert sidecar.requests == []


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ConnectError("secret-address 10.0.0.1"),
        httpx.Response(503, text="no healthy upstream"),
        httpx.Response(200, json={"case_id": "x", "events": "none"}),
    ],
    ids=["refused", "sidecar-503", "wrong-shape"],
)
def test_story_1_6_progress_and_audit_are_502_when_workflow_cannot_answer(
    client: TestClient, sidecar: FakeSidecar, answer: httpx.Response | Exception
) -> None:
    case_id = new_id()
    sidecar.answers["GET progress"] = answer
    sidecar.answers["GET audit"] = answer

    progress = client.get(f"/api/cases/{case_id}/progress", headers=CUSTOMER)
    audit = client.get(f"/api/cases/{case_id}/audit", headers=UNDERWRITER)

    for response in (progress, audit):
        assert response.status_code == 502
        assert error_of(response)[0] == "upstream_unavailable"
        assert "secret-address" not in response.text
