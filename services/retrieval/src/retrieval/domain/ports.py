"""What the ingestion and the search need from the outside world; adapters provide it."""

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from contracts.enums import ChunkSet
from retrieval.domain.entities import (
    ChunkRecord,
    IndexDocument,
    IndexedChunk,
    IndexHoldings,
    IngestRun,
    KnowledgeBaseReport,
    ParsedLayout,
    RankedDocument,
    Retrieval,
    StoredChunk,
)


class ManualMissing(Exception):
    """The `manual` container holds no blob of the configured name."""


class LayoutFailed(Exception):
    """The layout model did not parse the manual.

    `reason` is a short code for the log, never a message of the service:
    such a message could hold text of the document.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ModelUnavailable(Exception):
    """AD-16: the model gateway gave up after its retries."""


class ModelCallFailed(Exception):
    """A model refused the call, and would refuse it again. `reason` is a short code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ModelAnswerInvalid(Exception):
    """A model's answer cannot be matched to what was asked. `reason` is a short code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class IndexChanged(Exception):
    """The stored chunk set is no longer what the run planned from: another run wrote it."""


class ManualStore(Protocol):
    """The `manual` container (AD-4)."""

    async def read(self) -> bytes:
        """The manual PDF. Raises `ManualMissing` when it is not there."""
        ...


class LayoutParser(Protocol):
    """Document Intelligence's layout model (AD-12)."""

    async def parse(self, pdf: bytes) -> ParsedLayout:
        """The pages and paragraphs of the PDF. Raises `LayoutFailed`."""
        ...


class ChunkModel(Protocol):
    """The chat deployment and the embedding deployment, behind the one gateway (AD-16)."""

    async def context_line(self, rule_in_its_place: str) -> str:
        """Ask the chat model for one chunk's context line; its answer as it gave it.

        The answer is not looked at here: the caller parses it. Raises
        `ModelUnavailable` or `ModelCallFailed`.
        """
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the order given, as the model gave them.

        Raises `ModelUnavailable` or `ModelCallFailed`, and
        `ModelAnswerInvalid` when the answer does not hold one vector for
        each text.
        """
        ...


class ChunkRepository(Protocol):
    """The chunk table of schema `retrieval`."""

    async def stored(self, chunk_set: ChunkSet) -> Mapping[str, StoredChunk]:
        """What is stored of one chunk set, by `chunk_id`."""
        ...

    async def last_run(self, chunk_set: ChunkSet) -> IngestRun | None:
        """What the last successful run built the chunk set from, if one is recorded."""
        ...

    async def chunk_records(self, chunk_set: ChunkSet) -> Sequence[ChunkRecord]:
        """Every stored record of one chunk set, whole, in the order of their `chunk_id`."""
        ...

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
        """Bring the chunk set to what a run found, in one transaction.

        `write` inserts or replaces whole records; `move` sets the manual
        page of a chunk, by `chunk_id`; `remove` deletes; `run` is recorded
        as what the set was built from. Nothing else is touched, and a
        failure leaves everything as it was. One run at a time: when what
        is stored is no longer `planned_from`, which the run read at its
        start, nothing is written and `IndexChanged` is raised.
        """
        ...


class ModelNotConfigured(Exception):
    """The service was told of no embedding deployment: it cannot search at all."""


class IndexUnavailable(Exception):
    """The stored chunks could not be read just now. `reason` is a short code, never a message."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class QueryEmbedder(Protocol):
    """The one embedding deployment, behind the gateway (AD-16)."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, as `ChunkModel.embed` gives them, with the same errors.

        Raises `ModelNotConfigured` when there is no deployment to ask.
        """
        ...


class Reranker(Protocol):
    """AD-11, row `r4`: the chat deployment, asked how relevant each candidate is to a query."""

    async def relevance(self, query_and_candidates: str) -> str:
        """Ask the chat model for the relevance of each candidate; its answer as it gave it.

        The answer is not looked at here: the caller parses it. Raises
        `ModelUnavailable` or `ModelCallFailed`.
        """
        ...


