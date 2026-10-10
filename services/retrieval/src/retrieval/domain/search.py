"""The two reads of `retrieval`: the search and the rule read (spine AD-11, AD-12).

One search operation for every ladder row; all six are built.
The steps of `r3`, each a function of its own: the query is embedded
(`embed_query`), the vector search and the full-text search each answer a
ranked list of candidates (the index port), the two lists are fused
(`fusion.reciprocal_rank_fusion`), and the best of the fused list become the
ranked items (`rank_items`). The baseline rows `r1` and `r2` embed the query
the same way and answer the vector search's list alone (`vector_search`),
over the `fixed` and the `smart` chunks. Row `r4` takes the fused list of
`r3` as it is (`fused_candidates`, which both rows call) and has the
reranker deployment, Cohere Rerank, score its best candidates against the
query (`hybrid_reranked_search`): the order is the one thing that differs. Row
`r5` embeds the query the same way and hands the text and the vector to
Azure AI Search (`ai_search_hybrid`), whose index holds a copy of the
`smart` chunks: the store is the one thing that differs. Row `r6` hands the
query, as it was asked, to the knowledge base the search service keeps over
that same index (`ai_search_agentic`): the service plans and runs queries
of its own with its own model, and the references it returns are the items.
On the other rows no model rewrites the query. Nothing is stored and
nothing is cached. Every read of a search is from one unchanging view of
the index, and one deadline covers the whole search; rows `r4` and `r6`
each have a longer one of their own, since a reranker's or a planning
model's call does not fit the others'.
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
    RetrievedReference,
)
from retrieval.domain.fusion import Fused, reciprocal_rank_fusion
from retrieval.domain.ports import (
    AgenticRetriever,
    ChunkIndex,
    IndexUnavailable,
    KnowledgeBaseMissing,
    ModelAnswerInvalid,
    ModelCallFailed,
    ModelNotConfigured,
    ModelUnavailable,
    QueryEmbedder,
    Reranker,
    RuleSearchService,
    SearchServiceUnavailable,
)
from retrieval.domain.rerank import (
    RerankAnswerInvalid,
    rerank_document,
    scores_in_order,
)
from retrieval.domain.rows import (
    CHUNK_SET_NOT_INGESTED_MESSAGE,
    RetrieverRow,
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
RERANKER_NO_ANSWER_MESSAGE = (
    "The reranker gave no usable answer. Please try again shortly."
)
RERANKER_TOO_SLOW_MESSAGE = (
    "The reranker did not answer in time. Please try again shortly."
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
    # AD-11, row `r4`: the reranker deployment. None when the service was
    # told of none: the row is then refused as not available.
    reranker: Reranker | None = None
    # AD-11, row `r6`: the search service's knowledge base. None when the
    # service was told of no search endpoint or of no chat deployment,
    # which the knowledge base plans with: the row is then refused as not
    # available.
    knowledge_base: AgenticRetriever | None = None

    def available(self) -> frozenset[RetrieverConfig]:
        """The rows a search can run with here, by what the service was given."""
        return available_rows(
            self.search_service is not None,
            self.reranker is not None,
            self.knowledge_base is not None,
        )


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
    # Row `r4`: how many of the best fused candidates the reranker is
    # given, and never fewer than `top_k`; and the deadline of that row's
    # whole search, the rerank call included, in place of `deadline_seconds`.
    rerank_depth: int = 20
    rerank_deadline_seconds: float | None = 20.0
    # Row `r6`: the deadline of that row's whole search, the service's own
    # model calls included, in place of `deadline_seconds`.
    agentic_deadline_seconds: float | None = 20.0


@dataclass(slots=True)
class SearchStats:
    """What one search did, for its span and its log line. Counts only, never the query."""

    depth: int = 0
    embedded: bool = False
    vector_candidates: int = 0
    full_text_candidates: int = 0
    items: int = 0
    # Rows `r5` and `r6`: whether the search service was asked, how many
    # documents (for `r6`, references) it answered, and how many of them
    # were left out because pgvector holds no chunk of their id, or holds
    # it with other content (an index behind the chunk table).
    service_asked: bool = False
    service_documents: int = 0
    left_out: int = 0
    # The largest score the service's ranker gave a document of the answer,
    # as the service gave it; None when it answered none.
    max_reranker_score: float | None = None
    # Row `r4`: whether the reranker was asked, how many candidates it was
    # given, and how long its answer took.
    rerank_asked: bool = False
    reranked: int = 0
    rerank_ms: int = 0
    # Row `r6`: how many queries of its own the service says it ran, and
    # which rule the scores of the answer are by: `reranker` (the semantic
    # ranker's score, divided), or `rank` (one over the rank).
    subqueries: int = 0
    score_rule: str = "-"


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


async def fused_candidates(
    query: str,
    chunk_set: ChunkSet,
    top_k: int,
    *,
    ports: SearchPorts,
    options: SearchOptions,
    stats: SearchStats,
) -> tuple[list[Fused], dict[str, IndexedChunk]]:
    """Rows `r3` and `r4`: the fused list of the two searches, best first, and its chunks by id.

    The one step both rows share, so that `r4` reranks exactly what `r3`
    would answer from: the same embedding, the same two lists to the same
    depth, the same fusion.
    """
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
    return fused, chunks


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
    fused, chunks = await fused_candidates(
        query, chunk_set, top_k, ports=ports, options=options, stats=stats
    )
    return rank_items(fused, chunks, top_k)


def rerank_depth(top_k: int, options: SearchOptions) -> int:
    """How many fused candidates the reranker is given: the setting, or `top_k` if that is more."""
    return max(options.rerank_depth, top_k)


async def hybrid_reranked_search(
    query: str,
    chunk_set: ChunkSet,
    top_k: int,
    *,
    ports: SearchPorts,
    options: SearchOptions,
    stats: SearchStats,
) -> list[SearchItem]:
    """Row `r4`: the fused candidates of `r3`, in the order a reranker gives them.

    The best of the fused list are sent to the reranker deployment with
    the query, in one call. The items are the candidates by the score it
    gave them, largest first, and candidates of equal score in the fused
    order; `score` is that score. An answer that does not score exactly
    the candidates it was given fails the search: the fused order is never
    answered in its place.
    """
    reranker = ports.reranker
    if reranker is None:
        # `row_to_search` refuses the row before this is reached.
        raise DomainError(
            ErrorCode.RETRIEVER_NOT_AVAILABLE,
            row_not_available_message(ports.available()),
        )
    fused, chunks = await fused_candidates(
        query, chunk_set, top_k, ports=ports, options=options, stats=stats
    )
    candidates = [chunks[entry.chunk_id] for entry in fused][
        : rerank_depth(top_k, options)
    ]
    if not candidates:
        # Nothing to put in order, and nothing to ask a model about.
        return []
    stats.rerank_asked, stats.reranked = True, len(candidates)
    started = time.perf_counter()
    try:
        try:
            answer = await reranker.relevance(
                query, [rerank_document(chunk) for chunk in candidates]
            )
        finally:
            # Whatever the outcome, the deadline's cancellation included:
            # the time matters most when the reranker failed or was slow.
            stats.rerank_ms = max(0, round((time.perf_counter() - started) * 1000))
    except ModelUnavailable:
        raise DomainError(
            ErrorCode.MODEL_UNAVAILABLE, MODEL_UNAVAILABLE_MESSAGE
        ) from None
    except ModelCallFailed as error:
        # Codes only (security rule 31): never the query or a chunk's text.
        logger.error("rerank refused: reason=%s", error.reason)
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, MODEL_REFUSED_MESSAGE
        ) from None
    try:
        scores = scores_in_order(answer, len(candidates))
    except RerankAnswerInvalid as error:
        logger.error(
            "rerank answer invalid: reason=%s candidates=%d",
            error.reason,
            len(candidates),
        )
        raise DomainError(
            ErrorCode.MODEL_UNAVAILABLE, RERANKER_NO_ANSWER_MESSAGE
        ) from None
    # A stable sort: candidates of the same score stay in the fused order.
    ordered = sorted(
        zip(scores, candidates, strict=True), key=lambda scored: -scored[0]
    )
    return [
        SearchItem(
            chunk_id=chunk.chunk_id,
            rule_ids=list(chunk.rule_ids),
            rank=rank,
            score=score,
            text=chunk.text,
            manual_page=chunk.manual_page,
            impairment=chunk.impairment,
        )
        for rank, (score, chunk) in enumerate(ordered[:top_k], start=1)
    ]


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
            row_not_available_message(ports.available()),
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
    _note_left_out(documents, kept, stats)
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


def _note_left_out(
    answered: Sequence[RankedDocument | RetrievedReference],
    kept: Sequence[RankedDocument | RetrievedReference],
    stats: SearchStats,
) -> None:
    """Count, and log by id, what the search service answered and pgvector does not hold as it is."""
    stats.left_out = len(answered) - len(kept)
    if stats.left_out:
        # Ids only (security rule 31).
        logger.warning(
            "search service answered chunks pgvector does not hold: count=%d "
            "chunk_ids=%s",
            stats.left_out,
            ",".join(entry.chunk_id for entry in answered if entry not in kept),
        )


def rank_score(rank: int) -> float:
    """Row `r6`'s score where the service gave a reference none: one over its rank.

    1 for the first item, 0.5 for the second, and so on: larger is better
    and the order of the scores is the service's order. It says nothing
    else, and is no measure of relevance.
    """
    return 1.0 / rank


async def ai_search_agentic(
    query: str,
    chunk_set: ChunkSet,
    top_k: int,
    *,
    ports: SearchPorts,
    options: SearchOptions,
    stats: SearchStats,
) -> list[SearchItem]:
    """Row `r6`: Azure AI Search's agentic retrieval over the same index as row `r5`.

    The query goes to the knowledge base as it was asked. The service's
    model plans queries of its own, the service runs them on the index and
    returns references: the documents it found. Those are the items, in
    the service's order; an answer it might write is neither asked for nor
    read. Nothing is embedded here: the service embeds its own queries.

    A reference whose chunk pgvector does not hold, or holds with another
    content hash, is left out and counted, as for row `r5`. Two of the
    service's queries can find the same document: of the references to
    one chunk the first is kept, before the cut to `top_k`.

    A service that holds no knowledge base has not been loaded for this
    row: the row is refused as not available, as a chunk set that was
    never ingested is, and not answered as a fault that passes.

    `score`, by one rule for a whole answer: where every reference kept
    carries the semantic ranker's score, that score divided by 4, as for
    row `r5`; where one of them carries none (the service says a source may
    bypass its ranker), one over the item's rank for all of them.
    """
    knowledge_base = ports.knowledge_base
    if knowledge_base is None:
        # `row_to_search` refuses the row before this is reached.
        raise DomainError(
            ErrorCode.RETRIEVER_NOT_AVAILABLE,
            row_not_available_message(ports.available()),
        )
    stats.depth = top_k
    async with ports.index.snapshot() as index:
        check_deployment(await index.embedded_with(chunk_set), options)
        stored = await index.content_hashes(chunk_set)
    stats.service_asked = True
    try:
        retrieval = await knowledge_base.retrieve(query, top_k)
    except KnowledgeBaseMissing:
        logger.warning("search refused: the search service holds no knowledge base")
        raise DomainError(
            ErrorCode.RETRIEVER_NOT_AVAILABLE, CHUNK_SET_NOT_INGESTED_MESSAGE
        ) from None
    stats.service_documents = len(retrieval.references)
    stats.subqueries = retrieval.subqueries
    # The first reference to each chunk, in the service's order.
    first: dict[str, RetrievedReference] = {}
    for reference in retrieval.references:
        first.setdefault(reference.chunk_id, reference)
    references = list(first.values())
    stats.max_reranker_score = max(
        (
            reference.reranker_score
            for reference in references
            if reference.reranker_score is not None
        ),
        default=None,
    )
    _check_documents_deployment(references, options)
    kept = [
        reference
        for reference in references
        if stored.get(reference.chunk_id) == reference.content_hash
    ]
    _note_left_out(references, kept, stats)
    kept = kept[:top_k]
    by_the_ranker = [
        reference.reranker_score
        for reference in kept
        if reference.reranker_score is not None
    ]
    by_rank = len(by_the_ranker) != len(kept)
    stats.score_rule = "rank" if by_rank else "reranker"
    return [
        SearchItem(
            chunk_id=reference.chunk_id,
            rule_ids=list(reference.rule_ids),
            # The service's order, counted without gaps.
            rank=rank,
            score=rank_score(rank)
            if by_rank
            else reranker_score(by_the_ranker[rank - 1]),
            text=reference.text,
            manual_page=reference.manual_page,
            impairment=reference.impairment,
        )
        for rank, reference in enumerate(kept, start=1)
    ]


def _check_documents_deployment(
    documents: Sequence[RankedDocument | RetrievedReference], options: SearchOptions
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
    SearchMethod.HYBRID_RERANKED: hybrid_reranked_search,
    SearchMethod.AI_SEARCH_HYBRID: ai_search_hybrid,
    SearchMethod.AI_SEARCH_AGENTIC: ai_search_agentic,
}


def deadline_of(row: RetrieverRow, options: SearchOptions) -> float | None:
    """The deadline over one whole search with a row: `r4`'s or `r6`'s own, or the one of the other rows."""
    if row.needs_reranker:
        return options.rerank_deadline_seconds
    if row.needs_knowledge_base:
        return options.agentic_deadline_seconds
    return options.deadline_seconds


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
    row = row_to_search(
        request.retriever_config,
        ports.search_service is not None,
        ports.reranker is not None,
        ports.knowledge_base is not None,
    )
    search = _SEARCHES.get(row.method)
    if search is None:
        # A row marked as built without a search of its own: said as what it
        # is to the caller, never answered with another row's results.
        raise DomainError(
            ErrorCode.RETRIEVER_NOT_AVAILABLE,
            row_not_available_message(ports.available()),
        )
    started = clock()
    try:
        async with asyncio.timeout(deadline_of(row, options)) as deadline:
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
        # Row `r6` embeds nothing here: before the service is asked, it
        # can only have been waiting for the index.
        read_the_index = stats.embedded or row.needs_knowledge_base
        waited_for = "index" if read_the_index else "model"
        if stats.service_asked:
            waited_for = "search_service"
        if stats.rerank_asked:
            waited_for = "reranker"
        logger.warning(
            "search deadline passed: retriever_config=%s waited_for=%s rerank_ms=%d",
            row.config.value,
            waited_for,
            # Row `r4`: how long the reranker had been waited for; else 0.
            stats.rerank_ms,
        )
        if stats.service_asked:
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, SEARCH_SERVICE_TOO_SLOW_MESSAGE
            ) from None
        if stats.rerank_asked:
            raise DomainError(
                ErrorCode.MODEL_UNAVAILABLE, RERANKER_TOO_SLOW_MESSAGE
            ) from None
        if read_the_index:
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, INDEX_TOO_SLOW_MESSAGE
            ) from None
        raise DomainError(ErrorCode.MODEL_UNAVAILABLE, MODEL_TOO_SLOW_MESSAGE) from None
    stats.items = len(items)
    latency_ms = max(0, round((clock() - started) * 1000))
    # security rule 31: the row, counts and the timing, never the query.
    logger.info(
        "search: retriever_config=%s top_k=%d vector_candidates=%d "
        "full_text_candidates=%d reranked=%d rerank_ms=%d subqueries=%d "
        "service_documents=%d left_out=%d max_reranker_score=%s items=%d "
        "score_rule=%s latency_ms=%d",
        row.config.value,
        request.top_k,
        stats.vector_candidates,
        stats.full_text_candidates,
        # Row `r4`: the candidates its reranker was given, and its time.
        stats.reranked,
        stats.rerank_ms,
        # Row `r6`: the queries the service planned and ran, as a count.
        stats.subqueries,
        stats.service_documents,
        stats.left_out,
        # A number only: the ranker's raw score, before it is divided.
        "-" if stats.max_reranker_score is None else f"{stats.max_reranker_score:g}",
        len(items),
        # Row `r6`: which of its two rules the scores of this answer are by.
        stats.score_rule,
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
