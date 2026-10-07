"""A local stand-in for Azure AI Language's document PII redaction (story 1.7).

The Azure environment is down while the stories are built, so `intake`'s
redaction is proven against this: an HTTP app with the same job routes as the
service (submit, look up, cancel). It reads the source blob, masks what it
finds with entity tokens such as `[Person]`, and writes the redacted PDF and a
result file to the target container.

A dev tool only. It is part of `synthdata`, which no service depends on, so it
is in no service image; and `intake` refuses a plain-HTTP Language endpoint
that is not on loopback, so it cannot stand in for the service in Azure.

What it finds: email addresses, phone numbers, identity numbers and policy
numbers by their shape, and the names and addresses the generator plants in
the synthetic cases and in the classifier's training pages. It is not a recogniser; what the real service finds is
checked in Azure.

Run it: `uv run python -m synthdata.language_standin` (see README, 'Run locally').
"""

import argparse
import json
import re
import threading
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote

import pymupdf
import uvicorn
from azure.storage.blob import BlobServiceClient, ContentSettings
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from synthdata.cases import CASES
from synthdata.model import IdentifierCategory
from synthdata.training import TRAINING_SUBJECTS

JOBS_PATH = "/language/analyze-documents/jobs"
TASK_KIND = "PiiEntityRecognition"
ENTITY_MASK = "entityMask"
# The blob emulator's built-in account (compose.yaml); it is no secret.
EMULATOR = "UseDevelopmentStorage=true"
DEFAULT_PORT = 5100

# The category names the stand-in reports, by what the generator plants.
# `PolicyNumber` is this project's own name: see deferred-work.md.
CATEGORY_OF: dict[IdentifierCategory, str] = {
    IdentifierCategory.PERSON_NAME: "Person",
    IdentifierCategory.ADDRESS: "Address",
    IdentifierCategory.PHONE_NUMBER: "PhoneNumber",
    IdentifierCategory.EMAIL_ADDRESS: "Email",
    IdentifierCategory.IDENTITY_NUMBER: "USSocialSecurityNumber",
    IdentifierCategory.POLICY_NUMBER: "PolicyNumber",
}

# Found by shape, wherever they are.
_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("PhoneNumber", re.compile(r"\(\d{3}\) \d{3}-\d{4}")),
    ("USSocialSecurityNumber", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("PolicyNumber", re.compile(r"\bPOL-[A-Z]{3}-\d{7}\b")),
)
# A part of a name shorter than this is not looked for on its own.
_MIN_NAME_PART = 3
_MASK_FONT = "helv"
_MIN_TOKEN_SIZE = 3.0
# PyMuPDF's PDF_REDACT_IMAGE_NONE: images are left as they are.
_KEEP_IMAGES = 0


class Mode(StrEnum):
    """What the stand-in does with a job; tests switch it to see failures."""

    OK = "ok"
    # The job is accepted and then fails.
    FAIL = "fail"
    # The job is refused at submit.
    REJECT = "reject"
    # The job never ends, until it is cancelled.
    HANG = "hang"
    # The job succeeds, but its files are not a PDF and not JSON.
    UNREADABLE = "unreadable"


def planted_values() -> list[tuple[str, str]]:
    """The names and addresses of the synthetic cases, with each part of a name.

    Taken from the generator's case definitions, longest first, so a whole
    name is masked as one item before its parts are looked for. The people
    of the classifier's training pages are among them (story 4.2): those
    pages pass the same redaction as case pages before a classifier is
    trained on them.
    """
    values: set[tuple[str, str]] = set()
    for case in (*CASES, *TRAINING_SUBJECTS):
        for category, value in case.identifiers():
            values.add((CATEGORY_OF[category], value))
            if category is IdentifierCategory.PERSON_NAME:
                values.update(
                    (CATEGORY_OF[category], part)
                    for part in value.split()
                    if len(part.strip(".")) >= _MIN_NAME_PART
                )
    return sorted(values, key=lambda item: (-len(item[1]), item))


def _wanted(
    page: pymupdf.Page, known: Sequence[tuple[str, str]]
) -> list[tuple[str, str]]:
    text = str(page.get_text())  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    shaped = {
        (category, match.group())
        for category, shape in _SHAPES
        for match in shape.finditer(text)
    }
    return sorted(shaped | set(known), key=lambda item: (-len(item[1]), item))


