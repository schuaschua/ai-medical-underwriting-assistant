"""The app factory: one FastAPI app with the service's routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import httpx2
from fastapi import FastAPI

from contracts.audit import ai_actor
from contracts.enums import Service
from verdict.adapters.agent import FrameworkVerdictAgent
from verdict.adapters.dapr import Upstreams, build_http_client
from verdict.adapters.db import (
    SqlRunRepository,
    SqlSchemaRevision,
    build_database,
)
from verdict.adapters.http.errors import install_error_handlers
from verdict.adapters.http.middleware import (
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from verdict.adapters.http.routes import Dependencies, build_router
from verdict.adapters.migrations import bundled_head
from verdict.adapters.model import (
    ModelGateway,
    build_model_client,
    chat_deployment,
    model_token_for,
)
from verdict.adapters.telemetry import configure_telemetry, instrument_app
from verdict.domain.run import RunOptions, RunPorts
from verdict.settings import APP_ID, Settings, get_settings


def run_options(settings: Settings) -> RunOptions:
    """What a verdict run works with, as the settings say."""
    return RunOptions(
        # AD-8: the actor of a suggestion names the service and the model
        # deployment the agent ran on.
        actor=ai_actor(Service.VERDICT, chat_deployment(settings)),
        deadline_seconds=settings.run_deadline_seconds,
        stale_margin_seconds=settings.run_stale_margin_seconds,
        step_limit=settings.step_limit,
        agent_budget_seconds=settings.agent_time_budget_seconds,
        confidence_floor=settings.confidence_floor,
        search_top_k=settings.search_top_k,
        retriever_configs=frozenset(settings.available_retriever_configs),
        composed_search_limit=settings.composed_search_limit,
    )


def create_app(
    settings: Settings | None = None,
    *,
    dependencies: Dependencies | None = None,
    sidecar: httpx.AsyncBaseTransport | None = None,
    model: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the app. The server uses the environment; unit tests pass fakes in.

    Without `dependencies` the real adapters are built: PostgreSQL, the
    clients that read facts from `extraction` and rules from `retrieval`
    through the Dapr sidecar, the gateway to the chat deployment and the
    agent on it, as the settings describe them. Building them opens no
    connection. In tests `sidecar` stands in for the Dapr sidecar and `model`
    for the model's endpoint.
    """
    if settings is None:
        settings = get_settings()
    telemetry_on = configure_telemetry(settings)

    database = None
    upstreams = None
    gateway = None
    if dependencies is None:
        database = build_database(settings)
        upstreams = Upstreams(build_http_client(settings, sidecar), settings)
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
            run=RunPorts(
                repository=SqlRunRepository(database),
                facts=upstreams.extraction,
                rules=upstreams.retrieval,
                # AD-15, AD-16: the agent makes its model calls through the
                # gateway and no other way.
                agent=FrameworkVerdictAgent(gateway, step_limit=settings.step_limit),
            ),
            options=run_options(settings),
            schema_revision=SqlSchemaRevision(database),
            head_revision=bundled_head(),
            run_list_limit=settings.run_list_limit,
            step_list_limit=settings.step_list_limit,
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
                    if upstreams is not None:
                        await upstreams.aclose()
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
