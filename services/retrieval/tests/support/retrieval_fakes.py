"""In-memory stand-ins for what the ingestion works with, and builders of a made-up manual.

Unit tests use them in place of Blob Storage, Document Intelligence, the
models and PostgreSQL (coding-style rule 23). They live with the tests, on
pytest's `pythonpath`, so the service package and its image hold no test
code. The manual here is made up for the tests and is not the project's
manual: its rule ids are of the contracts' shape and belong to no rule, and
its text is marked so that a test can tell if any of it reached a log.
"""

import asyncio
import json
import math
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any

import httpx2

from contracts.enums import ChunkSet
from retrieval.adapters.search_index import (
    HASH_FIELD,
    KEY_FIELD,
    RERANKER_SCORE,
    document_body,
)
from retrieval.domain.entities import (
    EMBEDDING_DIMENSIONS,
    Chunk,
    ChunkRecord,
    IndexedChunk,
    IngestRun,
    LayoutPage,
    LayoutParagraph,
    ParsedLayout,
    StoredChunk,
)
from retrieval.domain.index_load import index_document
from retrieval.domain.ingest import IngestOptions
from retrieval.domain.ports import (
    IndexChanged,
    IndexUnavailable,
    LayoutFailed,
    ManualMissing,
)

CHAT = "chat-test"
EMBEDDING = "embedding-test"
PDF = b"%PDF-1.7 a made-up manual"
HEADER = "Made-Up Manual For Tests"
FOOTER = "TEST DOCUMENT. Nothing on this page is real."
# Words no log line may ever hold.
MARK = "SECRET-MANUAL-TEXT"
CONTEXT = "SECRET-CONTEXT From section 2, Raised blood sugar, part 2.4."

RULE_A = "UW-AA-001"
RULE_B = "UW-AA-002"
RULE_C = "UW-BB-001"


def options(**changes: Any) -> IngestOptions:
    values: dict[str, Any] = {
        "chat_deployment": CHAT,
        "embedding_deployment": EMBEDDING,
        "prompt_digest": "digest-1",
        "embedding_batch_size": 2,
        # The made-up manual has three rules: losing one of them is a third.
        "max_removed_share": 0.5,
    }
    return IngestOptions(**{**values, **changes})


def definition(rule_id: str, body: str = "") -> str:
    """One rule's definition paragraph, as a manual prints it."""
    body = body or f"{MARK} Threshold: a reading in the band of {rule_id}."
    return f"Rule {rule_id}: {body}"


Line = str | tuple[str, str]


def layout(*pages: Sequence[Line], furniture: bool = True) -> ParsedLayout:
    """A parsed manual: each page a list of paragraphs, or of (text, role) pairs.

    With `furniture` every page also gets a header, a page number and a
    footer, with the roles the layout model gives them.
    """
    paragraphs: list[LayoutParagraph] = []
    for number, lines in enumerate(pages, start=1):
        if furniture:
            paragraphs.append(LayoutParagraph(number, HEADER, "pageHeader"))
            paragraphs.append(LayoutParagraph(number, f"Page {number}", "pageNumber"))
        for line in lines:
            text, role = (line, None) if isinstance(line, str) else line
            paragraphs.append(LayoutParagraph(number, text, role))
        if furniture:
            paragraphs.append(LayoutParagraph(number, FOOTER, "pageFooter"))
    return ParsedLayout(
        pages=tuple(LayoutPage(number, True) for number in range(1, len(pages) + 1)),
        paragraphs=tuple(paragraphs),
    )


