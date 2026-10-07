"""Document Intelligence adapter: the custom classification model, over REST (spine AD-13, story 4.2).

The one module that calls Document Intelligence. It serves both entry points
of the image:

- the service asks the classifier of the configured id what one page is: the
  page goes up as a one-page PDF, the analysis is watched until it has ended,
  and the document type and confidence it gives come back as they are;
- the training job asks whether that classifier exists, and has it built from
  the `classifier-training` container, which the service reads with its own
  identity.

REST with the HTTP client the model gateway uses, not the service's SDK: each
call is one request and a poll, and written out it follows the patterns of the
other adapters (a transport a test can replace, no redirect followed, a span
per call, an Entra token, a retry that can be seen). Nothing that is sent or
answered is logged: only ids, statuses, codes and counts.
"""

import asyncio
import base64
import logging
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx2
from opentelemetry import trace

from classification.adapters.credential import azure_credential
from classification.adapters.db import EntraToken
from classification.adapters.telemetry import adapter_span
from classification.domain.entities import ClassifierAnswer
from classification.domain.ports import (
    ClassifierNotReady,
    ModelCallFailed,
    ModelUnavailable,
    TrainingFailed,
)
from classification.settings import APP_ID, Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

# The scope of an Entra token for Azure AI services.
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
CLASSIFIERS_PATH = "/documentintelligence/documentClassifiers"
OPERATIONS_PATH = "/documentintelligence/operations"
OPERATION_LOCATION = "operation-location"
RETRY_AFTER = "retry-after"
# The whole file is one document: the page is not split or grouped.
SPLIT_MODE = "none"
_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SUCCEEDED = "succeeded"
# Statuses after which an analysis or a build does not change any more.
_ENDED = frozenset({_SUCCEEDED, "failed", "canceled", "cancelled"})
# The longest wait the service may ask for with `Retry-After`: a longer one
# is cut to this.
MAX_RETRY_AFTER_SECONDS = 30.0
# This many looks in a row without an answer end the wait.
MAX_FAILED_POLLS = 5
# Answers to a classify call that pass: the classifier is not trained yet
# (or not again, after the local stand-in was started anew), or the service
# identity's role is not honoured yet.
_NOT_READY = frozenset({401, 403, 404})
# The answer to a build of a classifier that exists or is being built.
_CONFLICT = 409
_NOT_IN_A_CODE = re.compile(r"[^A-Za-z0-9_]")
_CODE_MAX_CHARS = 64


def classifier_id(settings: Settings) -> str:
    """The id of the classifier; it reaches code only as a setting."""
    if settings.doc_intelligence_classifier_id is None:
        raise ValueError(
            "Set CLASSIFICATION_DOC_INTELLIGENCE_CLASSIFIER_ID: the id of the "
            "classifier."
        )
    return settings.doc_intelligence_classifier_id


def classifier_configured(settings: Settings) -> bool:
    """Whether the service was given what the `doc-intelligence` contender needs."""
    return (
        settings.doc_intelligence_endpoint is not None
        and settings.doc_intelligence_classifier_id is not None
    )


def build_classifier_http(
    settings: Settings, transport: httpx2.AsyncBaseTransport | None = None
) -> httpx2.AsyncClient:
    """The HTTP client for Document Intelligence. Tests pass a transport that stands in for it."""
    if settings.doc_intelligence_endpoint is None:
        raise ValueError(
            "Set CLASSIFICATION_DOC_INTELLIGENCE_ENDPOINT: the Document "
            "Intelligence account, or the local stand-in."
        )
    return httpx2.AsyncClient(
        base_url=settings.doc_intelligence_endpoint.rstrip("/"),
        timeout=settings.doc_intelligence_timeout_seconds,
        transport=transport,
        # Never follow a redirect: the token must not leave the endpoint.
        follow_redirects=False,
        trust_env=False,
    )


def classifier_token_for(settings: Settings) -> EntraToken | None:
    """The token source for Document Intelligence in Azure; None for the local stand-in."""
    if not settings.doc_intelligence_entra_auth:
        return None
    return EntraToken(azure_credential(settings), scope=COGNITIVE_SERVICES_SCOPE)


