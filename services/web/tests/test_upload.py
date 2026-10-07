"""Story 1.5: `POST /api/cases` checks the upload and hands it to `intake` through Dapr.

The Dapr sidecar is a fake here: a transport that records what `web` sent and
answers as `intake` would.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import format_trace_id

from contracts.enums import Service
from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.web import UploadedCase
from contracts.upload import MAX_UPLOAD_BYTES
from web.adapters import dapr
from web.adapters.dapr import invoke_path, sidecar_base_url, trace_headers
from web.adapters.http.app import create_app
from web.adapters.http.errors import framework_error, is_api_path
from web.settings import Settings

CASE_PDF = Path(__file__).resolve().parents[3] / "data" / "cases" / "case-001.pdf"
CUSTOMER = {"X-Demo-Role": "customer", "Content-Type": "application/pdf"}
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"

Answer = Callable[[httpx.Request], Awaitable[httpx.Response]]


@dataclass
class FakeSidecar:
    """Stands in for the Dapr sidecar and, behind it, `intake`."""

    case_id: str = field(default_factory=new_id)
    document_id: str = field(default_factory=new_id)
    answer: Answer | None = None
    # Every call that arrived, and the bodies that arrived whole.
    requests: list[httpx.Request] = field(default_factory=list)
    bodies: list[bytes] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(await request.aread())
        if self.answer is not None:
            return await self.answer(request)
        return httpx.Response(
            201, json={"case_id": self.case_id, "document_id": self.document_id}
        )


def answering(status: int, **kwargs: object) -> Answer:
    async def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, **kwargs)  # type: ignore[arg-type]  # test helper: passes json= or text= through

    return answer


def intake_error(code: ErrorCode, message: str) -> Answer:
    error = DomainError(code, message)
    return answering(
        error.http_status, json=error.to_body(TRACE_ID).model_dump(mode="json")
    )


@pytest.fixture
def case_pdf() -> bytes:
    return CASE_PDF.read_bytes()


@pytest.fixture
def sidecar() -> FakeSidecar:
    return FakeSidecar()


@pytest.fixture
def client(settings: Settings, sidecar: FakeSidecar) -> Iterator[TestClient]:
    app = create_app(settings, sidecar=httpx.MockTransport(sidecar))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def error_of(response_json: object) -> tuple[str, str, str]:
    detail = ErrorBody.model_validate(response_json).error
    return detail.code.value, detail.message, detail.trace_id


# --- The upload ---------------------------------------------------------------


def test_story_1_5_customer_upload_creates_a_case_through_the_sidecar(
    client: TestClient, sidecar: FakeSidecar, case_pdf: bytes
) -> None:
    response = client.post(
        "/api/cases", content=case_pdf, headers={**CUSTOMER, "traceparent": TRACEPARENT}
    )

    assert response.status_code == 201
    uploaded = UploadedCase.model_validate(response.json())
    assert (uploaded.case_id, uploaded.document_id) == (
        sidecar.case_id,
        sidecar.document_id,
    )
    # The answer carries no status: the case is started by a call of its own
    # (story 1.6), and `workflow` reports its status from then on.
    assert set(response.json()) == {"case_id", "document_id"}
    assert response.headers["cache-control"] == "no-store"

    # One call, to `intake` by its Dapr app id, on loopback (AD-3).
    (request,) = sidecar.requests
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:3500/v1.0/invoke/intake/method/cases"
    assert request.headers["content-type"] == "application/pdf"
    assert request.headers["content-length"] == str(len(case_pdf))
    # One trace across services: the caller's trace context is passed on.
    assert request.headers["traceparent"] == TRACEPARENT
    # The role is `web`'s business; internal services never read the header.
    assert "x-demo-role" not in request.headers
    # The file arrived byte for byte.
    assert sidecar.bodies == [case_pdf]


def test_story_1_5_the_active_span_is_passed_on_to_the_sidecar() -> None:
    assert trace_headers(None) == {}
    assert trace_headers(TRACEPARENT) == {"traceparent": TRACEPARENT}

    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("request") as span:
        headers = trace_headers(TRACEPARENT)

    trace_id = format_trace_id(span.get_span_context().trace_id)
    assert re.fullmatch(
        rf"00-{trace_id}-[0-9a-f]{{16}}-[0-9a-f]{{2}}", headers["traceparent"]
    )


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({"X-Demo-Role": "underwriter"}, 403, "role_not_allowed"),
        ({}, 400, "invalid_role"),
        ({"X-Demo-Role": "admin"}, 400, "invalid_role"),
    ],
)
def test_story_1_5_only_the_customer_may_upload(
    client: TestClient,
    sidecar: FakeSidecar,
    case_pdf: bytes,
    headers: dict[str, str],
    status: int,
    code: str,
) -> None:
    response = client.post(
        "/api/cases",
        content=case_pdf,
        headers={**headers, "Content-Type": "application/pdf"},
    )

    assert response.status_code == status
    assert error_of(response.json())[0] == code
    assert sidecar.requests == []


def test_story_1_5_a_pdf_over_ten_megabytes_is_413_and_never_reaches_intake(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    response = client.post(
        "/api/cases", content=b"%PDF-1.7\n" + b"x" * MAX_UPLOAD_BYTES, headers=CUSTOMER
    )

    assert response.status_code == 413
    assert error_of(response.json())[0] == "file_too_large"
    assert sidecar.requests == []


def test_story_1_5_a_pdf_of_exactly_ten_megabytes_is_passed_on(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    at_limit = b"%PDF-" + b"x" * (MAX_UPLOAD_BYTES - 5)

    response = client.post("/api/cases", content=at_limit, headers=CUSTOMER)

    assert response.status_code == 201
    assert [len(body) for body in sidecar.bodies] == [MAX_UPLOAD_BYTES]


def chunks_over_the_limit() -> Iterator[bytes]:
    yield b"%PDF-1.7\n"
    for _ in range(11):
        yield b"x" * (1024 * 1024)


def test_story_1_5_a_body_with_no_declared_length_is_cut_off_at_the_limit(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    # A generator body is sent chunked: there is no Content-Length to check.
    response = client.post(
        "/api/cases", content=chunks_over_the_limit(), headers=CUSTOMER
    )

    assert response.status_code == 413
    assert error_of(response.json())[0] == "file_too_large"
    # The call to `intake` was begun and broken off: no whole body arrived.
    assert sidecar.bodies == []


def test_story_1_5_a_chunked_body_within_the_limit_is_passed_on_whole(
    client: TestClient, sidecar: FakeSidecar, case_pdf: bytes
) -> None:
    def chunks() -> Iterator[bytes]:
        for start in range(0, len(case_pdf), 3):
            yield case_pdf[start : start + 3]

    response = client.post("/api/cases", content=chunks(), headers=CUSTOMER)

    assert response.status_code == 201
    assert sidecar.bodies == [case_pdf]
    assert "content-length" not in sidecar.requests[0].headers


@pytest.mark.parametrize(
    "content",
    [b"plain text, renamed to report.pdf", b"PK\x03\x04 a zip", b"%PD", b"x"],
)
def test_story_1_5_content_that_is_no_pdf_is_415_and_never_reaches_intake(
    client: TestClient, sidecar: FakeSidecar, content: bytes
) -> None:
    # The declared type says PDF; the content decides.
    response = client.post("/api/cases", content=content, headers=CUSTOMER)

    assert response.status_code == 415
    assert error_of(response.json())[0] == "unsupported_file_type"
    assert sidecar.requests == []


@pytest.mark.parametrize("content_type", ["text/plain", "multipart/form-data", None])
def test_story_1_5_a_body_not_declared_as_pdf_is_415(
    client: TestClient,
    sidecar: FakeSidecar,
    case_pdf: bytes,
    content_type: str | None,
) -> None:
    headers = {"X-Demo-Role": "customer"}
    if content_type is not None:
        headers["Content-Type"] = content_type

    response = client.post("/api/cases", content=case_pdf, headers=headers)

    assert response.status_code == 415
    assert error_of(response.json())[0] == "unsupported_media_type"
    assert sidecar.requests == []


def test_story_1_5_an_empty_body_is_422(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    response = client.post(
        "/api/cases", content=b"", headers={**CUSTOMER, "traceparent": TRACEPARENT}
    )

    assert response.status_code == 422
    code, _, trace_id = error_of(response.json())
    assert (code, trace_id) == ("validation_failed", TRACE_ID)
    assert sidecar.requests == []


def test_story_1_5_an_empty_chunked_body_is_422(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    response = client.post("/api/cases", content=iter([b""]), headers=CUSTOMER)

    assert response.status_code == 422
    assert sidecar.requests == []


# --- When `intake` or the sidecar says no -------------------------------------


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (ErrorCode.FILE_TOO_LARGE, 413),
        (ErrorCode.PAYLOAD_TOO_LARGE, 413),
        (ErrorCode.UNSUPPORTED_FILE_TYPE, 415),
        (ErrorCode.UNSUPPORTED_MEDIA_TYPE, 415),
        (ErrorCode.VALIDATION_FAILED, 422),
    ],
)
def test_story_1_5_an_upload_intake_refuses_is_refused_with_its_code(
    client: TestClient,
    sidecar: FakeSidecar,
    case_pdf: bytes,
    code: ErrorCode,
    status: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sidecar.answer = intake_error(code, "Said by intake.")

    with caplog.at_level(logging.INFO, logger="web.adapters.dapr"):
        response = client.post("/api/cases", content=case_pdf, headers=CUSTOMER)

    assert response.status_code == status
    assert error_of(response.json())[:2] == (code.value, "Said by intake.")
    # The user's file was refused; the service is fine. Not an error line.
    (record,) = [r for r in caplog.records if r.name == "web.adapters.dapr"]
    assert record.levelno == logging.INFO
    assert record.getMessage() == (
        f"upload refused: service=intake operation=create_case status={status} "
        f"code={code.value}"
    )


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (ErrorCode.FILE_TOO_LARGE, 500),
        (ErrorCode.UNSUPPORTED_FILE_TYPE, 413),
        (ErrorCode.VALIDATION_FAILED, 502),
        (ErrorCode.PAYLOAD_TOO_LARGE, 400),
        (ErrorCode.UNSUPPORTED_MEDIA_TYPE, 503),
    ],
)
def test_story_1_5_a_refusal_code_under_the_wrong_status_is_not_passed_on(
    client: TestClient,
    sidecar: FakeSidecar,
    case_pdf: bytes,
    code: ErrorCode,
    status: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = DomainError(code, "Said by something.").to_body(TRACE_ID)
    sidecar.answer = answering(status, json=body.model_dump(mode="json"))

    response = client.post("/api/cases", content=case_pdf, headers=CUSTOMER)

    assert response.status_code == 502
    assert error_of(response.json())[0] == "upstream_unavailable"
    assert "Said by something." not in response.text
    (record,) = [r for r in caplog.records if r.name == "web.adapters.dapr"]
    assert record.levelno == logging.ERROR
    assert f"status={status} code={code.value}" in record.getMessage()


@pytest.mark.parametrize("status", [200, 202])
def test_story_1_5_any_2xx_with_the_created_case_is_a_created_case(
    client: TestClient, sidecar: FakeSidecar, case_pdf: bytes, status: int
) -> None:
    sidecar.answer = answering(
        status, json={"case_id": sidecar.case_id, "document_id": sidecar.document_id}
    )

    response = client.post("/api/cases", content=case_pdf, headers=CUSTOMER)

    assert response.status_code == 201
    assert response.json()["case_id"] == sidecar.case_id


def test_story_1_5_an_upstream_5xx_is_logged_as_an_error(
    client: TestClient,
    sidecar: FakeSidecar,
    case_pdf: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sidecar.answer = intake_error(ErrorCode.UPSTREAM_UNAVAILABLE, "Not stored.")

    client.post("/api/cases", content=case_pdf, headers=CUSTOMER)

    (record,) = [r for r in caplog.records if r.name == "web.adapters.dapr"]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == (
        "service call failed: service=intake operation=create_case status=502 "
        "code=upstream_unavailable"
    )


# --- Deadlines ----------------------------------------------------------------


def test_story_1_5_the_upload_timeout_setting_reaches_the_transport(
    settings: Settings, sidecar: FakeSidecar, case_pdf: bytes
) -> None:
    patient = settings.model_copy(
        update={"upload_timeout_seconds": 77.0, "service_timeout_seconds": 5.0}
    )
    app = create_app(patient, sidecar=httpx.MockTransport(sidecar))

    with TestClient(app) as client:
        client.post("/api/cases", content=case_pdf, headers=CUSTOMER)

    (request,) = sidecar.requests
    # Not the 5 seconds other calls get.
    assert request.extensions["timeout"] == {
        "connect": 77.0,
        "read": 77.0,
        "write": 77.0,
        "pool": 77.0,
    }


def test_story_1_5_the_whole_call_to_intake_has_one_deadline(
    settings: Settings,
    sidecar: FakeSidecar,
    case_pdf: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        # Never idle for long in any one phase, yet far too slow overall. A
        # mock transport does not apply httpx's own per-phase timeout.
        await asyncio.sleep(5)
        return httpx.Response(201, json={})

    sidecar.answer = slow
    impatient = settings.model_copy(update={"upload_timeout_seconds": 0.1})
    app = create_app(impatient, sidecar=httpx.MockTransport(sidecar))
    started = time.monotonic()

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/cases", content=case_pdf, headers=CUSTOMER)

    assert response.status_code == 502
    assert error_of(response.json())[0] == "upstream_unavailable"
    assert time.monotonic() - started < 3
    assert "operation=create_case type=TimeoutError" in caplog.text


def test_story_1_5_webs_upload_deadline_is_longer_than_intakes() -> None:
    # The order the settings comment states: intake 90 s < web 120 s < browser 150 s.
    assert Settings().upload_timeout_seconds == 120.0


# --- Headers that are not what they should be ---------------------------------


@pytest.mark.parametrize(
    "traceparent",
    [
        "not-a-traceparent",
        "00-XYZ-b7ad6b7169203331-01",
        f"00-{TRACE_ID}-b7ad6b7169203331-01; DROP",
        "tr\u00e4ce",
        "",
    ],
)
def test_story_1_5_a_malformed_traceparent_is_not_passed_on(
    client: TestClient, sidecar: FakeSidecar, case_pdf: bytes, traceparent: str
) -> None:
    response = client.post(
        "/api/cases",
        content=case_pdf,
        # As bytes throughout: the value need not be text a header may hold.
        headers=[
            *((name.encode(), value.encode()) for name, value in CUSTOMER.items()),
            (b"traceparent", traceparent.encode("latin-1")),
        ],
    )

    assert response.status_code == 201
    assert "traceparent" not in sidecar.requests[0].headers
    assert trace_headers(traceparent) == {}


def post_raw(app: FastAPI, headers: list[tuple[bytes, bytes]], body: bytes) -> int:
    """Call the app without an HTTP client, which would refuse to send such headers."""
    answer: dict[str, int] = {}

    async def call() -> None:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/cases",
            "raw_path": b"/api/cases",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"x-demo-role", b"customer"),
                (b"content-type", b"application/pdf"),
                *headers,
            ],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8000),
        }

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, object]) -> None:
            if message["type"] == "http.response.start":
                answer["status"] = int(str(message["status"]))

        await app(scope, receive, send)  # type: ignore[arg-type]  # plain dicts stand in for the ASGI types

    asyncio.run(call())
    return answer["status"]


@pytest.mark.parametrize(
    "value", ["-1", "+5", "1.5", "ten", "1e3", "9" * 5000, "\u00b2"]
)
def test_story_1_5_a_malformed_content_length_is_422_not_500(
    settings: Settings, sidecar: FakeSidecar, value: str
) -> None:
    app = create_app(settings, sidecar=httpx.MockTransport(sidecar))

    status = post_raw(app, [(b"content-length", value.encode("latin-1"))], b"%PDF-1.7")

    assert status == 422
    assert sidecar.requests == []


@pytest.mark.parametrize("declared", [6, 7, 9, 5000])
def test_story_1_5_a_body_that_differs_from_its_declared_length_is_422(
    client: TestClient, sidecar: FakeSidecar, declared: int
) -> None:
    response = client.post(
        "/api/cases",
        content=b"%PDF-1.7",
        headers={**CUSTOMER, "Content-Length": str(declared)},
    )

    assert response.status_code == 422
    assert error_of(response.json())[0] == "validation_failed"
    # The call to `intake` was not made, or was broken off: no whole body arrived.
    assert sidecar.bodies == []


@pytest.mark.parametrize(
    "answer",
    [
        intake_error(
            ErrorCode.UPSTREAM_UNAVAILABLE, "The document could not be stored."
        ),
        intake_error(ErrorCode.INTERNAL_ERROR, "Something went wrong."),
        intake_error(ErrorCode.NOT_FOUND, "Not found."),
        # The sidecar's own error format, when it cannot reach the app.
        answering(
            500, json={"errorCode": "ERR_DIRECT_INVOKE", "message": "secret-host"}
        ),
        answering(502, text="<html>secret-host bad gateway</html>"),
        # A success that is not the contract's payload.
        answering(201, json={"case_id": "not-an-id"}),
        answering(201, text=""),
        answering(200, json={}),
    ],
    ids=[
        "intake-502",
        "intake-500",
        "intake-404",
        "sidecar-error",
        "html",
        "bad-payload",
        "empty-201",
        "wrong-status",
    ],
)
def test_story_1_5_any_other_failure_of_the_call_is_502_upstream_unavailable(
    client: TestClient,
    sidecar: FakeSidecar,
    case_pdf: bytes,
    answer: Answer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sidecar.answer = answer

    response = client.post(
        "/api/cases", content=case_pdf, headers={**CUSTOMER, "traceparent": TRACEPARENT}
    )

    assert response.status_code == 502
    code, _, trace_id = error_of(response.json())
    assert (code, trace_id) == ("upstream_unavailable", TRACE_ID)
    assert "secret-host" not in response.text
    assert "secret-host" not in caplog.text
    assert "service=intake operation=create_case" in caplog.text


def test_story_1_5_an_unreachable_sidecar_is_502_and_its_address_is_not_logged(
    client: TestClient,
    sidecar: FakeSidecar,
    case_pdf: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to secret-host:3500")

    sidecar.answer = refuse

    response = client.post("/api/cases", content=case_pdf, headers=CUSTOMER)

    assert response.status_code == 502
    assert error_of(response.json())[0] == "upstream_unavailable"
    assert "type=ConnectError" in caplog.text
    assert "secret-host" not in caplog.text
    assert "secret-host" not in response.text


def test_story_1_5_logs_carry_no_file_name_and_no_content(
    client: TestClient, case_pdf: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/cases",
            content=case_pdf,
            headers={
                **CUSTOMER,
                "Content-Disposition": 'attachment; filename="jane-doe.pdf"',
            },
        )
        client.post("/api/cases", content=b"SSN 000-12-3456", headers=CUSTOMER)

    assert "jane-doe" not in caplog.text
    assert "000-12-3456" not in caplog.text
    assert "%PDF" not in caplog.text


# --- Methods, the route table and the framework's own errors ------------------


@pytest.mark.parametrize("method", ["PUT", "GET", "PATCH", "DELETE"])
def test_story_1_5_wrong_method_on_cases_is_405_in_the_error_shape(
    client: TestClient, sidecar: FakeSidecar, method: str
) -> None:
    response = client.request(method, "/api/cases", headers=CUSTOMER)

    assert response.status_code == 405
    assert response.headers["content-type"] == "application/json"
    assert error_of(response.json())[0] == "method_not_allowed"
    assert sidecar.requests == []


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_story_1_5_an_unknown_api_path_is_still_404_under_any_method(
    client: TestClient, method: str
) -> None:
    response = client.request(method, "/api/cases/x/nope", headers=CUSTOMER)

    assert response.status_code == 404
    assert error_of(response.json())[0] == "not_found"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (404, "not_found"),
        (405, "method_not_allowed"),
        (413, "payload_too_large"),
        (415, "unsupported_media_type"),
        (429, "too_many_requests"),
        (418, "validation_failed"),
        (503, "internal_error"),
    ],
)
def test_story_1_5_framework_errors_are_answered_with_their_own_code(
    status: int, code: str
) -> None:
    error = framework_error(status)

    assert error.code.value == code
    # The four statuses that had no code of their own keep their status now.
    if status in {404, 405, 413, 415, 429}:
        assert error.http_status == status


# GET and HEAD of health share one function, which only the schema builder minds.
@pytest.mark.filterwarnings("ignore:Duplicate Operation ID")
def test_story_1_5_no_route_returns_a_document_file(settings: Settings) -> None:
    app = create_app(settings)
    paths = get_openapi(title="web", version="0", routes=app.routes)["paths"]
    api_routes = {
        (method.upper(), path)
        for path, methods in paths.items()
        for method in methods
        if is_api_path(path)
    }

    # The whole API: nothing in it reads a document, let alone the original.
    assert api_routes == {
        ("GET", "/api/health"),
        ("HEAD", "/api/health"),
        ("GET", "/api/me"),
        ("POST", "/api/cases"),
        # Story 1.6: the case's lifecycle, read from and started in `workflow`.
        ("POST", "/api/cases/{case_id}/start"),
        ("GET", "/api/cases/{case_id}/progress"),
        ("GET", "/api/cases/{case_id}/audit"),
        # Story 1.10: what the classifier said of the pages, and a person's
        # decision about one. Neither returns a file.
        ("GET", "/api/cases/{case_id}/classifications"),
        ("POST", "/api/cases/{case_id}/pages/{page_id}/decisions"),
        # Story 1.11: the underwriter's queue, and the thumbnail `intake`
        # made of a redacted page. The one picture served is of the redacted
        # page; still nothing returns a document or anything of an original.
        ("GET", "/api/triage"),
        ("GET", "/api/pages/{page_id}/thumbnail"),
    }


def test_story_1_5_asking_for_the_original_is_404(
    client: TestClient, sidecar: FakeSidecar, case_pdf: bytes
) -> None:
    uploaded = client.post("/api/cases", content=case_pdf, headers=CUSTOMER).json()
    case_id, document_id = uploaded["case_id"], uploaded["document_id"]
    calls_for_the_upload = len(sidecar.requests)

    for path in (
        f"/api/cases/{case_id}",
        f"/api/cases/{case_id}/original",
        f"/api/documents/{document_id}",
        f"/api/documents/{document_id}/file",
        f"/api/documents/{document_id}/original",
        f"/api/originals/{case_id}/{document_id}.pdf",
    ):
        response = client.get(path, headers={"X-Demo-Role": "customer"})
        assert response.status_code == 404, path
        assert error_of(response.json())[0] == "not_found"
        assert b"%PDF" not in response.content
    # And no such request was passed on to another service.
    assert len(sidecar.requests) == calls_for_the_upload


# --- Settings and the client module -------------------------------------------


def test_story_1_5_the_sidecar_port_comes_from_dapr_or_from_web_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DAPR_HTTP_PORT", raising=False)
    monkeypatch.delenv("WEB_DAPR_HTTP_PORT", raising=False)
    assert Settings().dapr_http_port == 3500

    # Dapr tells the app its sidecar's port in this variable.
    monkeypatch.setenv("DAPR_HTTP_PORT", "3511")
    assert Settings().dapr_http_port == 3511
    assert sidecar_base_url(Settings()) == "http://127.0.0.1:3511"

    monkeypatch.setenv("WEB_DAPR_HTTP_PORT", "3522")
    assert Settings().dapr_http_port == 3522


def test_story_1_5_services_are_addressed_by_dapr_app_id_only() -> None:
    assert invoke_path(Service.INTAKE, "/cases") == "/v1.0/invoke/intake/method/cases"
    # AD-3: no Dapr SDK, and no other service's hostname anywhere in the module.
    source = Path(dapr.__file__).read_text()
    assert "import dapr" not in source
    assert "from dapr" not in source
    assert re.findall(r"https?://[^\s\"']+", source) == [
        "http://127.0.0.1:{settings.dapr_http_port}"
    ]


def test_story_1_5_only_the_dapr_module_makes_http_calls() -> None:
    package = Path(dapr.__file__).resolve().parents[1]
    importers = sorted(
        str(path.relative_to(package))
        for path in package.rglob("*.py")
        if re.search(r"^\s*(import|from) httpx", path.read_text(), flags=re.MULTILINE)
    )

    # The app factory only names the transport type it hands to that module.
    assert importers == ["adapters/dapr.py", "adapters/http/app.py"]