def manual(
    first: str | None = None,
    with_c: bool = True,
    filler_pages: int = 0,
    section_two: str = "Raised blood sugar",
) -> ParsedLayout:
    """A small manual: a contents page, an introduction and two sections with three rules.

    `first` replaces the definition of the first rule; without `with_c` the
    third rule is gone; `filler_pages` puts pages before the first section,
    so that every rule moves; `section_two` is the heading of the section
    the first two rules are printed in.
    """
    filler: list[list[Line]] = [
        [f"A page that pushes the rest back. {MARK}"] for _ in range(filler_pages)
    ]
    heading = "sectionHeading"
    pages: list[list[Line]] = [
        [
            ("Contents", heading),
            "1 Introduction",
            "2",
            f"2 {section_two}",
            "3",
            "3 Gout",
            "4",
        ],
        [("1 Introduction", heading), f"This manual is made up. {MARK}"],
        *filler,
        [
            (f"2 {section_two}", heading),
            ("2.1 The impairment", heading),
            # Numbered lines that are no headings: one by its words, one
            # that only the layout model's role tells apart.
            f"7.5 per cent is a reading, not a heading. {MARK}",
            "3 Months after diagnosis",
            ("2.4 Probable rating", heading),
            "Rule id",
            RULE_A,
            first or definition(RULE_A, f"{MARK} Mild. See rule {RULE_B}."),
            definition(RULE_B, f"{MARK} Worse. See rule {RULE_A}."),
            "What does not change the rating.",
        ],
        [
            ("3 Gout", heading),
            ("3.4 Probable rating", heading),
            *(
                [
                    definition(
                        RULE_C,
                        f"{MARK} Readings in the band. See rule {RULE_B} and see "
                        f"rule {RULE_A}.",
                    )
                ]
                if with_c
                else []
            ),
            f"3.5 Worked examples. Under rule {RULE_A} nothing is defined here.",
        ],
    ]
    return layout(*pages)


def without_roles(parsed: ParsedLayout, *dropped: str) -> ParsedLayout:
    """The same manual as a layout model without roles gives it, less some paragraphs."""
    return replace(
        parsed,
        paragraphs=tuple(
            replace(item, role=None)
            for item in parsed.paragraphs
            if item.text not in dropped
        ),
    )


@dataclass
class FakeManual:
    pdf: bytes | None = PDF
    reads: int = 0

    async def read(self) -> bytes:
        self.reads += 1
        if self.pdf is None:
            raise ManualMissing
        return self.pdf


@dataclass
class FakeLayout:
    parsed: ParsedLayout = field(default_factory=manual)
    fail: str | None = None
    parsed_pdfs: list[bytes] = field(default_factory=list)

    async def parse(self, pdf: bytes) -> ParsedLayout:
        self.parsed_pdfs.append(pdf)
        if self.fail is not None:
            raise LayoutFailed(self.fail)
        return self.parsed


def context_answer(line: str = CONTEXT) -> str:
    """The chat model's answer, as the prompt asks for it."""
    return json.dumps({"context_line": line})


