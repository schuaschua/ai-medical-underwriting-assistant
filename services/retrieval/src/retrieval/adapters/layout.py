"""Document Intelligence adapter: the layout model, over REST (spine AD-12).

The manual's bytes are sent to the service, the analysis is watched until it
has ended, and its result is read into the domain's pages and paragraphs.
REST with the HTTP client the model gateway uses, not the service's SDK: the
call is one submit and a poll, and written out it follows the patterns of
the other adapters (a transport a test can replace, no redirect followed, a
span per call, an Entra token, a retry that can be seen). Nothing the
service answers with is logged except statuses and codes.
"""

import asyncio
import base64
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx2
from opentelemetry import trace

from retrieval.adapters.credential import azure_credential
from retrieval.adapters.db import EntraToken
from retrieval.adapters.telemetry import adapter_span
from retrieval.domain.entities import LayoutPage, LayoutParagraph, ParsedLayout
from retrieval.domain.ports import LayoutFailed
from retrieval.settings import APP_ID, Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

# The scope of an Entra token for Azure AI services.
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
MODELS_PATH = "/documentintelligence/documentModels"
OPERATION_LOCATION = "operation-location"
RETRY_AFTER = "retry-after"
_RESULT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SUCCEEDED = "succeeded"
# Statuses after which an analysis does not change any more.
_ENDED = frozenset({_SUCCEEDED, "failed", "canceled", "cancelled"})
# The longest wait the service may ask for with `Retry-After`: a longer one
# is cut to this.
MAX_RETRY_AFTER_SECONDS = 30.0
# This many looks at an analysis in a row without an answer end the parse.
MAX_FAILED_POLLS = 5
_NOT_IN_A_CODE = re.compile(r"[^A-Za-z0-9_]")
_CODE_MAX_CHARS = 64


def build_layout_http(
    settings: Settings, transport: httpx2.AsyncBaseTransport | None = None
) -> httpx2.AsyncClient:
    """The HTTP client for Document Intelligence. Tests pass a transport that stands in for it."""
    if settings.layout_endpoint is None:
        raise ValueError(
            "Set RETRIEVAL_LAYOUT_ENDPOINT: the Document Intelligence account, or "
            "the local stand-in."
        )
    return httpx2.AsyncClient(
        base_url=settings.layout_endpoint.rstrip("/"),
        timeout=settings.layout_timeout_seconds,
        transport=transport,
        # Never follow a redirect: the token must not leave the endpoint.
        follow_redirects=False,
        trust_env=False,
    )


def layout_token_for(settings: Settings) -> EntraToken | None:
    """The token source for Document Intelligence in Azure; None for the local stand-in."""
    if not settings.layout_entra_auth:
        return None
    return EntraToken(azure_credential(settings), scope=COGNITIVE_SERVICES_SCOPE)


def _try_again(response: httpx2.Response) -> bool:
    """Whether the service could not answer just now: 429 or a 5xx."""
    return response.status_code == 429 or response.status_code >= 500


def _retry_after_seconds(response: httpx2.Response) -> float | None:
    """What `Retry-After` asks for, when it is a finite number of seconds above none."""
    try:
        seconds = float(response.headers.get(RETRY_AFTER, ""))
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _error_code(operation: dict[str, Any]) -> str:
    """The service's own code for a failed analysis, as an identifier; empty if it names none.

    Only letters, digits and underscores are kept, and not many of them:
    the code goes into a log line, and the service's message never does.
    """
    error = operation.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(code, str):
        return ""
    return _NOT_IN_A_CODE.sub("", code)[:_CODE_MAX_CHARS]


