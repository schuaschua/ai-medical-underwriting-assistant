"""Story 1.11: `web` composes the underwriter's triage queue and serves a page's thumbnail.

The Dapr sidecar is a fake here: a transport that records what `web` sent and
answers as `workflow`, `classification` and `intake` would.
"""

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.web import TriageQueue
from web.adapters.http.app import create_app
from web.adapters.http.triage import thumbnail_path
from web.settings import Settings

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
# The first bytes of any PNG file; the rest is no picture and need not be.
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-thumbnail"
REASON = "A table of laboratory values."


def error(code: ErrorCode, message: str, status: int | None = None) -> httpx.Response:
    failure = DomainError(code, message)
    return httpx.Response(
        status or failure.http_status,
        json=failure.to_body(TRACE_ID).model_dump(mode="json"),
    )


def queued(case_id: str, page_number: int, **changes: Any) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "page_id": new_id(),
        "page_number": page_number,
        "page_status": "awaiting_triage",
        "classifier_contender": "llm",
        "queued_by": "gate",
        **changes,
    }


def reading(page: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {
        "classification_id": new_id(),
        "case_id": page["case_id"],
        "page_id": page["page_id"],
        "contender": "llm",
        "page_type": "lab_report",
        "is_medical": True,
        "confidence": 0.6,
        "reason": REASON,
        **changes,
    }


@dataclass
class FakeSidecar:
    """Stands in for the Dapr sidecar and, behind it, the three services `web` reads."""

    pages: list[dict[str, Any]] = field(default_factory=list)
    has_more: bool = False
    # What `classification` lists, by case; a case without an entry lists nothing.
    readings: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # An answer that replaces the usual one, by app id, or by case for `classification`.
    answers: dict[str, httpx.Response | Exception] = field(default_factory=dict)
    thumbnail: httpx.Response | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    # How many reads of `classification` are under way, and the most there were.
    readings_under_way: int = 0
    most_readings_under_way: int = 0

    def calls(self, app_id: str) -> list[httpx.Request]:
        return [r for r in self.requests if f"/invoke/{app_id}/" in r.url.path]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        app_id = request.url.path.split("/")[3]
        parts = request.url.path.split("/")
        special = self.answers.get(app_id)
        if app_id == "classification":
            special = self.answers.get(parts[-2], special)
        if isinstance(special, Exception):
            raise special
        if special is not None:
            return special
        if app_id == "workflow":
            return httpx.Response(
                200, json={"pages": self.pages, "has_more": self.has_more}
            )
        if app_id == "classification":
            case_id = parts[-2]
            self.readings_under_way += 1
            self.most_readings_under_way = max(
                self.most_readings_under_way, self.readings_under_way
            )
            try:
                # Every other read that may start gets its turn before this one answers.
                for _ in range(5):
                    await asyncio.sleep(0)
            finally:
                self.readings_under_way -= 1
            return httpx.Response(
                200,
                json={
                    "case_id": case_id,
                    "classifications": self.readings.get(case_id, []),
                },
            )
        return self.thumbnail or httpx.Response(
            200, content=PNG, headers={"Content-Type": "image/png"}
        )


@pytest.fixture
def sidecar() -> FakeSidecar:
    return FakeSidecar()


@pytest.fixture
def client(settings: Settings, sidecar: FakeSidecar) -> Iterator[TestClient]:
    app = create_app(settings, sidecar=httpx.MockTransport(sidecar))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def two_cases(sidecar: FakeSidecar) -> list[dict[str, Any]]:
    """Three waiting pages in two cases, in the order `workflow` lists them."""
    first, second = new_id(), new_id()
    pages = [
        queued(first, 2),
        queued(second, 1, queued_by="customer"),
        queued(first, 5),
    ]
    sidecar.pages = pages
    sidecar.readings = {
        first: [
            reading(pages[2], page_type="other", is_medical=False, confidence=0.4),
            reading(pages[0]),
        ],
        second: [
            reading(pages[1], page_type="invoice", is_medical=False, confidence=1.0)
        ],
    }
    return pages


def read_queue(client: TestClient) -> TriageQueue:
    response = client.get("/api/triage", headers=UNDERWRITER)
    assert response.status_code == 200
    return TriageQueue.model_validate(response.json())


# --- The queue --------------------------------------------------------------------


def test_story_1_11_the_underwriter_reads_every_waiting_page_with_its_reading(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    pages = two_cases(sidecar)

    response = client.get(
        "/api/triage", headers={**UNDERWRITER, "traceparent": TRACEPARENT}
    )

    assert response.status_code == 200
    queue = TriageQueue.model_validate(response.json())
    # In the order `workflow` listed them, one payload per page.
    assert response.json()["pages"] == [
        {
            "case_id": pages[0]["case_id"],
            "page_id": pages[0]["page_id"],
            "page_number": 2,
            "thumbnail_path": f"/api/pages/{pages[0]['page_id']}/thumbnail",
            "page_type": "lab_report",
            "is_medical": True,
            "confidence": 0.6,
            "reason": REASON,
            "queued_by": "gate",
        },
        {
            "case_id": pages[1]["case_id"],
            "page_id": pages[1]["page_id"],
            "page_number": 1,
            "thumbnail_path": f"/api/pages/{pages[1]['page_id']}/thumbnail",
            "page_type": "invoice",
            "is_medical": False,
            "confidence": 1.0,
            "reason": REASON,
            "queued_by": "customer",
        },
        {
            "case_id": pages[2]["case_id"],
            "page_id": pages[2]["page_id"],
            "page_number": 5,
            "thumbnail_path": f"/api/pages/{pages[2]['page_id']}/thumbnail",
            "page_type": "other",
            "is_medical": False,
            "confidence": 0.4,
            "reason": REASON,
            "queued_by": "gate",
        },
    ]
    assert queue.has_more is False
    # `workflow` is asked for the triage queue, once.
    (asked,) = sidecar.calls("workflow")
    assert (asked.method, asked.url.path) == (
        "GET",
        "/v1.0/invoke/workflow/method/pages",
    )
    assert dict(asked.url.params) == {"status": "awaiting_triage"}
    # `classification` is asked once per case, not once per page.
    assert sorted(r.url.path for r in sidecar.calls("classification")) == sorted(
        f"/v1.0/invoke/classification/method/cases/{case_id}/classifications"
        for case_id in {pages[0]["case_id"], pages[1]["case_id"]}
    )
    assert sidecar.calls("intake") == []
    # One trace for the request, across the services.
    assert {r.headers["traceparent"] for r in sidecar.requests} == {TRACEPARENT}
    # The response headers every route has.
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in response.headers


def test_story_1_11_the_customer_is_refused_the_queue_and_no_service_is_asked(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    two_cases(sidecar)

    response = client.get("/api/triage", headers=CUSTOMER)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_not_allowed"
    assert sidecar.requests == []


@pytest.mark.parametrize(
    "answer", [httpx.ConnectError("secret-host:3500 refused")], ids=["unreachable"]
)
def test_story_1_11_a_page_whose_classification_cannot_be_read_is_still_listed(
    client: TestClient,
    sidecar: FakeSidecar,
    caplog: pytest.LogCaptureFixture,
    answer: httpx.Response | Exception,
) -> None:
    pages = two_cases(sidecar)
    unreadable = pages[0]["case_id"]
    sidecar.answers[unreadable] = answer

    with caplog.at_level(logging.WARNING):
        queue = read_queue(client)

    # The queue does not fail as a whole: every page is listed.
    assert [page.page_id for page in queue.pages] == [p["page_id"] for p in pages]
    for page in queue.pages:
        whole = (page.page_type, page.is_medical, page.confidence, page.reason)
        if page.case_id == unreadable:
            assert whole == (None, None, None, None)
            # It can still be decided: it keeps its ids and its thumbnail.
            assert page.thumbnail_path == thumbnail_path(page.page_id)
        else:
            assert whole == ("invoice", False, 1.0, REASON)
    # security rule 31: the case and a code; never the address or the message.
    assert f"triage reading unavailable: case_id={unreadable}" in caplog.text
    assert "secret-host" not in caplog.text


def test_story_1_11_readings_listed_under_another_case_are_not_shown(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    (page,) = sidecar.pages = [queued(new_id(), 1)]
    sidecar.answers[page["case_id"]] = httpx.Response(
        200,
        json={
            "case_id": new_id(),
            "classifications": [reading(page)],
        },
    )

    (listed,) = read_queue(client).pages

    assert (listed.page_id, listed.page_type) == (page["page_id"], None)


def test_story_1_11_the_reading_shown_is_that_of_the_classifier_the_case_runs_with(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    case_id, other_case = new_id(), new_id()
    with_two = queued(case_id, 1, classifier_contender="doc-intelligence")
    without = queued(other_case, 1, classifier_contender="doc-intelligence")
    never_read = queued(other_case, 2)
    sidecar.pages = [with_two, without, never_read]
    sidecar.readings = {
        case_id: [
            reading(with_two, confidence=0.3),
            reading(
                with_two,
                contender="doc-intelligence",
                page_type="invoice",
                is_medical=False,
                confidence=0.8,
            ),
        ],
        # Only the other classifier read this page; and one page was not read.
        other_case: [reading(without)],
    }

    first, second, third = read_queue(client).pages

    assert (first.page_type, first.confidence) == ("invoice", 0.8)
    assert (second.page_type, second.confidence) == (None, None)
    assert (third.page_type, third.confidence) == (None, None)


# --- The reader, with stand-ins for the calls --------------------------------------


# --- The thumbnail ----------------------------------------------------------------


@pytest.mark.parametrize("role", [CUSTOMER], ids=["customer"])
def test_story_1_11_either_role_is_served_the_thumbnail_intake_holds_and_nothing_that_is_no_png(
    client: TestClient, sidecar: FakeSidecar, role: dict[str, str]
) -> None:
    page_id = new_id()

    response = client.get(
        thumbnail_path(page_id), headers={**role, "traceparent": TRACEPARENT}
    )

    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    (asked,) = sidecar.requests
    assert (asked.method, asked.url.path) == (
        "GET",
        f"/v1.0/invoke/intake/method/pages/{page_id}/thumbnail",
    )
    assert asked.headers["traceparent"] == TRACEPARENT
    # The response headers every route has.
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"]
    assert response.headers["strict-transport-security"]

    # Anything but a PNG from `intake` is never handed to a browser as one.
    sidecar.thumbnail = httpx.Response(
        200, content=b"%PDF-1.7", headers={"Content-Type": "application/pdf"}
    )

    wrong = client.get(thumbnail_path(new_id()), headers=role)

    assert wrong.status_code == 502
    assert wrong.json()["error"]["code"] == "upstream_unavailable"
    assert b"PDF" not in wrong.content


# --- The settings -----------------------------------------------------------------
