"""The ingestion job: `python -m retrieval.ingest` (spine AD-12).

A one-off job on the `retrieval` image and identity, started by hand or by
the pipeline. It calls no other service of ours: it reads the manual from
the `manual` container, has Document Intelligence parse it, has the chat
model write a context line per chunk and the embedding model a vector, and
stores the chunks in schema `retrieval`. Run again over the same manual it
changes nothing and calls no model.

It ends with status 0 when the index is as the manual has it, and with 1
otherwise, after one log line that names the error code and the reason. A
failed run leaves the index as it was. Before the process ends its telemetry
is sent.
"""

import asyncio
import logging
import sys
import time
from dataclasses import dataclass

import httpx2
from opentelemetry import trace

from contracts.errors import ErrorCode
from retrieval.adapters.blob import BlobManualStore, build_blob_service
from retrieval.adapters.db import (
    SqlChunkRepository,
    SqlSchemaRevision,
    build_database,
)
from retrieval.adapters.layout import (
    DocumentLayout,
    build_layout_http,
    layout_token_for,
)
from retrieval.adapters.migrations import bundled_head
from retrieval.adapters.model import (
    ModelGateway,
    build_model_client,
    chat_deployment,
    embedding_deployment,
    model_token_for,
)
from retrieval.adapters.telemetry import (
    adapter_span,
    code_locations,
    configure_logging,
    configure_telemetry,
    shutdown_telemetry,
)
from retrieval.domain.entities import IngestReport
from retrieval.domain.ingest import (
    IngestError,
    IngestOptions,
    IngestPorts,
    ingest_manual,
)
from retrieval.prompts import CHUNK_CONTEXT, prompt_digest
from retrieval.settings import APP_ID, Settings, get_settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

SCHEMA_MESSAGE = "The database is not at the migration this build ships with."
OK, FAILED = 0, 1


@dataclass(frozen=True, slots=True)
class Transports:
    """Where a test puts its stand-ins for Document Intelligence and the models."""

    layout: httpx2.AsyncBaseTransport | None = None
    model: httpx2.AsyncBaseTransport | None = None


def missing_settings(settings: Settings) -> list[str]:
    """The variables the job cannot run without and was not given."""
    needed = {
        "RETRIEVAL_LAYOUT_ENDPOINT": settings.layout_endpoint,
        "RETRIEVAL_MODEL_ENDPOINT": settings.model_endpoint,
        "RETRIEVAL_CHAT_DEPLOYMENT": settings.chat_deployment,
        "RETRIEVAL_EMBEDDING_DEPLOYMENT": settings.embedding_deployment,
        "RETRIEVAL_BLOB_ACCOUNT_URL": settings.blob_account_url
        or settings.blob_connection_string,
    }
    return [name for name, value in needed.items() if value is None]


def ingest_options(settings: Settings) -> IngestOptions:
    """What a run works with, as the settings say."""
    return IngestOptions(
        chat_deployment=chat_deployment(settings),
        embedding_deployment=embedding_deployment(settings),
        prompt_digest=prompt_digest(CHUNK_CONTEXT),
        context_line_max_chars=settings.context_line_max_chars,
        embedding_batch_size=settings.embedding_batch_size,
        max_removed_share=settings.ingest_max_removed_share,
        allow_large_removal=settings.ingest_allow_large_removal,
        deadline_seconds=settings.ingest_deadline_seconds,
    )


