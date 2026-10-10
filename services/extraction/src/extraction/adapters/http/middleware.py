"""ASGI middleware: security headers on every response, and the last-resort error body."""

import logging

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from contracts.errors import DomainError, ErrorCode
from extraction.adapters.http.errors import INTERNAL_ERROR_MESSAGE, error_response
from extraction.adapters.telemetry import (
    code_locations,
    current_trace_id,
    record_error_on_span,
)

logger = logging.getLogger(__name__)

# security.md rule 25. The service answers with JSON only and is reached only
# from inside the environment; the headers are sent all the same.
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
    ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),
    # Answers are live data: nothing keeps them.
    ("Cache-Control", "no-store"),
)


class SecurityHeadersMiddleware:
    """Adds the security headers to every HTTP response, error responses included."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    headers[name] = value
            await send(message)

        await self._app(scope, receive, send_with_headers)


class UnhandledErrorMiddleware:
    """Answers any error no handler caught with a plain 500 in the error shape."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self._app(scope, receive, tracking_send)
        except Exception as error:
            traceparent = Headers(scope=scope).get("traceparent")
            # security rule 31: the error's type and where it was raised, never
            # its message, which can hold input values.
            logger.error(
                "unhandled error: type=%s trace_id=%s at=%s",
                type(error).__qualname__,
                current_trace_id(traceparent),
                " <- ".join(reversed(code_locations(error))),
            )
            record_error_on_span(error)
            if started:
                # Too late to change the response; let the server close the connection.
                raise
            response = error_response(
                DomainError(ErrorCode.INTERNAL_ERROR, INTERNAL_ERROR_MESSAGE),
                traceparent,
            )
            await response(scope, receive, send)
