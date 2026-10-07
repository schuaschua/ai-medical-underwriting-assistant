"""The ingestion job: `python -m retrieval.ingest` (spine AD-12).

A one-off job on the `retrieval` image and identity, started by hand or by
the pipeline. It calls no other service of ours: it reads the manual from
the `manual` container, has Document Intelligence parse it, has the chat
model write a context line per chunk and the embedding model a vector, and
stores the chunks in schema `retrieval`. Run again over the same manual it
changes nothing and calls no model.

It writes the chunk sets the settings name (`smart` and `fixed` unless told
otherwise), each as a run of its own over the one parsed manual: the
`fixed` set gets no context line, only vectors from the same embedding
deployment.

It ends with status 0 when every chunk set is as the manual has it, and with
1 otherwise, after one log line per chunk set that names its counts, or the
error code and the reason. A failed run leaves its chunk set as it was, and
the other set as its own run left it. When the stored sets then stand on
different manuals the job says so in a line of its own and ends with 1 as
well: the rows of the ladder are compared with each other. Before the process ends its telemetry
is sent.
"""

import asyncio
import logging
import sys
import time
from dataclasses import dataclass

import httpx2
from opentelemetry import trace

from contracts.enums import ChunkSet
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
    built_from_different_manuals,
    ingest_chunk_sets,
)
from retrieval.prompts import CHUNK_CONTEXT, prompt_digest
from retrieval.settings import APP_ID, Settings, get_settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

SCHEMA_MESSAGE = "The database is not at the migration this build ships with."
# The reason of a job after which the stored chunk sets stand on different manuals.
DIFFERENT_MANUALS = "chunk_sets_built_from_different_manuals"
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
        deadline_seconds=settings.ingest_deadline_seconds,
        fixed_chunk_words=settings.fixed_chunk_words,
        fixed_overlap_words=settings.fixed_chunk_overlap_words,
    )


@dataclass(frozen=True, slots=True)
class JobResult:
    """How a job ended."""

    # Each chunk set's report, or the error its run ended with.
    chunk_sets: dict[ChunkSet, IngestReport | Exception]
    # The manual each stored set was last built from, when they differ; else empty.
    different_manuals: dict[ChunkSet, str]


