"""Story 1.5: `POST /api/cases` checks the upload and hands it to `intake` through Dapr.

The Dapr sidecar is a fake here: a transport that records what `web` sent and
answers as `intake` would.
"""

import logging
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from fastapi.openapi.utils import get_openapi
from fastapi.testclient import TestClient

from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.web import UploadedCase
from contracts.upload import MAX_UPLOAD_BYTES
from web.adapters.http.app import create_app
from web.adapters.http.errors import is_api_path
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


def test_story_1_5_only_the_customer_may_upload(
    client: TestClient, sidecar: FakeSidecar, case_pdf: bytes
) -> None:
    for headers, status, code in [
        ({"X-Demo-Role": "underwriter"}, 403, "role_not_allowed"),
        ({}, 400, "invalid_role"),
        ({"X-Demo-Role": "admin"}, 400, "invalid_role"),
    ]:
        response = client.post(
            "/api/cases",
            content=case_pdf,
            headers={**headers, "Content-Type": "application/pdf"},
        )

        assert response.status_code == status
        assert error_of(response.json())[0] == code
        assert sidecar.requests == []


def test_story_1_5_the_ten_megabyte_limit_holds_to_the_byte_before_intake_is_called(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    at_limit = b"%PDF-" + b"x" * (MAX_UPLOAD_BYTES - 5)

    passed = client.post("/api/cases", content=at_limit, headers=CUSTOMER)
    refused = client.post("/api/cases", content=at_limit + b"x", headers=CUSTOMER)

    assert passed.status_code == 201
    assert refused.status_code == 413
    assert error_of(refused.json())[0] == "file_too_large"
    # Only the file within the limit reached `intake`.
    assert [len(body) for body in sidecar.bodies] == [MAX_UPLOAD_BYTES]
    assert len(sidecar.requests) == 1


def test_story_1_5_content_that_is_no_pdf_is_415_and_never_reaches_intake(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    for content in [
        b"plain text, renamed to report.pdf",
        b"PK\x03\x04 a zip",
        b"%PD",
        b"x",
    ]:
        response = client.post("/api/cases", content=content, headers=CUSTOMER)

        assert response.status_code == 415
        assert error_of(response.json())[0] == "unsupported_file_type"
        assert sidecar.requests == []


# --- When `intake` or the sidecar says no -------------------------------------


@pytest.mark.parametrize(("code", "status"), [(ErrorCode.FILE_TOO_LARGE, 413)])
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


# --- Deadlines ----------------------------------------------------------------


# --- Headers that are not what they should be ---------------------------------


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


# GET and HEAD of health share one function, which only the schema builder minds.
@pytest.mark.filterwarnings("ignore:Duplicate Operation ID")
def test_story_1_5_no_route_returns_an_original_and_asking_for_one_is_404(
    settings: Settings, client: TestClient, sidecar: FakeSidecar, case_pdf: bytes
) -> None:
    app = create_app(settings)
    paths = get_openapi(title="web", version="0", routes=app.routes)["paths"]
    api_routes = {
        (method.upper(), path)
        for path, methods in paths.items()
        for method in methods
        if is_api_path(path)
    }

    # The whole API: the one document it returns is the redacted PDF, and
    # nothing in it reads an original.
    assert api_routes == {
        ("GET", "/api/health"),
        ("HEAD", "/api/health"),
        ("GET", "/api/me"),
        ("POST", "/api/cases"),
        # Story 1.13: the underwriter's list of cases: ids, statuses and counts.
        ("GET", "/api/cases"),
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
        # Story 2.7: the underwriter's result view. The file is `intake`'s
        # redacted PDF (AD-21): `intake` has no route for an original, and
        # `web` calls no other for a file.
        ("GET", "/api/cases/{case_id}/facts"),
        ("GET", "/api/cases/{case_id}/verdict-runs"),
        ("GET", "/api/rules/{rule_id}"),
        ("GET", "/api/cases/{case_id}/pages"),
        ("GET", "/api/pages/{page_id}/boxes"),
        ("GET", "/api/documents/{document_id}/file"),
        # Story 2.8: the agent's log, read only. Neither returns a file.
        ("GET", "/api/verdict-runs/{verdict_run_id}/steps"),
        ("GET", "/api/cases/{case_id}/agent-steps"),
        # Story 3.4: the bake-off runner's eval search and its read of a
        # page's text, which is the redacted page's (AD-21).
        ("POST", "/api/searches"),
        ("GET", "/api/pages/{page_id}/text"),
        # Story 3.5: the two scoreboard files, read only (AD-17): figures
        # and counts, no document and no page text. No route takes a score.
        ("GET", "/api/scoreboards/retrieval"),
        ("GET", "/api/scoreboards/redaction"),
    }

    # Asking for the original, under any name one might try, is 404.
    uploaded = client.post("/api/cases", content=case_pdf, headers=CUSTOMER).json()
    case_id, document_id = uploaded["case_id"], uploaded["document_id"]
    calls_for_the_upload = len(sidecar.requests)

    for path in (
        f"/api/cases/{case_id}",
        f"/api/cases/{case_id}/original",
        f"/api/documents/{document_id}",
        f"/api/documents/{document_id}/original",
        f"/api/originals/{case_id}/{document_id}.pdf",
    ):
        response = client.get(path, headers={"X-Demo-Role": "customer"})
        assert response.status_code == 404, path
        assert error_of(response.json())[0] == "not_found"
        assert b"%PDF" not in response.content
    # Since story 2.7 one route returns a file, the redacted PDF, and not to
    # the customer who uploaded the original.
    refused = client.get(
        f"/api/documents/{document_id}/file", headers={"X-Demo-Role": "customer"}
    )
    assert refused.status_code == 403
    assert b"%PDF" not in refused.content
    # And no such request was passed on to another service.
    assert len(sidecar.requests) == calls_for_the_upload


# --- Settings and the client module -------------------------------------------
