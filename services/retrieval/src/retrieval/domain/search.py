"""The two reads of `retrieval`: the search and the rule read (spine AD-11, AD-12).

One search operation for every ladder row; row `r3` is the one built. Its
steps, each a function of its own: the query is embedded (`embed_query`),
the vector search and the full-text search each answer a ranked list of
candidates (the index port), the two lists are fused
(`fusion.reciprocal_rank_fusion`), and the best of the fused list become the
ranked items (`rank_items`). Nothing is stored, nothing is cached, and no
model rewrites the query. Both lists are read from one unchanging view of
the index, and one deadline covers the whole search.
"""

import asyncio
import logging
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from contracts.enums import ChunkSet, RetrieverConfig
from contracts.errors import DomainError, ErrorCode
from contracts.models.retrieval import (
    RuleText,
    SearchItem,
    SearchRequest,
    SearchResponse,
)
from retrieval.domain.entities import EMBEDDING_DIMENSIONS, IndexedChunk
from retrieval.domain.fusion import Fused, reciprocal_rank_fusion
from retrieval.domain.ports import (
    ChunkIndex,
    IndexUnavailable,
    ModelAnswerInvalid,
    ModelCallFailed,
    ModelNotConfigured,
    ModelUnavailable,
    QueryEmbedder,
)
from retrieval.domain.rows import (
    ROW_NOT_AVAILABLE_MESSAGE,
    SearchMethod,
    chunk_set_to_read,
    row_to_search,
)

logger = logging.getLogger(__name__)

MODEL_UNAVAILABLE_MESSAGE = "The model is not available. Please try again shortly."
MODEL_REFUSED_MESSAGE = "The model refused the call."
INVALID_OUTPUT_MESSAGE = "The model's answer was not what was asked for."
RULE_NOT_FOUND_MESSAGE = "The manual defines no rule of that id."
INVALID_QUERY_MESSAGE = "The request is not valid."
NOT_CONFIGURED_MESSAGE = "This service is not configured to search the manual."
INDEX_UNAVAILABLE_MESSAGE = (
    "The manual's index is not available. Please try again shortly."
)
MODEL_TOO_SLOW_MESSAGE = "The model did not answer in time. Please try again shortly."
INDEX_TOO_SLOW_MESSAGE = (
    "The manual's index did not answer in time. Please try again shortly."
)
ANOTHER_DEPLOYMENT_MESSAGE = (
    "The search is not available: the manual was indexed with another embedding "
    "model than the one this service is set to use. An operator must set the "
    "service to that model, or ingest the manual again."
)
# The largest value a stored vector's 4-byte float holds: pgvector takes no larger one.
FLOAT4_MAX = 3.4028234663852886e38
# A character no text of the database or of a model request may hold.
_NUL = "\x00"

# A rule id as someone writes it in a query: its letters in either case, and
# neither preceded nor followed by an ASCII letter, a digit or an underscore.
# The one definition for a query: `rule_ids_named_in` reads it here, and the
# full-text search rewrites the query with the same pattern in SQL
# (`adapters/index.py`). Its two groups are the letters and the number.
NAMED_RULE_ID = r"(?<![A-Za-z0-9_])[Uu][Ww]-([A-Za-z]{2,4})-([0-9]{3})(?![A-Za-z0-9_])"
_NAMED_RULE_ID = re.compile(NAMED_RULE_ID)


@dataclass(frozen=True, slots=True)
class SearchPorts:
    model: QueryEmbedder
    index: ChunkIndex


@dataclass(frozen=True, slots=True)
class SearchOptions:
    """What a search works with, as the settings say."""

    # How many chunks each side of a hybrid search hands to the fusion, and
    # never fewer than twice `top_k`: a chunk that is far down one list and
    # high in the other must still be weighed.
    candidate_depth: int = 50
    # The whole search ends after this long; None is no limit.
    deadline_seconds: float | None = 8.0
    # AD-16: the deployment queries are embedded with. A search is refused
    # when the index says its chunks were embedded with another one. None
    # when the service has no model, and then no search gets that far.
    embedding_deployment: str | None = None


@dataclass(slots=True)
class SearchStats:
    """What one search did, for its span and its log line. Counts only, never the query."""

    depth: int = 0
    embedded: bool = False
    vector_candidates: int = 0
    full_text_candidates: int = 0
    items: int = 0