async def run(settings: Settings, transports: Transports | None = None) -> JobResult:
    """One run per chunk set with the real adapters, as the settings describe them.

    Answers each set's report, or the error its run ended with; that set is
    then as it was. Raises `IngestError` when no run could begin: the
    schema is not the one this build ships with.
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
            except IngestError as error:
                _note_failure(span, error)
                raise
            outcomes = await ingest_chunk_sets(
                ports,
                options,
                settings.ingest_chunk_sets,
                allow_large_removal=settings.ingest_allow_large_removal,
            )
            different = await built_from_different_manuals(ports.repository)
            failed = {
                chunk_set: outcome
                for chunk_set, outcome in outcomes.items()
                if isinstance(outcome, Exception)
            }
            reports = [o for o in outcomes.values() if isinstance(o, IngestReport)]
            if failed:
                first = next(iter(failed.values()))
                span.set_attribute("retrieval.ingest.outcome", "failed")
                span.set_attribute("error.type", _code_of(first).value)
                span.set_attribute("retrieval.ingest.reason", _reason_of(first))
                span.set_attribute(
                    "retrieval.ingest.failed_chunk_sets",
                    [chunk_set.value for chunk_set in failed],
                )
            elif different:
                span.set_attribute("retrieval.ingest.outcome", "failed")
                span.set_attribute("error.type", ErrorCode.STAGE_FAILED.value)
                span.set_attribute("retrieval.ingest.reason", DIFFERENT_MANUALS)
            else:
                span.set_attribute(
                    "retrieval.ingest.outcome",
                    "unchanged" if all(r.skipped for r in reports) else "done",
                )
            span.set_attribute(
                "retrieval.ingest.chunk_sets",
                [chunk_set.value for chunk_set in outcomes],
            )
            # Over the chunk sets whose run was done.
            span.set_attribute("retrieval.chunks.count", sum(r.chunks for r in reports))
            span.set_attribute(
                "retrieval.chunks.written", sum(r.written for r in reports)
            )
            span.set_attribute("retrieval.chunks.moved", sum(r.moved for r in reports))
            span.set_attribute(
                "retrieval.chunks.removed", sum(r.removed for r in reports)
            )
            span.set_attribute(
                "retrieval.chunks.unchanged", sum(r.unchanged for r in reports)
            )
            return JobResult(outcomes, different)
    finally:
        # Each resource is closed even if the one before it failed to close.
        try:
            await gateway.aclose()
        finally:
            try:
                await layout.aclose()
            finally:
                await database.dispose()


def _note_failure(span: trace.Span, error: IngestError) -> None:
    span.set_attribute("retrieval.ingest.outcome", "failed")
    span.set_attribute("error.type", error.code.value)
    span.set_attribute("retrieval.ingest.reason", error.reason)


def _code_of(error: Exception) -> ErrorCode:
    return error.code if isinstance(error, IngestError) else ErrorCode.INTERNAL_ERROR


def _reason_of(error: Exception) -> str:
    """A short code for a set's failure: the run's own reason, or the error's type, never its message."""
    return error.reason if isinstance(error, IngestError) else type(error).__qualname__


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
        result = asyncio.run(run(settings, transports))
    except IngestError as error:
        _log_failure(error, "-", started)
        return FAILED
    except Exception as error:  # noqa: BLE001 - whatever went wrong, the job says so and ends non-zero
        _log_unexpected(error, "-", started)
        return FAILED
    status = OK
    for chunk_set, outcome in result.chunk_sets.items():
        if isinstance(outcome, IngestError):
            _log_failure(outcome, chunk_set.value, started)
            status = FAILED
        elif isinstance(outcome, Exception):
            _log_unexpected(outcome, chunk_set.value, started)
            status = FAILED
        else:
            logger.info(
                "ingestion done: pages=%d chunks=%d written=%d moved=%d removed=%d "
                "unchanged=%d skipped=%s chunk_set=%s seconds=%.1f",
                outcome.pages,
                outcome.chunks,
                outcome.written,
                outcome.moved,
                outcome.removed,
                outcome.unchanged,
                "yes" if outcome.skipped else "no",
                chunk_set.value,
                time.monotonic() - started,
            )
    if result.different_manuals:
        # An error of its own, whatever each set's run said: a search on one
        # row and a search on another no longer read the same manual.
        logger.error(
            "ingestion failed: code=%s reason=%s chunk_sets=%s manual_sha256=%s",
            ErrorCode.STAGE_FAILED.value,
            DIFFERENT_MANUALS,
            ",".join(chunk_set.value for chunk_set in result.different_manuals),
            ",".join(result.different_manuals.values()),
        )
        status = FAILED
    return status


def _log_unexpected(error: Exception, chunk_set: str, started: float) -> None:
    # The error's type and where it was raised, never its message, which
    # can hold a statement or an address.
    logger.error(
        "ingestion failed: code=%s reason=%s at=%s chunk_set=%s seconds=%.1f",
        ErrorCode.INTERNAL_ERROR.value,
        type(error).__qualname__,
        " <- ".join(reversed(code_locations(error))),
        chunk_set,
        time.monotonic() - started,
    )


def _log_failure(error: IngestError, chunk_set: str, started: float) -> None:
    # security rule 31: codes and ids only.
    logger.error(
        "ingestion failed: code=%s reason=%s where=%s chunk_set=%s seconds=%.1f",
        error.code.value,
        error.reason,
        error.where or "-",
        chunk_set,
        time.monotonic() - started,
    )


if __name__ == "__main__":
    sys.exit(main())
