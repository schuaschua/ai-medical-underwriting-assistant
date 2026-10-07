"""The app factory: one FastAPI app with the service's routes."""

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx2
from fastapi import FastAPI

from retrieval.adapters.db import SqlSchemaRevision, build_database
from retrieval.adapters.http.errors import install_error_handlers
from retrieval.adapters.http.middleware import (
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from retrieval.adapters.http.routes import Dependencies, build_router
from retrieval.adapters.index import SqlChunkIndex
from retrieval.adapters.migrations import bundled_head
from retrieval.adapters.model import (
    ModelGateway,
    build_model_client,
    model_token_for,
)
from retrieval.adapters.telemetry import configure_telemetry, instrument_app
from retrieval.domain.ports import ModelNotConfigured, QueryEmbedder
from retrieval.domain.search import SearchOptions, SearchPorts
from retrieval.settings import APP_ID, Settings, get_settings

logger = logging.getLogger(__name__)


SEARCH_SETTINGS = ("RETRIEVAL_MODEL_ENDPOINT", "RETRIEVAL_EMBEDDING_DEPLOYMENT")


class NoEmbeddingModel:
    """Stands where the gateway would be when the service was told of no embedding deployment.

    The probes and the rule read need no model; a search does, and is told
    that this service is not configured to search.
    """

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise ModelNotConfigured


def build_query_gateway(
    settings: Settings, transport: httpx2.AsyncBaseTransport | None = None
) -> ModelGateway | None:
    """The gateway a search embeds its query through; None when it is not configured.

    Tests pass a transport that stands in for the deployment. A query's
    call has the search's own short budget, not the ingestion job's.
    """
    if settings.model_endpoint is None or settings.embedding_deployment is None:
        return None
    # No wait between two attempts outlasts the search itself.
    longest_wait = min(
        settings.model_max_retry_seconds, settings.search_deadline_seconds
    )
    return ModelGateway(
        build_model_client(
            settings,
            transport,
            model_token_for(settings),
            timeout_seconds=settings.search_embedding_timeout_seconds,
        ),
        # AD-16: the one embedding deployment, the same the chunks were
        # embedded with at ingestion.
        embedding_deployment=settings.embedding_deployment,
        max_retries=settings.search_embedding_max_retries,
        retry_seconds=min(settings.model_retry_seconds, longest_wait),
        max_retry_seconds=longest_wait,
        max_concurrent_calls=settings.model_max_concurrent_calls,
    )


def search_options(settings: Settings) -> SearchOptions:
    """What a search works with, as the settings say."""
    return SearchOptions(
        candidate_depth=settings.search_candidate_depth,
        deadline_seconds=settings.search_deadline_seconds,
        embedding_deployment=settings.embedding_deployment,
    )


def create_app(
    settings: Settings | None = None,
    *,
    dependencies: Dependencies | None = None,
    model_transport: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the app. The server uses the environment; unit tests pass fakes in.

    Without `dependencies` the real adapters are built, as the settings
    describe them. Building them opens no connection and calls no model. The
    service never reads the manual itself: only the ingestion job does.
    `model_transport` stands in for the embedding deployment in a test of
    that wiring.
    """
    if settings is None:
        settings = get_settings()
    telemetry_on = configure_telemetry(settings)

    database = None
    gateway: ModelGateway | None = None
    if dependencies is None:
        database = build_database(settings)
        gateway = build_query_gateway(settings, model_transport)
        model: QueryEmbedder = gateway if gateway is not None else NoEmbeddingModel()
        if gateway is None:
            # Said once, here: every search is then refused, at warning.
            missing = [
                name
                for name, value in zip(
                    SEARCH_SETTINGS,
                    (settings.model_endpoint, settings.embedding_deployment),
                    strict=True,
                )
                if value is None
            ]
            logger.warning(
                "searches are off: not configured: missing=%s", ",".join(missing)
            )
        dependencies = Dependencies(
            search=SearchPorts(model=model, index=SqlChunkIndex(database)),
            schema_revision=SqlSchemaRevision(database),
            head_revision=bundled_head(),
            options=search_options(settings),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Migrations never run here (azure.md rule 22): the pipeline runs them.
        try:
            yield
        finally:
            # Each resource is closed even if the one before it failed to close.
            try:
                if gateway is not None:
                    await gateway.aclose()
            finally:
                if database is not None:
                    await database.dispose()

    app = FastAPI(
        title=APP_ID, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    install_error_handlers(app)
    # The middleware added last runs first: headers wrap the error fallback.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(build_router(dependencies))
    # What the app was built around, for whoever holds the app.
    app.state.dependencies = dependencies
    if telemetry_on:
        instrument_app(app)
    return app