def candidate_depth(top_k: int, options: SearchOptions) -> int:
    """How many candidates each side is asked for: the setting, or twice `top_k` if that is more."""
    return max(options.candidate_depth, 2 * top_k)


def rule_ids_named_in(query: str) -> tuple[str, ...]:
    """The rule ids written in a query, as the manual prints them, in order of first mention."""
    return tuple(
        dict.fromkeys(
            f"UW-{letters.upper()}-{number}"
            for letters, number in _NAMED_RULE_ID.findall(query)
        )
    )


async def embed_query(query: str, model: QueryEmbedder) -> tuple[float, ...]:
    """The query's vector, from the deployment the chunks were embedded with.

    The text is sent exactly as it was asked, as a chunk's text is at
    ingestion: nothing is put before it, taken from it or changed in it.
    """
    try:
        vectors = await model.embed([query])
    except ModelNotConfigured:
        logger.warning("search refused: no embedding deployment is configured")
        raise DomainError(ErrorCode.MODEL_UNAVAILABLE, NOT_CONFIGURED_MESSAGE) from None
    except ModelUnavailable:
        raise DomainError(
            ErrorCode.MODEL_UNAVAILABLE, MODEL_UNAVAILABLE_MESSAGE
        ) from None
    except ModelCallFailed as error:
        # Codes only (security rule 31): never the query.
        logger.error("query embedding refused: reason=%s", error.reason)
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, MODEL_REFUSED_MESSAGE
        ) from None
    except ModelAnswerInvalid as error:
        raise _invalid_output(error.reason) from None
    if len(vectors) != 1:
        raise _invalid_output("embedding_count_differs")
    (vector,) = vectors
    # A vector of another size, or one that is no vector, would be compared
    # with the stored ones as if it meant something.
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise _invalid_output("embedding_wrong_size")
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in vector
    ):
        raise _invalid_output("embedding_not_numbers")
    if any(abs(value) > FLOAT4_MAX for value in vector):
        raise _invalid_output("embedding_out_of_range")
    # A vector without a direction is equally near every chunk: the vector
    # side would answer the chunks in the order of their ids.
    if not any(vector):
        raise _invalid_output("embedding_all_zero")
    return tuple(float(value) for value in vector)


def _invalid_output(reason: str) -> DomainError:
    logger.error("query embedding invalid: reason=%s", reason)
    return DomainError(ErrorCode.INVALID_MODEL_OUTPUT, INVALID_OUTPUT_MESSAGE)


def rank_items(
    fused: Sequence[Fused], chunks: Mapping[str, IndexedChunk], top_k: int
) -> list[SearchItem]:
    """The best `top_k` of a fused list as the items of the answer, ranked from 1 without gaps."""
    items: list[SearchItem] = []
    for rank, entry in enumerate(fused[:top_k], start=1):
        chunk = chunks[entry.chunk_id]
        items.append(
            SearchItem(
                chunk_id=chunk.chunk_id,
                rule_ids=list(chunk.rule_ids),
                rank=rank,
                # The fused score as it was computed: no rescaling.
                score=entry.score,
                text=chunk.text,
                manual_page=chunk.manual_page,
                impairment=chunk.impairment,
            )
        )
    return items


async def hybrid_search(
    query: str,
    chunk_set: ChunkSet,
    top_k: int,
    *,
    ports: SearchPorts,
    options: SearchOptions,
    stats: SearchStats,
) -> list[SearchItem]:
    """Row `r3`: vector and full-text candidates, fused into the ranked items."""
    stats.depth = depth = candidate_depth(top_k, options)
    vector = await embed_query(query, ports.model)
    stats.embedded = True
    # Both lists from one view of the index: an ingestion that ends between
    # the two reads cannot give the fusion two different indexes.
    async with ports.index.snapshot() as index:
        check_deployment(await index.embedded_with(chunk_set), options)
        nearest = await index.nearest(chunk_set, vector, depth)
        matching = await index.matching(
            chunk_set, query, rule_ids_named_in(query), depth
        )
    stats.vector_candidates, stats.full_text_candidates = len(nearest), len(matching)
    fused = reciprocal_rank_fusion(
        [chunk.chunk_id for chunk in nearest],
        [chunk.chunk_id for chunk in matching],
    )
    chunks = {chunk.chunk_id: chunk for chunk in (*nearest, *matching)}
    return rank_items(fused, chunks, top_k)


