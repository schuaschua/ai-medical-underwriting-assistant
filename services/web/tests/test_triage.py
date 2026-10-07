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

from contracts.enums import ClassifierContender, PageStatus
from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.classification import ClassificationList
from contracts.models.web import TriageQueue
from contracts.models.workflow import PageQueue
from web.adapters.http.app import create_app
from web.adapters.http.triage import TriageReader, thumbnail_path
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


def test_story_1_11_the_queue_passes_on_that_more_pages_wait(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    two_cases(sidecar)
    sidecar.has_more = True

    assert read_queue(client).has_more is True


def test_story_1_11_an_empty_queue_asks_classification_nothing(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    queue = read_queue(client)

    assert (queue.pages, queue.has_more) == ([], False)
    assert sidecar.calls("classification") == []


def test_story_1_11_the_customer_is_refused_the_queue_and_no_service_is_asked(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    two_cases(sidecar)

    response = client.get("/api/triage", headers=CUSTOMER)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "role_not_allowed"
    assert sidecar.requests == []


def test_story_1_11_the_queue_without_a_role_is_400(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    response = client.get("/api/triage")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_role"
    assert sidecar.requests == []


@pytest.mark.parametrize(
    "answer",
    [
        error(ErrorCode.UPSTREAM_UNAVAILABLE, "The service is not ready."),
        error(ErrorCode.NOT_FOUND, "That case could not be found."),
        httpx.Response(200, json={"classifications": "none"}),
        httpx.ConnectError("secret-host:3500 refused"),
    ],
    ids=["unavailable", "not-found", "invalid-body", "unreachable"],
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


@pytest.mark.parametrize(
    "answer",
    [
        error(ErrorCode.UPSTREAM_UNAVAILABLE, "The service is not ready."),
        # Nothing the user sent: `web` names the status itself.
        error(ErrorCode.VALIDATION_FAILED, "That status is not a queue."),
        httpx.Response(200, json={"pages": []}),
        httpx.Response(500, text="sidecar says no"),
        httpx.ConnectError("secret-host:3500 refused"),
    ],
    ids=["unavailable", "refused", "invalid-body", "not-our-shape", "unreachable"],
)
def test_story_1_11_a_queue_workflow_cannot_answer_is_502(
    client: TestClient, sidecar: FakeSidecar, answer: httpx.Response | Exception
) -> None:
    two_cases(sidecar)
    sidecar.answers["workflow"] = answer

    response = client.get(
        "/api/triage", headers={**UNDERWRITER, "traceparent": TRACEPARENT}
    )

    assert response.status_code == 502
    detail = ErrorBody.model_validate(response.json()).error
    assert (detail.code, detail.trace_id) == (ErrorCode.UPSTREAM_UNAVAILABLE, TRACE_ID)
    assert "secret-host" not in response.text
    assert sidecar.calls("classification") == []


# --- The reader, with stand-ins for the calls --------------------------------------


@dataclass
class SlowServices:
    """Answers as the service client does; a reading waits until it is let through."""

    queue: PageQueue
    let_through: asyncio.Event = field(default_factory=asyncio.Event)
    # Cases whose reading never comes.
    stuck: frozenset[str] = frozenset()
    # Cases whose reading fails in a way no service call should.
    broken: frozenset[str] = frozenset()
    under_way: int = 0
    most_under_way: int = 0
    asked: list[str] = field(default_factory=list)

    async def list_pages_by_status(
        self, status: PageStatus, *, traceparent: str | None
    ) -> PageQueue:
        assert status is PageStatus.AWAITING_TRIAGE
        return self.queue

    async def list_classifications(
        self, case_id: str, *, traceparent: str | None
    ) -> ClassificationList:
        self.asked.append(case_id)
        self.under_way += 1
        self.most_under_way = max(self.most_under_way, self.under_way)
        try:
            if case_id in self.stuck:
                await asyncio.Event().wait()
            if case_id in self.broken:
                raise RuntimeError("secret-detail of a fault")
            # Gives every other read its turn before this one answers.
            await asyncio.sleep(0)
            return ClassificationList.model_validate(
                {
                    "case_id": case_id,
                    "classifications": [
                        reading(page.model_dump(mode="json"))
                        for page in self.queue.pages
                        if page.case_id == case_id
                    ],
                }
            )
        finally:
            self.under_way -= 1


def queue_of(cases: int) -> PageQueue:
    return PageQueue.model_validate(
        {"pages": [queued(new_id(), 1) for _ in range(cases)], "has_more": False}
    )


def test_story_1_11_the_readings_are_read_side_by_side_within_a_bound() -> None:
    services = SlowServices(queue_of(7))
    reader = TriageReader(
        services,  # type: ignore[arg-type]  # a stand-in with the two calls the reader makes
        max_concurrent_reads=3,
        deadline_seconds=30.0,
    )

    queue = asyncio.run(reader.read(traceparent=None))

    assert len(services.asked) == 7
    assert services.most_under_way == 3
    assert all(page.page_type == "lab_report" for page in queue.pages)
    assert {page.classifier_contender for page in services.queue.pages} == {
        ClassifierContender.LLM
    }


def test_story_1_11_a_reading_that_is_late_leaves_its_page_listed_without_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pages = queue_of(3)
    stuck = pages.pages[1].case_id
    services = SlowServices(pages, stuck=frozenset({stuck}))
    reader = TriageReader(
        services,  # type: ignore[arg-type]  # a stand-in with the two calls the reader makes
        max_concurrent_reads=8,
        # The deadline of the whole queue: the stuck reading is not waited for.
        deadline_seconds=0.05,
    )

    with caplog.at_level(logging.WARNING):
        queue = asyncio.run(reader.read(traceparent=None))

    assert [page.page_id for page in queue.pages] == [p.page_id for p in pages.pages]
    assert [page.page_type for page in queue.pages] == [
        "lab_report",
        None,
        "lab_report",
    ]
    # The late read was ended, not left running.
    assert services.under_way == 0
    assert "triage readings late: cases=1" in caplog.text


def test_story_1_11_a_reading_that_fails_for_any_reason_leaves_its_page_listed_without_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pages = queue_of(2)
    broken = pages.pages[0].case_id
    services = SlowServices(pages, broken=frozenset({broken}))
    reader = TriageReader(
        services,  # type: ignore[arg-type]  # a stand-in with the two calls the reader makes
        max_concurrent_reads=8,
        deadline_seconds=30.0,
    )

    with caplog.at_level(logging.WARNING):
        queue = asyncio.run(reader.read(traceparent=None))

    # Not a 500 for the whole queue: the page is listed, the other has its reading.
    assert [page.page_type for page in queue.pages] == [None, "lab_report"]
    # The error's type only; never its message.
    assert (
        f"triage reading unavailable: case_id={broken} type=RuntimeError" in caplog.text
    )
    assert "secret-detail" not in caplog.text


def test_story_1_11_a_cancelled_request_ends_the_readings_it_began() -> None:
    pages = queue_of(3)
    services = SlowServices(pages, stuck=frozenset(p.case_id for p in pages.pages))
    reader = TriageReader(
        services,  # type: ignore[arg-type]  # a stand-in with the two calls the reader makes
        max_concurrent_reads=8,
        deadline_seconds=30.0,
    )

    async def scenario() -> tuple[int, int]:
        request = asyncio.create_task(reader.read(traceparent=None))
        # Until every reading is under way.
        while services.under_way < 3:
            await asyncio.sleep(0)
        under_way = services.under_way
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        return under_way, services.under_way

    assert asyncio.run(scenario()) == (3, 0)


def test_story_1_11_a_queue_read_that_uses_the_deadline_up_begins_no_reading(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pages = queue_of(3)
    services = SlowServices(pages)
    # The clock is read before the queue is asked for and after it answered:
    # by then the whole deadline has gone.
    times = iter([100.0, 120.0])
    reader = TriageReader(
        services,  # type: ignore[arg-type]  # a stand-in with the two calls the reader makes
        max_concurrent_reads=8,
        deadline_seconds=20.0,
        clock=lambda: next(times),
    )

    with caplog.at_level(logging.WARNING):
        queue = asyncio.run(reader.read(traceparent=None))

    # Every page is listed, without its reading, and can be decided.
    assert [page.page_id for page in queue.pages] == [p.page_id for p in pages.pages]
    assert {page.page_type for page in queue.pages} == {None}
    assert services.asked == []
    assert "triage readings late: cases=3" in caplog.text


def test_story_1_11_time_left_before_the_deadline_is_given_to_the_readings() -> None:
    pages = queue_of(3)
    services = SlowServices(pages)
    # One second short of the deadline when the queue has answered.
    times = iter([100.0, 119.0])
    reader = TriageReader(
        services,  # type: ignore[arg-type]  # a stand-in with the two calls the reader makes
        max_concurrent_reads=8,
        deadline_seconds=20.0,
        clock=lambda: next(times),
    )

    queue = asyncio.run(reader.read(traceparent=None))

    assert {page.page_type for page in queue.pages} == {"lab_report"}
    assert len(services.asked) == 3


@pytest.mark.parametrize(("bound", "most"), [(1, 1), (8, 5)])
def test_story_1_11_the_setting_bounds_the_reads_of_classification_under_way_at_once(
    settings: Settings, sidecar: FakeSidecar, bound: int, most: int
) -> None:
    sidecar.pages = [queued(new_id(), 1) for _ in range(5)]
    app = create_app(
        settings.model_copy(update={"triage_max_concurrent_reads": bound}),
        sidecar=httpx.MockTransport(sidecar),
    )

    with TestClient(app) as client:
        queue = read_queue(client)

    assert len(queue.pages) == 5
    assert len(sidecar.calls("classification")) == 5
    assert sidecar.most_readings_under_way == most


# --- The thumbnail ----------------------------------------------------------------


@pytest.mark.parametrize(
    "role", [CUSTOMER, UNDERWRITER], ids=["customer", "underwriter"]
)
def test_story_1_11_either_role_is_served_the_thumbnail_intake_holds(
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


def test_story_1_11_the_thumbnail_of_an_unknown_page_is_404(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    sidecar.thumbnail = error(ErrorCode.NOT_FOUND, "That page could not be found.")

    response = client.get(thumbnail_path(new_id()), headers=UNDERWRITER)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.headers["content-type"] == "application/json"


def test_story_1_11_a_thumbnail_address_that_is_no_page_id_is_422_before_any_call(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    response = client.get("/api/pages/not-an-id/thumbnail", headers=UNDERWRITER)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert sidecar.requests == []


def test_story_1_11_the_thumbnail_without_a_role_is_400(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    response = client.get(thumbnail_path(new_id()))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_role"
    assert sidecar.requests == []


@pytest.mark.parametrize(
    "answer",
    [
        # Not a picture: never handed to the browser as one.
        httpx.Response(200, json={"page_id": "x"}),
        httpx.Response(200, content=b"", headers={"Content-Type": "image/png"}),
        httpx.Response(
            200, content=b"%PDF-1.7", headers={"Content-Type": "application/pdf"}
        ),
        error(ErrorCode.NOT_REDACTED, "The document is not redacted yet."),
        error(ErrorCode.UPSTREAM_UNAVAILABLE, "The service is not ready."),
        httpx.Response(500, text="sidecar says no"),
    ],
    ids=["json", "empty", "pdf", "not-redacted", "unavailable", "not-our-shape"],
)
def test_story_1_11_anything_but_a_png_from_intake_is_502(
    client: TestClient, sidecar: FakeSidecar, answer: httpx.Response
) -> None:
    sidecar.thumbnail = answer

    response = client.get(thumbnail_path(new_id()), headers=CUSTOMER)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    assert b"PDF" not in response.content


def test_story_1_11_an_unreachable_intake_is_502_without_its_address(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    sidecar.answers["intake"] = httpx.ConnectError("secret-host:3500 refused")

    response = client.get(thumbnail_path(new_id()), headers=CUSTOMER)

    assert response.status_code == 502
    assert "secret-host" not in response.text


# --- The settings -----------------------------------------------------------------


def test_story_1_11_the_bound_on_readings_is_a_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().triage_max_concurrent_reads == 8

    monkeypatch.setenv("WEB_TRIAGE_MAX_CONCURRENT_READS", "2")

    assert Settings().triage_max_concurrent_reads == 2
