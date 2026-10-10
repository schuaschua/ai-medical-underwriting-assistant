"""Azure AI Language adapter: document PII redaction over REST (AD-21).

The service reads the original from Blob Storage and writes the redacted PDF
and a result file there, with its own identity. This adapter only submits the
job, watches it and can cancel it: it never holds the original's content.
Nothing the service answers with is logged except statuses and codes.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
from opentelemetry import trace

from intake.adapters.credential import azure_credential
from intake.adapters.db import EntraToken
from intake.adapters.telemetry import adapter_span
from intake.domain.entities import JobOutput
from intake.domain.ports import DocumentTextEmpty, RedactionJobError
from intake.settings import APP_ID, Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(APP_ID)

# The scope of an Entra token for Azure AI services.
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
JOBS_PATH = "/language/analyze-documents/jobs"
TASK_KIND = "PiiEntityRecognition"
# AD-21: each found item is replaced by its category, such as `[Person]`.
ENTITY_MASK = "entityMask"
OPERATION_LOCATION = "operation-location"
_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
# Statuses after which a job does not change any more.
_SUCCEEDED = "succeeded"
_ENDED = frozenset(
    {_SUCCEEDED, "failed", "cancelled", "cancelling", "partiallycompleted"}
)
# How often a submit that was answered 429 or 5xx is sent again.
SUBMIT_RETRIES = 3
# What a cancel may be answered with: taken, or nothing left to cancel.
_CANCEL_ANSWERS = frozenset({200, 202, 404, 409})
_RESULT_SUFFIX = ".json"
_PDF_SUFFIX = ".pdf"


def build_language_http(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """The HTTP client for Language. Tests pass a transport that stands in for it."""
    if settings.language_endpoint is None:
        raise ValueError(
            "Set INTAKE_LANGUAGE_ENDPOINT: the Azure AI Language account, or the "
            "local stand-in."
        )
    return httpx.AsyncClient(
        base_url=settings.language_endpoint.rstrip("/"),
        timeout=settings.language_timeout_seconds,
        transport=transport,
        # Never follow a redirect: the token must not leave the endpoint.
        follow_redirects=False,
        trust_env=False,
    )


def language_token_for(settings: Settings) -> EntraToken | None:
    """The token source for Language in Azure; None for the local stand-in."""
    if not settings.language_entra_auth:
        return None
    return EntraToken(azure_credential(settings), scope=COGNITIVE_SERVICES_SCOPE)


class LanguageRedaction:
    """Submits, watches and cancels document redaction jobs."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        api_version: str,
        originals_url: str,
        cases_url: str,
        poll_seconds: float,
        token: EntraToken | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._parameters = {"api-version": api_version}
        self._originals_url = originals_url.rstrip("/")
        self._cases_url = cases_url.rstrip("/")
        self._poll_seconds = poll_seconds
        self._token = token
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _headers(self) -> dict[str, str]:
        if self._token is None:
            return {}
        # Fetched off the event loop, and kept until it is near its end.
        await self._token.refresh()
        return {"Authorization": f"Bearer {self._token.value()}"}

    async def start(
        self, original_blob_name: str, case_id: str, categories: Sequence[str]
    ) -> str:
        """Submit one job for one document; return the job's id."""
        body = {
            "displayName": f"redaction {case_id}",
            "analysisInput": {
                "documents": [
                    {
                        "id": case_id,
                        "language": "en",
                        # Addresses only: the service reads and writes the
                        # blobs with its own identity.
                        "source": {
                            "location": f"{self._originals_url}/{original_blob_name}"
                        },
                        "target": {"location": f"{self._cases_url}/{case_id}"},
                    }
                ]
            },
            "tasks": [
                {
                    "kind": TASK_KIND,
                    "taskName": "redact",
                    "parameters": {
                        # API version 2026-05-01 takes a list of policies
                        # and wants one marked as the default (seen in the
                        # Azure session of 2026-10-10: the single
                        # `redactionPolicy` of the preview is refused).
                        "redactionPolicies": [
                            {"policyKind": ENTITY_MASK, "isDefault": True}
                        ],
                        "piiCategories": list(categories),
                    },
                }
            ],
        }
        with adapter_span(tracer, "intake.language.submit"):
            for attempt in range(SUBMIT_RETRIES + 1):
                try:
                    response = await self._http.post(
                        JOBS_PATH,
                        params=self._parameters,
                        json=body,
                        headers=await self._headers(),
                    )
                except httpx.HTTPError as error:
                    # Not tried again: the job may have been accepted.
                    raise RedactionJobError(
                        f"submit_{type(error).__qualname__}"
                    ) from None
                if attempt == SUBMIT_RETRIES or not _try_again(response):
                    break
                # The service refused for now, as it may while a job is
                # looked at. The caller's deadline bounds the waits.
                await self._sleep(self._poll_seconds)
        if response.status_code != httpx.codes.ACCEPTED:
            raise RedactionJobError(f"submit_status_{response.status_code}")
        # Only the id is taken from the address the service names; every
        # later call goes to the configured endpoint, never to that address.
        location = str(response.headers.get(OPERATION_LOCATION, ""))
        job_id = urlsplit(location).path.rstrip("/").rpartition("/")[2]
        if _JOB_ID.fullmatch(job_id) is None:
            raise RedactionJobError("submit_no_job_id")
        logger.info("redaction job submitted: case_id=%s job_id=%s", case_id, job_id)
        return job_id

    async def output(self, job_id: str, case_id: str) -> JobOutput:
        """Look at the job until it has ended; the caller's deadline bounds the wait."""
        with adapter_span(tracer, "intake.language.wait"):
            while True:
                job = await self._job(job_id)
                status = str(job.get("status", "")).lower() if job is not None else ""
                if status in _ENDED:
                    break
                await self._sleep(self._poll_seconds)
        if job is None or status != _SUCCEEDED:
            raise RedactionJobError(f"job_{status}")
        return self._output_of(job, case_id)

    async def _job(self, job_id: str) -> dict[str, Any] | None:
        """The job's state, or None when the service could not say just now."""
        try:
            response = await self._http.get(
                f"{JOBS_PATH}/{job_id}",
                params=self._parameters,
                headers=await self._headers(),
            )
        except httpx.HTTPError:
            return None
        if _try_again(response):
            return None
        if response.status_code != httpx.codes.OK:
            raise RedactionJobError(f"job_status_{response.status_code}")
        try:
            job = response.json()
        except ValueError:
            raise RedactionJobError("job_not_json") from None
        if not isinstance(job, dict):
            raise RedactionJobError("job_not_an_object")
        return job

    def _output_of(self, job: dict[str, Any], case_id: str) -> JobOutput:
        """Find the redacted PDF and the result file among the job's targets."""
        names = [
            self._blob_name(location, case_id) for location in _target_locations(job)
        ]
        pdfs = [name for name in names if name.lower().endswith(_PDF_SUFFIX)]
        results = [name for name in names if name.lower().endswith(_RESULT_SUFFIX)]
        if len(pdfs) != 1 or len(results) != 1:
            raise RedactionJobError("job_output_incomplete")
        return JobOutput(redacted_blob_name=pdfs[0], result_blob_name=results[0])

    def _blob_name(self, location: str, case_id: str) -> str:
        """The name, inside the `cases` container, of a blob the service names.

        Only a blob under the case's own prefix is accepted: anything else
        could be another case's file, which would then be read, copied as
        this case's and removed.
        """
        prefix = f"{self._cases_url}/"
        # Any query is dropped: the name is all that is used of the address.
        address = location.split("?", 1)[0]
        name = unquote(address[len(prefix) :])
        own = f"{case_id}/"
        if (
            not address.startswith(prefix)
            or not name.startswith(own)
            or len(name) == len(own)
            or ".." in name.split("/")
        ):
            raise RedactionJobError("job_output_outside_case")
        return name

    async def cancel(self, job_id: str) -> None:
        with adapter_span(tracer, "intake.language.cancel"):
            response = await self._http.post(
                f"{JOBS_PATH}/{job_id}:cancel",
                params=self._parameters,
                headers=await self._headers(),
            )
        # A job that has ended, or is unknown, has nothing left to cancel.
        # Any other answer (not signed in, refused for now) is not a cancel.
        if response.status_code not in _CANCEL_ANSWERS:
            raise RedactionJobError(f"cancel_status_{response.status_code}")
        logger.info(
            "redaction job cancel asked: job_id=%s status=%d",
            job_id,
            response.status_code,
        )


