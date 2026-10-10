"""A local stand-in for Azure AI Language's document PII redaction (story 1.7).

The Azure environment is down while the stories are built, so `intake`'s
redaction is proven against this: an HTTP app with the same job routes as the
service (submit, look up, cancel). It reads the source blob, masks what it
finds, and writes the redacted PDF and a result file to the target container.

The redacted PDF is what the real service writes (seen in the Azure session
of 2026-10-10, API version 2026-05-01): every page is one picture, each mask
is drawn on it as a short label with a small raised number (`PER` and `1`
for the first person found), and the only text left in the file is those
labels and numbers. There is no text layer to read the page from: whoever
needs the page text has to read the picture, as `intake` does with Document
Intelligence's read model.

The stand-in for that read model (`synthdata.layout_standin`) cannot read a
picture. So this stand-in keeps what a reader would see, the words of the
masked page and where they are, in the PDF itself: as a stream of JSON that
the page's picture object points to under the key `StandInWords`. It rides
with the picture when `intake` cuts one page out of the file, no PDF reader
shows it, and only the stand-ins look for it (`words_of_page`).

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
import math
import re
import threading
import uuid
from collections import Counter
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
# The label the service draws for a category; `POL` is the stand-in's own,
# for the project's own category.
MASK_LABEL_OF: dict[str, str] = {
    "Person": "PER",
    "Address": "ADR",
    "PhoneNumber": "PHN",
    "Email": "EML",
    "USSocialSecurityNumber": "SSN",
    "PolicyNumber": "POL",
}
# A part of a name shorter than this is not looked for on its own.
_MIN_NAME_PART = 3
_MASK_FONT = "helv"
_MIN_LABEL_SIZE = 3.0
# How a mask is set in the height of what it covers: the label's size, where
# its baseline is, and the number beside it, smaller and raised.
_LABEL_SIZE, _BASELINE, _NUMBER_SIZE, _NUMBER_RAISE = 0.62, 0.78, 0.6, 0.45
# Helvetica, as a share of the font size: above and below the baseline.
_ASCENT, _DESCENT = 1.075, 0.299
# PyMuPDF's PDF_REDACT_IMAGE_NONE: images are left as they are.
_KEEP_IMAGES = 0
# PDF text render mode 3: neither filled nor stroked, so it is not seen.
_INVISIBLE = 3
# The service writes each page at 200 dots per inch. Fewer here: enough to
# read on a screen, and every test that redacts pays for it.
PICTURE_DPI = 150
# Where a page's picture points to the words a reader would see on it.
WORDS_KEY = "StandInWords"


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


def _quarter(across: float, down: float) -> int:
    """A direction on the page as a turn clockwise, to the nearest quarter."""
    return round(math.degrees(math.atan2(down, across)) / 90) % 4 * 90


def _reading_turn(page: pymupdf.Page) -> int:
    """How the page's text is turned on the sheet as it is stored: most of its letters decide."""
    turns: Counter[int] = Counter()
    for block in page.get_text("dict")["blocks"]:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        for line in block.get("lines", []):
            letters = sum(len(span["text"]) for span in line["spans"])
            turns[_quarter(*line["dir"])] += letters
    return turns.most_common(1)[0][0] if turns else 0


@dataclass(frozen=True)
class _Frame:
    """A box as text turned by `turn` runs through it: where it starts, and how far."""

    box: pymupdf.Rect
    turn: int

    @property
    def _axes(self) -> tuple[tuple[float, float], tuple[float, float]]:
        across = (
            round(math.cos(math.radians(self.turn))),
            round(math.sin(math.radians(self.turn))),
        )
        return across, (-across[1], across[0])

    @property
    def thickness(self) -> float:
        return float(self.box.width if self.turn % 180 else self.box.height)

    @property
    def length(self) -> float:
        return float(self.box.height if self.turn % 180 else self.box.width)

    def point(self, along: float, down: float) -> pymupdf.Point:
        """A place in the box: `along` the text from its start, `down` from its top."""
        corner = {
            0: self.box.tl,
            90: self.box.tr,
            180: self.box.br,
            270: self.box.bl,
        }[self.turn]
        across, below = self._axes
        return pymupdf.Point(  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            corner.x + along * across[0] + down * below[0],
            corner.y + along * across[1] + down * below[1],
        )

    def part(self, start: float, end: float, top: float, bottom: float) -> pymupdf.Rect:
        """The box of text that runs from `start` to `end` between `top` and `bottom`."""
        box = pymupdf.Rect(self.point(start, top), self.point(end, bottom))  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        box.normalize()  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        return box

    def write(
        self, page: pymupdf.Page, text: str, size: float, start: float, baseline: float
    ) -> pymupdf.Rect:
        """Draw text in the box, seen; return the box its letters take."""
        page.insert_text(
            self.point(start, baseline),
            text,
            fontname=_MASK_FONT,
            fontsize=size,
            # PyMuPDF turns text counter-clockwise.
            rotate=(360 - self.turn) % 360,
        )
        width = pymupdf.get_text_length(text, fontname=_MASK_FONT, fontsize=size)
        return self.part(
            start, start + width, baseline - _ASCENT * size, baseline + _DESCENT * size
        )