def vector_for(text: str) -> list[float]:
    """A vector of the right size that differs from text to text."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[len(text) % EMBEDDING_DIMENSIONS] = 1.0
    vector[1] = float(sum(text.encode()) % 997) / 997.0
    return vector


@dataclass
class StubModel:
    """The gateway stub: answers what it is told to, and notes what it was asked."""

    # What a context-line call is answered with: one answer for all, or a
    # function of what the model was shown.
    answer: Any = field(default_factory=context_answer)
    # An error raised in place of an answer, per call.
    context_error: Exception | None = None
    embed_error: Exception | None = None
    # A vector of this size is answered in place of the right one.
    dimensions: int = EMBEDDING_DIMENSIONS
    # Only this many vectors are answered per call, if set.
    vectors_per_call: int | None = None
    shown: list[str] = field(default_factory=list)
    embedded: list[list[str]] = field(default_factory=list)
    # Row `r4` (story 3.7): what a rerank call is answered with, one answer
    # for all or a function of the message it was sent; an error raised in
    # its place; and the messages it was sent.
    rerank_answer: Any = ""
    rerank_error: Exception | None = None
    reranked: list[str] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.shown) + len(self.embedded) + len(self.reranked)

    async def relevance(self, query_and_candidates: str) -> str:
        self.reranked.append(query_and_candidates)
        if self.rerank_error is not None:
            raise self.rerank_error
        answer = self.rerank_answer
        return str(answer(query_and_candidates) if callable(answer) else answer)

    async def context_line(self, rule_in_its_place: str) -> str:
        self.shown.append(rule_in_its_place)
        if self.context_error is not None:
            raise self.context_error
        answer = self.answer
        return str(answer(rule_in_its_place) if callable(answer) else answer)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedded.append(list(texts))
        if self.embed_error is not None:
            raise self.embed_error
        vectors = [
            (vector_for(text) + [0.0] * self.dimensions)[: self.dimensions]
            for text in texts
        ]
        return vectors[: self.vectors_per_call]


class StoreDown(Exception):
    """What a failing stand-in raises. Its message must never reach a log."""

    def __init__(self) -> None:
        super().__init__("secret-store-detail host=10.0.0.1 INSERT INTO chunk")


@dataclass
class MemoryRepository:
    """Keeps to the repository's contract: all of a run is applied, or none of it."""

    records: dict[str, ChunkRecord] = field(default_factory=dict)
    run: IngestRun | None = None
    fail_apply: bool = False
    # What to do just before an apply, as another run that got in first would.
    before_apply: Any = None
    applies: list[tuple[list[str], dict[str, int], list[str]]] = field(
        default_factory=list
    )
    reads: int = 0

    async def stored(self, chunk_set: ChunkSet) -> Mapping[str, StoredChunk]:
        self.reads += 1
        return self._stored(chunk_set)

    def _stored(self, chunk_set: ChunkSet) -> dict[str, StoredChunk]:
        return {
            chunk_id: StoredChunk(
                chunk_id,
                record.content_hash,
                record.chunk.manual_page,
                record.chunk.rule_ids,
            )
            for chunk_id, record in self.records.items()
            if record.chunk.chunk_set is chunk_set
        }

    async def last_run(self, chunk_set: ChunkSet) -> IngestRun | None:
        return self.run

    async def chunk_records(self, chunk_set: ChunkSet) -> Sequence[ChunkRecord]:
        return sorted(
            (
                record
                for record in self.records.values()
                if record.chunk.chunk_set is chunk_set
            ),
            key=lambda record: record.chunk.chunk_id,
        )

    async def apply(
        self,
        chunk_set: ChunkSet,
        *,
        planned_from: Mapping[str, StoredChunk],
        write: Sequence[ChunkRecord],
        move: Mapping[str, int],
        remove: Sequence[str],
        run: IngestRun,
    ) -> None:
        if self.fail_apply:
            raise StoreDown
        if self.before_apply is not None:
            self.before_apply()
        if self._stored(chunk_set) != dict(planned_from):
            raise IndexChanged
        self.applies.append(
            ([record.chunk.chunk_id for record in write], dict(move), list(remove))
        )
        for record in write:
            self.records[record.chunk.chunk_id] = record
        for chunk_id, page in move.items():
            before = self.records[chunk_id]
            self.records[chunk_id] = replace(
                before, chunk=replace(before.chunk, manual_page=page)
            )
        for chunk_id in remove:
            del self.records[chunk_id]
        self.run = run


@dataclass
class MemorySchemaRevision:
    revision: str | None
    fail: bool = False

    async def current(self) -> str | None:
        if self.fail:
            raise StoreDown
        return self.revision


# --- The search (story 2.3) ----------------------------------------------------------------