class IndexSnapshot(Protocol):
    """The stored chunks as they are at one moment: every read of it sees the same index.

    Each search answers its own ranked list, best first, and is of no use to
    the other: a row that needs only one of them calls only that one.
    """

    async def embedded_with(self, chunk_set: ChunkSet) -> str | None:
        """The embedding deployment the last ingest run of the set recorded, if one is recorded."""
        ...

    async def content_hashes(self, chunk_set: ChunkSet) -> Mapping[str, str]:
        """The `content_hash` of every stored chunk of the set, by `chunk_id`."""
        ...

    async def nearest(
        self, chunk_set: ChunkSet, vector: Sequence[float], limit: int
    ) -> Sequence[IndexedChunk]:
        """The chunks whose vectors are nearest the given one by cosine, nearest first.

        Exact, not approximate (AD-12). Chunks equally near come in the
        order of their `chunk_id`. Each chunk carries its `cosine_distance`.
        """
        ...

    async def matching(
        self,
        chunk_set: ChunkSet,
        query: str,
        named_rule_ids: Sequence[str],
        limit: int,
    ) -> Sequence[IndexedChunk]:
        """The chunks whose text holds words of the query, best match first.

        A chunk needs only some of the words. A chunk that defines one of
        `named_rule_ids` comes before every chunk that does not. Chunks that
        match equally well come in the order of their `chunk_id`. A query
        without a word the text search keeps matches nothing.
        """
        ...


class ChunkIndex(Protocol):
    """The stored chunks, read-only. Every read raises `IndexUnavailable` when the store fails."""

    def snapshot(self) -> AbstractAsyncContextManager[IndexSnapshot]:
        """One unchanging, read-only view of the index for the reads of one search."""
        ...

    async def defining(self, chunk_set: ChunkSet, rule_id: str) -> IndexedChunk | None:
        """The chunk of the set that holds the rule's definition marker, or None when none does.

        When two `fixed` chunks hold it (a marker inside an overlap), the later one.
        """
        ...


class SearchServiceUnavailable(Exception):
    """The search service gave no usable answer. `reason` is a short code, never a message."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class KnowledgeBaseMissing(Exception):
    """The search service holds no knowledge base of the configured name: the job has not made it."""


class KnowledgeBaseUnusable(Exception):
    """What the search service holds is not what row `r6` needs. `reason` is a short code.

    A knowledge source or knowledge base that is there and reads something
    else than the settings name, or an index without a vectorizer.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RuleSearchService(Protocol):
    """AD-11, row `r5`: the search service's index of the `smart` chunks, as a search asks it."""

    async def hybrid(
        self, query: str, vector: Sequence[float], top: int, candidates: int
    ) -> Sequence[RankedDocument]:
        """The best `top` documents for the text and the vector together, best first.

        `candidates` is how many documents the vector side hands to the
        fusion. Text search and exact vector search, fused and then ordered by the
        service's semantic ranker; each document carries that ranker's
        score. Raises `SearchServiceUnavailable`, also when the service
        answers without the ranker's scores: there is no partial answer.
        """
        ...


class AgenticRetriever(Protocol):
    """AD-11, row `r6`: the search service's knowledge base over the index of the `smart` chunks."""

    async def retrieve(self, query: str, top: int) -> Retrieval:
        """The references the knowledge base returns for a query, at most `top`, in its order.

        The service plans and runs its own queries on the index with its
        own model. No answer is asked of it, only the documents it found.
        Raises `SearchServiceUnavailable`, also for an answer that is only
        partly there, and `KnowledgeBaseMissing` when the service holds no
        such knowledge base.
        """
        ...


class KnowledgeBaseStore(Protocol):
    """The knowledge source and knowledge base of row `r6`, as the ingestion job makes them."""

    async def ensure(self) -> KnowledgeBaseReport:
        """Create the knowledge source and the knowledge base where the service has none of that name.

        One that is there must be what would be created, and the index
        must name a vectorizer: `KnowledgeBaseUnusable` otherwise. Raises
        `SearchServiceUnavailable`.
        """
        ...


class SearchIndexStore(Protocol):
    """The search service's index, as the ingestion job loads it. Every call raises `SearchServiceUnavailable`."""

    async def ensure(self) -> bool:
        """Create the index if the service has none of its name; whether it was created."""
        ...

    async def held(self) -> IndexHoldings:
        """The service's count of its documents, and each one's hash by `chunk_id`."""
        ...

    async def upload(self, documents: Sequence[IndexDocument]) -> None:
        """Store the documents, each in place of the one of its `chunk_id` if there is one."""
        ...

    async def remove(self, chunk_ids: Sequence[str]) -> None:
        """Delete the documents of these ids; an id the index does not hold is no error."""
        ...
