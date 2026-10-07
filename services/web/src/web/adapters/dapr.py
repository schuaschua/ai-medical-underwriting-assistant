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
from contracts.enums import PageStatus, Service
from contracts.errors import HTTP_STATUS, DomainError, ErrorBody, ErrorCode
from contracts.models.classification import ClassificationList
from contracts.models.extraction import FactList
from contracts.models.intake import (
    CaseCreated,
    PageBoxes,
    PageBoxesQuery,
    PageList,
    PageText,
)
from contracts.models.retrieval import RuleText, SearchRequest, SearchResponse
from contracts.models.verdict import (
    AgentStepList,
    AgentStepQuery,
    RunStepQuery,
    VerdictRunList,
)
from contracts.models.workflow import (
    AuditTrail,
    CaseList,
    CaseProgress,
    CaseStarted,
    DecisionRecorded,
    DecisionRequest,
    PageQueue,
    PageQueueQuery,
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
# The queue is asked for with a status `web` itself names, so no refusal of
# it is the user's: whatever `workflow` refuses is this service's fault.
_NO_REFUSAL: frozenset[ErrorCode] = frozenset()
# What an owner may say about a read of one of its resources (a page's
# thumbnail, the reads of the result view of story 2.7 and of the agent's log
# of story 2.8): the case, rule, page, document or run is unknown to it, or
# the request is not valid. For the
# document also that there is no redacted file yet (AD-21).
_UNKNOWN_RESOURCE = frozenset({ErrorCode.NOT_FOUND, ErrorCode.VALIDATION_FAILED})
_NO_FILE = _UNKNOWN_RESOURCE | {ErrorCode.NOT_REDACTED}
# AD-17: what `retrieval` may say about an eval search that is the caller's
# to know: the request is not valid, or the ladder row cannot be searched
# with here. The bake-off runner records such a row as not measured.
_SEARCH_REFUSALS = frozenset(
    {ErrorCode.VALIDATION_FAILED, ErrorCode.RETRIEVER_NOT_AVAILABLE}
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


def _asked(query: ContractModel) -> dict[str, str]:
    """A checked query as it is sent on: only the fields that were asked for."""
    return {
        name: str(value)
        for name, value in query.model_dump(mode="json", exclude_none=True).items()
    }


def _unavailable() -> DomainError:
    return DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, UPSTREAM_UNAVAILABLE_MESSAGE)


class ServiceClient:
    """Calls the operations of other services that `web` is allowed to call."""

    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._upload_timeout = settings.upload_timeout_seconds
        self._lifecycle_timeout = settings.lifecycle_timeout_seconds
        self._document_timeout = settings.document_timeout_seconds

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
        options: StartCaseRequest,
        *,
        traceparent: str | None,
    ) -> CaseStarted:
        """`POST /cases/{case_id}/start` on `workflow` (AD-2): idempotent on the case id.

        The request names the demo role that asks, as its actor (AD-9).
        """
        return await self._call(
            get_operation("start_case"),
            {"case_id": case_id},
            CaseStarted,
            passed_on=_INVALID_START,
            traceparent=traceparent,
            # Only what the caller set is sent; `workflow` fills in the rest.
            body=options.model_dump(mode="json", exclude_none=True),
        )

    async def list_cases(self, *, traceparent: str | None) -> CaseList:
        """`GET /cases` on `workflow`: the cases, newest started first."""
        return await self._call(
            get_operation("list_cases"),
            {},
            CaseList,
            passed_on=_NO_REFUSAL,
            traceparent=traceparent,
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

    async def list_pages_by_status(
        self, status: PageStatus, *, traceparent: str | None
    ) -> PageQueue:
        """`GET /pages?status=` on `workflow`: the pages across cases that wait in that status."""
        return await self._call(
            get_operation("list_pages_by_status"),
            {},
            PageQueue,
            passed_on=_NO_REFUSAL,
            traceparent=traceparent,
            query=PageQueueQuery(status=status).model_dump(mode="json"),
        )

    async def read_page_thumbnail(
        self, page_id: str, *, traceparent: str | None
    ) -> bytes:
        """`GET /pages/{page_id}/thumbnail` on `intake`: the redacted page as a PNG."""
        return await self._read_file(
            get_operation("read_page_thumbnail"),
            {"page_id": page_id},
            passed_on=_UNKNOWN_RESOURCE,
            traceparent=traceparent,
        )

    async def list_facts(self, case_id: str, *, traceparent: str | None) -> FactList:
        """`GET /cases/{case_id}/facts` on `extraction`: the case's facts, each with its quote."""
        return await self._call(
            get_operation("list_facts"),
            {"case_id": case_id},
            FactList,
            passed_on=_UNKNOWN_RESOURCE,
            traceparent=traceparent,
        )

    async def list_verdict_runs(
        self, case_id: str, *, traceparent: str | None
    ) -> VerdictRunList:
        """`GET /cases/{case_id}/verdict-runs` on `verdict`: the case's suggested verdicts."""
        return await self._call(
            get_operation("list_verdict_runs"),
            {"case_id": case_id},
            VerdictRunList,
            passed_on=_UNKNOWN_RESOURCE,
            traceparent=traceparent,
        )

    async def list_run_steps(
        self, verdict_run_id: str, query: RunStepQuery, *, traceparent: str | None
    ) -> AgentStepList:
        """`GET /verdict-runs/{verdict_run_id}/steps` on `verdict`: one run's tool calls in order (AD-15)."""
        return await self._call(
            get_operation("list_run_steps"),
            {"verdict_run_id": verdict_run_id},
            AgentStepList,
            passed_on=_UNKNOWN_RESOURCE,
            traceparent=traceparent,
            query=_asked(query),
        )

    async def list_case_agent_steps(
        self, case_id: str, query: AgentStepQuery, *, traceparent: str | None
    ) -> AgentStepList:
        """`GET /cases/{case_id}/agent-steps` on `verdict`: the tool calls of every run of a case (AD-15)."""
        return await self._call(
            get_operation("list_case_agent_steps"),
            {"case_id": case_id},
            AgentStepList,
            passed_on=_UNKNOWN_RESOURCE,
            traceparent=traceparent,
            query=_asked(query),
        )

    async def read_rule(self, rule_id: str, *, traceparent: str | None) -> RuleText:
        """`GET /rules/{rule_id}` on `retrieval`: the manual's text of one rule."""
        return await self._call(
            get_operation("read_rule"),
            {"rule_id": rule_id},
            RuleText,
            passed_on=_UNKNOWN_RESOURCE,
            traceparent=traceparent,
        )

    async def search_rules(
        self, search: SearchRequest, *, traceparent: str | None
    ) -> SearchResponse:
        """`POST /searches` on `retrieval`: the eval search of the bake-off runner (AD-17).

        The search as it was asked, with the row it names; nothing is stored.
        """
        return await self._call(
            get_operation("search_rules"),
            {},
            SearchResponse,
            passed_on=_SEARCH_REFUSALS,
            traceparent=traceparent,
            body=search.model_dump(mode="json"),
        )

    async def read_page_text(
        self, page_id: str, *, traceparent: str | None
    ) -> PageText:
        """`GET /pages/{page_id}/text` on `intake`: the stored text of a redacted page."""
        return await self._call(
            get_operation("read_page_text"),
            {"page_id": page_id},
            PageText,
            passed_on=_UNKNOWN_RESOURCE,
            traceparent=traceparent,
        )

    async def list_pages(self, case_id: str, *, traceparent: str | None) -> PageList:
        """`GET /cases/{case_id}/pages` on `intake`: empty until redaction is done."""
        return await self._call(
            get_operation("list_pages"),
            {"case_id": case_id},
            PageList,
            passed_on=_UNKNOWN_RESOURCE,
            traceparent=traceparent,
        )

    async def read_page_boxes(
        self, page_id: str, query: PageBoxesQuery, *, traceparent: str | None
    ) -> PageBoxes:
        """`GET /pages/{page_id}/boxes` on `intake`: the word boxes an offset range touches."""
        return await self._call(
            get_operation("read_page_boxes"),
            {"page_id": page_id},
            PageBoxes,
            passed_on=_UNKNOWN_RESOURCE,
            traceparent=traceparent,
            # Only the range that was asked for is sent on.
            query=_asked(query),
        )

    async def read_document_file(
        self, document_id: str, *, traceparent: str | None
    ) -> bytes:
        """`GET /documents/{document_id}/file` on `intake`: the redacted PDF (AD-21).

        `intake` has no other file to answer with: the original is never served.
        """
        return await self._read_file(
            get_operation("read_document_file"),
            {"document_id": document_id},
            passed_on=_NO_FILE,
            traceparent=traceparent,
            # A PDF of up to 10 MB through two sidecars: its own deadline.
            deadline_seconds=self._document_timeout,
        )

    async def _read_file(
        self,
        operation: Operation,
        path_parameters: dict[str, str],
        *,
        passed_on: frozenset[ErrorCode],
        traceparent: str | None,
        deadline_seconds: float | None = None,
    ) -> bytes:
        """One operation that answers with a file: its bytes, of the media type it names."""
        response = await self._send(
            operation,
            path_parameters,
            passed_on=passed_on,
            traceparent=traceparent,
            deadline_seconds=deadline_seconds,
        )
        media_type = response.headers.get("content-type", "").split(";")[0].strip()
        if media_type != operation.response_media_type or not response.content:
            # Whatever this is, it is not handed to a browser as that file.
            raise self._invalid_body(operation, response)
        return response.content

    async def _call[T: ContractModel](
        self,
        operation: Operation,
        path_parameters: dict[str, str],
        answer: type[T],
        *,
        passed_on: frozenset[ErrorCode],
        traceparent: str | None,
        body: dict[str, object] | None = None,
        query: dict[str, str] | None = None,
    ) -> T:
        """One JSON operation of another service, answered with its contract model."""
        response = await self._send(
            operation,
            path_parameters,
            passed_on=passed_on,
            traceparent=traceparent,
            body=body,
            query=query,
        )
        try:
            return answer.model_validate_json(response.content)
        except ValidationError:
            raise self._invalid_body(operation, response) from None

    async def _send(
        self,
        operation: Operation,
        path_parameters: dict[str, str],
        *,
        passed_on: frozenset[ErrorCode],
        traceparent: str | None,
        body: dict[str, object] | None = None,
        query: dict[str, str] | None = None,
        deadline_seconds: float | None = None,
    ) -> httpx.Response:
        """One operation of another service: its successful answer, or the error to raise."""
        deadline = deadline_seconds or self._lifecycle_timeout
        try:
            # One deadline for the whole call, shorter than the browser's:
            #   workflow's scheduler call 10 s  <  web 20 s
            #   (WEB_LIFECYCLE_TIMEOUT_SECONDS)  <  browser 30 s.
            # httpx's own timeout applies to each phase separately, so it
            # alone would not bound the call.
            # The document file has a longer one of its own (see the settings).
            async with asyncio.timeout(deadline):
                response = await self._http.request(
                    operation.method.value,
                    # The parameters are ids the route has already checked.
                    invoke_path(
                        operation.owner, operation.path.format(**path_parameters)
                    ),
                    json=body,
                    params=query,
                    headers=trace_headers(traceparent),
                    timeout=deadline,
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
            return response
        raise self._refusal(
            response, operation.owner, operation.name, passed_on, "request"
        )

    @staticmethod
    def _invalid_body(operation: Operation, response: httpx.Response) -> DomainError:
        """A success that does not carry what the operation answers with: the service's fault."""
        logger.error(
            "service call failed: service=%s operation=%s status=%d code=invalid_body",
            operation.owner.value,
            operation.name,
            response.status_code,
        )
        return _unavailable()

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
