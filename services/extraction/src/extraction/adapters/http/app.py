"""The app factory: one FastAPI app with the service's routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import httpx2
from fastapi import FastAPI

from contracts.audit import ai_actor
from contracts.enums import Service
from extraction.adapters.dapr import IntakeClient, build_http_client
from extraction.adapters.db import (
    SqlFactRepository,
    SqlSchemaRevision,
    build_database,
)
from extraction.adapters.http.errors import install_error_handlers
from extraction.adapters.http.middleware import (
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from extraction.adapters.http.routes import Dependencies, build_router
from extraction.adapters.migrations import bundled_head
from extraction.adapters.model import (
    ModelGateway,
    build_model_client,
    chat_deployment,
    model_token_for,
)
from extraction.adapters.telemetry import configure_telemetry, instrument_app
from extraction.domain.extract import ExtractOptions, ExtractPorts
from extraction.settings import APP_ID, Settings, get_settings


def extract_options(settings: Settings) -> ExtractOptions:
    """What an extraction runs with, as the settings say."""
    return ExtractOptions(
        # AD-8: the actor of an extraction names the service and the model
        # deployment that did it.
        actor=ai_actor(Service.EXTRACTION, chat_deployment(settings)),
        deadline_seconds=settings.extract_deadline_seconds,
        stale_margin_seconds=settings.extract_stale_margin_seconds,
    )


def create_app(
    settings: Settings | None = None,
    *,
    dependencies: Dependencies | None = None,
    sidecar: httpx.AsyncBaseTransport | None = None,
    model: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the app. The server uses the environment; unit tests pass fakes in.

    Without `dependencies` the real adapters are built: PostgreSQL, the client
    that reads page text from `intake` through the Dapr sidecar, and the gateway
    to the chat deployment, as the settings describe them. Building them
    opens no connection. In tests `sidecar` stands in for the Dapr sidecar
    and `model` for the model's endpoint.
    """
    if settings is None:
        settings = get_settings()
    telemetry_on = configure_telemetry(settings)

    database = None
    intake = None
    gateway = None
    if dependencies is None:
        database = build_database(settings)
        intake = IntakeClient(build_http_client(settings, sidecar))
        gateway = ModelGateway(
            build_model_client(settings, model, model_token_for(settings)),
            deployment=chat_deployment(settings),
            max_retries=settings.model_max_retries,
            retry_seconds=settings.model_retry_seconds,
            max_retry_seconds=settings.model_max_retry_seconds,
            max_completion_tokens=settings.model_max_completion_tokens,
            max_concurrent_calls=settings.model_max_concurrent_calls,
        )
        dependencies = Dependencies(
            extract=ExtractPorts(
                repository=SqlFactRepository(database),
                pages=intake,
                model=gateway,
            ),
            options=extract_options(settings),
            schema_revision=SqlSchemaRevision(database),
            head_revision=bundled_head(),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Migrations never run here (azure.md rule 22): the pipeline runs them.
        # Each resource is closed even if the one before it failed to close.
        try:
            yield
        finally:
            try:
                if gateway is not None:
                    await gateway.aclose()
            finally:
                try:
                    if intake is not None:
                        await intake.aclose()
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
