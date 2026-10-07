"""The two reads of `retrieval`: the search and the rule read (spine AD-11, AD-12).

One search operation for every ladder row; rows `r1`, `r2`, `r3` and `r5`
are built. The steps of `r3`, each a function of its own: the query is embedded
(`embed_query`), the vector search and the full-text search each answer a
ranked list of candidates (the index port), the two lists are fused
(`fusion.reciprocal_rank_fusion`), and the best of the fused list become the
ranked items (`rank_items`). The baseline rows `r1` and `r2` embed the query
the same way and answer the vector search's list alone (`vector_search`),
over the `fixed` and the `smart` chunks. Row `r5` embeds the query the same
way and hands the text and the vector to Azure AI Search (`ai_search_hybrid`),
whose index holds a copy of the `smart` chunks: the store is the one thing
that differs. Nothing is stored, nothing is
cached, and no model rewrites the query. Every read of a search is from one
unchanging view of the index, and one deadline covers the whole search.
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
from retrieval.domain.chunker import definition_in, references_in
from retrieval.domain.entities import (
    EMBEDDING_DIMENSIONS,
    IndexedChunk,
    RankedDocument,
)
from retrieval.domain.fusion import Fused, reciprocal_rank_fusion
from retrieval.domain.ports import (
    ChunkIndex,
    IndexUnavailable,
    ModelAnswerInvalid,
    ModelCallFailed,
    ModelNotConfigured,
    ModelUnavailable,
    QueryEmbedder,
    RuleSearchService,
    SearchServiceUnavailable,
)
from retrieval.domain.rows import (
    CHUNK_SET_NOT_INGESTED_MESSAGE,
    SearchMethod,
    available_rows,
    chunk_set_to_read,
    row_not_available_message,
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
SEARCH_SERVICE_UNAVAILABLE_MESSAGE = (
    "The search service is not available. Please try again shortly."
)
SEARCH_SERVICE_TOO_SLOW_MESSAGE = (
    "The search service did not answer in time. Please try again shortly."
)
# AD-11, row `r5`: the largest score Azure AI Search's semantic ranker gives.
# Its scores run from 0 (irrelevant) to 4 (the best answer) and are no
# probability. A row's `score` is that score divided by this, so 0 to 1 and
# larger is better; the order of the items is the service's own.
RERANKER_MAX_SCORE = 4.0
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
    # AD-11, row `r5`: Azure AI Search. None when the service was told of
    # no search endpoint: the row is then refused as not available.
    search_service: RuleSearchService | None = None


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
    # Row `r5`: whether the search service was asked, how many documents it
    # answered, and how many of them were left out because pgvector holds
    # no chunk of their id (an index behind the chunk table).
    service_asked: bool = False
    service_documents: int = 0
    left_out: int = 0
    # The largest score the service's ranker gave a document of the answer,
    # as the service gave it; None when it answered none.
    max_reranker_score: float | None = None


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


def similarity(cosine_distance: float | None) -> float:
    """A vector search's score: 1 for the same direction, 0.5 for none in common, 0 for the opposite.

    The cosine similarity, moved from its -1 to 1 onto the contract's 0 to
    1 without cutting anything off, so that the order of the scores is the
    order of the distances. A distance that is no number (a stored vector
    without a direction) scores 0.
    """
    if cosine_distance is None or not math.isfinite(cosine_distance):
        return 0.0
    return min(1.0, max(0.0, 1.0 - cosine_distance / 2.0))


async def vector_search(
    query: str,
    chunk_set: ChunkSet,
    top_k: int,
    *,
    ports: SearchPorts,
    options: SearchOptions,
    stats: SearchStats,
) -> list[SearchItem]:
    """Rows `r1` and `r2`: the chunks nearest the query's vector, and nothing else.

    No full-text search and no fusion. The items come in the index's order:
    nearest first, and chunks equally near in the order of their `chunk_id`.
    """
    stats.depth = top_k
    vector = await embed_query(query, ports.model)
    stats.embedded = True
    async with ports.index.snapshot() as index:
        check_deployment(await index.embedded_with(chunk_set), options)
        nearest = await index.nearest(chunk_set, vector, top_k)
    stats.vector_candidates = len(nearest)
    return [
        SearchItem(
            chunk_id=chunk.chunk_id,
            rule_ids=list(chunk.rule_ids),
            rank=rank,
            score=similarity(chunk.cosine_distance),
            text=chunk.text,
            manual_page=chunk.manual_page,
            impairment=chunk.impairment,
        )
        for rank, chunk in enumerate(nearest[:top_k], start=1)
    ]


def reranker_score(score: float) -> float:
    """Row `r5`'s score: the semantic ranker's 0 to 4, divided by 4.

    A fixed rule, not a probability: 1 is the ranker's best score and 0 its
    worst. A score outside the documented range is cut to it.
    """
    return min(1.0, max(0.0, score / RERANKER_MAX_SCORE))


async def ai_search_hybrid(
    query: str,
    chunk_set: ChunkSet,
    top_k: int,
    *,
    ports: SearchPorts,
    options: SearchOptions,
    stats: SearchStats,
) -> list[SearchItem]:
    """Row `r5`: Azure AI Search's hybrid search with its semantic ranker, over the same chunks.

    The query is embedded once, with the deployment the chunks were
    embedded with, and sent as text and vector together. The items are the
    service's documents in the service's order. A document whose id
    pgvector does not hold, or holds with another content hash, is left out
    and counted: the index is then behind the chunk table (a load failed),
    and the search must not answer a text the rule read no longer does.
    The vector side hands the fusion as many candidates as row `r3`'s does.
    """
    service = ports.search_service
    if service is None:
        # `row_to_search` refuses the row before this is reached.
        raise DomainError(
            ErrorCode.RETRIEVER_NOT_AVAILABLE,
            row_not_available_message(available_rows(search_service=False)),
        )
    stats.depth = depth = candidate_depth(top_k, options)
    vector = await embed_query(query, ports.model)
    stats.embedded = True
    async with ports.index.snapshot() as index:
        check_deployment(await index.embedded_with(chunk_set), options)
        stored = await index.content_hashes(chunk_set)
    stats.service_asked = True
    documents = await service.hybrid(query, vector, top_k, depth)
    stats.service_documents = len(documents)
    stats.max_reranker_score = max(
        (document.reranker_score for document in documents), default=None
    )
    _check_documents_deployment(documents, options)
    kept = [
        document
        for document in documents
        if stored.get(document.chunk_id) == document.content_hash
    ]
    stats.left_out = len(documents) - len(kept)
    if stats.left_out:
        # Ids only (security rule 31).
        logger.warning(
            "search service answered chunks pgvector does not hold: count=%d "
            "chunk_ids=%s",
            stats.left_out,
            ",".join(
                document.chunk_id for document in documents if document not in kept
            ),
        )
    return [
        SearchItem(
            chunk_id=document.chunk_id,
            rule_ids=list(document.rule_ids),
            # The service's order, counted without gaps.
            rank=rank,
            score=reranker_score(document.reranker_score),
            text=document.text,
            manual_page=document.manual_page,
            impairment=document.impairment,
        )
        for rank, document in enumerate(kept[:top_k], start=1)
    ]


def _check_documents_deployment(
    documents: Sequence[RankedDocument], options: SearchOptions
) -> None:
    """Refuse an answer from documents another embedding deployment made the vectors of.

    The run record says what pgvector holds; the index says it for itself,
    document by document, because a load that failed leaves it behind.
    """
    configured = options.embedding_deployment
    if configured is None:
        return
    others = {document.embedding_deployment for document in documents} - {configured}
    if not others:
        return
    logger.error(
        "search refused: embedding deployment differs: configured=%s search_index=%s",
        configured,
        ",".join(sorted(others)),
    )
    raise DomainError(ErrorCode.MODEL_UNAVAILABLE, ANOTHER_DEPLOYMENT_MESSAGE)


# How each built row searches. A row the table marks as built and that has
# no entry here is refused, never answered with another row's results.
_SEARCHES = {
    SearchMethod.VECTOR: vector_search,
    SearchMethod.HYBRID: hybrid_search,
    SearchMethod.AI_SEARCH_HYBRID: ai_search_hybrid,
}


def check_deployment(indexed_with: str | None, options: SearchOptions) -> None:
    """Refuse a search whose query was embedded by another deployment than the chunks.

    Vectors of two models are not comparable, and the search would answer
    plausible nonsense. A chunk set that records no run was never ingested:
    its row is refused as not available, not answered as "nothing found".
    """
    if indexed_with is None:
        raise DomainError(
            ErrorCode.RETRIEVER_NOT_AVAILABLE, CHUNK_SET_NOT_INGESTED_MESSAGE
        )
    configured = options.embedding_deployment
    if configured is None or indexed_with == configured:
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
    has_search_service = ports.search_service is not None
    row = row_to_search(request.retriever_config, has_search_service)
    search = _SEARCHES.get(row.method)
    if search is None:
        # A row marked as built without a search of its own: said as what it
        # is to the caller, never answered with another row's results.
        raise DomainError(
            ErrorCode.RETRIEVER_NOT_AVAILABLE,
            row_not_available_message(available_rows(has_search_service)),
        )
    started = clock()
    try:
        async with asyncio.timeout(options.deadline_seconds) as deadline:
            items = await search(
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
    except SearchServiceUnavailable as error:
        # No partial answer: the caller is told, and may ask again.
        logger.warning(
            "search failed: search service unavailable: retriever_config=%s reason=%s",
            row.config.value,
            error.reason,
        )
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, SEARCH_SERVICE_UNAVAILABLE_MESSAGE
        ) from None
    except TimeoutError:
        # Only the search's own deadline: a time-out of something a port
        # called is that port's failure, and is raised as it is.
        if not deadline.expired():
            raise
        waited_for = "index" if stats.embedded else "model"
        if stats.service_asked:
            waited_for = "search_service"
        logger.warning(
            "search deadline passed: retriever_config=%s waited_for=%s",
            row.config.value,
            waited_for,
        )
        if stats.service_asked:
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, SEARCH_SERVICE_TOO_SLOW_MESSAGE
            ) from None
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
        "full_text_candidates=%d service_documents=%d left_out=%d "
        "max_reranker_score=%s items=%d latency_ms=%d",
        row.config.value,
        request.top_k,
        stats.vector_candidates,
        stats.full_text_candidates,
        stats.service_documents,
        stats.left_out,
        # A number only: the ranker's raw score, before it is divided.
        "-" if stats.max_reranker_score is None else f"{stats.max_reranker_score:g}",
        len(items),
        latency_ms,
    )
    return SearchResponse(
        retriever_config=row.config, latency_ms=latency_ms, items=items
    )


async def read_rule(
    rule_id: str, retriever_config: RetrieverConfig | None, *, index: ChunkIndex
) -> RuleText:
    """The chunk that defines a rule, from the chunk set of the row named; `not_found` when none does.

    `retriever_not_available` when that chunk set was never ingested. From
    the `fixed` set the references answered are those of the rule's own
    definition, from its marker to the end of its paragraph: the chunk's
    text holds other rules as well, and a read of one rule does not open
    what its neighbours refer to. A rule defined elsewhere in the same
    chunk is among them when this rule refers to it.
    """
    chunk_set = chunk_set_to_read(retriever_config)
    try:
        async with index.snapshot() as view:
            ingested = await view.embedded_with(chunk_set) is not None
        chunk = await index.defining(chunk_set, rule_id) if ingested else None
    except IndexUnavailable as error:
        logger.warning("rule read failed: index unavailable: reason=%s", error.reason)
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, INDEX_UNAVAILABLE_MESSAGE
        ) from None
    if not ingested:
        raise DomainError(
            ErrorCode.RETRIEVER_NOT_AVAILABLE, CHUNK_SET_NOT_INGESTED_MESSAGE
        )
    if chunk is None:
        raise DomainError(ErrorCode.NOT_FOUND, RULE_NOT_FOUND_MESSAGE)
    references = chunk.reference_rule_ids
    if chunk.chunk_set is ChunkSet.FIXED:
        references = references_in(definition_in(chunk.text, rule_id) or "", [rule_id])
    return RuleText(
        rule_id=rule_id,
        chunk_id=chunk.chunk_id,
        chunk_set=chunk.chunk_set,
        text=chunk.text,
        manual_page=chunk.manual_page,
        impairment=chunk.impairment,
        reference_rule_ids=list(references),
    )
