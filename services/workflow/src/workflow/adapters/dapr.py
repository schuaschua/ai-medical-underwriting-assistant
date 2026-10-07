"""The one module that calls other services: HTTP through the Dapr sidecar (spine AD-3).

`workflow` commands the stage services (AD-2). They are addressed by Dapr app
id; `workflow` holds no other service's hostname. No Dapr SDK is used.
"""

import asyncio
import logging
from collections.abc import Mapping

import httpx
from opentelemetry import propagate, trace
from pydantic import ValidationError

from contracts.enums import Service
from contracts.errors import HTTP_STATUS, DomainError, ErrorBody, ErrorCode
from contracts.models.intake import RedactionCommand, RedactionResult
from contracts.operations import get_operation
from workflow.settings import APP_ID, Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

UPSTREAM_UNAVAILABLE_MESSAGE = "A stage service is not available right now."
WRONG_CASE_MESSAGE = "The stage answered about another case."

# What a stage may say that is an answer in itself, passed on with its code:
# the stage is still working on the same command (AD-6), or it does not hold
# the case. Anything else that is not a result is `upstream_unavailable`.
_STAGE_ANSWERS = frozenset(
    {
        ErrorCode.IN_PROGRESS,
        ErrorCode.NOT_FOUND,
        # The stage will refuse the same command every time: not retried.
        ErrorCode.VALIDATION_FAILED,
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
        timeout=settings.stage_timeout_seconds,
        transport=transport,
        # The sidecar is on this machine: no proxy setting applies to it.
        trust_env=False,
    )


def trace_headers() -> dict[str, str]:
    """The W3C trace context of the caller, to pass on with a call (Conventions).

    Read where the trace is active: an activity runs on a worker thread and
    its HTTP call on the service's event loop, which does not see that thread's
    context.
    """
    headers: dict[str, str] = {}
    propagate.inject(headers)
    return headers


def _unavailable() -> DomainError:
    return DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, UPSTREAM_UNAVAILABLE_MESSAGE)


class StageClient:
    """Calls the stage commands `workflow` is allowed to send."""

    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._stage_timeout = settings.stage_timeout_seconds

    async def aclose(self) -> None:
        await self._http.aclose()

    async def redact_document(
        self,
        case_id: str,
        *,
        eval_run_id: str | None,
        trace_context: Mapping[str, str],
    ) -> RedactionResult:
        """`POST /cases/{case_id}/redaction` on `intake` (AD-21): idempotent on the case id.

        The answer is the stored result, done or failed. `in_progress` and
        `not_found` are raised with their own codes; any other failure of the
        call is `upstream_unavailable`.
        """
        operation = get_operation("redact_document")
        command = RedactionCommand(eval_run_id=eval_run_id)
        # The call belongs to the trace of the activity that makes it. That
        # context was read on the activity's thread; this coroutine runs on
        # the service's loop, which does not see it.
        with tracer.start_as_current_span(
            f"workflow.stage.{operation.name}",
            context=propagate.extract(trace_context),
        ):
            # The span is the caller of the stage; without telemetry the
            # activity's own context is passed on as it was.
            headers = {**trace_context, **trace_headers()}
            try:
                # AD-6: one deadline for the whole call, longer than the
                # stage's own (200 s against 180 s), so the stage's answer is
                # heard. httpx's timeout applies to each phase separately.
                async with asyncio.timeout(self._stage_timeout):
                    response = await self._http.request(
                        operation.method.value,
                        invoke_path(
                            operation.owner, operation.path.format(case_id=case_id)
                        ),
                        json=command.model_dump(mode="json"),
                        headers=headers,
                        timeout=self._stage_timeout,
                    )
            except (httpx.HTTPError, TimeoutError) as error:
                # security rule 31: the error's type; its message can hold an address.
                logger.error(
                    "stage call failed: service=%s operation=%s case_id=%s type=%s",
                    operation.owner.value,
                    operation.name,
                    case_id,
                    type(error).__qualname__,
                )
                raise _unavailable() from error
        if not response.is_success:
            raise self._refusal(response, operation.owner, operation.name, case_id)
        try:
            result = RedactionResult.model_validate_json(response.content)
        except ValidationError:
            logger.error(
                "stage call failed: service=%s operation=%s case_id=%s status=%d "
                "code=invalid_body",
                operation.owner.value,
                operation.name,
                case_id,
                response.status_code,
            )
            raise _unavailable() from None
        if result.case_id != case_id:
            # Recording it would change another case.
            raise DomainError(ErrorCode.VALIDATION_FAILED, WRONG_CASE_MESSAGE)
        return result

    @staticmethod
    def _refusal(
        response: httpx.Response, owner: Service, name: str, case_id: str
    ) -> DomainError:
        try:
            detail = ErrorBody.model_validate_json(response.content).error
        except ValidationError:
            # Not our error shape: the sidecar's own answer.
            detail = None
        code = detail.code.value if detail is not None else "none"
        passed_on = (
            detail is not None
            and detail.code in _STAGE_ANSWERS
            and response.status_code == HTTP_STATUS[detail.code]
        )
        log = logger.info if passed_on else logger.error
        log(
            "stage call refused: service=%s operation=%s case_id=%s status=%d code=%s",
            owner.value,
            name,
            case_id,
            response.status_code,
            code,
        )
        if detail is not None and passed_on:
            return DomainError(detail.code, detail.message)
        return _unavailable()
