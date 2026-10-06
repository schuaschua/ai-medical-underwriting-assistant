"""The app factory: one FastAPI app with the service's routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from intake.adapters.blob import BlobOriginalStore, build_blob_service
from intake.adapters.db import SqlCaseRepository, SqlSchemaRevision, build_database
from intake.adapters.http.errors import install_error_handlers
from intake.adapters.http.middleware import (
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from intake.adapters.http.routes import Dependencies, build_router
from intake.adapters.migrations import bundled_head
from intake.adapters.telemetry import configure_telemetry, instrument_app
from intake.settings import APP_ID, Settings, get_settings


def create_app(
    settings: Settings | None = None, *, dependencies: Dependencies | None = None
) -> FastAPI:
    """Build the app. The server uses the environment; unit tests pass fakes in.

    Without `dependencies` the real adapters are built: PostgreSQL and Blob
    Storage as the settings describe them. Building them opens no connection.
    """
    if settings is None:
        settings = get_settings()
    telemetry_on = configure_telemetry(settings)

    database = None
    if dependencies is None:
        database = build_database(settings)
        dependencies = Dependencies(
            store=BlobOriginalStore(
                build_blob_service(settings), settings.originals_container
            ),
            repository=SqlCaseRepository(database),
            schema_revision=SqlSchemaRevision(database),
            head_revision=bundled_head(),
            upload_deadline_seconds=settings.upload_deadline_seconds,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Migrations never run here (azure.md rule 22): the pipeline runs them.
        yield
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
    if telemetry_on:
        instrument_app(app)
    return app
