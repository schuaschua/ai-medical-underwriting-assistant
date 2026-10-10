"""In-memory stand-ins for what `extraction` works with, and the gateway stub.

Unit tests use them in place of PostgreSQL, `intake` and the model
(coding-style rule 23). They live with the tests, on pytest's `pythonpath`,
so the service package and its image hold no test code. All page text here is
made up for the tests: synthetic, and marked so that a test can tell if any
of it reached a log.
"""

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.extraction import Fact, FactSetResult
from extraction.domain.entities import FactSetKey, KeyRow, ModelAnswer, PageReading
from extraction.domain.ports import ModelUnavailable

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
DEPLOYMENT = "chat-test"
ACTOR = f"extraction:{DEPLOYMENT}"
# Words no log line, error body or audit record may ever hold. The page is
# laid out as `intake` stores one: a line per cell of a table.
PAGE_TEXT = (
    "SECRET-PAGE-TEXT Laboratory Report\n"
    "Patient name\n[Person]\n"
    "Results\nTest\nResult\nUnit\n"
    "HbA1c\n7.4\n%\n"
    "Fasting plasma glucose\n142\nmg/dL\n"
    "Latest HbA1c\n7.4 % on 2026-09-07"
)
STATEMENT = "SECRET-STATEMENT HbA1c 7.4 %"
QUOTE = "HbA1c 7.4 %"


def fact(statement: str = STATEMENT, quote: str = QUOTE) -> dict[str, str]:
    """One proposed fact, as the prompt asks for it."""
    return {"statement": statement, "quote": quote}


def answer(*facts: dict[str, str]) -> str:
    """The model's answer for a page: the facts given, or one whose quote is on `PAGE_TEXT`."""
    return json.dumps({"facts": list(facts) if facts else [fact()]})


class StoreDown(Exception):
    """What a failing stand-in raises. Its message must never reach a log or a body."""

    def __init__(self) -> None:
        super().__init__("secret-store-detail host=10.0.0.1 SELECT 1")


@dataclass
class MemoryRepository:
    """Keeps to the repository's contract: one row per key, settled once."""

    rows: dict[FactSetKey, KeyRow] = field(default_factory=dict)
    # Every stored fact, in the order stored.
    stored: list[Fact] = field(default_factory=list)
    fail_finish: bool = False
    # Only the next this many attempts to store a result fail.
    failing_finishes: int = 0
    fail_release: bool = False
    finishes: int = 0
    released: list[str] = field(default_factory=list)
    taken_over: list[str] = field(default_factory=list)

    async def find(self, key: FactSetKey) -> KeyRow | None:
        return self.rows.get(key)

    async def begin(
        self, fact_set_id: str, key: FactSetKey, started_at: datetime
    ) -> KeyRow | None:
        if key in self.rows:
            return self.rows[key]
        self.rows[key] = KeyRow(fact_set_id, key, started_at, None)
        return None

    async def take_over(self, row: KeyRow, started_at: datetime) -> bool:
        held = self.rows.get(row.key)
        if held is None or not held.running or held.started_at != row.started_at:
            return False
        self.rows[row.key] = KeyRow(held.fact_set_id, row.key, started_at, None)
        self.taken_over.append(held.fact_set_id)
        return True

    async def finish(
        self, fact_set_id: str, result_json: str, facts: Sequence[Fact] | None
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
            if row.fact_set_id == fact_set_id
        )
        if row.result_json is not None:
            return row.result_json
        self.rows[key] = KeyRow(fact_set_id, key, row.started_at, result_json)
        self.stored.extend(facts or ())
        return result_json

    async def release(self, fact_set_id: str) -> None:
        if self.fail_release:
            raise StoreDown
        for key, row in list(self.rows.items()):
            if row.fact_set_id == fact_set_id and row.running:
                del self.rows[key]
                self.released.append(fact_set_id)

    async def of_case(self, case_id: str) -> list[Fact]:
        # Page order; the sort is stable, so facts of one page keep the order
        # they were stored in.
        return sorted(
            (item for item in self.stored if item.case_id == case_id),
            key=lambda item: item.page_number,
        )

    def result_of(self, key: FactSetKey) -> FactSetResult | None:
        row = self.rows.get(key)
        if row is None or row.result_json is None:
            return None
        return FactSetResult.model_validate_json(row.result_json)


@dataclass
class FakePages:
    """Stands in for `intake`: the pages of each case, as redaction left them."""

    cases: dict[str, dict[str, PageReading]] = field(default_factory=dict)
    # Reading a page's content fails, or finds it gone, after it was listed.
    fail_read: bool = False
    # `intake` cannot be reached for the next this many reads of a page.
    unavailable_reads: int = 0
    gone: bool = False
    listings: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    trace_contexts: list[dict[str, str]] = field(default_factory=list)

    def add(self, case_id: str, text: str = PAGE_TEXT) -> str:
        """Give a case one more page, numbered after those it has; return its id."""
        page_id = new_id()
        pages = self.cases.setdefault(case_id, {})
        pages[page_id] = PageReading(page_number=len(pages) + 1, text=text)
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
    ) -> PageReading | None:
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
    """The gateway stub: answers each call from a script, and notes what it was shown.

    `answers` are given out in order, the last one again once they run out.
    An entry that is an exception is raised instead. With `hold` set, every
    call waits for it first.
    """

    answers: list[str | ModelAnswer | Exception] = field(
        default_factory=lambda: [answer()]
    )
    hold: asyncio.Event | None = None
    texts: list[str] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.texts)

    async def extract(self, page_text: str) -> ModelAnswer:
        position = len(self.texts)
        self.texts.append(page_text)
        if self.hold is not None:
            await self.hold.wait()
        await asyncio.sleep(0)
        given = self.answers[min(position, len(self.answers) - 1)]
        if isinstance(given, Exception):
            raise given
        # A plain text is an answer the model finished by itself.
        return given if isinstance(given, ModelAnswer) else ModelAnswer(given, "stop")


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
    r"/v1\.0/invoke/intake/method/pages/(?P<page_id>[0-9a-f-]{36})/text"
)


@dataclass
class IntakeSidecar:
    """Stands in for this service's Dapr sidecar, with `intake` behind it.

    An `httpx` transport handler: `httpx.MockTransport(sidecar.handle)`. It
    answers the two reads as `intake` does, over HTTP and in the contracts'
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
                page = pages[page_id]
                return httpx.Response(
                    200,
                    json={
                        "page_id": page_id,
                        "page_number": page.page_number,
                        "text": page.text,
                    },
                )
        return self._not_found()


def completion(content: Any = None, finish_reason: Any = "stop") -> dict[str, Any]:
    """A chat completion as the deployment answers it, around one message content."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": DEPLOYMENT,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
