"""Every error leaves `web` in the contracts error shape (security rule 26)."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from contracts.errors import DomainError, ErrorCode
from web.adapters.telemetry import current_trace_id

# Plain sentences only: no stack trace, SQL, path or input value.
NOT_FOUND_MESSAGE = "Not found."
INVALID_REQUEST_MESSAGE = "The request is not valid."
INTERNAL_ERROR_MESSAGE = "Something went wrong. Please try again."


API_PREFIX = "/api"


def is_api_path(path: str) -> bool:
    """Whether a request path belongs to the API, not to the SPA."""
    return path == API_PREFIX or path.startswith(f"{API_PREFIX}/")


def error_response(error: DomainError, traceparent: str | None) -> JSONResponse:
    """Build the JSON response for a domain error."""
    body = error.to_body(current_trace_id(traceparent))
    return JSONResponse(body.model_dump(mode="json"), status_code=error.http_status)


def _from_request(request: Request, error: DomainError) -> JSONResponse:
    return error_response(error, request.headers.get("traceparent"))


def framework_error(status_code: int, path: str = "") -> DomainError:
    """Map an HTTP error raised by the framework to a code from the catalogue.

    The catalogue fixes one status per code, so a framework status with no code
    of its own (405, say) is answered as the nearest code and that code's status.
    Under `/api`, a method the path does not take is the same as an unknown
    path: there is nothing there to call.
    """
    if status_code == 404 or (status_code == 405 and is_api_path(path)):
        return DomainError(ErrorCode.NOT_FOUND, NOT_FOUND_MESSAGE)
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
        return _from_request(
            request, framework_error(error.status_code, request.url.path)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        # The framework's detail echoes the input, so it is not passed on.
        return _from_request(
            request, DomainError(ErrorCode.VALIDATION_FAILED, INVALID_REQUEST_MESSAGE)
        )
