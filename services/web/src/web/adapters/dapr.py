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

from contracts.base import ContractModel
from contracts.enums import Service
from contracts.errors import HTTP_STATUS, DomainError, ErrorBody, ErrorCode
from contracts.models.classification import ClassificationList
from contracts.models.intake import CaseCreated
from contracts.models.workflow import (
    AuditTrail,
    CaseProgress,
    CaseStarted,
    DecisionRecorded,
    DecisionRequest,
    StartCaseRequest,
)
from contracts.operations import Operation, get_operation
from contracts.upload import IDEMPOTENCY_KEY_HEADER
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


# What `workflow` may say about the caller's own request, passed on to the
# browser as it is: the case is unknown, or the start options are not valid.
_UNKNOWN_CASE = frozenset({ErrorCode.NOT_FOUND, ErrorCode.VALIDATION_FAILED})
_INVALID_START = frozenset({ErrorCode.VALIDATION_FAILED})
# AD-10: what `workflow` may say about a decision. The rule is its own; its
# verdict is passed on as it is. A decision whose event could not be raised
# is `upstream_unavailable` there and here: the caller sends it again.
_DECISION_REFUSALS = frozenset(
    {
        ErrorCode.NOT_FOUND,
        ErrorCode.VALIDATION_FAILED,
        ErrorCode.ACTOR_NOT_HUMAN,
        ErrorCode.ROLE_NOT_ALLOWED,
        ErrorCode.NOT_AWAITING_DECISION,
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
        self._lifecycle_timeout = settings.lifecycle_timeout_seconds

    async def aclose(self) -> None:
        await self._http.aclose()

    async def create_case(
        self,
        pdf: AsyncIterator[bytes],
        *,
        content_length: int | None,
        idempotency_key: str | None,
        traceparent: str | None,
    ) -> CaseCreated:
        """`POST /cases` on `intake`: hand the PDF on as it arrives, without keeping it.

        The browser's idempotency key goes with it, so a retried upload is
        answered with the case the first one created.
        """
        operation = get_operation("create_case")
        headers = trace_headers(traceparent)
        headers["Content-Type"] = operation.request_media_type or ""
        if idempotency_key is not None:
            headers[IDEMPOTENCY_KEY_HEADER] = idempotency_key
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
        raise self._refusal(
            response, operation.owner, operation.name, _UPLOAD_REFUSALS, "upload"
        )

    async def start_case(
        self,
        case_id: str,
        options: StartCaseRequest | None,
        *,
        traceparent: str | None,
    ) -> CaseStarted:
        """`POST /cases/{case_id}/start` on `workflow` (AD-2): idempotent on the case id."""
        return await self._call(
            get_operation("start_case"),
            {"case_id": case_id},
            CaseStarted,
            passed_on=_INVALID_START,
            traceparent=traceparent,
            # Only what the caller set is sent; `workflow` fills in the rest.
            body=options.model_dump(mode="json", exclude_none=True)
            if options is not None
            else {},
        )

    async def read_progress(
        self, case_id: str, *, traceparent: str | None
    ) -> CaseProgress:
        """`GET /cases/{case_id}/progress` on `workflow`."""
        return await self._call(
            get_operation("read_progress"),
            {"case_id": case_id},
            CaseProgress,
            passed_on=_UNKNOWN_CASE,
            traceparent=traceparent,
        )

    async def read_audit_trail(
        self, case_id: str, *, traceparent: str | None
    ) -> AuditTrail:
        """`GET /cases/{case_id}/audit` on `workflow`."""
        return await self._call(
            get_operation("read_audit_trail"),
            {"case_id": case_id},
            AuditTrail,
            passed_on=_UNKNOWN_CASE,
            traceparent=traceparent,
        )

    async def list_classifications(
        self, case_id: str, *, traceparent: str | None
    ) -> ClassificationList:
        """`GET /cases/{case_id}/classifications` on `classification`."""
        return await self._call(
            get_operation("list_classifications"),
            {"case_id": case_id},
            ClassificationList,
            passed_on=_UNKNOWN_CASE,
            traceparent=traceparent,
        )

    async def record_decision(
        self,
        case_id: str,
        page_id: str,
        decision: DecisionRequest,
        *,
        traceparent: str | None,
    ) -> DecisionRecorded:
        """`POST /cases/{case_id}/pages/{page_id}/decisions` on `workflow` (AD-10).

        Safe to repeat: the same decision again is answered with the stored one.
        """
        return await self._call(
            get_operation("record_decision"),
            {"case_id": case_id, "page_id": page_id},
            DecisionRecorded,
            passed_on=_DECISION_REFUSALS,
            traceparent=traceparent,
            body=decision.model_dump(mode="json"),
        )

    async def _call[T: ContractModel](
        self,
        operation: Operation,
        path_parameters: dict[str, str],
        answer: type[T],
        *,
        passed_on: frozenset[ErrorCode],
        traceparent: str | None,
        body: dict[str, object] | None = None,
    ) -> T:
        """One JSON operation of another service, answered with its contract model."""
        try:
            # One deadline for the whole call, shorter than the browser's:
            #   workflow's scheduler call 10 s  <  web 20 s
            #   (WEB_LIFECYCLE_TIMEOUT_SECONDS)  <  browser 30 s.
            # httpx's own timeout applies to each phase separately, so it
            # alone would not bound the call.
            async with asyncio.timeout(self._lifecycle_timeout):
                response = await self._http.request(
                    operation.method.value,
                    # The parameters are ids the route has already checked.
                    invoke_path(
                        operation.owner, operation.path.format(**path_parameters)
                    ),
                    json=body,
                    headers=trace_headers(traceparent),
                    timeout=self._lifecycle_timeout,
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
            try:
                return answer.model_validate_json(response.content)
            except ValidationError:
                logger.error(
                    "service call failed: service=%s operation=%s status=%d "
                    "code=invalid_body",
                    operation.owner.value,
                    operation.name,
                    response.status_code,
                )
                raise _unavailable() from None
        raise self._refusal(
            response, operation.owner, operation.name, passed_on, "request"
        )

    @staticmethod
    def _refusal(
        response: httpx.Response,
        owner: Service,
        name: str,
        passed_on: frozenset[ErrorCode],
        what: str,
    ) -> DomainError:
        try:
            detail = ErrorBody.model_validate_json(response.content).error
        except ValidationError:
            # Not our error shape: the sidecar's own answer, or a proxy's.
            detail = None
        code = detail.code.value if detail is not None else "none"
        # A refusal of what the user sent is passed on only when the answer
        # holds together: a code this call may pass on, under that code's own status.
        if (
            detail is not None
            and detail.code in passed_on
            and response.status_code == HTTP_STATUS[detail.code]
        ):
            # The user's request was refused: nothing is wrong with the service.
            logger.info(
                "%s refused: service=%s operation=%s status=%d code=%s",
                what,
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