def _mask_page(
    page: pymupdf.Page, categories: Collection[str], known: Sequence[tuple[str, str]]
) -> list[str]:
    """Mask one page in place; return the category of each item masked."""
    taken: list[tuple[pymupdf.Rect, str]] = []
    for category, value in _wanted(page, known):
        if category not in categories:
            continue
        for found in page.search_for(value):
            # Inside something already masked: a name's part within the name.
            if any(found.intersects(other) for other, _ in taken):
                continue
            taken.append((found, category))
    for found, _ in taken:
        page.add_redact_annot(found)
    if taken:
        page.apply_redactions(images=_KEEP_IMAGES)
    for found, category in taken:
        token = f"[{category}]"
        # As large as the line, made smaller until the token fits its place.
        size = found.height * 0.7
        width = pymupdf.get_text_length(token, fontname=_MASK_FONT, fontsize=size)
        if width > found.width:
            size = max(size * found.width / width, _MIN_TOKEN_SIZE)
        page.insert_text(
            (found.x0, found.y1 - found.height * 0.25),
            token,
            fontname=_MASK_FONT,
            fontsize=size,
        )
    return [category for _, category in taken]


def redact_pdf(
    pdf: bytes,
    categories: Collection[str],
    known: Sequence[tuple[str, str]] | None = None,
) -> tuple[bytes, list[str]]:
    """Mask a PDF with entity tokens; return it with the category of each item masked."""
    if known is None:
        known = planted_values()
    masked: list[str] = []
    with pymupdf.open(stream=pdf, filetype="pdf") as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        for page in document:
            masked.extend(_mask_page(page, categories, known))
        # garbage: what was masked is taken out of the file, not just hidden.
        return bytes(document.tobytes(garbage=4, deflate=True)), masked


def result_file(document_id: str, masked: Sequence[str]) -> bytes:
    """The job's result file: one entry per item masked, by category, never its text."""
    return json.dumps(
        {
            "kind": "PiiEntityRecognitionResults",
            "results": {
                "documents": [
                    {
                        "id": document_id,
                        "entities": [
                            {"category": category, "confidenceScore": 1.0}
                            for category in masked
                        ],
                        "warnings": [],
                    }
                ],
                "errors": [],
                "modelVersion": "stand-in",
            },
        }
    ).encode()


@dataclass
class Job:
    job_id: str
    document_id: str
    status: str
    targets: list[str] = field(default_factory=list)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code
    )


