"""PostgreSQL adapter: the two searches of a hybrid row and the rule read (spine AD-11, AD-12).

Every value is a bound parameter (security rule 21), and every read runs
in a read-only transaction: the database itself refuses a write there. The
vector search and the full-text search are two statements of their own, so
that a row which needs only one of them runs only that one; a search runs
them on one connection in one repeatable-read transaction, so both see the
same index. A database that cannot be reached, or that ends a statement for
its time limit, is `IndexUnavailable`, logged by the error's type only.

The full-text side: the query is turned into words with `plainto_tsquery`
(it takes any text, and nothing a caller writes is read as an operator), in
the configuration the stored column was built with, after every rule id in
it is written as one word, as in the stored column. `plainto_tsquery` asks
for all of the words; its `&` operators are made `|`, so that a chunk which
holds some of the words matches: a query is a sentence about a fact, not a keyword
list, and no chunk holds all of a sentence. The matches are ranked with
`ts_rank`, which adds up what each word of the query contributes and lets a
repeated word count for less each time; `ts_rank_cd` ranks by how near the
words stand to each other, which only means something when all of them are
required. The rank is divided by 1 + the logarithm of the chunk's length
(normalisation 1), so that a long chunk is not ahead for its length alone.
"""

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from opentelemetry import trace
from sqlalchemy import (
    Float,
    Select,
    Text,
    bindparam,
    cast,
    func,
    literal_column,
    select,
)
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from contracts.enums import ChunkSet
from retrieval.adapters.db import (
    RULE_ID_AS_ONE_WORD,
    TEXT_SEARCH_CONFIG,
    Database,
    chunk_table,
    ingest_run_table,
)
from retrieval.adapters.telemetry import adapter_span
from retrieval.domain.entities import IndexedChunk
from retrieval.domain.ports import IndexUnavailable
from retrieval.domain.search import NAMED_RULE_ID
from retrieval.settings import APP_ID

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

# `ts_rank`'s normalisation: divide the rank by 1 + log(document length).
RANK_BY_LOG_LENGTH = 1
# Every rule id of a query, not only the first. What a rule id in a query
# is, is the domain's one definition (`NAMED_RULE_ID`); the stored text
# prints its ids in capitals and is rewritten by the stored column itself.
_EVERY_MATCH = "g"
# How `plainto_tsquery` prints the operator between two words. Every word is
# printed in quotes, a quote inside a word is printed twice, and no word
# holds a space, so this text is never part of a word.
_AND_BETWEEN_WORDS = "' & '"
_OR_BETWEEN_WORDS = "' | '"

# The name the vector search gives the distance it orders by.
COSINE_DISTANCE = "cosine_distance"

_CHUNK = (
    chunk_table.c.chunk_id,
    chunk_table.c.chunk_set,
    chunk_table.c.rule_ids,
    chunk_table.c.reference_rule_ids,
    chunk_table.c.text,
    chunk_table.c.manual_page,
    chunk_table.c.impairment,
)


def _chunk(row: Any) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=row.chunk_id,
        chunk_set=ChunkSet(row.chunk_set),
        rule_ids=tuple(row.rule_ids),
        reference_rule_ids=tuple(row.reference_rule_ids),
        text=row.text,
        manual_page=row.manual_page,
        impairment=row.impairment,
        # Only the vector search selects it.
        cosine_distance=getattr(row, COSINE_DISTANCE, None),
    )


def any_word_of(query: ColumnElement[str]) -> ColumnElement[Any]:
    """The full-text query that a chunk matches when it holds any word of the text.

    Only the operators `plainto_tsquery` wrote between two words are
    changed: a `&` inside a word (an address, say) stays as it is.
    """
    config: ColumnElement[Any] = literal_column(f"'{TEXT_SEARCH_CONFIG}'::regconfig")
    one_word_ids = func.regexp_replace(
        query, NAMED_RULE_ID, RULE_ID_AS_ONE_WORD, _EVERY_MATCH
    )
    all_words = func.plainto_tsquery(config, one_word_ids)
    return cast(
        func.replace(cast(all_words, Text), _AND_BETWEEN_WORDS, _OR_BETWEEN_WORDS),
        TSQUERY,
    )


def nearest_statement(
    chunk_set: ChunkSet, vector: Sequence[float], limit: int
) -> Select[Any]:
    """Exact cosine nearest-neighbour: every chunk of the set is compared (AD-12).

    Each chunk comes with its distance, which the vector-only rows score by.
    """
    distance = chunk_table.c.embedding.cosine_distance(list(vector))
    return (
        select(*_CHUNK, cast(distance, Float).label(COSINE_DISTANCE))
        .where(chunk_table.c.chunk_set == chunk_set.value)
        .order_by(distance, chunk_table.c.chunk_id)
        .limit(limit)
    )


