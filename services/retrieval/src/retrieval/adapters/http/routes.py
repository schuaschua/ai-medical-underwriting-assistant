"""The routes of `retrieval`: the probes, the search and the rule read (spine, Operations).

`POST /searches` searches the manual with one ladder row and stores nothing.
`GET /rules/{rule_id}` answers the chunk that defines a rule. Both only
read. The ingestion is no route: it is a job on this image
(`python -m retrieval.ingest`).
"""

import logging
from dataclasses import dataclass, field
from typing import Annotated, Protocol

from fastapi import APIRouter, Path, Query
from opentelemetry import trace

from contracts.errors import DomainError, ErrorCode
from contracts.models.retrieval import (
    RuleReadQuery,
    RuleText,
    SearchRequest,
    SearchResponse,
)
from contracts.models.web import Health
from contracts.operations import get_operation
from contracts.rules import RULE_ID_PATTERN
from retrieval.adapters.telemetry import adapter_span
from retrieval.domain.search import (
    SearchOptions,
    SearchPorts,
    SearchStats,
    read_rule,
    search_rules,
)
from retrieval.settings import APP_ID, HEALTH_PATH, READY_PATH

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

NOT_READY_MESSAGE = "The service is not ready."

# An id that is not of the manual's form is refused before anything is looked up.
RuleIdPath = Annotated[str, Path(pattern=rf"^{RULE_ID_PATTERN}$")]


class SchemaRevision(Protocol):
    async def current(self) -> str | None:
        """The migration revision the database is at, or None if there is none."""
        ...


@dataclass(frozen=True, slots=True)
class Dependencies:
    """What the routes work with; the app factory or a test provides it."""

    search: SearchPorts
    schema_revision: SchemaRevision
    # The newest migration bundled with this build.
    head_revision: str
    options: SearchOptions = field(default_factory=SearchOptions)


def build_router(dependencies: Dependencies) -> APIRouter:
    """Build the service's routes around one set of dependencies."""
    router = APIRouter()
    search = get_operation("search_rules")
    rule_read = get_operation("read_rule")

    @router.api_route(HEALTH_PATH, methods=["GET", "HEAD"])
    async def health() -> Health:
        return Health()

    @router.api_route(READY_PATH, methods=["GET", "HEAD"])
    async def ready() -> Health:
        # azure.md rule 22: ready only when the schema is at the bundled head.
        try:
            current = await dependencies.schema_revision.current()
        except Exception as error:
            # The database cannot be reached. security rule 31: the error's
            # type only; its message can hold the connection's address.
            logger.warning("not ready: database type=%s", type(error).__qualname__)
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, NOT_READY_MESSAGE
            ) from error
        if current != dependencies.head_revision:
            logger.warning(
                "not ready: schema_revision=%s head_revision=%s",
                current,
                dependencies.head_revision,
            )
            raise DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, NOT_READY_MESSAGE)
        return Health()

    @router.post(search.path)
    async def search_rules_route(request: SearchRequest) -> SearchResponse:
        stats = SearchStats()
        # One span for the whole search, above the model's and the
        # database's own. security rule 31: never the query.
        with adapter_span(tracer, "retrieval.search") as span:
            span.set_attribute("retrieval.retriever_config", request.retriever_config)
            span.set_attribute("retrieval.top_k", request.top_k)
            try:
                return await search_rules(
                    request,
                    ports=dependencies.search,
                    options=dependencies.options,
                    stats=stats,
                )
            except DomainError as error:
                span.set_attribute("error.type", error.code.value)
                raise
            finally:
                span.set_attribute("retrieval.candidate_depth", stats.depth)
                span.set_attribute(
                    "retrieval.vector_candidates", stats.vector_candidates
                )
                span.set_attribute(
                    "retrieval.full_text_candidates", stats.full_text_candidates
                )
                span.set_attribute("retrieval.items", stats.items)
                if stats.rerank_asked:
                    # Row `r4` only: how many candidates the reranker was
                    # given, and how long its answer took.
                    span.set_attribute("retrieval.reranked", stats.reranked)
                    span.set_attribute("retrieval.rerank_ms", stats.rerank_ms)
                if stats.service_asked:
                    # Row `r5` only: what the search service answered, and
                    # how many of its documents pgvector does not hold.
                    span.set_attribute(
                        "retrieval.search_service.documents",
                        stats.service_documents,
                    )
                    span.set_attribute("retrieval.left_out", stats.left_out)
                    if stats.max_reranker_score is not None:
                        # The ranker's raw score, as the service gave it.
                        span.set_attribute(
                            "retrieval.search_service.max_reranker_score",
                            stats.max_reranker_score,
                        )

    @router.get(rule_read.path)
    async def read_rule_route(
        rule_id: RuleIdPath, query: Annotated[RuleReadQuery, Query()]
    ) -> RuleText:
        return await read_rule(
            rule_id, query.retriever_config, index=dependencies.search.index
        )

    return router