def check_deployment(indexed_with: str | None, options: SearchOptions) -> None:
    """Refuse a search whose query was embedded by another deployment than the chunks.

    Vectors of two models are not comparable, and the search would answer
    plausible nonsense. An index that records no run (an empty one) has
    nothing to compare.
    """
    configured = options.embedding_deployment
    if indexed_with is None or configured is None or indexed_with == configured:
        return
    # Deployment names are settings, not content.
    logger.error(
        "search refused: embedding deployment differs: configured=%s index=%s",
        configured,
        indexed_with,
    )
    raise DomainError(ErrorCode.MODEL_UNAVAILABLE, ANOTHER_DEPLOYMENT_MESSAGE)


async def search_rules(
    request: SearchRequest,
    *,
    ports: SearchPorts,
    options: SearchOptions,
    clock: Callable[[], float] = time.perf_counter,
    stats: SearchStats | None = None,
) -> SearchResponse:
    """Search the manual with one ladder row; the ranked chunks, or a `DomainError`.

    `latency_ms` is the time this service spent on the search, the embedding
    call included. A row that is not built is refused before anything is
    spent. `stats`, when given, is filled in as the search goes, also when
    it fails.
    """
    stats = stats if stats is not None else SearchStats()
    if _NUL in request.query:
        raise DomainError(ErrorCode.VALIDATION_FAILED, INVALID_QUERY_MESSAGE)
    row = row_to_search(request.retriever_config)
    if row.method is not SearchMethod.HYBRID:
        # A row marked as built without a search of its own: said as what it
        # is to the caller, never answered with another row's results.
        raise DomainError(ErrorCode.RETRIEVER_NOT_AVAILABLE, ROW_NOT_AVAILABLE_MESSAGE)
    started = clock()
    try:
        async with asyncio.timeout(options.deadline_seconds) as deadline:
            items = await hybrid_search(
                request.query,
                row.chunk_set,
                request.top_k,
                ports=ports,
                options=options,
                stats=stats,
            )
    except IndexUnavailable as error:
        logger.warning("search failed: index unavailable: reason=%s", error.reason)
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, INDEX_UNAVAILABLE_MESSAGE
        ) from None
    except TimeoutError:
        # Only the search's own deadline: a time-out of something a port
        # called is that port's failure, and is raised as it is.
        if not deadline.expired():
            raise
        waited_for = "index" if stats.embedded else "model"
        logger.warning(
            "search deadline passed: retriever_config=%s waited_for=%s",
            row.config.value,
            waited_for,
        )
        if stats.embedded:
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, INDEX_TOO_SLOW_MESSAGE
            ) from None
        raise DomainError(ErrorCode.MODEL_UNAVAILABLE, MODEL_TOO_SLOW_MESSAGE) from None
    stats.items = len(items)
    latency_ms = max(0, round((clock() - started) * 1000))
    # security rule 31: the row, counts and the timing, never the query.
    logger.info(
        "search: retriever_config=%s top_k=%d vector_candidates=%d "
        "full_text_candidates=%d items=%d latency_ms=%d",
        row.config.value,
        request.top_k,
        stats.vector_candidates,
        stats.full_text_candidates,
        len(items),
        latency_ms,
    )
    return SearchResponse(
        retriever_config=row.config, latency_ms=latency_ms, items=items
    )


async def read_rule(
    rule_id: str, retriever_config: RetrieverConfig | None, *, index: ChunkIndex
) -> RuleText:
    """The chunk that defines a rule, from the chunk set of the row named; `not_found` when none does."""
    chunk_set = chunk_set_to_read(retriever_config)
    try:
        chunk = await index.defining(chunk_set, rule_id)
    except IndexUnavailable as error:
        logger.warning("rule read failed: index unavailable: reason=%s", error.reason)
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, INDEX_UNAVAILABLE_MESSAGE
        ) from None
    if chunk is None:
        raise DomainError(ErrorCode.NOT_FOUND, RULE_NOT_FOUND_MESSAGE)
    return RuleText(
        rule_id=rule_id,
        chunk_id=chunk.chunk_id,
        chunk_set=chunk.chunk_set,
        text=chunk.text,
        manual_page=chunk.manual_page,
        impairment=chunk.impairment,
        reference_rule_ids=list(chunk.reference_rule_ids),
    )
