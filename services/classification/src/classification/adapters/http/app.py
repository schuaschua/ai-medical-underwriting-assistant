"""The app factory: one FastAPI app with the service's routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import httpx2
from fastapi import FastAPI

from classification.adapters.classifier import (
    build_classifier,
    classifier_configured,
    classifier_id,
)
from classification.adapters.dapr import IntakeClient, build_http_client
from classification.adapters.db import (
    SqlClassificationRepository,
    SqlSchemaRevision,
    build_database,
)
from classification.adapters.http.errors import install_error_handlers
from classification.adapters.http.middleware import (
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from classification.adapters.http.routes import Dependencies, build_router
from classification.adapters.migrations import bundled_head
from classification.adapters.model import (
    ModelGateway,
    build_model_client,
    chat_deployment,
    model_token_for,
)
from classification.adapters.telemetry import configure_telemetry, instrument_app
from classification.domain.classify import ClassifyOptions, ClassifyPorts
from classification.settings import APP_ID, Settings, get_settings
from contracts.audit import ai_actor
from contracts.enums import Service


def classify_options(settings: Settings) -> ClassifyOptions:
    """What a classification runs with, as the settings say."""
    return ClassifyOptions(
        # AD-8: the actor of a classification names the service and the model
        # deployment that did it.
        actor=ai_actor(Service.CLASSIFICATION, chat_deployment(settings)),
        # The Document Intelligence contender is named by its classifier id.
        classifier_actor=ai_actor(Service.CLASSIFICATION, classifier_id(settings))
        if classifier_configured(settings)
        else None,
        runs=settings.classifier_runs,
        max_concurrent_runs=settings.classifier_max_concurrent_runs,
        deadline_seconds=settings.classify_deadline_seconds,
        stale_margin_seconds=settings.classify_stale_margin_seconds,
    )


def create_app(
    settings: Settings | None = None,
    *,
    dependencies: Dependencies | None = None,
    sidecar: httpx.AsyncBaseTransport | None = None,
    model: httpx2.AsyncBaseTransport | None = None,
    classifier: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the app. The server uses the environment; unit tests pass fakes in.

    Without `dependencies` the real adapters are built: PostgreSQL, the client
    that reads pages from `intake` through the Dapr sidecar, and the gateway
    to the chat deployment, as the settings describe them. Building them
    opens no connection. In tests `sidecar` stands in for the Dapr sidecar,
    `model` for the model's endpoint and `classifier` for Document
    Intelligence. The Document Intelligence client is built only where the
    settings name its endpoint and a classifier id (story 4.2): elsewhere
    the `doc-intelligence` contender is refused as not available.
    """
    if settings is None:
        settings = get_settings()
    telemetry_on = configure_telemetry(settings)

    database = None
    intake = None
    gateway = None
    document_classifier = None
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
        if classifier_configured(settings):
            document_classifier = build_classifier(settings, classifier)
        dependencies = Dependencies(
            classify=ClassifyPorts(
                repository=SqlClassificationRepository(database),
                pages=intake,
                model=gateway,
                classifier=document_classifier,
            ),
            options=classify_options(settings),
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
                if document_classifier is not None:
                    await document_classifier.aclose()
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
