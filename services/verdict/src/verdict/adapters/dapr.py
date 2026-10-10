"""The one module that calls other services: HTTP through the Dapr sidecar (spine AD-3).

`verdict` reads the case's facts from `extraction`, and searches the manual
and reads rules at `retrieval`. Both are addressed by their Dapr app ids;
this service holds no other service's hostname. No Dapr SDK is used. These
are the only calls the agent's tools make: the agent has no other way to any
data. Nothing read or sent here is logged.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

import httpx
from opentelemetry import propagate, trace
from pydantic import ValidationError

from contracts.base import ContractModel
from contracts.enums import RetrieverConfig, Service
from contracts.errors import HTTP_STATUS, DomainError, ErrorBody, ErrorCode
from contracts.models.extraction import Fact, FactList
from contracts.models.retrieval import RuleText, SearchRequest, SearchResponse
from contracts.operations import Operation, get_operation
from verdict.adapters.telemetry import adapter_span
from verdict.settings import APP_ID, Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

UPSTREAM_UNAVAILABLE_MESSAGE = "A service the verdict needs could not be reached."
ROW_NOT_AVAILABLE_MESSAGE = "The manual cannot be searched with that retrieval row yet."

# Answers that say "not now": the call is sent again.
_NOT_NOW = frozenset(
    {
        httpx.codes.REQUEST_TIMEOUT,
        httpx.codes.TOO_MANY_REQUESTS,
        httpx.codes.BAD_GATEWAY,
        httpx.codes.SERVICE_UNAVAILABLE,
        httpx.codes.GATEWAY_TIMEOUT,
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
        timeout=settings.upstream_timeout_seconds,
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


class _Sidecar:
    """One call to another service, sent again a few times when it gets no answer."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        max_retries: int = 2,
        retry_seconds: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._max_retries = max_retries
        self._retry_seconds = retry_seconds
        self._sleep = sleep

    async def call(
        self,
        operation: Operation,
        path: str,
        subject_id: str,
        trace_context: Mapping[str, str],
        *,
        body: ContractModel | None = None,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """The answer of one operation; `upstream_unavailable` when none could be had.

        Sent again, up to the set number of times, when there is no answer or
        the answer is 408, 429, 502, 503 or 504. Any other answer is returned as it is:
        the caller reads what it means.
        """
        with adapter_span(
            tracer, f"verdict.{operation.owner.value}.{operation.name}"
        ) as span:
            # The span is the caller of the other service; without telemetry
            # the request's own context is passed on as it was.
            headers = {**trace_context, **trace_headers()}
            for attempt in range(1, self._max_retries + 2):
                span.set_attribute("verdict.upstream.attempts", attempt)
                try:
                    response = await self._http.request(
                        operation.method.value,
                        invoke_path(operation.owner, path),
                        json=body.model_dump(mode="json") if body is not None else None,
                        params=params,
                        headers=headers,
                    )
                except httpx.HTTPError as error:
                    # security rule 31: the error's type; its message can hold an address.
                    code = type(error).__qualname__
                else:
                    if response.status_code not in _NOT_NOW:
                        return response
                    code = f"status_{response.status_code}"
                logger.warning(
                    "upstream call not answered: service=%s operation=%s id=%s "
                    "attempt=%d code=%s",
                    operation.owner.value,
                    operation.name,
                    subject_id,
                    attempt,
                    code,
                )
                if attempt <= self._max_retries:
                    await self._sleep(self._retry_seconds * 2.0 ** (attempt - 1))
            span.set_attribute("error.type", ErrorCode.UPSTREAM_UNAVAILABLE.value)
        logger.error(
            "upstream unavailable: service=%s operation=%s id=%s attempts=%d",
            operation.owner.value,
            operation.name,
            subject_id,
            self._max_retries + 1,
        )
        raise _unavailable()


def _refused(
    operation: Operation, subject_id: str, response: httpx.Response
) -> DomainError:
    """An answer that is neither the result nor "not now": the service refused the call."""
    try:
        code = ErrorBody.model_validate_json(response.content).error.code
    except ValidationError:
        # Not our error shape: the sidecar's own answer.
        code = None
    logger.error(
        "upstream call refused: service=%s operation=%s id=%s status=%d code=%s",
        operation.owner.value,
        operation.name,
        subject_id,
        response.status_code,
        code.value if code is not None else "none",
    )
    if (
        code is ErrorCode.RETRIEVER_NOT_AVAILABLE
        and response.status_code == HTTP_STATUS[code]
    ):
        return DomainError(code, ROW_NOT_AVAILABLE_MESSAGE)
    return _unavailable()


def _says_not_found(response: httpx.Response) -> bool:
    """Whether the answer is the contract's `not_found`: a 404 in our error shape, with that code.

    Any other 404 (the sidecar's own, for an app or a path it cannot reach)
    says nothing about the manual, and is not taken for "not defined".
    """
    if response.status_code != httpx.codes.NOT_FOUND:
        return False
    try:
        code = ErrorBody.model_validate_json(response.content).error.code
    except ValidationError:
        return False
    return code is ErrorCode.NOT_FOUND


def _invalid_body(
    operation: Operation, subject_id: str, response: httpx.Response
) -> DomainError:
    logger.error(
        "upstream call failed: service=%s operation=%s id=%s status=%d code=invalid_body",
        operation.owner.value,
        operation.name,
        subject_id,
        response.status_code,
    )
    return _unavailable()


class ExtractionClient:
    """The one read of `extraction` that `verdict` is allowed (spine, call diagram)."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        max_retries: int = 2,
        retry_seconds: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._sidecar = _Sidecar(
            http, max_retries=max_retries, retry_seconds=retry_seconds, sleep=sleep
        )

    async def facts_of_case(
        self, case_id: str, trace_context: Mapping[str, str]
    ) -> list[Fact]:
        """`GET /cases/{case_id}/facts` on `extraction`: the case's facts, in page order."""
        operation = get_operation("list_facts")
        response = await self._sidecar.call(
            operation, operation.path.format(case_id=case_id), case_id, trace_context
        )
        if not response.is_success:
            raise _refused(operation, case_id, response)
        try:
            listed = FactList.model_validate_json(response.content)
        except ValidationError:
            raise _invalid_body(operation, case_id, response) from None
        if listed.case_id != case_id or any(
            fact.case_id != case_id for fact in listed.facts
        ):
            # Another case's facts must never be reasoned about as this case's.
            raise _invalid_body(operation, case_id, response)
        return list(listed.facts)


class RetrievalClient:
    """The two reads of `retrieval` that `verdict` is allowed (spine, call diagram)."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        max_retries: int = 2,
        retry_seconds: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._sidecar = _Sidecar(
            http, max_retries=max_retries, retry_seconds=retry_seconds, sleep=sleep
        )

    async def search(
        self,
        query: str,
        retriever_config: RetrieverConfig,
        top_k: int,
        trace_context: Mapping[str, str],
    ) -> SearchResponse:
        """`POST /searches` on `retrieval`: the manual searched with one ladder row."""
        operation = get_operation("search_rules")
        subject = retriever_config.value
        response = await self._sidecar.call(
            operation,
            operation.path,
            subject,
            trace_context,
            body=SearchRequest(
                query=query, retriever_config=retriever_config, top_k=top_k
            ),
        )
        if not response.is_success:
            raise _refused(operation, subject, response)
        try:
            found = SearchResponse.model_validate_json(response.content)
        except ValidationError:
            raise _invalid_body(operation, subject, response) from None
        if found.retriever_config is not retriever_config:
            # Another row's answer would be logged, and compared, as this row's.
            raise _invalid_body(operation, subject, response)
        return found

    async def read(
        self,
        rule_id: str,
        retriever_config: RetrieverConfig,
        trace_context: Mapping[str, str],
    ) -> RuleText | None:
        """`GET /rules/{rule_id}` on `retrieval`; None for a rule the manual does not define."""
        operation = get_operation("read_rule")
        response = await self._sidecar.call(
            operation,
            operation.path.format(rule_id=rule_id),
            rule_id,
            trace_context,
            params={"retriever_config": retriever_config.value},
        )
        if _says_not_found(response):
            return None
        if not response.is_success:
            raise _refused(operation, rule_id, response)
        try:
            rule = RuleText.model_validate_json(response.content)
        except ValidationError:
            raise _invalid_body(operation, rule_id, response) from None
        if rule.rule_id != rule_id:
            raise _invalid_body(operation, rule_id, response)
        return rule


class Upstreams:
    """The two clients on one HTTP client, closed together."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        settings: Settings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self.extraction = ExtractionClient(
            http,
            max_retries=settings.upstream_max_retries,
            retry_seconds=settings.upstream_retry_seconds,
            sleep=sleep,
        )
        self.retrieval = RetrievalClient(
            http,
            max_retries=settings.upstream_max_retries,
            retry_seconds=settings.upstream_retry_seconds,
            sleep=sleep,
        )

    async def aclose(self) -> None:
        await self._http.aclose()
