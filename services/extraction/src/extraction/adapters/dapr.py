"""The one module that calls other services: HTTP through the Dapr sidecar (spine AD-3).

`extraction` reads a page from `intake`, one page at a time: its number and
the text stored for it, which is the redacted reading and the only text a
quote is checked against (AD-14). `intake` is addressed by its Dapr app id; this service holds no
other service's hostname. No Dapr SDK is used. Nothing read here is logged.
"""

import logging
from collections.abc import Mapping

import httpx
from opentelemetry import propagate, trace
from pydantic import ValidationError

from contracts.enums import Service
from contracts.errors import DomainError, ErrorCode
from contracts.models.intake import PageList, PageText
from contracts.operations import Operation, get_operation
from extraction.adapters.telemetry import adapter_span
from extraction.domain.entities import PageReading
from extraction.settings import APP_ID, Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

UPSTREAM_UNAVAILABLE_MESSAGE = "The page could not be read right now."


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
        timeout=settings.intake_timeout_seconds,
        transport=transport,
        # The sidecar is on this machine: no proxy setting applies to it.
        trust_env=False,
    )


def trace_headers() -> dict[str, str]:
    """The W3C trace context of the active span, to pass on with a call (Conventions)."""
    headers: dict[str, str] = {}
    propagate.inject(headers)
    return headers


def _unavailable() -> DomainError:
    return DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, UPSTREAM_UNAVAILABLE_MESSAGE)


class IntakeClient:
    """The reads of `intake` that `extraction` is allowed (spine, call diagram)."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def aclose(self) -> None:
        await self._http.aclose()

    async def page_ids_of_case(
        self, case_id: str, trace_context: Mapping[str, str]
    ) -> list[str] | None:
        """`GET /cases/{case_id}/pages` on `intake`; None for a case it does not hold."""
        operation = get_operation("list_pages")
        response = await self._get(
            operation, operation.path.format(case_id=case_id), case_id, trace_context
        )
        if response is None:
            return None
        try:
            pages = PageList.model_validate_json(response.content)
        except ValidationError:
            raise self._invalid_body(operation, case_id, response) from None
        if pages.case_id != case_id:
            raise self._invalid_body(operation, case_id, response)
        return [page.page_id for page in pages.pages]

    async def read_page(
        self, page_id: str, trace_context: Mapping[str, str]
    ) -> PageReading | None:
        """The page's number and stored text; None for a page `intake` does not hold."""
        operation = get_operation("read_page_text")
        response = await self._get(
            operation, operation.path.format(page_id=page_id), page_id, trace_context
        )
        if response is None:
            return None
        try:
            text = PageText.model_validate_json(response.content)
        except ValidationError:
            raise self._invalid_body(operation, page_id, response) from None
        if text.page_id != page_id:
            # Another page's text must never be read as this page's: its
            # quotes would be checked against the wrong page.
            raise self._invalid_body(operation, page_id, response)
        return PageReading(page_number=text.page_number, text=text.text)

    async def _get(
        self,
        operation: Operation,
        path: str,
        subject_id: str,
        trace_context: Mapping[str, str],
    ) -> httpx.Response | None:
        """One read; None when `intake` answers that it does not hold the record."""
        with adapter_span(tracer, f"extraction.intake.{operation.name}"):
            # The span is the caller of `intake`; without telemetry the
            # request's own context is passed on as it was.
            headers = {**trace_context, **trace_headers()}
            try:
                response = await self._http.request(
                    operation.method.value,
                    invoke_path(operation.owner, path),
                    headers=headers,
                )
            except httpx.HTTPError as error:
                # security rule 31: the error's type; its message can hold an address.
                logger.error(
                    "intake read failed: operation=%s id=%s type=%s",
                    operation.name,
                    subject_id,
                    type(error).__qualname__,
                )
                raise _unavailable() from error
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if not response.is_success:
            logger.error(
                "intake read refused: operation=%s id=%s status=%d",
                operation.name,
                subject_id,
                response.status_code,
            )
            raise _unavailable()
        return response

    @staticmethod
    def _invalid_body(
        operation: Operation, subject_id: str, response: httpx.Response
    ) -> DomainError:
        logger.error(
            "intake read failed: operation=%s id=%s status=%d code=invalid_body",
            operation.name,
            subject_id,
            response.status_code,
        )
        return _unavailable()