def axis(*dimensions: int) -> tuple[float, ...]:
    """A hand-made vector: 1 along each named dimension, 0 along the rest."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for dimension in dimensions:
        vector[dimension] = 1.0
    return tuple(vector)


def chunk_record(
    rule_id: str,
    text: str,
    vector: Sequence[float],
    *,
    references: Sequence[str] = (),
    chunk_set: ChunkSet = ChunkSet.SMART,
    manual_page: int = 12,
    impairment: str = "Raised blood sugar",
) -> ChunkRecord:
    """A stored chunk made by hand, for a search to find."""
    return ChunkRecord(
        chunk=Chunk(
            chunk_id=f"{chunk_set.value}-{rule_id}",
            chunk_set=chunk_set,
            rule_ids=(rule_id,),
            text=f"Rule {rule_id}: {text}",
            reference_rule_ids=tuple(references),
            section_id="2.4",
            section_title="Probable rating",
            impairment=impairment,
            manual_page=manual_page,
        ),
        context_line=f"Where {rule_id} sits.",
        embedding=tuple(vector),
        content_hash="0" * 64,
        chat_deployment=CHAT,
        embedding_deployment=EMBEDDING,
    )


def fixed_record(
    position: int,
    text: str,
    vector: Sequence[float],
    rule_ids: Sequence[str] = (),
    *,
    references: Sequence[str] = (),
    manual_page: int = 12,
    impairment: str = "Raised blood sugar",
) -> ChunkRecord:
    """A stored `fixed` chunk made by hand: a run of text that defines none, one or several rules."""
    return ChunkRecord(
        chunk=Chunk(
            chunk_id=f"fixed-{position:04d}",
            chunk_set=ChunkSet.FIXED,
            rule_ids=tuple(rule_ids),
            text=text,
            reference_rule_ids=tuple(references),
            section_id="2.4",
            section_title="Probable rating",
            impairment=impairment,
            manual_page=manual_page,
        ),
        # The plain baseline: no context line and no chat deployment.
        context_line="",
        embedding=tuple(vector),
        content_hash="0" * 64,
        chat_deployment="",
        embedding_deployment=EMBEDDING,
    )


def cosine_distance(one: Sequence[float], other: Sequence[float]) -> float:
    """1 less the cosine of the angle between two vectors, as pgvector answers it."""
    dot = sum(a * b for a, b in zip(one, other, strict=True))
    lengths = math.sqrt(sum(a * a for a in one) * sum(b * b for b in other))
    return 1.0 - dot / lengths if lengths else math.nan


def indexed(record: ChunkRecord) -> IndexedChunk:
    chunk = record.chunk
    return IndexedChunk(
        chunk_id=chunk.chunk_id,
        chunk_set=chunk.chunk_set,
        rule_ids=chunk.rule_ids,
        reference_rule_ids=chunk.reference_rule_ids,
        text=chunk.text,
        manual_page=chunk.manual_page,
        impairment=chunk.impairment,
    )


@dataclass
class MemoryIndex:
    """Stands where the chunk table would be: answers the two lists a test gives it.

    `vector_order` and `text_order` are the rule ids each side finds, best
    first. It keeps to the port's contract: only the chunk set asked for, no
    more than the limit, and a chunk that defines a rule the query names
    before the others.
    """

    records: list[ChunkRecord] = field(default_factory=list)
    vector_order: list[str] = field(default_factory=list)
    text_order: list[str] = field(default_factory=list)
    # Every read fails, as with a database that cannot be reached; or only
    # the full-text read, after the vector read was answered.
    fail: bool = False
    fail_matching: bool = False
    # What the last ingest run recorded the chunks were embedded with.
    embedding_deployment: str | None = EMBEDDING
    # What it was asked, in order: the side and the arguments.
    asked: list[tuple[Any, ...]] = field(default_factory=list)
    # Views opened and views closed again.
    opened: int = 0
    closed: int = 0

    @asynccontextmanager
    async def snapshot(self) -> AsyncIterator["MemoryIndex"]:
        if self.fail:
            raise IndexUnavailable("StoreDown")
        self.opened += 1
        try:
            yield self
        finally:
            self.closed += 1

    async def embedded_with(self, chunk_set: ChunkSet) -> str | None:
        return self.embedding_deployment

    async def content_hashes(self, chunk_set: ChunkSet) -> Mapping[str, str]:
        return {
            record.chunk.chunk_id: record.content_hash
            for record in self.records
            if record.chunk.chunk_set is chunk_set
        }

    def _chunk(self, chunk_set: ChunkSet, rule_id: str) -> IndexedChunk | None:
        """The chunk of the set that defines the rule; of several, the last by its id, as the table answers."""
        holding = [
            record
            for record in self.records
            if record.chunk.chunk_set is chunk_set and rule_id in record.chunk.rule_ids
        ]
        if not holding:
            return None
        return indexed(max(holding, key=lambda record: record.chunk.chunk_id))

    def _listed(
        self, chunk_set: ChunkSet, order: Sequence[str], limit: int
    ) -> list[IndexedChunk]:
        found = [self._chunk(chunk_set, rule_id) for rule_id in order]
        return [chunk for chunk in found if chunk is not None][:limit]

    async def nearest(
        self, chunk_set: ChunkSet, vector: Sequence[float], limit: int
    ) -> Sequence[IndexedChunk]:
        self.asked.append(("nearest", chunk_set, tuple(vector), limit))
        distances = {
            record.chunk.chunk_id: cosine_distance(record.embedding, vector)
            for record in self.records
        }
        return [
            replace(chunk, cosine_distance=distances[chunk.chunk_id])
            for chunk in self._listed(chunk_set, self.vector_order, limit)
        ]

    async def matching(
        self,
        chunk_set: ChunkSet,
        query: str,
        named_rule_ids: Sequence[str],
        limit: int,
    ) -> Sequence[IndexedChunk]:
        self.asked.append(("matching", chunk_set, query, tuple(named_rule_ids), limit))
        if self.fail_matching:
            raise IndexUnavailable("QueryCanceled")
        order = sorted(
            self.text_order, key=lambda rule_id: rule_id not in named_rule_ids
        )
        return self._listed(chunk_set, order, limit)

    async def defining(self, chunk_set: ChunkSet, rule_id: str) -> IndexedChunk | None:
        self.asked.append(("defining", chunk_set, rule_id))
        if self.fail:
            raise IndexUnavailable("StoreDown")
        return self._chunk(chunk_set, rule_id)


# --- The services' answers, for the real adapters ----------------------------------------


def completion(content: Any = None) -> dict[str, Any]:
    """A chat completion as the deployment answers it."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": CHAT,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def rerank_answer(relevance: Mapping[str, Any]) -> str:
    """The reranker's answer, as the prompt asks for it: one entry per candidate.

    A test may give a relevance that is no number, as a model might.
    """
    return json.dumps(
        {
            "ranking": [
                {"chunk_id": chunk_id, "relevance": value}
                for chunk_id, value in relevance.items()
            ]
        }
    )


