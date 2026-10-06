"""The one module that calls other services: HTTP through the Dapr sidecar (spine AD-3).

Services are addressed by Dapr app id; `web` holds no other service's
hostname. No Dapr SDK is used.
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx
from opentelemetry import propagate
from pydantic import ValidationError

from contracts.enums import Service
from contracts.errors import HTTP_STATUS, DomainError, ErrorBody, ErrorCode
from contracts.models.intake import CaseCreated
from contracts.operations import get_operation
from web.adapters.telemetry import is_traceparent
from web.settings import Settings

logger = logging.getLogger(__name__)

UPSTREAM_UNAVAILABLE_MESSAGE = (
    "The service is not available right now. Please try again."
)

# The upload rules `intake` checks again (AD-3); its verdict on them is passed
# on to the browser. Any other failure of the call is `upstream_unavailable`.
_UPLOAD_REFUSALS = frozenset(
    {
        ErrorCode.VALIDATION_FAILED,
        ErrorCode.FILE_TOO_LARGE,
        ErrorCode.UNSUPPORTED_FILE_TYPE,
        ErrorCode.PAYLOAD_TOO_LARGE,
        ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    }
)


def sidecar_base_url(settings: Settings) -> str:
    """The sidecar listens on loopback, beside the app."""
    return f"http://127.0.0.1:{settings.dapr_http_port}"


def invoke_path(service: Service, path: str) -> str:
    """The sidecar's service invocation path for one operation of another service."""
    return f"/v1.0/invoke/{service.value}/method{path}"


def build_http_client(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """The HTTP client for the sidecar. Tests pass a transport that stands in for it."""
    return httpx.AsyncClient(
        base_url=sidecar_base_url(settings),
        timeout=settings.service_timeout_seconds,
        transport=transport,
        # The sidecar is on this machine: no proxy setting applies to it.
        trust_env=False,
    )


def trace_headers(traceparent: str | None) -> dict[str, str]:
    """The headers that carry the request's trace on to the next service."""
    # Conventions: the W3C trace context is passed on every Dapr call. The
    # active span wins; without telemetry the caller's own header is passed
    # on, but only a well-formed one: anything else is the caller's text.
    headers: dict[str, str] = {}
    propagate.inject(headers)
    if "traceparent" not in headers and is_traceparent(traceparent):
        headers["traceparent"] = str(traceparent)
    return headers


def _unavailable() -> DomainError:
    return DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, UPSTREAM_UNAVAILABLE_MESSAGE)


class ServiceClient:
    """Calls the operations of other services that `web` is allowed to call."""

    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._upload_timeout = settings.upload_timeout_seconds

    async def aclose(self) -> None:
        await self._http.aclose()

    async def create_case(
        self,
        pdf: AsyncIterator[bytes],
        *,
        content_length: int | None,
        traceparent: str | None,
    ) -> CaseCreated:
        """`POST /cases` on `intake`: hand the PDF on as it arrives, without keeping it."""
        operation = get_operation("create_case")
        headers = trace_headers(traceparent)
        headers["Content-Type"] = operation.request_media_type or ""
        if content_length is not None:
            # A known length is sent as such; otherwise the body goes chunked.
            headers["Content-Length"] = str(content_length)
        try:
            # One deadline for the whole call (see the settings for the three
            # upload deadlines and their order). httpx's own timeout applies
            # to each phase separately, so it alone would not bound the call.
            async with asyncio.timeout(self._upload_timeout):
                response = await self._http.request(
                    operation.method.value,
                    invoke_path(operation.owner, operation.path),
                    content=pdf,
                    headers=headers,
                    timeout=self._upload_timeout,
                )
        except (httpx.HTTPError, TimeoutError) as error:
            # security rule 31: the error's type; its message can hold an address.
            logger.error(
                "service call failed: service=%s operation=%s type=%s",
                operation.owner.value,
                operation.name,
                type(error).__qualname__,
            )
            raise _unavailable() from error

        if response.is_success:
            # Any 2xx that carries the contract's payload is the created case.
            try:
                return CaseCreated.model_validate_json(response.content)
            except ValidationError:
                logger.error(
                    "service call failed: service=%s operation=%s status=%d "
                    "code=invalid_body",
                    operation.owner.value,
                    operation.name,
                    response.status_code,
                )
                raise _unavailable() from None
        raise self._refusal(response, operation.owner, operation.name)

    @staticmethod
    def _refusal(response: httpx.Response, owner: Service, name: str) -> DomainError:
        try:
            detail = ErrorBody.model_validate_json(response.content).error
        except ValidationError:
            # Not our error shape: the sidecar's own answer, or a proxy's.
            detail = None
        code = detail.code.value if detail is not None else "none"
        # A refusal of the user's file is passed on only when the answer holds
        # together: a code from the upload rules, under that code's own status.
        if (
            detail is not None
            and detail.code in _UPLOAD_REFUSALS
            and response.status_code == HTTP_STATUS[detail.code]
        ):
            # The user's file was refused: nothing is wrong with the service.
            logger.info(
                "upload refused: service=%s operation=%s status=%d code=%s",
                owner.value,
                name,
                response.status_code,
                code,
            )
            return DomainError(detail.code, detail.message)
        # A 5xx, or an answer this call should never get: the service's fault.
        logger.error(
            "service call failed: service=%s operation=%s status=%d code=%s",
            owner.value,
            name,
            response.status_code,
            code,
        )
        return _unavailable()
