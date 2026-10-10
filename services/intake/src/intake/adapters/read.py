"""Document Intelligence adapter: the read model, over REST (AD-14, AD-21).

The redacted PDF's pages are pictures, so their text is read by OCR: the
PDF's bytes are sent to the read model, the analysis is watched until it has
ended, and its result is read into the domain's pages, lines and words. Only
ever the redacted PDF: this adapter has no way to an original.

REST with the HTTP client the Language adapter uses, in the same patterns (a
transport a test can replace, no redirect followed, a span per call, an Entra
token, a retry that can be seen). Nothing the service answers with is logged
except statuses, codes and counts: its answer is the page text.
"""

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from opentelemetry import trace

from intake.adapters.credential import azure_credential
from intake.adapters.db import EntraToken
from intake.adapters.language import COGNITIVE_SERVICES_SCOPE
from intake.adapters.telemetry import adapter_span
from intake.domain.entities import ReadPage, ReadWord
from intake.domain.ports import RedactionJobError
from intake.settings import APP_ID, Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

MODELS_PATH = "/documentintelligence/documentModels"
OPERATION_LOCATION = "operation-location"
RETRY_AFTER = "retry-after"
PDF_CONTENT = {"Content-Type": "application/pdf"}
_RESULT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SUCCEEDED = "succeeded"
# Statuses after which an analysis does not change any more.
_ENDED = frozenset({_SUCCEEDED, "failed", "canceled", "cancelled"})
# The longest wait the service may ask for with `Retry-After`: a longer one
# is cut to this.
MAX_RETRY_AFTER_SECONDS = 30.0
# This many looks at an analysis in a row without an answer end the reading.
MAX_FAILED_POLLS = 5
_NOT_IN_A_CODE = re.compile(r"[^A-Za-z0-9_]")
_CODE_MAX_CHARS = 64


