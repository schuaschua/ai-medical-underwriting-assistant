"""What the ingestion and the search need from the outside world; adapters provide it."""

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from contracts.enums import ChunkSet
from retrieval.domain.entities import (
    ChunkRecord,
    IndexedChunk,
    IngestRun,
    ParsedLayout,
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


class IndexSnapshot(Protocol):
    """The stored chunks as they are at one moment: every read of it sees the same index.

    Each search answers its own ranked list, best first, and is of no use to
    the other: a row that needs only one of them calls only that one.
    """

    async def embedded_with(self, chunk_set: ChunkSet) -> str | None:
        """The embedding deployment the last ingest run of the set recorded, if one is recorded."""
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
