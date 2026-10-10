"""The app factory: one FastAPI app with the service's routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from intake.adapters.blob import (
    BlobCaseFiles,
    BlobOriginalStore,
    build_blob_service,
    container_url,
)
from intake.adapters.db import SqlCaseRepository, SqlSchemaRevision, build_database
from intake.adapters.http.errors import install_error_handlers
from intake.adapters.http.middleware import (
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from intake.adapters.http.page_routes import build_page_router
from intake.adapters.http.routes import Dependencies, build_router
from intake.adapters.language import (
    LanguageRedaction,
    build_language_http,
    language_token_for,
)
from intake.adapters.migrations import bundled_head
from intake.adapters.pdf import PdfPageSplitter
from intake.adapters.read import DocumentRead, build_read_http, read_token_for
from intake.adapters.redaction_db import SqlRedactionRepository
from intake.adapters.telemetry import configure_telemetry, instrument_app
from intake.domain.redaction import RedactionPorts
from intake.settings import APP_ID, Settings, get_settings


def create_app(
    settings: Settings | None = None,
    *,
    dependencies: Dependencies | None = None,
    language: httpx.AsyncBaseTransport | None = None,
    read: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the app. The server uses the environment; unit tests pass fakes in.

    Without `dependencies` the real adapters are built: PostgreSQL, Blob
    Storage, Azure AI Language and Document Intelligence's read model as the
    settings describe them. Building them opens no connection. `language`
    and `read` stand in for those two endpoints in tests.
    """
    if settings is None:
        settings = get_settings()
    telemetry_on = configure_telemetry(settings)

    database = None
    redaction_service = None
    reader = None
    if dependencies is None:
        database = build_database(settings)
        blobs = build_blob_service(settings)
        redaction_service = LanguageRedaction(
            build_language_http(settings, language),
            api_version=settings.language_api_version,
            # AD-21: the redaction service is the one reader of `originals`.
            originals_url=container_url(blobs, settings.originals_container),
            cases_url=container_url(blobs, settings.cases_container),
            poll_seconds=settings.language_poll_seconds,
            token=language_token_for(settings),
        )
        reader = DocumentRead(
            build_read_http(settings, read),
            api_version=settings.read_api_version,
            model=settings.read_model,
            poll_seconds=settings.read_poll_seconds,
            max_retries=settings.read_max_retries,
            token=read_token_for(settings),
        )
        redactions = SqlRedactionRepository(database)
        dependencies = Dependencies(
            store=BlobOriginalStore(blobs, settings.originals_container),
            repository=SqlCaseRepository(database),
            schema_revision=SqlSchemaRevision(database),
            head_revision=bundled_head(),
            pages=redactions,
            redaction=RedactionPorts(
                repository=redactions,
                language=redaction_service,
                files=BlobCaseFiles(blobs, settings.cases_container),
                splitter=PdfPageSplitter(
                    settings.thumbnail_width_px,
                    settings.thumbnail_max_height_px,
                    settings.max_pages,
                ),
                reader=reader,
            ),
            upload_deadline_seconds=settings.upload_deadline_seconds,
            redaction_categories=tuple(settings.redaction_categories),
            redaction_deadline_seconds=settings.redaction_deadline_seconds,
            redaction_stale_margin_seconds=settings.redaction_stale_margin_seconds,
            redaction_cancel_seconds=settings.redaction_cancel_seconds,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Migrations never run here (azure.md rule 22): the pipeline runs them.
        yield
        if redaction_service is not None:
            await redaction_service.aclose()
        if reader is not None:
            await reader.aclose()
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
    app.include_router(build_page_router(dependencies))
    if telemetry_on:
        instrument_app(app)
    return app
