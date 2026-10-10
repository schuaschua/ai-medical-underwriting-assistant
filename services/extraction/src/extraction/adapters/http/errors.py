"""Every error leaves `extraction` in the contracts error shape (security rule 26)."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from contracts.errors import DomainError, ErrorCode
from extraction.adapters.telemetry import current_trace_id

# Plain sentences only: no stack trace, SQL, path or input value.
NOT_FOUND_MESSAGE = "Not found."
METHOD_NOT_ALLOWED_MESSAGE = "That method is not allowed here."
PAYLOAD_TOO_LARGE_MESSAGE = "The request is too large."
UNSUPPORTED_MEDIA_TYPE_MESSAGE = "The request's content type is not supported."
TOO_MANY_REQUESTS_MESSAGE = "Too many requests. Please try again shortly."
INVALID_REQUEST_MESSAGE = "The request is not valid."
INTERNAL_ERROR_MESSAGE = "Something went wrong. Please try again."

# The framework statuses that have a code of their own in the catalogue.
_FRAMEWORK_ERRORS: dict[int, tuple[ErrorCode, str]] = {
    404: (ErrorCode.NOT_FOUND, NOT_FOUND_MESSAGE),
    405: (ErrorCode.METHOD_NOT_ALLOWED, METHOD_NOT_ALLOWED_MESSAGE),
    413: (ErrorCode.PAYLOAD_TOO_LARGE, PAYLOAD_TOO_LARGE_MESSAGE),
    415: (ErrorCode.UNSUPPORTED_MEDIA_TYPE, UNSUPPORTED_MEDIA_TYPE_MESSAGE),
    429: (ErrorCode.TOO_MANY_REQUESTS, TOO_MANY_REQUESTS_MESSAGE),
}


def error_response(error: DomainError, traceparent: str | None) -> JSONResponse:
    """Build the JSON response for a domain error."""
    body = error.to_body(current_trace_id(traceparent))
    return JSONResponse(body.model_dump(mode="json"), status_code=error.http_status)


def _from_request(request: Request, error: DomainError) -> JSONResponse:
    return error_response(error, request.headers.get("traceparent"))


def framework_error(status_code: int) -> DomainError:
    """Map an HTTP error raised by the framework to a code from the catalogue."""
    if status_code in _FRAMEWORK_ERRORS:
        return DomainError(*_FRAMEWORK_ERRORS[status_code])
    if 400 <= status_code < 500:
        return DomainError(ErrorCode.VALIDATION_FAILED, INVALID_REQUEST_MESSAGE)
    return DomainError(ErrorCode.INTERNAL_ERROR, INTERNAL_ERROR_MESSAGE)


def install_error_handlers(app: FastAPI) -> None:
    """Register the handlers that turn every raised error into the error shape."""

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, error: DomainError) -> JSONResponse:
        return _from_request(request, error)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        return _from_request(request, framework_error(error.status_code))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        # The framework's detail echoes the input, so it is not passed on.
        return _from_request(
            request, DomainError(ErrorCode.VALIDATION_FAILED, INVALID_REQUEST_MESSAGE)
        )