def _try_again(response: httpx.Response) -> bool:
    """Whether the service could not answer just now: 429 or a 5xx."""
    return response.status_code == httpx.codes.TOO_MANY_REQUESTS or (
        response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR
    )


# What the service says of a document without any text (seen in the Azure
# session of 2026-10-10): among the error's details, an inner error with this
# code and the message "Document text is empty."
_INVALID_DOCUMENT = "InvalidDocument"
_TEXT_IS_EMPTY = "text is empty"


def _says_text_is_empty(error: object) -> bool:
    """Whether a document's error says its text is empty, and nothing else.

    Both must hold of one inner error: the code, which the service also
    gives a document it cannot open, and the message, which says why. The
    message is compared, never logged or kept.
    """
    pending: list[object] = [error]
    while pending:
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(node)
        elif isinstance(node, dict):
            message = node.get("message")
            if (
                node.get("code") == _INVALID_DOCUMENT
                and isinstance(message, str)
                and _TEXT_IS_EMPTY in message.casefold()
            ):
                return True
            pending.extend(node.values())
    return False


def _target_locations(job: dict[str, Any]) -> list[str]:
    """Every target address the job's documents report, and nothing else of it."""
    try:
        items = job["tasks"]["items"]
        locations = [
            target["location"]
            for item in items
            for document in item["results"]["documents"]
            for target in document["targets"]
        ]
    except (KeyError, TypeError):
        raise RedactionJobError("job_output_missing") from None
    errors = [
        error for item in items for error in item.get("results", {}).get("errors") or []
    ]
    if errors:
        if not locations and all(_says_text_is_empty(error) for error in errors):
            # Not a failure of the service: the document has no text to redact.
            raise DocumentTextEmpty("job_document_text_empty")
        # The service could not process the document.
        raise RedactionJobError("job_document_error")
    if not all(isinstance(location, str) for location in locations):
        raise RedactionJobError("job_output_missing")
    return locations