async def run(settings: Settings, transports: Transports | None = None) -> IngestReport:
    """One run with the real adapters, as the settings describe them.

    Raises `IngestError` when the run could not be done; the index is then
    as it was.
    """
    transports = transports or Transports()
    options = ingest_options(settings)
    database = build_database(settings)
    layout = DocumentLayout(
        build_layout_http(settings, transports.layout),
        api_version=settings.layout_api_version,
        model=settings.layout_model,
        poll_seconds=settings.layout_poll_seconds,
        deadline_seconds=settings.layout_deadline_seconds,
        max_retries=settings.layout_max_retries,
        token=layout_token_for(settings),
    )
    gateway = ModelGateway(
        build_model_client(settings, transports.model, model_token_for(settings)),
        chat_deployment=options.chat_deployment,
        embedding_deployment=options.embedding_deployment,
        max_retries=settings.model_max_retries,
        retry_seconds=settings.model_retry_seconds,
        max_retry_seconds=settings.model_max_retry_seconds,
        max_completion_tokens=settings.model_max_completion_tokens,
        max_concurrent_calls=settings.model_max_concurrent_calls,
    )
    ports = IngestPorts(
        manual=BlobManualStore(
            build_blob_service(settings),
            settings.manual_container,
            settings.manual_blob_name,
        ),
        layout=layout,
        model=gateway,
        repository=SqlChunkRepository(database),
    )
    try:
        with adapter_span(tracer, "retrieval.ingest") as span:
            try:
                # Before anything is spent: a schema that is behind this
                # build would fail the run at its very end.
                current = await SqlSchemaRevision(database).current()
                if current != bundled_head():
                    raise IngestError(
                        ErrorCode.UPSTREAM_UNAVAILABLE,
                        SCHEMA_MESSAGE,
                        "schema_not_at_head",
                        str(current),
                    )
                report = await ingest_manual(ports, options)
            except IngestError as error:
                span.set_attribute("retrieval.ingest.outcome", "failed")
                span.set_attribute("error.type", error.code.value)
                span.set_attribute("retrieval.ingest.reason", error.reason)
                raise
            span.set_attribute(
                "retrieval.ingest.outcome", "unchanged" if report.skipped else "done"
            )
            span.set_attribute("retrieval.chunks.count", report.chunks)
            span.set_attribute("retrieval.chunks.written", report.written)
            span.set_attribute("retrieval.chunks.moved", report.moved)
            span.set_attribute("retrieval.chunks.removed", report.removed)
            span.set_attribute("retrieval.chunks.unchanged", report.unchanged)
            return report
    finally:
        # Each resource is closed even if the one before it failed to close.
        try:
            await gateway.aclose()
        finally:
            try:
                await layout.aclose()
            finally:
                await database.dispose()


def main(settings: Settings | None = None, transports: Transports | None = None) -> int:
    """Run the job once and say how it ended; the return value is the exit status."""
    configure_logging()
    started = time.monotonic()
    telemetry_on = False
    try:
        try:
            if settings is None:
                settings = get_settings()
            missing = missing_settings(settings)
            if not missing:
                telemetry_on = configure_telemetry(settings)
        except Exception as error:  # noqa: BLE001 - a job that cannot be set up says so in its one line, like any other failure
            # The type only: a settings error's message repeats the values
            # it refused, and one of them may be a connection string.
            logger.error(
                "ingestion failed: code=%s reason=setup_%s",
                ErrorCode.INTERNAL_ERROR.value,
                type(error).__qualname__,
            )
            return FAILED
        if missing:
            # Said before anything is built: the service runs without
            # these, the job does not.
            logger.error(
                "ingestion failed: code=%s reason=not_configured missing=%s",
                ErrorCode.INTERNAL_ERROR.value,
                ",".join(missing),
            )
            return FAILED
        return _run_and_report(settings, transports, started)
    finally:
        if telemetry_on:
            # The process ends after this: what the run's spans and log
            # lines hold is sent first.
            shutdown_telemetry()


def _run_and_report(
    settings: Settings, transports: Transports | None, started: float
) -> int:
    try:
        report = asyncio.run(run(settings, transports))
    except IngestError as error:
        # security rule 31: codes and ids only.
        logger.error(
            "ingestion failed: code=%s reason=%s where=%s seconds=%.1f",
            error.code.value,
            error.reason,
            error.where or "-",
            time.monotonic() - started,
        )
        return FAILED
    except Exception as error:  # noqa: BLE001 - whatever went wrong, the job says so and ends non-zero
        # The error's type and where it was raised, never its message, which
        # can hold a statement or an address.
        logger.error(
            "ingestion failed: code=%s reason=%s at=%s seconds=%.1f",
            ErrorCode.INTERNAL_ERROR.value,
            type(error).__qualname__,
            " <- ".join(reversed(code_locations(error))),
            time.monotonic() - started,
        )
        return FAILED
    logger.info(
        "ingestion done: pages=%d chunks=%d written=%d moved=%d removed=%d "
        "unchanged=%d skipped=%s seconds=%.1f",
        report.pages,
        report.chunks,
        report.written,
        report.moved,
        report.removed,
        report.unchanged,
        "yes" if report.skipped else "no",
        time.monotonic() - started,
    )
    return OK


if __name__ == "__main__":
    sys.exit(main())
