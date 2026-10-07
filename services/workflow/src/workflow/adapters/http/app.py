"""The app factory: one FastAPI app with the service's routes, and the lifecycle worker beside it."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Protocol

import httpx
from fastapi import FastAPI

from workflow.adapters.dapr import StageClient, build_http_client
from workflow.adapters.db import (
    SqlCaseStore,
    SqlSchemaRevision,
    SqlTrailGuard,
    build_database,
)
from workflow.adapters.http.errors import install_error_handlers
from workflow.adapters.http.middleware import (
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from workflow.adapters.http.routes import Dependencies, SchemaRevision, build_router
from workflow.adapters.migrations import bundled_head
from workflow.adapters.scheduler import (
    Activities,
    SchedulerEngine,
    build_client,
    build_worker,
)
from workflow.adapters.telemetry import configure_telemetry, instrument_app
from workflow.domain.entities import StartParameters
from workflow.settings import APP_ID, Settings, get_settings

logger = logging.getLogger(__name__)


class Worker(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


def default_parameters(settings: Settings) -> StartParameters:
    """What a case is started with when the start request leaves a field out."""
    return StartParameters(
        classifier_contender=settings.default_classifier_contender,
        retriever_configs=tuple(settings.default_retriever_configs),
        stop_after=None,
        eval_run_id=None,
    )


async def start_worker_when_ready(
    worker: Worker,
    schema_revision: SchemaRevision,
    head_revision: str,
    check_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Start the worker once the schema is at the bundled head, looking again until it is.

    A worker that ran before the migrations would fail every activity, and
    could work against tables an older or newer build expects.
    """
    while True:
        try:
            current = await schema_revision.current()
        except Exception as error:  # noqa: BLE001 - any failure means "not yet"; it is looked at again
            # security rule 31: the type; the message can hold an address.
            logger.warning(
                "lifecycle worker waiting: database type=%s", type(error).__qualname__
            )
        else:
            if current == head_revision:
                # Returns at once: the worker connects, and reconnects, in the background.
                worker.start()
                logger.info("lifecycle worker started: schema_revision=%s", current)
                return
            logger.warning(
                "lifecycle worker waiting: schema_revision=%s head_revision=%s",
                current,
                head_revision,
            )
        await sleep(check_seconds)


async def _bounded(name: str, work: Awaitable[None], seconds: float) -> None:
    """Wait for one shutdown step, but not for ever; a failure is logged, not raised."""
    try:
        await asyncio.wait_for(work, seconds)
    except Exception as error:  # noqa: BLE001 - shutdown goes on to the next step whatever this one did
        logger.error(
            "shutdown step failed: step=%s type=%s", name, type(error).__qualname__
        )


@asynccontextmanager
async def running_worker(
    worker: Worker,
    schema_revision: SchemaRevision,
    head_revision: str,
    *,
    check_seconds: float,
    shutdown_seconds: float,
) -> AsyncIterator[None]:
    """The worker's life beside the app: started when the schema allows, stopped at the end."""
    starting = asyncio.create_task(
        start_worker_when_ready(worker, schema_revision, head_revision, check_seconds)
    )
    try:
        yield
    finally:
        starting.cancel()
        with suppress(asyncio.CancelledError):
            await starting
        # Off the loop: activities still running need it to finish. Stopping
        # a worker that never started does nothing.
        await _bounded("worker", asyncio.to_thread(worker.stop), shutdown_seconds)


def create_app(
    settings: Settings | None = None,
    *,
    dependencies: Dependencies | None = None,
    sidecar: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the app. The server uses the environment; unit tests pass fakes in.

    Without `dependencies` the real adapters are built: PostgreSQL and the
    Durable Task Scheduler as the settings describe them, the client that
    commands the stage services through the Dapr sidecar, and the worker that
    runs the case orchestration. Building them opens no connection; the
    worker starts once the schema is migrated and stops with the app.
    `sidecar` stands in for the Dapr sidecar in tests.
    """
    if settings is None:
        settings = get_settings()
    telemetry_on = configure_telemetry(settings)

    database = None
    engine = None
    store = None
    stages = None
    if dependencies is None:
        database = build_database(settings)
        store = SqlCaseStore(database)
        engine = SchedulerEngine(build_client(settings))
        stages = StageClient(build_http_client(settings, sidecar), settings)
        dependencies = Dependencies(
            store=store,
            engine=engine,
            schema_revision=SqlSchemaRevision(database),
            trail_guard=SqlTrailGuard(database),
            head_revision=bundled_head(),
            defaults=default_parameters(settings),
            page_queue_limit=settings.page_queue_limit,
            audit_trail_limit=settings.audit_trail_limit,
            case_list_limit=settings.case_list_limit,
        )
    wired = dependencies

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Migrations never run here (azure.md rule 22): the pipeline runs them.
        # Each resource is closed even if the one before it failed to close.
        try:
            try:
                if store is None or stages is None:
                    yield
                else:
                    # Activities run on the worker's threads and do their
                    # database work and their stage calls on this loop.
                    activities = Activities(
                        store,
                        asyncio.get_running_loop(),
                        settings.activity_timeout_seconds,
                        stages,
                        settings.stage_timeout_seconds,
                        # AD-7: the one place the threshold enters the lifecycle.
                        settings.gate_threshold,
                    )
                    async with running_worker(
                        build_worker(settings, activities),
                        wired.schema_revision,
                        wired.head_revision,
                        check_seconds=settings.worker_start_check_seconds,
                        shutdown_seconds=settings.shutdown_timeout_seconds,
                    ):
                        yield
            finally:
                try:
                    if engine is not None:
                        await _bounded(
                            "scheduler_client",
                            engine.aclose(),
                            settings.shutdown_timeout_seconds,
                        )
                finally:
                    if stages is not None:
                        await _bounded(
                            "stage_client",
                            stages.aclose(),
                            settings.shutdown_timeout_seconds,
                        )
        finally:
            if database is not None:
                await _bounded(
                    "database", database.dispose(), settings.shutdown_timeout_seconds
                )

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