def build_classifier(
    settings: Settings,
    transport: httpx2.AsyncBaseTransport | None = None,
    *,
    poll_seconds: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> "DocumentClassifier":
    """The classifier client the settings describe. Building it makes no network call.

    The training job waits longer between two looks at a build than the
    service does between two looks at an analysis: it passes its own wait.
    """
    return DocumentClassifier(
        build_classifier_http(settings, transport),
        api_version=settings.doc_intelligence_api_version,
        classifier_id=classifier_id(settings),
        poll_seconds=poll_seconds or settings.doc_intelligence_poll_seconds,
        max_retries=settings.doc_intelligence_max_retries,
        token=classifier_token_for(settings),
        sleep=sleep,
    )


class _NotAnswered(Exception):
    """The service could not be had: no answer, 429 or a 5xx, after the retries."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _NotReady(Exception):
    """The service holds no such classifier, or does not let the caller in."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Refused(Exception):
    """The service refused the call, or what it started did not end well."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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
    """The service's own code for a failure, as an identifier; empty if it names none.

    Only letters, digits and underscores are kept, and not many of them:
    the code goes into a log line, and the service's message never does.
    """
    error = operation.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(code, str):
        return ""
    return _NOT_IN_A_CODE.sub("", code)[:_CODE_MAX_CHARS]


def _operation_id(response: httpx2.Response) -> str | None:
    """The id at the end of the address the service names; never the address itself.

    Every later call goes to the configured endpoint, never to that address.
    """
    location = str(response.headers.get(OPERATION_LOCATION, ""))
    found = urlsplit(location).path.rstrip("/").rpartition("/")[2]
    return found if _OPERATION_ID.fullmatch(found) else None


def answer_of(result: object) -> ClassifierAnswer:
    """What an `analyzeResult` says of the document: its first document's type and confidence.

    The page was sent as one document and is read as one. A result that
    names no document gives an answer without a type, which the domain refuses.
    """
    documents = result.get("documents") if isinstance(result, dict) else None
    first = documents[0] if isinstance(documents, list) and documents else None
    if not isinstance(first, dict):
        return ClassifierAnswer(doc_type=None, confidence=None)
    doc_type, confidence = first.get("docType"), first.get("confidence")
    return ClassifierAnswer(
        doc_type=doc_type if isinstance(doc_type, str) else None,
        confidence=confidence
        if isinstance(confidence, int | float) and not isinstance(confidence, bool)
        else None,
    )


class DocumentClassifier:
    """The classifier of one id: asked about a page, looked for, and built."""

    def __init__(
        self,
        http: httpx2.AsyncClient,
        *,
        api_version: str,
        classifier_id: str,
        poll_seconds: float = 1.0,
        max_retries: int = 3,
        max_failed_polls: int = MAX_FAILED_POLLS,
        token: EntraToken | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._parameters = {"api-version": api_version}
        self._classifier_id = classifier_id
        self._poll_seconds = poll_seconds
        self._max_retries = max_retries
        self._max_failed_polls = max_failed_polls
        self._token = token
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- The service: one page ----------------------------------------------------

    async def classify(self, pdf: bytes) -> ClassifierAnswer:
        """Send the one-page PDF, wait for the analysis, and read its answer.

        The caller sets the deadline: the stage's one deadline covers the
        submit, its retries and the wait.
        """
        path = f"{CLASSIFIERS_PATH}/{self._classifier_id}"
        try:
            with adapter_span(tracer, "classification.classifier.classify_page"):
                # The bytes, not a blob's address: `classification` reads no
                # blob of a case, and the service needs no way into storage.
                submitted = await self._send(
                    "POST",
                    f"{path}:analyze",
                    what="classifier_submit",
                    params={"split": SPLIT_MODE},
                    json={"base64Source": base64.b64encode(pdf).decode("ascii")},
                )
                if submitted.status_code in _NOT_READY:
                    raise _NotReady(f"classifier_submit_status_{submitted.status_code}")
                if submitted.status_code != 202:
                    raise _Refused(f"classifier_submit_status_{submitted.status_code}")
                result_id = _operation_id(submitted)
                if result_id is None:
                    raise _Refused("classifier_submit_no_result_id")
                operation, polls = await self._wait(
                    f"{path}/analyzeResults/{result_id}", "classifier"
                )
        except _NotAnswered as error:
            logger.error(
                "classifier unavailable: classifier_id=%s code=%s",
                self._classifier_id,
                error.code,
            )
            raise ModelUnavailable from None
        except _NotReady as error:
            logger.warning(
                "classifier not ready: classifier_id=%s code=%s",
                self._classifier_id,
                error.code,
            )
            raise ClassifierNotReady(error.code) from None
        except _Refused as error:
            logger.error(
                "classifier call refused: classifier_id=%s code=%s",
                self._classifier_id,
                error.code,
            )
            raise ModelCallFailed(error.code) from None
        # Counts only: never the type it gave or anything of the page.
        logger.info(
            "classifier run: classifier_id=%s result_id=%s polls=%d",
            self._classifier_id,
            result_id,
            polls,
        )
        return answer_of(operation.get("analyzeResult"))

    # --- The training job ---------------------------------------------------------

    async def exists(self) -> bool:
        """Whether the classifier of the configured id is there: built once, it is never built again."""
        try:
            with adapter_span(tracer, "classification.classifier.exists"):
                response = await self._send(
                    "GET",
                    f"{CLASSIFIERS_PATH}/{self._classifier_id}",
                    what="classifier_read",
                )
        except (_NotAnswered, _Refused) as error:
            raise TrainingFailed(error.code) from None
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise TrainingFailed(f"classifier_read_status_{response.status_code}")
        return True

    async def build(self, container_url: str, prefixes: Mapping[str, str]) -> None:
        """Have the service build the classifier from the container, and wait for it.

        The service reads the container with its own identity: the request
        names the container and a folder per document type, and carries no
        key and no signed address. A build of this classifier that is under
        way already (a request sent again after a lost answer, or another
        run of the job) is not an error: the classifier is waited for. The
        caller's deadline ends a wait that is too long.
        """
        body = {
            "classifierId": self._classifier_id,
            "description": "Page types of the underwriting demo (synthetic pages).",
            "docTypes": {
                doc_type: {
                    "azureBlobSource": {"containerUrl": container_url, "prefix": prefix}
                }
                for doc_type, prefix in prefixes.items()
            },
        }
        try:
            with adapter_span(tracer, "classification.classifier.build"):
                submitted = await self._send(
                    "POST",
                    f"{CLASSIFIERS_PATH}:build",
                    what="classifier_build",
                    json=body,
                )
                if submitted.status_code == _CONFLICT:
                    logger.info(
                        "classifier build under way already: classifier_id=%s",
                        self._classifier_id,
                    )
                    while not await self.exists():
                        await self._sleep(self._poll_seconds)
                    return
                if submitted.status_code != 202:
                    raise _Refused(f"classifier_build_status_{submitted.status_code}")
                operation_id = _operation_id(submitted)
                if operation_id is None:
                    raise _Refused("classifier_build_no_operation_id")
                logger.info(
                    "classifier build submitted: classifier_id=%s operation_id=%s",
                    self._classifier_id,
                    operation_id,
                )
                await self._wait(
                    f"{OPERATIONS_PATH}/{operation_id}", "classifier_build"
                )
        except (_NotAnswered, _Refused) as error:
            raise TrainingFailed(error.code) from None

    # --- Calls --------------------------------------------------------------------

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
            raise _NotAnswered(f"token_{type(error).__qualname__}") from None

    def _wait_after(self, response: httpx2.Response | None) -> float:
        """How long to wait before the next call: what `Retry-After` asks, within a limit."""
        asked = _retry_after_seconds(response) if response is not None else None
        if asked is None:
            return self._poll_seconds
        return min(asked, MAX_RETRY_AFTER_SECONDS)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        what: str,
        params: Mapping[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx2.Response:
        """One call, sent again while the service says "not now"; its answer, whatever it is.

        An analysis changes nothing and a classifier is looked for first, so
        a call that got no answer is sent again.
        """
        for attempt in range(1, self._max_retries + 2):
            answered: httpx2.Response | None = None
            try:
                response = await self._http.request(
                    method,
                    path,
                    params={**self._parameters, **(params or {})},
                    json=json,
                    headers=await self._headers(),
                )
            except httpx2.HTTPError as error:
                # security rule 31: the type only; the message can hold an address.
                code = f"{what}_{type(error).__qualname__}"
            else:
                if not _try_again(response):
                    return response
                answered = response
                code = f"{what}_status_{response.status_code}"
            logger.warning(
                "classifier call not answered: classifier_id=%s attempt=%d code=%s",
                self._classifier_id,
                attempt,
                code,
            )
            if attempt > self._max_retries:
                raise _NotAnswered(code)
            await self._sleep(self._wait_after(answered))
        raise _NotAnswered(what)  # pragma: no cover - the loop always returns or raises

    async def _wait(self, path: str, what: str) -> tuple[dict[str, Any], int]:
        """Look at an analysis or a build until it has ended; its state, and how many looks it took.

        A look that gets no answer is tried again, but not for ever: after
        a few in a row the wait ends with the last one's code. The caller's
        deadline ends a wait that is merely long.
        """
        polls, unanswered = 0, 0
        while True:
            polls += 1
            wait_seconds = self._poll_seconds
            code = ""
            operation: dict[str, Any] | None = None
            try:
                response = await self._http.get(
                    path, params=self._parameters, headers=await self._headers()
                )
            except httpx2.HTTPError as error:
                code = type(error).__qualname__
            else:
                wait_seconds = self._wait_after(response)
                if _try_again(response):
                    code = f"status_{response.status_code}"
                elif response.status_code != 200:
                    raise _Refused(f"{what}_result_status_{response.status_code}")
                else:
                    operation = _object_of(response, what)
            if operation is None:
                unanswered += 1
                logger.warning(
                    "classifier poll not answered: classifier_id=%s attempt=%d "
                    "code=%s in_a_row=%d",
                    self._classifier_id,
                    polls,
                    code,
                    unanswered,
                )
                if unanswered >= self._max_failed_polls:
                    raise _NotAnswered(f"{what}_poll_{code}")
            else:
                unanswered = 0
                status = str(operation.get("status", "")).lower()
                if status == _SUCCEEDED:
                    return operation, polls
                if status in _ENDED:
                    # The service's own code says why, where it gives one.
                    service_code = _error_code(operation)
                    raise _Refused(
                        f"{what}_{status}_{service_code}"
                        if service_code
                        else f"{what}_{status}"
                    )
            await self._sleep(wait_seconds)


def _object_of(response: httpx2.Response, what: str) -> dict[str, Any]:
    try:
        operation = response.json()
    except ValueError:
        raise _Refused(f"{what}_result_not_json") from None
    if not isinstance(operation, dict):
        raise _Refused(f"{what}_result_not_an_object")
    return operation