def _hide(page: pymupdf.Page, text: str, box: pymupdf.Rect, turn: int) -> None:
    """Put text on a page where nobody sees it, so that it takes up `box`."""
    frame = _Frame(box, turn)
    size = max(frame.thickness / (_ASCENT + _DESCENT), 1.0)
    page.insert_text(
        frame.point(0.0, _ASCENT * size),
        text,
        fontname=_MASK_FONT,
        fontsize=size,
        rotate=(360 - turn) % 360,
        render_mode=_INVISIBLE,
    )


@dataclass(frozen=True)
class _Drawn:
    """One mask as drawn on a page: its label and number, and the box of each."""

    category: str
    label: str
    number: str
    label_box: pymupdf.Rect
    number_box: pymupdf.Rect


def _mask_page(
    page: pymupdf.Page,
    categories: Collection[str],
    known: Sequence[tuple[str, str]],
    entities: dict[tuple[str, str], str],
) -> list[_Drawn]:
    """Mask one page in place, as the service draws it; return what was drawn.

    What was found is taken out of the page and a label with a raised
    number is drawn in its place. `entities` gives each thing found its
    number, the same wherever it is found again in the document.
    """
    taken: list[tuple[pymupdf.Rect, tuple[str, str]]] = []
    for category, value in _wanted(page, known):
        if category not in categories:
            continue
        for found in page.search_for(value):
            # Inside something already masked: a name's part within the name.
            if any(found.intersects(other) for other, _ in taken):
                continue
            taken.append((found, (category, value)))
    turn = _reading_turn(page)
    for found, _ in taken:
        page.add_redact_annot(found)
    if taken:
        page.apply_redactions(images=_KEEP_IMAGES)
    drawn: list[_Drawn] = []
    for found, entity in taken:
        number = entities.setdefault(entity, str(len(entities) + 1))
        label = MASK_LABEL_OF.get(entity[0], entity[0][:3].upper())
        frame = _Frame(found, turn)
        # As large as the line, made smaller until label and number fit.
        size = frame.thickness * _LABEL_SIZE
        width = pymupdf.get_text_length(
            label, fontname=_MASK_FONT, fontsize=size
        ) + pymupdf.get_text_length(
            number, fontname=_MASK_FONT, fontsize=size * _NUMBER_SIZE
        )
        if width > frame.length:
            size = max(size * frame.length / width, _MIN_LABEL_SIZE)
        baseline = frame.thickness * _BASELINE
        label_box = frame.write(page, label, size, 0.0, baseline)
        number_box = frame.write(
            page,
            number,
            size * _NUMBER_SIZE,
            pymupdf.get_text_length(label, fontname=_MASK_FONT, fontsize=size),
            baseline - size * _NUMBER_RAISE,
        )
        drawn.append(_Drawn(entity[0], label, number, label_box, number_box))
    return drawn


def read_words(page: pymupdf.Page) -> dict[str, Any]:
    """What a reader would make of a page with a text layer: its lines of words, and its turn.

    `turn` is how the text lies on the page as it is shown, clockwise in
    degrees. The lines are in reading order whatever the turn: row by row
    and, within a row, from its start. Each word is its text and its box in
    PDF points on the page as it is shown.
    """
    to_shown = page.rotation_matrix
    turn = (_reading_turn(page) + page.rotation) % 360
    across = (math.cos(math.radians(turn)), math.sin(math.radians(turn)))
    lines: dict[tuple[int, int], list[tuple[float, list[Any]]]] = {}
    for x0, y0, x1, y1, text, block, line, _ in page.get_text("words"):  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        box = pymupdf.Rect(x0, y0, x1, y1) * to_shown  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        box.normalize()
        starts = min(
            corner.x * across[0] + corner.y * across[1]
            for corner in (box.tl, box.tr, box.bl, box.br)
        )
        lines.setdefault((block, line), []).append(
            (starts, [text, *(round(value, 2) for value in box)])
        )
    placed: list[tuple[float, float, float, list[list[Any]]]] = []
    for words in lines.values():
        words.sort(key=lambda word: word[0])
        boxes = [pymupdf.Rect(word[1:]) for _, word in words]  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        # Where the line is down the page as it is read, and how high it is.
        downs = [
            -corner.x * across[1] + corner.y * across[0]
            for box in boxes
            for corner in (box.tl, box.br)
        ]
        placed.append(
            (
                (min(downs) + max(downs)) / 2,
                max(downs) - min(downs),
                words[0][0],
                [word for _, word in words],
            )
        )
    placed.sort(key=lambda line: line[0])
    rows: list[list[tuple[float, float, float, list[list[Any]]]]] = []
    for line in placed:
        # A line whose middle is within half a line of the row's first is in that row.
        if rows and line[0] - rows[-1][0][0] < rows[-1][0][1] / 2:
            rows[-1].append(line)
        else:
            rows.append([line])
    return {
        "turn": turn,
        "lines": [
            line[3] for row in rows for line in sorted(row, key=lambda line: line[2])
        ],
    }