def embedding_answer(
    texts: Sequence[str], dimensions: int | None = None
) -> dict[str, Any]:
    """An embedding answer as the deployment gives it, its items in reverse order."""
    items = [
        {
            "object": "embedding",
            "index": index,
            "embedding": vector_for(text)[:dimensions],
        }
        for index, text in enumerate(texts)
    ]
    return {
        "object": "list",
        "model": EMBEDDING,
        "data": list(reversed(items)),
        "usage": {"prompt_tokens": 3, "total_tokens": 3},
    }


def analyze_result(parsed: ParsedLayout | None = None) -> dict[str, Any]:
    """An `analyzeResult` of the layout model for a parsed manual."""
    parsed = parsed or manual()
    return {
        "apiVersion": "2024-11-30",
        "modelId": "prebuilt-layout",
        "content": "\n".join(paragraph.text for paragraph in parsed.paragraphs),
        "pages": [
            {
                "pageNumber": page.page_number,
                "words": [{"content": "word"}] if page.has_text else [],
                "lines": [{"content": "word"}] if page.has_text else [],
            }
            for page in parsed.pages
        ],
        "paragraphs": [
            {
                "content": paragraph.text,
                "boundingRegions": [{"pageNumber": paragraph.page_number}],
                **({"role": paragraph.role} if paragraph.role else {}),
            }
            for paragraph in parsed.paragraphs
        ],
    }


# --- Azure AI Search, for the real client (story 3.3) -----------------------------------------


class FakeCredential:
    """Stands in for the Azure identity library's credential."""

    def __init__(self, lifetime_seconds: float = 3600.0) -> None:
        self.scopes: list[str] = []
        self.lifetime_seconds = lifetime_seconds

    def get_token(self, *scopes: str, **options: Any) -> Any:
        self.scopes.extend(scopes)

        @dataclass
        class Token:
            token: str
            expires_on: float

        return Token(
            f"entra-token-{len(self.scopes)}", time.time() + self.lifetime_seconds
        )


