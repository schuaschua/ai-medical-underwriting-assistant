"""The runner's one way to the system: `web`'s API, as a user would use it (spine AD-17, AD-9).

Every call carries a demo role. The runner uploads and answers for the
customer as the customer, and starts cases, decides triage and reads as the
underwriter. It reads no database, no blob and no other service.
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from bakeoff.settings import Settings
from contracts.base import ContractModel
from contracts.decisions import decision_rule
from contracts.enums import Decision, DemoRole
from contracts.errors import ErrorBody, ErrorCode
from contracts.models.intake import PageList, PageText
from contracts.models.retrieval import SearchRequest, SearchResponse
from contracts.models.verdict import VerdictRunList
from contracts.models.web import PageDecisionRequest, UploadedCase
from contracts.models.workflow import CaseProgress, StartCaseOptions
from contracts.operations import PDF
from contracts.upload import IDEMPOTENCY_KEY_HEADER

logger = logging.getLogger(__name__)

ROLE_HEADER = "X-Demo-Role"
# Answers that say "not now": the call is sent again.
_PASSING_STATUSES = frozenset({429, 502, 503, 504})

Sleep = Callable[[float], Awaitable[None]]


class WebError(Exception):
    """A call to `web` that was not answered with what was asked for."""

    def __init__(self, status: int | None, code: ErrorCode | None) -> None:
        super().__init__(status, code)
        # None when no answer came at all.
        self.status = status
        # None when the answer was not in the error shape.
        self.code = code

    def __str__(self) -> str:
        code = self.code.value if self.code is not None else "none"
        return f"status={self.status} code={code}"


@dataclass(frozen=True, slots=True)
class _Call:
    method: str
    path: str
    role: DemoRole
    json: dict[str, object] | None = None
    content: bytes | None = None
    headers: tuple[tuple[str, str], ...] = ()
    timeout: float | None = None


def build_http_client(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """The HTTP client for `web`. Tests pass a transport that stands in for it."""
    return httpx.AsyncClient(
        base_url=settings.web_address,
        timeout=settings.request_timeout_seconds,
        transport=transport,
    )


def upload_key(eval_run_id: str, case_key: str) -> str:
    """The idempotency key of one case's upload in one bake-off run.

    The same for every attempt, so an upload sent again after a lost answer
    is answered with the case the first one created.
    """
    return hashlib.sha256(f"{eval_run_id}:{case_key}".encode()).hexdigest()


class WebClient:
    """The calls of `web`'s API that the runner makes."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        settings: Settings,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http = http
        self._retries = settings.request_retries
        self._retry_seconds = settings.retry_seconds
        self._upload_timeout = settings.upload_timeout_seconds
        self._sleep = sleep

    async def me(self) -> None:
        """Ask `web` who the runner is: a first call that shows the address answers."""
        await self._send(_Call("GET", "/api/me", DemoRole.UNDERWRITER))

    async def upload(self, pdf: bytes, idempotency_key: str) -> UploadedCase:
        """Upload one case document, as the customer does."""
        return await self._ask(
            _Call(
                "POST",
                "/api/cases",
                DemoRole.CUSTOMER,
                content=pdf,
                headers=(
                    ("Content-Type", PDF),
                    (IDEMPOTENCY_KEY_HEADER, idempotency_key),
                ),
                timeout=self._upload_timeout,
            ),
            UploadedCase,
        )

    async def start(self, case_id: str, options: StartCaseOptions) -> None:
        """Start a case with its rows and its eval run; only the underwriter may say how."""
        await self._send(
            _Call(
                "POST",
                f"/api/cases/{case_id}/start",
                DemoRole.UNDERWRITER,
                json=options.model_dump(mode="json", exclude_none=True),
            )
        )

    async def progress(self, case_id: str) -> CaseProgress | None:
        """The case's progress; None when the case was never started."""
        try:
            return await self._ask(
                _Call("GET", f"/api/cases/{case_id}/progress", DemoRole.UNDERWRITER),
                CaseProgress,
            )
        except WebError as error:
            if error.code is ErrorCode.NOT_FOUND:
                return None
            raise

    async def decide(self, case_id: str, page_id: str, decision: Decision) -> None:
        """Answer a human wait, as the role that owns the decision (AD-10)."""
        await self._send(
            _Call(
                "POST",
                f"/api/cases/{case_id}/pages/{page_id}/decisions",
                decision_rule(decision).role,
                json=PageDecisionRequest(decision=decision).model_dump(mode="json"),
            )
        )

    async def verdict_runs(self, case_id: str) -> VerdictRunList:
        return await self._ask(
            _Call("GET", f"/api/cases/{case_id}/verdict-runs", DemoRole.UNDERWRITER),
            VerdictRunList,
        )

    async def pages(self, case_id: str) -> PageList:
        return await self._ask(
            _Call("GET", f"/api/cases/{case_id}/pages", DemoRole.UNDERWRITER),
            PageList,
        )

    async def page_text(self, page_id: str) -> PageText:
        return await self._ask(
            _Call("GET", f"/api/pages/{page_id}/text", DemoRole.UNDERWRITER), PageText
        )

    async def document_file(self, document_id: str) -> bytes:
        """The redacted PDF of a document, the only file `web` serves (AD-21)."""
        response = await self._send(
            _Call("GET", f"/api/documents/{document_id}/file", DemoRole.UNDERWRITER)
        )
        return response.content

    async def search(self, search: SearchRequest) -> SearchResponse:
        """One eval search: `retrieval`'s search with the row the request names."""
        return await self._ask(
            _Call(
                "POST",
                "/api/searches",
                DemoRole.UNDERWRITER,
                json=search.model_dump(mode="json"),
            ),
            SearchResponse,
        )

    async def _ask[T: ContractModel](self, call: _Call, answer: type[T]) -> T:
        response = await self._send(call)
        try:
            return answer.model_validate_json(response.content)
        except ValidationError:
            logger.error(
                "web answered in another shape: method=%s status=%d",
                call.method,
                response.status_code,
            )
            raise WebError(response.status_code, None) from None

    async def _send(self, call: _Call) -> httpx.Response:
        """One call, sent again while the answer says "not now"; its success, or `WebError`."""
        attempts = self._retries + 1
        failure = WebError(None, None)
        for attempt in range(attempts):
            if attempt:
                await self._sleep(self._retry_seconds)
            try:
                response = await self._http.request(
                    call.method,
                    call.path,
                    json=call.json,
                    content=call.content,
                    headers={ROLE_HEADER: call.role.value, **dict(call.headers)},
                    timeout=call.timeout
                    if call.timeout is not None
                    else httpx.USE_CLIENT_DEFAULT,
                )
            except httpx.HTTPError as error:
                # The error's type only: its message can hold an address.
                logger.warning(
                    "no answer from web: method=%s type=%s",
                    call.method,
                    type(error).__qualname__,
                )
                failure = WebError(None, None)
                continue
            if response.is_success:
                return response
            failure = WebError(response.status_code, _error_code(response))
            if response.status_code not in _PASSING_STATUSES:
                break
        raise failure


def _error_code(response: httpx.Response) -> ErrorCode | None:
    try:
        return ErrorBody.model_validate_json(response.content).error.code
    except ValidationError:
        return None