def words_of_page(document: pymupdf.Document, page: pymupdf.Page) -> dict[str, Any]:
    """What a reader would see on a page: what this stand-in kept for it, or its text layer.

    A page this stand-in redacted is a picture that points to the words
    kept for it. Any other page is read from its own text layer, as the
    real services read a PDF that has one.
    """
    for image in page.get_images():  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        kind, value = document.xref_get_key(image[0], WORDS_KEY)  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        if kind == "xref":
            kept: dict[str, Any] = json.loads(
                document.xref_stream(int(value.split()[0]))  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            )
            return kept
    return read_words(page)


def _as_picture(
    target: pymupdf.Document, page: pymupdf.Page, drawn: Sequence[_Drawn]
) -> None:
    """Add a masked page to the redacted file as the service writes it: one picture.

    The picture is of the page as it is shown, so a turned page comes out
    upright in the file with its content turned. The only text put on it is
    each mask's label and number, unseen, where they are drawn.
    """
    words = read_words(page)
    to_shown = page.rotation_matrix
    picture = page.get_pixmap(dpi=PICTURE_DPI, alpha=False)
    sheet = target.new_page(width=page.rect.width, height=page.rect.height)
    image = sheet.insert_image(sheet.rect, pixmap=picture)  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    for mask in drawn:
        for text, box in ((mask.label, mask.label_box), (mask.number, mask.number_box)):
            shown = pymupdf.Rect(box) * to_shown  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            shown.normalize()
            _hide(sheet, text, shown, words["turn"])
    kept = target.get_new_xref()  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    target.update_object(kept, "<<>>")  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    target.update_stream(kept, json.dumps(words).encode())  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    target.xref_set_key(image, WORDS_KEY, f"{kept} 0 R")  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call


def redact_pdf(
    pdf: bytes,
    categories: Collection[str],
    known: Sequence[tuple[str, str]] | None = None,
) -> tuple[bytes, list[tuple[str, str, str]]]:
    """Mask a PDF as the service does; return it with each entity found.

    The redacted file has a picture per page and no text but the masks'
    labels. An entity is its category, its mask's label and its number.
    """
    if known is None:
        known = planted_values()
    entities: dict[tuple[str, str], str] = {}
    labels: dict[str, tuple[str, str, str]] = {}
    with (
        pymupdf.open(stream=pdf, filetype="pdf") as document,  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        pymupdf.open() as redacted,  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    ):
        for page in document:
            drawn = _mask_page(page, categories, known, entities)
            for mask in drawn:
                labels[mask.number] = (mask.category, mask.label, mask.number)
            _as_picture(redacted, page, drawn)
        redacted.set_metadata(document.metadata)
        return bytes(redacted.tobytes(garbage=4, deflate=True)), list(labels.values())


def result_file(document_id: str, entities: Sequence[tuple[str, str, str]]) -> bytes:
    """The job's result file, in the service's shape: one entry per entity found.

    Its category (`type`), its mask's label and its number. The real file
    also holds each entity's found text; the stand-in leaves that out.
    """
    return json.dumps(
        {
            "id": document_id,
            "statistics": {"transactionsCount": 1},
            "entities": [
                {
                    "type": category,
                    "entityId": number,
                    "mask": label,
                    "confidenceScore": 1.0,
                    "tags": [{"name": category, "confidenceScore": 1.0}],
                }
                for category, label, number in entities
            ],
            "warnings": [],
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
                # As the service at API version 2026-05-01: a list of
                # policies with exactly one default.
                and [
                    policy["policyKind"]
                    for policy in parameters["redactionPolicies"]
                    if policy.get("isDefault") is True
                ]
                == [ENTITY_MASK]
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