def matching_statement(
    chunk_set: ChunkSet, query: str, named_rule_ids: Sequence[str], limit: int
) -> Select[Any]:
    """Full-text search over the stored column, best match first."""
    asked = select(any_word_of(bindparam("query", query, type_=Text))).scalar_subquery()
    rank = cast(
        func.ts_rank(chunk_table.c.text_search, asked, RANK_BY_LOG_LENGTH), Float
    )
    # A chunk that defines a rule the query names by its id comes first: the
    # chunks that only refer to that rule hold the id just as often.
    rule_ids = chunk_table.c.rule_ids
    defines_a_named_rule = rule_ids.overlap(
        cast(
            bindparam("named_rule_ids", list(named_rule_ids), type_=rule_ids.type),
            rule_ids.type,
        )
    )
    return (
        select(*_CHUNK)
        .where(
            chunk_table.c.chunk_set == chunk_set.value,
            chunk_table.c.text_search.bool_op("@@")(asked),
        )
        .order_by(defines_a_named_rule.desc(), rank.desc(), chunk_table.c.chunk_id)
        .limit(limit)
    )


def defining_statement(chunk_set: ChunkSet, rule_id: str) -> Select[Any]:
    """The chunk of the set that holds the rule's definition marker.

    One chunk in the `smart` set. In the `fixed` set a marker inside an
    overlap is in two chunks: the later one is answered, in which the
    marker stands near the start and most of the definition follows it.
    """
    return (
        select(*_CHUNK)
        .where(
            chunk_table.c.chunk_set == chunk_set.value,
            chunk_table.c.rule_ids.contains([rule_id]),
        )
        .order_by(chunk_table.c.chunk_id.desc())
        .limit(1)
    )


def embedded_with_statement(chunk_set: ChunkSet) -> Select[Any]:
    """The embedding deployment the last successful ingest run of the set recorded."""
    return select(ingest_run_table.c.embedding_deployment).where(
        ingest_run_table.c.chunk_set == chunk_set.value
    )


@asynccontextmanager
async def read_only_transaction(database: Database) -> AsyncIterator[AsyncConnection]:
    """One connection in one read-only, repeatable-read transaction.

    Every statement in it sees the index as it was at the first one, and the
    server refuses any write.
    """
    async with database.connect() as connection:
        connection = await connection.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        async with connection.begin():
            yield connection


def _unavailable(error: SQLAlchemyError) -> IndexUnavailable:
    """What a failed read is to the domain: the error's type, never its message."""
    kind = type(getattr(error, "orig", None) or error).__qualname__
    # security rule 31: a driver's message holds the statement or the address.
    logger.warning("index read failed: type=%s", kind)
    return IndexUnavailable(kind)


async def _read(
    connection: AsyncConnection, span_name: str, statement: Select[Any]
) -> list[IndexedChunk]:
    with adapter_span(tracer, span_name) as span:
        result = await connection.execute(statement)
        chunks = [_chunk(row) for row in result]
        span.set_attribute("retrieval.chunks.count", len(chunks))
        return chunks


class SqlIndexSnapshot:
    """The reads of one search, all on the one transaction it was opened with."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def embedded_with(self, chunk_set: ChunkSet) -> str | None:
        with adapter_span(tracer, "retrieval.db.read_ingest_run"):
            result = await self._connection.execute(embedded_with_statement(chunk_set))
            deployment: str | None = result.scalar_one_or_none()
            return deployment

    async def nearest(
        self, chunk_set: ChunkSet, vector: Sequence[float], limit: int
    ) -> Sequence[IndexedChunk]:
        return await _read(
            self._connection,
            "retrieval.db.vector_search",
            nearest_statement(chunk_set, vector, limit),
        )

    async def matching(
        self,
        chunk_set: ChunkSet,
        query: str,
        named_rule_ids: Sequence[str],
        limit: int,
    ) -> Sequence[IndexedChunk]:
        return await _read(
            self._connection,
            "retrieval.db.full_text_search",
            matching_statement(chunk_set, query, named_rule_ids, limit),
        )


class SqlChunkIndex:
    """The chunk table, read as a search reads it. Each read has its span and its time limit."""

    def __init__(self, database: Database) -> None:
        self._database = database

    @asynccontextmanager
    async def snapshot(self) -> AsyncIterator[SqlIndexSnapshot]:
        try:
            async with read_only_transaction(self._database) as connection:
                yield SqlIndexSnapshot(connection)
        except SQLAlchemyError as error:
            raise _unavailable(error) from None

    async def defining(self, chunk_set: ChunkSet, rule_id: str) -> IndexedChunk | None:
        try:
            async with read_only_transaction(self._database) as connection:
                chunks = await _read(
                    connection,
                    "retrieval.db.read_rule",
                    defining_statement(chunk_set, rule_id),
                )
        except SQLAlchemyError as error:
            raise _unavailable(error) from None
        return chunks[0] if chunks else None