class LanguageStandIn:
    """The stand-in's state and its HTTP app. Tests look at what it was asked."""

    def __init__(self, blobs: BlobServiceClient, mode: Mode = Mode.OK) -> None:
        self._blobs = blobs
        self._account_url = str(blobs.url).rstrip("/")
        self.mode = mode
        self.jobs: dict[str, Job] = {}
        # What it was asked, for tests: every submitted body, every cancelled job.
        self.submitted: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self._lock = threading.Lock()

    def _place(self, location: str) -> tuple[str, str]:
        """The container and the name (or prefix) an address in the account names."""
        prefix = f"{self._account_url}/"
        if not location.startswith(prefix):
            raise ValueError("the location is not in the stand-in's storage account")
        container, _, name = unquote(location[len(prefix) :]).partition("/")
        return container, name.strip("/")

    def _run(self, job: Job, document: dict[str, Any], categories: list[str]) -> None:
        container, name = self._place(document["source"]["location"])
        target_container, target_prefix = self._place(document["target"]["location"])
        original = (
            self._blobs.get_blob_client(container, name).download_blob().readall()
        )
        if self.mode is Mode.UNREADABLE:
            pdf, result = b"not a pdf", b"not json"
        else:
            pdf, masked = redact_pdf(original, categories)
            result = result_file(job.document_id, masked)
        # As the service lays its output out: a folder per job and task.
        folder = "/".join(
            part for part in (target_prefix, job.job_id, TASK_KIND, "0001") if part
        )
        stem = PurePosixPath(name).stem
        for file_name, content, content_type in (
            (f"{stem}.pdf", pdf, "application/pdf"),
            (f"{stem}.result.json", result, "application/json"),
        ):
            blob_name = f"{folder}/{file_name}"
            self._blobs.get_blob_client(target_container, blob_name).upload_blob(
                content,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )
            job.targets.append(f"{self._account_url}/{target_container}/{blob_name}")

    def submit(self, body: dict[str, Any]) -> Job | None:
        """Take a job; None if the body is not a redaction job for one document."""
        try:
            (document,) = body["analysisInput"]["documents"]
            (task,) = body["tasks"]
            parameters = task["parameters"]
            categories = [str(name) for name in parameters["piiCategories"]]
            valid = (
                task["kind"] == TASK_KIND
                and parameters["redactionPolicy"]["policyKind"] == ENTITY_MASK
                and isinstance(document["source"]["location"], str)
                and isinstance(document["target"]["location"], str)
            )
        except (KeyError, TypeError, ValueError):
            return None
        if not valid:
            return None
        job = Job(uuid.uuid4().hex, str(document.get("id", "1")), "running")
        with self._lock:
            self.submitted.append(body)
            self.jobs[job.job_id] = job
        if self.mode is Mode.FAIL:
            job.status = "failed"
        elif self.mode is not Mode.HANG:
            try:
                self._run(job, document, categories)
                job.status = "succeeded"
            except Exception:  # noqa: BLE001 - whatever went wrong, the job is reported as failed
                job.status = "failed"
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        with self._lock:
            self.cancelled.append(job_id)
            if job.status == "running":
                job.status = "cancelled"
        return True

    def state(self, job: Job) -> dict[str, Any]:
        """The job as the service reports it: its status and where its files are."""
        succeeded = job.status == "succeeded"
        documents = [
            {
                "id": job.document_id,
                "targets": [
                    {"kind": "AzureBlob", "location": location}
                    for location in job.targets
                ],
                "warnings": [],
            }
        ]
        return {
            "jobId": job.job_id,
            "status": job.status,
            "errors": []
            if job.status != "failed"
            else [{"code": "InternalServerError", "message": "The job failed."}],
            "tasks": {
                "completed": int(succeeded),
                "failed": int(job.status == "failed"),
                "inProgress": int(job.status == "running"),
                "total": 1,
                "items": [
                    {
                        "kind": f"{TASK_KIND}LROResults",
                        "status": job.status,
                        "results": {
                            "documents": documents if succeeded else [],
                            "errors": [],
                            "modelVersion": "stand-in",
                        },
                    }
                ],
            },
        }

    def app(self) -> FastAPI:
        """The HTTP app: the service's three job routes."""
        app = FastAPI(
            title="language-stand-in", docs_url=None, redoc_url=None, openapi_url=None
        )

        # Plain `def` routes: the blob work blocks, so it runs on a worker thread.
        @app.post(JOBS_PATH)
        def submit(request: Request, body: dict[str, Any]) -> Response:
            if self.mode is Mode.REJECT:
                return _error(400, "InvalidRequest", "The job was refused.")
            job = self.submit(body)
            if job is None:
                return _error(400, "InvalidRequest", "Not a redaction job.")
            location = (
                f"{str(request.base_url).rstrip('/')}{JOBS_PATH}/{job.job_id}"
                f"?{request.url.query}"
            )
            return Response(status_code=202, headers={"operation-location": location})

        @app.get(f"{JOBS_PATH}/{{job_id}}")
        def look_up(job_id: str) -> Response:
            job = self.jobs.get(job_id)
            if job is None:
                return _error(404, "NotFound", "No such job.")
            return JSONResponse(self.state(job))

        @app.post(f"{JOBS_PATH}/{{job_id}}:cancel")
        def cancel(job_id: str) -> Response:
            if not self.cancel(job_id):
                return _error(404, "NotFound", "No such job.")
            return Response(status_code=202)

        return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="synthdata.language_standin", description=__doc__
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--blob-connection-string",
        default=EMULATOR,
        help="the storage it reads and writes (default: the local blob emulator)",
    )
    parser.add_argument(
        "--mode",
        type=Mode,
        choices=list(Mode),
        default=Mode.OK,
        help="what happens to every job (default: ok)",
    )
    args = parser.parse_args(argv)
    stand_in = LanguageStandIn(
        BlobServiceClient.from_connection_string(args.blob_connection_string),
        args.mode,
    )
    # Loopback only: it is never reachable from another machine.
    uvicorn.run(stand_in.app(), host="127.0.0.1", port=args.port, server_header=False)


if __name__ == "__main__":
    main()
