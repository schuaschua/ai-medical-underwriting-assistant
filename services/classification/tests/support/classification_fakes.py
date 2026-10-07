"""In-memory stand-ins for what `classification` works with, and the gateway stub.

Unit tests use them in place of PostgreSQL, `intake` and the model
(coding-style rule 23). They live with the tests, on pytest's `pythonpath`,
so the service package and its image hold no test code. All page text here is
made up for the tests: synthetic, and marked so that a test can tell if any
of it reached a log.
"""

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from classification.domain.entities import ClassificationKey, KeyRow, PageContent
from classification.domain.ports import ModelUnavailable
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.classification import Classification, ClassificationResult

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
DEPLOYMENT = "chat-test"
ACTOR = f"classification:{DEPLOYMENT}"
# Words no log line, error body or audit record may ever hold.
PAGE_TEXT = "SECRET-PAGE-TEXT Laboratory Report for [Person], HbA1c 6.1 %"
REASON = "SECRET-REASON a table of laboratory values"
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-thumbnail"


def answer(page_type: str = "lab_report", reason: str = REASON) -> str:
    """One run's answer, as the prompt asks for it."""
    return json.dumps({"page_type": page_type, "reason": reason})


class StoreDown(Exception):
    """What a failing stand-in raises. Its message must never reach a log or a body."""

    def __init__(self) -> None:
        super().__init__("secret-store-detail host=10.0.0.1 SELECT 1")


@dataclass
class MemoryRepository:
    """Keeps to the repository's contract: one row per key, settled once."""

    rows: dict[ClassificationKey, KeyRow] = field(default_factory=dict)
    stored: dict[str, Classification] = field(default_factory=dict)
    fail_finish: bool = False
    # Only the next this many attempts to store a result fail.
    failing_finishes: int = 0
    fail_release: bool = False
    finishes: int = 0
    released: list[str] = field(default_factory=list)

    async def find(self, key: ClassificationKey) -> KeyRow | None:
        return self.rows.get(key)

    async def begin(
        self, classification_id: str, key: ClassificationKey, started_at: datetime
    ) -> KeyRow | None:
        if key in self.rows:
            return self.rows[key]
        self.rows[key] = KeyRow(classification_id, key, started_at, None)
        return None

    async def finish(
        self,
        classification_id: str,
        result_json: str,
        classification: Classification | None,
    ) -> str:
        self.finishes += 1
        if self.fail_finish:
            raise StoreDown
        if self.failing_finishes > 0:
            self.failing_finishes -= 1
            raise StoreDown
        key, row = next(
            (key, row)
            for key, row in self.rows.items()
            if row.classification_id == classification_id
        )
        if row.result_json is not None:
            return row.result_json
        self.rows[key] = KeyRow(
            classification_id, key, row.started_at, result_json=result_json
        )
        if classification is not None:
            self.stored[classification_id] = classification
        return result_json

    async def release(self, classification_id: str) -> None:
        if self.fail_release:
            raise StoreDown
        for key, row in list(self.rows.items()):
            if row.classification_id == classification_id and row.running:
                del self.rows[key]
                self.released.append(classification_id)

    async def of_case(self, case_id: str) -> list[Classification]:
        # In the order they were stored, as the table's read is oldest first.
        return [item for item in self.stored.values() if item.case_id == case_id]

    def result_of(self, key: ClassificationKey) -> ClassificationResult | None:
        row = self.rows.get(key)
        if row is None or row.result_json is None:
            return None
        return ClassificationResult.model_validate_json(row.result_json)


@dataclass
class FakePages:
    """Stands in for `intake`: the pages of each case, as redaction left them."""

    cases: dict[str, dict[str, PageContent]] = field(default_factory=dict)
    # Reading a page's content fails, or finds it gone, after it was listed.
    fail_read: bool = False
    # `intake` cannot be reached for the next this many reads of a page.
    unavailable_reads: int = 0
    gone: bool = False
    listings: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    trace_contexts: list[dict[str, str]] = field(default_factory=list)

    def add(self, case_id: str, text: str = PAGE_TEXT, image: bytes = PNG) -> str:
        """Give a case one more page; return its id."""
        page_id = new_id()
        self.cases.setdefault(case_id, {})[page_id] = PageContent(text, image)
        return page_id

    async def page_ids_of_case(
        self, case_id: str, trace_context: Mapping[str, str]
    ) -> list[str] | None:
        self.listings.append(case_id)
        self.trace_contexts.append(dict(trace_context))
        pages = self.cases.get(case_id)
        return list(pages) if pages is not None else None

    async def read_page(
        self, page_id: str, trace_context: Mapping[str, str]
    ) -> PageContent | None:
        self.reads.append(page_id)
        self.trace_contexts.append(dict(trace_context))
        if self.unavailable_reads > 0:
            self.unavailable_reads -= 1
            raise DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, "Not available.")
        if self.fail_read:
            raise StoreDown
        if self.gone:
            return None
        for pages in self.cases.values():
            if page_id in pages:
                return pages[page_id]
        return None


