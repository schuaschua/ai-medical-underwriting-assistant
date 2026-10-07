"""Logging, OpenTelemetry export to Application Insights, and the trace id of a request."""

import logging
import re
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.trace import Span, StatusCode, Tracer, TracerProvider

from intake.adapters.credential import azure_credential
from intake.settings import APP_ID, HEALTH_PATH, READY_PATH, Settings

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
# Libraries that log every call at INFO. The Azure SDK's lines list request
# headers, which security rule 31 keeps out of the logs.
QUIET_LOGGERS = ("azure", "alembic")
# The instrumentation matches these patterns against the request URL.
EXCLUDED_URLS = f"{re.escape(HEALTH_PATH)}$,{re.escape(READY_PATH)}$"


def configure_logging() -> None:
    """Send the service's own log records to standard error. Call once, at start-up."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


# W3C trace context: version, trace id, parent id, flags.
_TRACEPARENT_RE = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


def configure_telemetry(
    settings: Settings, *, exporter_setup: Callable[..., None] | None = None
) -> bool:
    """Switch telemetry export on if a connection string is configured.

    Returns whether export was switched on; if so, pass the app to
    `instrument_app` once it exists.
    """
    connection_string = settings.applicationinsights_connection_string
    if connection_string is None:
        return False
    if exporter_setup is None:
        # Imported here so a process without telemetry does not load the exporter.
        from azure.monitor.opentelemetry import configure_azure_monitor

        exporter_setup = configure_azure_monitor
    exporter_setup(
        connection_string=connection_string.get_secret_value(),
        # Application Insights has local authentication off, so telemetry is
        # sent with an Entra token.
        credential=azure_credential(settings),
        sampling_ratio=settings.otel_sampling_ratio,
        resource=Resource.create({SERVICE_NAME: APP_ID}),
        # The app is instrumented by `instrument_app`, which leaves out the
        # probe routes; the automatic instrumentation would trace every probe.
        instrumentation_options={"fastapi": {"enabled": False}},
    )
    return True


def instrument_app(app: FastAPI, tracer_provider: TracerProvider | None = None) -> None:
    """Trace the app's requests, except the probes'.

    Without a tracer provider the global one is used, which
    `configure_telemetry` has set.
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=tracer_provider, excluded_urls=EXCLUDED_URLS
    )


def code_locations(error: BaseException) -> list[str]:
    """Where an error was raised: file, line and function of each frame.

    The error's own message and the source lines are left out, because they
    can hold input values (security rule 31).
    """
    return [
        f"{frame.filename}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(error.__traceback__)
    ]


def record_error_on_span(error: BaseException) -> None:
    """Mark the active span as failed, with the error's type and code locations."""
    span = trace.get_current_span()
    if not span.is_recording():
        return
    # Not `span.record_exception`: that stores the error's message as well.
    span.add_event(
        "exception",
        {
            "exception.type": type(error).__qualname__,
            "exception.stacktrace": "\n".join(code_locations(error)),
        },
    )
    span.set_status(StatusCode.ERROR)


@contextmanager
def adapter_span(tracer: Tracer, name: str, **options: Any) -> Iterator[Span]:
    """Open a span of an adapter's own, the current one while its block runs.

    The tracing library would put a raised error's message on the span by
    itself, and a database error's message holds the statement that failed.
    That is switched off here: an error is marked as `record_error_on_span`
    marks it, by its type and where it was raised (security rule 31).
    """
    with tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False, **options
    ) as span:
        try:
            yield span
        except Exception as error:
            record_error_on_span(error)
            raise


def current_trace_id(traceparent: str | None) -> str | None:
    """The trace id of the request being handled, if there is one.

    The active span wins; without telemetry the caller's `traceparent` header
    is used, so an error can still be matched to the caller's trace.
    """
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        return trace.format_trace_id(context.trace_id)
    if traceparent is not None:
        match = _TRACEPARENT_RE.fullmatch(traceparent.strip())
        if match is not None:
            return match.group(1)
    return None