class DocumentLayout:
    """Has the layout model parse one PDF."""

    def __init__(
        self,
        http: httpx2.AsyncClient,
        *,
        api_version: str,
        model: str,
        poll_seconds: float,
        deadline_seconds: float,
        max_retries: int = 3,
        max_failed_polls: int = MAX_FAILED_POLLS,
        token: EntraToken | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._parameters = {"api-version": api_version}
        self._model = model
        self._poll_seconds = poll_seconds
        self._deadline_seconds = deadline_seconds
        self._max_retries = max_retries
        self._max_failed_polls = max_failed_polls
        self._token = token
        self._sleep = sleep
        self._clock = clock

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _headers(self) -> dict[str, str]:
        if self._token is None:
            return {}
        try:
            # Fetched off the event loop, and kept until it is near its end.
            await self._token.refresh()
            return {"Authorization": f"Bearer {self._token.value()}"}
        except Exception as error:  # noqa: BLE001 - whatever kept the token away, the call was not made
            # security rule 31: the type only; the identity library's
            # message can hold an address or a tenant.
            raise LayoutFailed(f"layout_token_{type(error).__qualname__}") from None

    def _wait_after(self, response: httpx2.Response | None) -> float:
        """How long to wait before the next call: what `Retry-After` asks, within a limit."""
        asked = _retry_after_seconds(response) if response is not None else None
        if asked is None:
            return self._poll_seconds
        return min(asked, MAX_RETRY_AFTER_SECONDS)

    async def parse(self, pdf: bytes) -> ParsedLayout:
        """Submit the PDF, wait for the analysis, and read its result.

        One deadline covers all of it: the submit, its retries and their
        waits, and the wait for the analysis.
        """
        ends_at = self._clock() + self._deadline_seconds
        result_id = await self._submit(pdf, ends_at)
        result = await self._wait(result_id, ends_at)
        layout = layout_of(result)
        logger.info(
            "layout parsed: result_id=%s pages=%d paragraphs=%d",
            result_id,
            len(layout.pages),
            len(layout.paragraphs),
        )
        return layout

    async def _submit(self, pdf: bytes, ends_at: float) -> str:
        # The bytes, not the blob's address: the job has read the manual
        # with its own identity, and the service then needs no way into the
        # storage account.
        body = {"base64Source": base64.b64encode(pdf).decode("ascii")}
        with adapter_span(tracer, "retrieval.layout.submit") as span:
            # The analysis changes nothing, so a submit that got no answer
            # is safe to send again.
            for attempt in range(1, self._max_retries + 2):
                span.set_attribute("retrieval.layout.attempts", attempt)
                answered: httpx2.Response | None = None
                try:
                    response = await self._http.post(
                        f"{MODELS_PATH}/{self._model}:analyze",
                        params=self._parameters,
                        json=body,
                        headers=await self._headers(),
                    )
                except httpx2.HTTPError as error:
                    code = f"layout_submit_{type(error).__qualname__}"
                else:
                    if not _try_again(response):
                        break
                    answered = response
                    code = f"layout_submit_status_{response.status_code}"
                logger.warning(
                    "layout submit not answered: attempt=%d code=%s", attempt, code
                )
                if attempt > self._max_retries:
                    raise LayoutFailed(code)
                wait_seconds = self._wait_after(answered)
                if self._clock() + wait_seconds > ends_at:
                    # The next try would start after the parse must have ended.
                    raise LayoutFailed("layout_timeout")
                await self._sleep(wait_seconds)
            span.set_attribute("http.response.status_code", response.status_code)
        if response.status_code != 202:
            raise LayoutFailed(f"layout_submit_status_{response.status_code}")
        # Only the id is taken from the address the service names; every
        # later call goes to the configured endpoint, never to that address.
        location = str(response.headers.get(OPERATION_LOCATION, ""))
        result_id = urlsplit(location).path.rstrip("/").rpartition("/")[2]
        if _RESULT_ID.fullmatch(result_id) is None:
            raise LayoutFailed("layout_submit_no_result_id")
        logger.info("layout analysis submitted: result_id=%s", result_id)
        return result_id

    async def _wait(self, result_id: str, ends_at: float) -> dict[str, Any]:
        """Look at the analysis until it has ended, or the deadline has passed.

        A look that gets no answer is logged and tried again, but not for
        ever: after a few in a row the parse fails with the last one's code,
        well before the deadline would have said only "too long".
        """
        attempt, unanswered = 0, 0
        with adapter_span(tracer, "retrieval.layout.wait") as span:
            while True:
                attempt += 1
                operation, code, wait_seconds = await self._look(result_id)
                status = ""
                if operation is None:
                    unanswered += 1
                    logger.warning(
                        "layout poll not answered: result_id=%s attempt=%d code=%s "
                        "in_a_row=%d",
                        result_id,
                        attempt,
                        code,
                        unanswered,
                    )
                    if unanswered >= self._max_failed_polls:
                        raise LayoutFailed(f"layout_poll_{code}")
                else:
                    unanswered = 0
                    status = str(operation.get("status", "")).lower()
                    if status in _ENDED:
                        break
                if self._clock() >= ends_at:
                    raise LayoutFailed("layout_timeout")
                await self._sleep(wait_seconds)
            span.set_attribute("retrieval.layout.polls", attempt)
        if status != _SUCCEEDED:
            # The service's own code says why, where it gives one.
            service_code = _error_code(operation)
            raise LayoutFailed(
                f"layout_{status}_{service_code}"
                if service_code
                else f"layout_{status}"
            )
        result = operation.get("analyzeResult")
        if not isinstance(result, dict):
            raise LayoutFailed("layout_result_missing")
        return result

    async def _look(self, result_id: str) -> tuple[dict[str, Any] | None, str, float]:
        """The analysis's state, or None and why the service could not say just now.

        The last value is the wait before the next look: the service's
        `Retry-After` when it sends one, also with a running analysis.
        """
        try:
            response = await self._http.get(
                f"{MODELS_PATH}/{self._model}/analyzeResults/{result_id}",
                params=self._parameters,
                headers=await self._headers(),
            )
        except httpx2.HTTPError as error:
            # security rule 31: the type only.
            return None, type(error).__qualname__, self._poll_seconds
        wait_seconds = self._wait_after(response)
        if _try_again(response):
            return None, f"status_{response.status_code}", wait_seconds
        if response.status_code != 200:
            raise LayoutFailed(f"layout_result_status_{response.status_code}")
        try:
            operation = response.json()
        except ValueError:
            raise LayoutFailed("layout_result_not_json") from None
        if not isinstance(operation, dict):
            raise LayoutFailed("layout_result_not_an_object")
        return operation, "", wait_seconds


def layout_of(result: dict[str, Any]) -> ParsedLayout:
    """The pages and paragraphs of an `analyzeResult`, in the order the service gives them.

    Only what the chunker uses is read: each page's number and whether it
    holds any text, and each paragraph's text, role and first page.
    """
    try:
        pages = tuple(
            LayoutPage(
                page_number=int(page["pageNumber"]),
                has_text=bool(page.get("words")) or bool(page.get("lines")),
            )
            for page in result["pages"]
        )
        paragraphs = tuple(
            LayoutParagraph(
                # A paragraph that runs over a page break is where it starts.
                page_number=int(paragraph["boundingRegions"][0]["pageNumber"]),
                text=_text(paragraph["content"]),
                role=_role(paragraph.get("role")),
            )
            for paragraph in result.get("paragraphs", [])
        )
    except (KeyError, IndexError, TypeError, ValueError):
        raise LayoutFailed("layout_result_malformed") from None
    return ParsedLayout(pages=pages, paragraphs=paragraphs)


def _text(content: object) -> str:
    if not isinstance(content, str):
        raise TypeError("a paragraph's content is not text")
    return content


def _role(role: object) -> str | None:
    return role if isinstance(role, str) and role else None