def build_read_http(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """The HTTP client for Document Intelligence. Tests pass a transport that stands in for it."""
    if settings.read_endpoint is None:
        raise ValueError(
            "Set INTAKE_READ_ENDPOINT: the Document Intelligence account, or the "
            "local stand-in."
        )
    return httpx.AsyncClient(
        base_url=settings.read_endpoint.rstrip("/"),
        timeout=settings.read_timeout_seconds,
        transport=transport,
        # Never follow a redirect: the token must not leave the endpoint.
        follow_redirects=False,
        trust_env=False,
    )


def read_token_for(settings: Settings) -> EntraToken | None:
    """The token source for Document Intelligence in Azure; None for the local stand-in."""
    if not settings.read_entra_auth:
        return None
    return EntraToken(azure_credential(settings), scope=COGNITIVE_SERVICES_SCOPE)


def _try_again(response: httpx.Response) -> bool:
    """Whether the service could not answer just now: 429 or a 5xx."""
    return response.status_code == httpx.codes.TOO_MANY_REQUESTS or (
        response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """What `Retry-After` asks for, when it is a finite number of seconds above none."""
    try:
        seconds = float(response.headers.get(RETRY_AFTER, ""))
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _error_code(operation: dict[str, Any]) -> str:
    """The service's own code for a failed analysis, as an identifier; empty if it names none.

    Only letters, digits and underscores are kept, and not many of them: the
    code goes into a log line, and the service's message never does.
    """
    error = operation.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(code, str):
        return ""
    return _NOT_IN_A_CODE.sub("", code)[:_CODE_MAX_CHARS]


class DocumentRead:
    """Has the read model read one redacted PDF."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        api_version: str,
        model: str,
        poll_seconds: float,
        max_retries: int = 3,
        max_failed_polls: int = MAX_FAILED_POLLS,
        token: EntraToken | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._parameters = {"api-version": api_version}
        self._model = model
        self._poll_seconds = poll_seconds
        self._max_retries = max_retries
        self._max_failed_polls = max_failed_polls
        self._token = token
        self._sleep = sleep

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
            raise RedactionJobError(f"read_token_{type(error).__qualname__}") from None

    def _wait_after(self, response: httpx.Response | None) -> float:
        """How long to wait before the next call: what `Retry-After` asks, within a limit."""
        asked = _retry_after_seconds(response) if response is not None else None
        if asked is None:
            return self._poll_seconds
        return min(asked, MAX_RETRY_AFTER_SECONDS)

    async def read(self, pdf: bytes) -> list[ReadPage]:
        """Submit the PDF, wait for the analysis, and read its result.

        The caller's deadline bounds all of it: the redaction's one deadline
        covers the job, this reading and the storing of the pages.
        """
        result_id = await self._submit(pdf)
        pages = pages_of(await self._wait(result_id))
        logger.info(
            "redacted document read: result_id=%s pages=%d lines=%d words=%d",
            result_id,
            len(pages),
            sum(len(page.lines) for page in pages),
            sum(len(line) for page in pages for line in page.lines),
        )
        return pages

    async def _submit(self, pdf: bytes) -> str:
        with adapter_span(tracer, "intake.read.submit") as span:
            # The analysis changes nothing, so a submit that got no answer
            # is safe to send again.
            for attempt in range(1, self._max_retries + 2):
                span.set_attribute("intake.read.attempts", attempt)
                answered: httpx.Response | None = None
                try:
                    response = await self._http.post(
                        f"{MODELS_PATH}/{self._model}:analyze",
                        params=self._parameters,
                        # The bytes, not the blob's address: the service
                        # then needs no way into the storage account.
                        content=pdf,
                        headers={**PDF_CONTENT, **await self._headers()},
                    )
                except httpx.HTTPError as error:
                    code = f"read_submit_{type(error).__qualname__}"
                else:
                    if not _try_again(response):
                        break
                    answered = response
                    code = f"read_submit_status_{response.status_code}"
                logger.warning(
                    "read submit not answered: attempt=%d code=%s", attempt, code
                )
                if attempt > self._max_retries:
                    raise RedactionJobError(code)
                await self._sleep(self._wait_after(answered))
            span.set_attribute("http.response.status_code", response.status_code)
        if response.status_code != httpx.codes.ACCEPTED:
            raise RedactionJobError(f"read_submit_status_{response.status_code}")
        # Only the id is taken from the address the service names; every
        # later call goes to the configured endpoint, never to that address.
        location = str(response.headers.get(OPERATION_LOCATION, ""))
        result_id = urlsplit(location).path.rstrip("/").rpartition("/")[2]
        if _RESULT_ID.fullmatch(result_id) is None:
            raise RedactionJobError("read_submit_no_result_id")
        logger.info("read analysis submitted: result_id=%s", result_id)
        return result_id

    async def _wait(self, result_id: str) -> dict[str, Any]:
        """Look at the analysis until it has ended.

        A look that gets no answer is logged and tried again, but not for
        ever: after a few in a row the reading fails with the last one's code.
        """
        attempt, unanswered = 0, 0
        with adapter_span(tracer, "intake.read.wait") as span:
            while True:
                attempt += 1
                operation, code, wait_seconds = await self._look(result_id)
                if operation is None:
                    unanswered += 1
                    logger.warning(
                        "read poll not answered: result_id=%s attempt=%d code=%s "
                        "in_a_row=%d",
                        result_id,
                        attempt,
                        code,
                        unanswered,
                    )
                    if unanswered >= self._max_failed_polls:
                        raise RedactionJobError(f"read_poll_{code}")
                else:
                    unanswered = 0
                    status = str(operation.get("status", "")).lower()
                    if status in _ENDED:
                        break
                await self._sleep(wait_seconds)
            span.set_attribute("intake.read.polls", attempt)
        if status != _SUCCEEDED:
            # The service's own code says why, where it gives one.
            service_code = _error_code(operation)
            raise RedactionJobError(
                f"read_{status}_{service_code}" if service_code else f"read_{status}"
            )
        result = operation.get("analyzeResult")
        if not isinstance(result, dict):
            raise RedactionJobError("read_result_missing")
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
        except httpx.HTTPError as error:
            # security rule 31: the type only.
            return None, type(error).__qualname__, self._poll_seconds
        wait_seconds = self._wait_after(response)
        if _try_again(response):
            return None, f"status_{response.status_code}", wait_seconds
        if response.status_code != httpx.codes.OK:
            raise RedactionJobError(f"read_result_status_{response.status_code}")
        try:
            operation = response.json()
        except ValueError:
            raise RedactionJobError("read_result_not_json") from None
        if not isinstance(operation, dict):
            raise RedactionJobError("read_result_not_an_object")
        return operation, "", wait_seconds


def pages_of(result: dict[str, Any]) -> list[ReadPage]:
    """The pages of an `analyzeResult`, each with its lines and their words.

    The service lists a page's lines in reading order and its words apart
    from them; a word belongs to the line whose span of the document's
    content holds the word's own. Each word's place is the bounds of its
    polygon, in the page's own unit: the domain turns that into PDF points.
    """
    try:
        return [_page(page) for page in result["pages"]]
    except (KeyError, IndexError, TypeError, ValueError):
        raise RedactionJobError("read_result_malformed") from None


def _page(page: dict[str, Any]) -> ReadPage:
    spans = [
        (int(span["offset"]), int(span["offset"]) + int(span["length"]))
        for line in page.get("lines", [])
        for span in line["spans"][:1]
    ]
    lines: list[list[ReadWord]] = [[] for _ in spans]
    # Words no line holds, in the order given: kept, as one line at the end.
    loose: list[ReadWord] = []
    for word in page.get("words", []):
        at = int(word["span"]["offset"])
        polygon = [float(value) for value in word["polygon"]]
        if len(polygon) < 8 or not all(math.isfinite(value) for value in polygon):
            raise ValueError("a word's polygon is not four corners")
        read = ReadWord(
            content=_text(word["content"]),
            x0=min(polygon[0::2]),
            y0=min(polygon[1::2]),
            x1=max(polygon[0::2]),
            y1=max(polygon[1::2]),
        )
        holder = next(
            (number for number, (start, end) in enumerate(spans) if start <= at < end),
            None,
        )
        (lines[holder] if holder is not None else loose).append(read)
    angle = float(page.get("angle") or 0.0)
    return ReadPage(
        page_number=int(page["pageNumber"]),
        angle=angle if math.isfinite(angle) else 0.0,
        width=float(page["width"]),
        height=float(page["height"]),
        lines=tuple(tuple(line) for line in (*lines, loose) if line),
    )


def _text(content: object) -> str:
    if not isinstance(content, str):
        raise TypeError("a word's content is not text")
    return content
