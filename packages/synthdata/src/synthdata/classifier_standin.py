"""A local stand-in for Document Intelligence's custom classification model (story 4.2).

The Azure environment is down while the stories are built, so
`classification`'s second contender and its training job are proven against
this: the routes the two call (build a classifier from a blob container,
look at the build, read a classifier, submit a document to it, look its
result up), answered in the shapes of the service.

A dev tool only. It is part of `synthdata`, which no service depends on, so it
is in no service image; and `classification` refuses a plain-HTTP Document
Intelligence endpoint that is not on loopback, so it cannot stand in for the
service in Azure. Its routes are served by the layout stand-in's process
(`synthdata.layout_standin`), as the one real account serves both.

It is not a classifier and it learns nothing. A build counts the PDFs under
each document type's folder of the container and fails where a type has
fewer than five, as the service refuses too little to learn from. A document
is then told apart by the heading the generator prints on a page, exactly as
the chat stand-in tells it, and given a fixed confidence. A page without a
text layer (the handwritten note) has no heading and comes out as `other`:
the real service reads the picture. What the real service answers is checked
in Azure.
"""

import base64
import binascii
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import unquote, urlsplit

import pymupdf
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from synthdata.foundry_standin import classify_text

CLASSIFIERS_PATH = "/documentintelligence/documentClassifiers"
OPERATIONS_PATH = "/documentintelligence/operations"
API_VERSION = "2024-11-30"
# The fewest documents of one type a build takes.
MIN_DOCUMENTS_PER_TYPE = 5
# What the stand-in is sure of, and what it answers in the `unsure` mode:
# one above and one under the gate's threshold of 0.90.
SURE_CONFIDENCE = 0.97
UNSURE_CONFIDENCE = 0.55
# A document type that is no page type of the contracts.
UNKNOWN_DOC_TYPE = "newsletter"

# Given a container's address and a folder, the names of the blobs in it.
ContainerReader = Callable[[str, str], list[str]]


class Mode(StrEnum):
    """What the stand-in does with a call; tests switch it to see failures."""

    OK = "ok"
    # Every document is answered with a confidence under the gate's threshold.
    UNSURE = "unsure"
    # Every document is given a type that is no page type.
    UNKNOWN_TYPE = "unknown_type"
    # The analysis succeeds and names no document.
    NO_DOCUMENT = "no_document"
    # An analysis or a build is accepted and then fails.
    FAIL = "fail"
    # Every call is answered 429.
    THROTTLED = "throttled"
    # An analysis or a build never ends.
    HANG = "hang"


def blob_container_reader(service: BlobServiceClient) -> ContainerReader:
    """A reader over one storage account: the container is the last part of the address."""

    def read(container_url: str, prefix: str) -> list[str]:
        container = unquote(urlsplit(container_url).path.rstrip("/").rpartition("/")[2])
        try:
            return [
                str(name)
                for name in service.get_container_client(container).list_blob_names(
                    name_starts_with=prefix
                )
            ]
        except ResourceNotFoundError:
            return []

    return read


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code
    )


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def page_type_of(pdf: bytes) -> str:
    """The document type the stand-in gives a PDF: by the headings in its text."""
    with pymupdf.open(stream=pdf, filetype="pdf") as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        text = "\n".join(str(page.get_text()) for page in document)
    return classify_text(text).value


@dataclass
class _Operation:
    """A build or an analysis, as the service reports one."""

    status: str
    created: str = field(default_factory=_now)
    result: dict[str, Any] | None = None
    error_code: str = "InternalServerError"

    def state(self, result_name: str) -> dict[str, Any]:
        state: dict[str, Any] = {
            "status": self.status,
            "createdDateTime": self.created,
            "lastUpdatedDateTime": _now(),
        }
        if self.status == "succeeded":
            state[result_name] = self.result
        elif self.status == "failed":
            state["error"] = {"code": self.error_code, "message": "It failed."}
        return state