@dataclass
class StubModel:
    """The gateway stub: answers each run from a script, and notes what it was shown.

    `answers` are given out in order, the last one again once they run out.
    An entry that is an exception is raised instead. With `hold` set, every
    run waits for it first.
    """

    answers: list[str | Exception] = field(default_factory=lambda: [answer()])
    hold: asyncio.Event | None = None
    pages: list[PageContent] = field(default_factory=list)
    running: int = 0
    most_running: int = 0

    @property
    def calls(self) -> int:
        return len(self.pages)

    async def classify(self, page: PageContent) -> str:
        position = len(self.pages)
        self.pages.append(page)
        self.running += 1
        self.most_running = max(self.most_running, self.running)
        try:
            if self.hold is not None:
                await self.hold.wait()
            # So that runs started together are under way together.
            await asyncio.sleep(0)
            given = self.answers[min(position, len(self.answers) - 1)]
            if isinstance(given, Exception):
                raise given
            return given
        finally:
            self.running -= 1


def unavailable() -> ModelUnavailable:
    return ModelUnavailable()


@dataclass
class MemorySchemaRevision:
    revision: str | None
    fail: bool = False

    async def current(self) -> str | None:
        if self.fail:
            raise StoreDown
        return self.revision


# --- `intake` behind the Dapr sidecar, for the real client module ---------------------

_PAGES_PATH = re.compile(
    r"/v1\.0/invoke/intake/method/cases/(?P<case_id>[0-9a-f-]{36})/pages"
)
_PAGE_PATH = re.compile(
    r"/v1\.0/invoke/intake/method/pages/(?P<page_id>[0-9a-f-]{36})/(?P<what>text|thumbnail)"
)


@dataclass
class IntakeSidecar:
    """Stands in for this service's Dapr sidecar, with `intake` behind it.

    An `httpx` transport handler: `httpx.MockTransport(sidecar.handle)`. It
    answers the three reads as `intake` does, over HTTP and in the contracts'
    shapes, so the real client module is what the test runs.
    """

    pages: FakePages = field(default_factory=FakePages)
    requests: list[httpx.Request] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    @staticmethod
    def _not_found() -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": "not_found",
                    "message": "Not found.",
                    "trace_id": "0" * 32,
                }
            },
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method != "GET":
            return httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE"})
        listing = _PAGES_PATH.fullmatch(request.url.path)
        if listing is not None:
            case_id = listing["case_id"]
            pages = self.pages.cases.get(case_id)
            if pages is None:
                return self._not_found()
            return httpx.Response(
                200,
                json={
                    "case_id": case_id,
                    "pages": [
                        {
                            "page_id": page_id,
                            "case_id": case_id,
                            "document_id": case_id,
                            "page_number": number,
                        }
                        for number, page_id in enumerate(pages, start=1)
                    ],
                },
            )
        read = _PAGE_PATH.fullmatch(request.url.path)
        if read is None:
            # As the sidecar answers for an app or a method it cannot reach.
            return httpx.Response(500, json={"errorCode": "ERR_DIRECT_INVOKE"})
        page_id = read["page_id"]
        for pages in self.pages.cases.values():
            if page_id in pages:
                content = pages[page_id]
                number = list(pages).index(page_id) + 1
                if read["what"] == "text":
                    return httpx.Response(
                        200,
                        json={
                            "page_id": page_id,
                            "page_number": number,
                            "text": content.text,
                        },
                    )
                return httpx.Response(
                    200, content=content.image, headers={"content-type": "image/png"}
                )
        return self._not_found()


def completion(content: Any = None) -> dict[str, Any]:
    """A chat completion as the deployment answers it, around one message content."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": DEPLOYMENT,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