def search_document(record: ChunkRecord) -> dict[str, Any]:
    """A stored record as the search service's index holds it."""
    return document_body(index_document(record))


@dataclass
class FakeSearchService:
    """Stands where Azure AI Search would be: an `httpx2` transport handler.

    It keeps one index in memory and speaks the REST routes the client
    calls. It ranks nothing: a query is answered with the documents a test
    names, in that order, each with the reranker score the test gives it.
    """

    definition: dict[str, Any] | None = None
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Every call is answered with this status, if set.
    status: int | None = None
    # Documents of these ids are said to be stored and are not.
    loses: set[str] = field(default_factory=set)
    # What a query answers: `chunk_id` and reranker score, best first. A
    # score of None leaves the score out, as an answer without the ranker.
    ranked: list[tuple[str, float | None]] = field(default_factory=list)
    # A query is not answered while the test runs.
    never_answers: bool = False
    # After a change, this many listings still answer what the index held
    # before it: the real service counts what it was sent a moment later.
    lag: int = 0
    _stale: int = 0
    _before: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)
    # What it was asked, in order: the call, and how many documents it carried.
    calls: list[tuple[str, int]] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def count(self, call: str) -> int:
        return sum(1 for name, _ in self.calls if name == call)

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status is not None:
            return httpx2.Response(
                self.status,
                headers={"retry-after": "0"},
                json={"error": {"code": "x", "message": "SECRET-SERVICE-MESSAGE"}},
            )
        path = request.url.path
        if request.method == "GET":
            self.calls.append(("read_index", 0))
            if self.definition is None:
                return httpx2.Response(404, json={"error": {"code": "NotFound"}})
            return httpx2.Response(200, json=self.definition)
        body = json.loads(request.content)
        if request.method == "PUT":
            self.calls.append(("create_index", 0))
            self.definition = body
            return httpx2.Response(201, json=body)
        if self.definition is None:
            return httpx2.Response(404, json={"error": {"code": "NotFound"}})
        if path.endswith("/docs/index"):
            return self._change(body["value"])
        if body.get("search") == "*":
            return self._list(body)
        self.calls.append(("query", 0))
        self.queries.append(body)
        if self.never_answers:
            await asyncio.Event().wait()
        value = []
        for chunk_id, score in self.ranked[: body["top"]]:
            document = self.documents[chunk_id]
            entry = {name: document[name] for name in body["select"].split(",")}
            if score is not None:
                entry[RERANKER_SCORE] = score
            value.append({"@search.score": 0.03, **entry})
        return httpx2.Response(200, json={"value": value})

    def _change(self, actions: list[dict[str, Any]]) -> httpx2.Response:
        deletes = [a for a in actions if a["@search.action"] == "delete"]
        self.calls.append(("delete" if deletes else "upload", len(actions)))
        if self.lag and not self._stale:
            self._before = dict(self.documents)
        self._stale = self.lag
        for action in actions:
            document = {k: v for k, v in action.items() if k != "@search.action"}
            if action["@search.action"] == "delete":
                self.documents.pop(action[KEY_FIELD], None)
            elif action[KEY_FIELD] not in self.loses:
                self.documents[action[KEY_FIELD]] = document
        results = [
            {"key": action[KEY_FIELD], "status": True, "statusCode": 200}
            for action in actions
        ]
        return httpx2.Response(200, json={"value": results})

    def _list(self, body: dict[str, Any]) -> httpx2.Response:
        self.calls.append(("list", 0))
        documents = self.documents
        if self._stale:
            self._stale -= 1
            documents = self._before
        page = sorted(documents)[body["skip"] : body["skip"] + body["top"]]
        return httpx2.Response(
            200,
            json={
                "@odata.count": len(documents),
                "value": [
                    {KEY_FIELD: chunk_id, HASH_FIELD: documents[chunk_id][HASH_FIELD]}
                    for chunk_id in page
                ],
            },
        )