@dataclass
class ClassifierStandIn:
    """The stand-in's state and its routes. Tests look at what it was asked."""

    read_container: ContainerReader
    mode: Mode = Mode.OK
    # What a throttled call is told to wait; none by default, so that a
    # test or a local run fails at once instead of waiting.
    retry_after_seconds: int = 0
    # The classifiers built so far, by id: each with its document types.
    classifiers: dict[str, dict[str, Any]] = field(default_factory=dict)
    builds: dict[str, _Operation] = field(default_factory=dict)
    analyses: dict[str, _Operation] = field(default_factory=dict)
    # The build requests taken, as they came, and every document analysed.
    build_requests: list[dict[str, Any]] = field(default_factory=list)
    analysed: list[bytes] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _throttled(self) -> Response:
        return JSONResponse(
            {"error": {"code": "429", "message": "Rate limit reached."}},
            status_code=429,
            headers={"retry-after": str(self.retry_after_seconds)},
        )

    def build(self, body: dict[str, Any]) -> _Operation | None:
        """Take a build; None if the body does not describe one."""
        classifier_id, doc_types = body.get("classifierId"), body.get("docTypes")
        if not isinstance(classifier_id, str) or not isinstance(doc_types, dict):
            return None
        counts: dict[str, int] = {}
        for doc_type, source in doc_types.items():
            blob = source.get("azureBlobSource") if isinstance(source, dict) else None
            if not isinstance(blob, dict) or not isinstance(
                blob.get("containerUrl"), str
            ):
                return None
            names = self.read_container(
                blob["containerUrl"], str(blob.get("prefix", ""))
            )
            counts[doc_type] = sum(name.lower().endswith(".pdf") for name in names)
        operation = _Operation("running")
        if self.mode is Mode.FAIL:
            operation.status = "failed"
        elif any(count < MIN_DOCUMENTS_PER_TYPE for count in counts.values()):
            operation.status, operation.error_code = "failed", "TrainingContentMissing"
        elif self.mode is not Mode.HANG:
            details = {
                "classifierId": classifier_id,
                "createdDateTime": operation.created,
                "apiVersion": API_VERSION,
                "docTypes": doc_types,
            }
            operation.status, operation.result = "succeeded", details
            with self._lock:
                self.classifiers[classifier_id] = details
        return operation

    def analyse(self, pdf: bytes) -> _Operation:
        """Classify one document, as the mode says."""
        operation = _Operation("running")
        if self.mode is Mode.FAIL:
            operation.status = "failed"
            return operation
        if self.mode is Mode.HANG:
            return operation
        try:
            doc_type = page_type_of(pdf)
        except Exception:  # noqa: BLE001 - whatever went wrong reading the file, the analysis is reported as failed
            operation.status, operation.error_code = "failed", "InvalidContent"
            return operation
        documents = [
            {
                "docType": UNKNOWN_DOC_TYPE
                if self.mode is Mode.UNKNOWN_TYPE
                else doc_type,
                "confidence": UNSURE_CONFIDENCE
                if self.mode is Mode.UNSURE
                else SURE_CONFIDENCE,
                "boundingRegions": [{"pageNumber": 1}],
                "spans": [],
            }
        ]
        operation.status = "succeeded"
        operation.result = {
            "apiVersion": API_VERSION,
            "stringIndexType": "textElements",
            "content": "",
            "pages": [{"pageNumber": 1}],
            "documents": [] if self.mode is Mode.NO_DOCUMENT else documents,
        }
        return operation

    def add_routes(self, app: FastAPI) -> None:
        """Put the classifier routes on an app: the stand-in's own, or the layout stand-in's."""

        # Plain `def` routes: reading a container or a PDF blocks, so they
        # run on worker threads.
        @app.post(f"{CLASSIFIERS_PATH}:build")
        def build(request: Request, body: dict[str, Any]) -> Response:
            if self.mode is Mode.THROTTLED:
                return self._throttled()
            with self._lock:
                self.build_requests.append(body)
                exists = body.get("classifierId") in self.classifiers
            if exists:
                return _error(409, "ModelExists", "The classifier exists already.")
            operation = self.build(body)
            if operation is None:
                return _error(400, "InvalidRequest", "Not a build request.")
            operation_id = uuid.uuid4().hex
            with self._lock:
                self.builds[operation_id] = operation
            location = (
                f"{str(request.base_url).rstrip('/')}{OPERATIONS_PATH}/{operation_id}"
                f"?{request.url.query}"
            )
            return Response(status_code=202, headers={"operation-location": location})

        @app.get(f"{OPERATIONS_PATH}/{{operation_id}}")
        def look_at_build(operation_id: str) -> Response:
            if self.mode is Mode.THROTTLED:
                return self._throttled()
            operation = self.builds.get(operation_id)
            if operation is None:
                return _error(404, "NotFound", "No such operation.")
            return JSONResponse(operation.state("result"))

        @app.get(f"{CLASSIFIERS_PATH}/{{classifier_id}}")
        def read(classifier_id: str) -> Response:
            if self.mode is Mode.THROTTLED:
                return self._throttled()
            classifier = self.classifiers.get(classifier_id)
            if classifier is None:
                return _error(404, "ModelNotFound", "No such classifier.")
            return JSONResponse(classifier)

        @app.post(f"{CLASSIFIERS_PATH}/{{classifier_id}}:analyze")
        def submit(
            classifier_id: str, request: Request, body: dict[str, Any]
        ) -> Response:
            if self.mode is Mode.THROTTLED:
                return self._throttled()
            if classifier_id not in self.classifiers:
                return _error(404, "ModelNotFound", "No such classifier.")
            source = body.get("base64Source")
            try:
                pdf = base64.b64decode(source, validate=True) if source else b""
            except (binascii.Error, ValueError, TypeError):
                pdf = b""
            if not pdf:
                return _error(
                    400, "InvalidRequest", "Send the document as base64Source."
                )
            operation = self.analyse(pdf)
            result_id = uuid.uuid4().hex
            with self._lock:
                self.analysed.append(pdf)
                self.analyses[result_id] = operation
            location = (
                f"{str(request.base_url).rstrip('/')}{CLASSIFIERS_PATH}/{classifier_id}"
                f"/analyzeResults/{result_id}?{request.url.query}"
            )
            return Response(status_code=202, headers={"operation-location": location})

        @app.get(f"{CLASSIFIERS_PATH}/{{classifier_id}}/analyzeResults/{{result_id}}")
        def look_up(classifier_id: str, result_id: str) -> Response:
            if self.mode is Mode.THROTTLED:
                return self._throttled()
            operation = self.analyses.get(result_id)
            if operation is None or classifier_id not in self.classifiers:
                return _error(404, "NotFound", "No such analysis.")
            return JSONResponse(operation.state("analyzeResult"))

    def app(self) -> FastAPI:
        """An HTTP app with the classifier routes alone."""
        app = FastAPI(
            title="classifier-stand-in", docs_url=None, redoc_url=None, openapi_url=None
        )
        self.add_routes(app)
        return app
