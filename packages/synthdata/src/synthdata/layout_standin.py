"""A local stand-in for Document Intelligence's layout model (story 2.2) and its read model.

The Azure environment is down while the stories are built, so `retrieval`'s
ingestion job is proven against this: an HTTP app with the two routes the job
calls (submit an analysis, look its result up). It reads the PDF it is sent
with PyMuPDF and answers in the shape of the service's `analyzeResult`: the
document's text, its pages with their lines and words, and its paragraphs in
reading order, each with its page and, where it can tell, a role (page
header, page footer, page number, title, section heading).

A dev tool only. It is part of `synthdata`, which no service depends on, so it
is in no service image; and `retrieval` refuses a plain-HTTP layout endpoint
that is not on loopback, so it cannot stand in for the service in Azure.

It is not a layout model. A paragraph is a text block of the PDF, a block
whose lines stand side by side is read as cells, and a role is told from
where a block is on the page and how it is set. Word boxes are shared out
along their line, not measured. It knows nothing of the manual: what the real
service makes of the manual's pages is checked in Azure. Start it with
`--no-roles` to see the result without any role.

It does not keep to the PDF's paragraphs, because the real service does not
(seen in Azure on 2026-10-10, API version 2024-11-30): on some pages the
footer comes without a role, or as one paragraph per line, and some
paragraphs are ended early, after a line that ends with a colon or with a
full stop, the rest coming as the next paragraph. Which pages and paragraphs
is a matter of counting, not of what they say (`_as_the_service`). Start it
with `--tidy` for the PDF's own paragraphs, every footer with its role.

It answers for the read model too (`prebuilt-read`), which `intake` sends
each redacted PDF to: that file's pages are pictures, and the page text is
read from them. The stand-in cannot read a picture. For a page the Language
stand-in redacted it answers with the words that stand-in kept for it in the
file (`synthdata.language_standin.words_of_page`); for any other page with
its text layer, as the real model reads a PDF that has one. A mask comes back
as a reader sees it, such as `PER5`, never as a token: making the token is
`intake`'s work. For a picture nobody kept words for (the original's pages
that `intake` draws for a document the redaction service found no text in) it
tells paper from ink: no word for a blank page, the one word `(unread)` for a
page with anything on it. The read model takes the PDF as the request's body, or as
`base64Source` like the layout model.

As a process it also serves the routes of the classifier stand-in
(`synthdata.classifier_standin`, story 4.2): in Azure one Document
Intelligence account answers both, so locally one port does. A build reads
its training pages from the blob emulator of compose.yaml.

Run it: `uv run python -m synthdata.layout_standin` (see README, 'Run locally').
"""

import argparse
import base64
import binascii
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import pymupdf
import uvicorn
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from synthdata.classifier_standin import ClassifierStandIn, blob_container_reader
from synthdata.classifier_standin import Mode as ClassifierMode
from synthdata.language_standin import EMULATOR, kept_words, read_words

MODELS_PATH = "/documentintelligence/documentModels"
LAYOUT_MODEL = "prebuilt-layout"
READ_MODEL = "prebuilt-read"
_MODELS = frozenset({LAYOUT_MODEL, READ_MODEL})
PDF_CONTENT = "application/pdf"
# The word answered for a picture with something drawn on it, and how a page
# is told to be blank: looked at coarsely, all of it is one colour.
UNREAD_WORD = "(unread)"
_INK_DPI = 36
_PAPER_SHARE = 0.9995
# How many of the PDFs sent to the read model are kept to look at.
READ_KEPT = 8
API_VERSION = "2024-11-30"
DEFAULT_PORT = 5102

_POINTS_PER_INCH = 72.0
# A block that ends above this share of the page's height is its header, one
# that starts below the other its footer.
_HEADER_SHARE = 0.075
_FOOTER_SHARE = 0.92
# Lines whose left edges are closer than this (points) start in one column.
_SAME_COLUMN = 1.5
# A heading is set in bold and larger than this; the title larger than that.
_HEADING_SIZE = 11.0
_TITLE_SIZE = 20.0
_PAGE_NUMBER = re.compile(r"(?:Page\s+)?\d+", re.IGNORECASE)
# As the real service: the footer's lines are a paragraph each on every page
# whose number divides by the first, and the footer has no role on every
# page whose number leaves the remainder after the second. On a page of both
# kinds (20, 55 ...) each footer line stands alone without a role, which is
# what a chunker cannot tell from body text by its repeating.
_FOOTER_SPLIT_EVERY = 5
_FOOTER_NO_ROLE_EVERY, _FOOTER_NO_ROLE_REMAINDER = 7, 6
# As the real service: a body paragraph is ended after a line that ends with
# a colon; and of those with a line that ends a sentence, every so many are
# ended after the first such line.
_SENTENCE_SPLIT_EVERY = 4
_BOLD = 16  # PyMuPDF's span flag for a bold font


class Mode(StrEnum):
    """What the stand-in does with an analysis; tests switch it to see failures."""

    OK = "ok"
    # The analysis is accepted and then fails.
    FAIL = "fail"
    # The analysis is refused at submit.
    REJECT = "reject"
    # Every submit is answered 429.
    THROTTLED = "throttled"
    # The analysis never ends.
    HANG = "hang"


@dataclass(frozen=True)
class _Line:
    text: str
    box: tuple[float, float, float, float]
    size: float
    bold: bool


@dataclass
class _Paragraph:
    lines: list[_Line]
    role: str | None = None

    @property
    def text(self) -> str:
        return " ".join(line.text for line in self.lines)

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (
            min(line.box[0] for line in self.lines),
            min(line.box[1] for line in self.lines),
            max(line.box[2] for line in self.lines),
            max(line.box[3] for line in self.lines),
        )


def _polygon(box: tuple[float, float, float, float]) -> list[float]:
    """A box as the service gives it: four corners, clockwise, in inches."""
    x0, y0, x1, y1 = (round(value / _POINTS_PER_INCH, 4) for value in box)
    return [x0, y0, x1, y0, x1, y1, x0, y1]


def _lines_of(block: dict[str, Any]) -> list[_Line]:
    """The lines of a text block, each a run of text set in one go.

    Two texts drawn side by side with little room between them come out of
    the PDF as one line with an empty span between them: they are two lines
    here, as they are two cells on the page.
    """
    lines: list[_Line] = []
    for line in block["lines"]:
        run: list[dict[str, Any]] = []
        for span in [*line["spans"], {"text": " "}]:
            if span["text"].strip():
                run.append(span)
                continue
            if run:
                lines.append(
                    _Line(
                        text=" ".join("".join(part["text"] for part in run).split()),
                        box=(
                            min(part["bbox"][0] for part in run),
                            min(part["bbox"][1] for part in run),
                            max(part["bbox"][2] for part in run),
                            max(part["bbox"][3] for part in run),
                        ),
                        size=max(float(part["size"]) for part in run),
                        bold=all(int(part["flags"]) & _BOLD for part in run),
                    )
                )
            run = []
    return lines


def _paragraphs_of(lines: list[_Line]) -> list[_Paragraph]:
    """The paragraphs of one text block.

    Lines that all start at one left edge are one paragraph. Lines that stand
    side by side are cells: a line at the block's left edge starts a row, and
    the lines of a row that share a left edge are one cell.
    """
    if not lines:
        return []
    left = min(line.box[0] for line in lines)
    if all(abs(line.box[0] - left) < _SAME_COLUMN for line in lines):
        return [_Paragraph(lines)]
    rows: list[list[_Line]] = []
    for line in sorted(lines, key=lambda item: (round(item.box[1]), item.box[0])):
        if not rows or abs(line.box[0] - left) < _SAME_COLUMN:
            rows.append([])
        rows[-1].append(line)
    cells: list[_Paragraph] = []
    for row in rows:
        columns: list[_Paragraph] = []
        for line in row:
            for cell in columns:
                if abs(cell.lines[0].box[0] - line.box[0]) < _SAME_COLUMN:
                    cell.lines.append(line)
                    break
            else:
                columns.append(_Paragraph([line]))
        cells.extend(sorted(columns, key=lambda cell: cell.lines[0].box[0]))
    return cells


def _role(paragraph: _Paragraph, page_number: int, height: float) -> str | None:
    """What a layout model would call the paragraph, from its place and its type."""
    _, top, _, bottom = paragraph.box
    if bottom < height * _HEADER_SHARE:
        if _PAGE_NUMBER.fullmatch(paragraph.text):
            return "pageNumber"
        return "pageHeader"
    if top > height * _FOOTER_SHARE:
        return "pageFooter"
    first = paragraph.lines[0]
    if all(line.bold for line in paragraph.lines) and first.size > _HEADING_SIZE:
        if page_number == 1 and first.size > _TITLE_SIZE:
            return "title"
        return "sectionHeading"
    return None


def _split_after(paragraph: _Paragraph, last: int) -> list[_Paragraph]:
    """The paragraph as two: its lines up to the one at `last`, and the rest."""
    return [
        _Paragraph(paragraph.lines[: last + 1], paragraph.role),
        _Paragraph(paragraph.lines[last + 1 :], paragraph.role),
    ]


def _as_the_service(
    paragraph: _Paragraph, page_number: int, sentence_ends: list[int]
) -> list[_Paragraph]:
    """A paragraph as the real service may give it: without its role, or in pieces.

    `sentence_ends` counts the body paragraphs with a line that ends a
    sentence, across the document.
    """
    if paragraph.role == "pageFooter":
        pieces = (
            [_Paragraph([line], paragraph.role) for line in paragraph.lines]
            if page_number % _FOOTER_SPLIT_EVERY == 0
            else [paragraph]
        )
        if page_number % _FOOTER_NO_ROLE_EVERY == _FOOTER_NO_ROLE_REMAINDER:
            for piece in pieces:
                piece.role = None
        return pieces
    if paragraph.role is not None:
        return [paragraph]
    inner = paragraph.lines[:-1]
    for number, line in enumerate(inner):
        if line.text.endswith(":"):
            return _split_after(paragraph, number)
    for number, line in enumerate(inner):
        if line.text.endswith("."):
            sentence_ends[0] += 1
            if sentence_ends[0] % _SENTENCE_SPLIT_EVERY == 0:
                return _split_after(paragraph, number)
            break
    return [paragraph]


def analyze_pdf(pdf: bytes, roles: bool = True, tidy: bool = False) -> dict[str, Any]:
    """The `analyzeResult` of the layout model for a PDF, as the stand-in reads it.

    With `tidy` the paragraphs are the PDF's own and every footer has its
    role; without, some are as the real service gives them.
    """
    sentence_ends = [0]
    content: list[str] = []
    offset = 0
    pages: list[dict[str, Any]] = []
    paragraphs: list[dict[str, Any]] = []
    with pymupdf.open(stream=pdf, filetype="pdf") as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        for page_number, page in enumerate(document, start=1):
            page_start = offset
            lines_out: list[dict[str, Any]] = []
            words_out: list[dict[str, Any]] = []
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if block["type"] != 0:
                    continue
                read = _paragraphs_of(_lines_of(block))
                for whole in read:
                    whole.role = _role(whole, page_number, page.rect.height)
                # Only a block that is one paragraph is cut: cells stay cells.
                if not tidy and len(read) == 1:
                    read = _as_the_service(read[0], page_number, sentence_ends)
                for paragraph in read:
                    start = offset
                    for line in paragraph.lines:
                        lines_out.append(
                            {
                                "content": line.text,
                                "polygon": _polygon(line.box),
                                "spans": [{"offset": offset, "length": len(line.text)}],
                            }
                        )
                        words_out.extend(_words(line, offset))
                        # One character after every line: a space inside a
                        # paragraph, a line break after it.
                        offset += len(line.text) + 1
                    text = paragraph.text
                    content.append(text)
                    entry: dict[str, Any] = {
                        "spans": [{"offset": start, "length": len(text)}],
                        "boundingRegions": [
                            {
                                "pageNumber": page_number,
                                "polygon": _polygon(paragraph.box),
                            }
                        ],
                        "content": text,
                    }
                    if roles and paragraph.role is not None:
                        entry["role"] = paragraph.role
                    paragraphs.append(entry)
            pages.append(
                {
                    "pageNumber": page_number,
                    "angle": 0,
                    "width": round(page.rect.width / _POINTS_PER_INCH, 4),
                    "height": round(page.rect.height / _POINTS_PER_INCH, 4),
                    "unit": "inch",
                    "words": words_out,
                    "lines": lines_out,
                    "spans": [
                        {
                            "offset": page_start,
                            "length": max(offset - page_start - 1, 0),
                        }
                    ],
                }
            )
    return {
        "apiVersion": API_VERSION,
        "modelId": LAYOUT_MODEL,
        "stringIndexType": "textElements",
        "contentFormat": "text",
        "content": "\n".join(content),
        "pages": pages,
        "paragraphs": paragraphs,
        # The stand-in does not rebuild tables or the section tree: the cells
        # of a table are paragraphs, as they also are in the service's result.
        "tables": [],
        "sections": [],
    }


def _unread(page: pymupdf.Page) -> list[list[list[Any]]]:
    """What the stand-in answers for a picture nobody kept words for.

    It cannot read a picture, but it can tell paper from ink: a page that is
    one colour all over has no word, and a page with anything drawn on it
    (the handwritten note) is answered with one word that says so, where
    the real model would give the words themselves.
    """
    picture = page.get_pixmap(dpi=_INK_DPI, alpha=False)
    if picture.color_topusage()[0] >= _PAPER_SHARE:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        return []
    return [[[UNREAD_WORD, 0.0, 0.0, page.rect.width, page.rect.height]]]


def read_pdf(pdf: bytes) -> dict[str, Any]:
    """The `analyzeResult` of the read model for a PDF, as the stand-in reads it.

    Each page with its turn, its words and its lines in reading order, in
    inches; the document's content is its lines, one after the other. Words
    of a line are a space apart and lines a line break, as the service
    writes them.
    """
    content: list[str] = []
    offset = 0
    pages: list[dict[str, Any]] = []
    paragraphs: list[dict[str, Any]] = []
    with pymupdf.open(stream=pdf, filetype="pdf") as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        for page_number, page in enumerate(document, start=1):
            seen = kept_words(document, page) or read_words(page)
            if not seen["lines"] and kept_words(document, page) is None:
                seen = {**seen, "lines": _unread(page)}
            page_start = offset
            lines_out: list[dict[str, Any]] = []
            words_out: list[dict[str, Any]] = []
            for line in seen["lines"]:
                line_start = offset
                for text, *box in line:
                    words_out.append(
                        {
                            "content": text,
                            "polygon": _polygon((box[0], box[1], box[2], box[3])),
                            "confidence": 1.0,
                            "span": {"offset": offset, "length": len(text)},
                        }
                    )
                    offset += len(text) + 1
                text = " ".join(word[0] for word in line)
                whole = (
                    min(word[1] for word in line),
                    min(word[2] for word in line),
                    max(word[3] for word in line),
                    max(word[4] for word in line),
                )
                span = {"offset": line_start, "length": len(text)}
                lines_out.append(
                    {"content": text, "polygon": _polygon(whole), "spans": [span]}
                )
                paragraphs.append(
                    {
                        "spans": [span],
                        "boundingRegions": [
                            {"pageNumber": page_number, "polygon": _polygon(whole)}
                        ],
                        "content": text,
                    }
                )
                content.append(text)
            pages.append(
                {
                    "pageNumber": page_number,
                    "angle": seen["turn"],
                    "width": round(page.rect.width / _POINTS_PER_INCH, 4),
                    "height": round(page.rect.height / _POINTS_PER_INCH, 4),
                    "unit": "inch",
                    "words": words_out,
                    "lines": lines_out,
                    "spans": [
                        {
                            "offset": page_start,
                            "length": max(offset - page_start - 1, 0),
                        }
                    ],
                }
            )
    return {
        "apiVersion": API_VERSION,
        "modelId": READ_MODEL,
        "stringIndexType": "textElements",
        "contentFormat": "text",
        "content": "\n".join(content),
        "pages": pages,
        "paragraphs": paragraphs,
        "styles": [],
    }


def _words(line: _Line, offset: int) -> list[dict[str, Any]]:
    """The words of a line, each with a share of the line's box by its place in the text."""
    x0, y0, x1, y1 = line.box
    width = (x1 - x0) / max(len(line.text), 1)
    words: list[dict[str, Any]] = []
    for found in re.finditer(r"\S+", line.text):
        box = (x0 + found.start() * width, y0, x0 + found.end() * width, y1)
        words.append(
            {
                "content": found.group(),
                "polygon": _polygon(box),
                "confidence": 1.0,
                "span": {
                    "offset": offset + found.start(),
                    "length": len(found.group()),
                },
            }
        )
    return words


@dataclass
class Analysis:
    result_id: str
    status: str
    created: str
    result: dict[str, Any] | None = None


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code
    )


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _document(body: bytes, content_type: str) -> bytes | None:
    """The PDF a submit carries: its body, or the `base64Source` of a JSON body."""
    if content_type.split(";")[0].strip().lower() == PDF_CONTENT:
        return body or None
    try:
        source = json.loads(body).get("base64Source")
        return base64.b64decode(source, validate=True) or None
    except (AttributeError, TypeError, ValueError, binascii.Error):
        return None


@dataclass
class LayoutStandIn:
    """The stand-in's state and its HTTP app. Tests look at what it was asked."""

    mode: Mode = Mode.OK
    # Whether paragraphs carry a role. The real service may leave roles out.
    roles: bool = True
    # Whether the paragraphs are the PDF's own; by default some are as the
    # real service gives them (see `_as_the_service`).
    tidy: bool = False
    # What a throttled submit is told to wait; none by default, so that a
    # test or a local run fails at once instead of waiting.
    retry_after_seconds: int = 0
    analyses: dict[str, Analysis] = field(default_factory=dict)
    # How many analyses were submitted, accepted or not, and how many looks
    # at a result there were.
    submits: int = 0
    looks: int = 0
    # The last few PDFs the read model was sent, for tests: each is only ever
    # to be a redacted one.
    read: list[bytes] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def submit(self, pdf: bytes, model: str = LAYOUT_MODEL) -> Analysis:
        """Take a document for one of the two models and analyse it."""
        analysis = Analysis(uuid.uuid4().hex, "running", _now())
        if self.mode is Mode.FAIL:
            analysis.status = "failed"
        elif self.mode is not Mode.HANG:
            try:
                analysis.result = (
                    read_pdf(pdf)
                    if model == READ_MODEL
                    else analyze_pdf(pdf, self.roles, self.tidy)
                )
                analysis.status = "succeeded"
            except Exception:  # noqa: BLE001 - whatever went wrong, the analysis is reported as failed
                analysis.status = "failed"
        with self._lock:
            if model == READ_MODEL:
                self.read.append(pdf)
                # The same stand-in runs for as long as tools/dev.sh does.
                del self.read[:-READ_KEPT]
            self.analyses[analysis.result_id] = analysis
        return analysis

    def state(self, analysis: Analysis) -> dict[str, Any]:
        """The analysis as the service reports it."""
        state: dict[str, Any] = {
            "status": analysis.status,
            "createdDateTime": analysis.created,
            "lastUpdatedDateTime": _now(),
        }
        if analysis.status == "succeeded":
            state["analyzeResult"] = analysis.result
        elif analysis.status == "failed":
            state["error"] = {
                "code": "InternalServerError",
                "message": "The analysis failed.",
            }
        return state

    def app(self) -> FastAPI:
        """The HTTP app: the service's two routes for one analysis, by either model."""
        app = FastAPI(
            title="layout-stand-in", docs_url=None, redoc_url=None, openapi_url=None
        )

        @app.post(f"{MODELS_PATH}/{{model}}:analyze")
        async def submit(model: str, request: Request) -> Response:
            with self._lock:
                self.submits += 1
            if self.mode is Mode.THROTTLED:
                return JSONResponse(
                    {"error": {"code": "429", "message": "Rate limit reached."}},
                    status_code=429,
                    headers={"retry-after": str(self.retry_after_seconds)},
                )
            if self.mode is Mode.REJECT:
                return _error(400, "InvalidRequest", "The analysis was refused.")
            if model not in _MODELS:
                return _error(404, "ModelNotFound", "No such model.")
            pdf = _document(
                await request.body(), request.headers.get("content-type", "")
            )
            if pdf is None:
                return _error(
                    400,
                    "InvalidRequest",
                    "Send the document as the body or as base64Source.",
                )
            # Reading the PDF blocks, so it runs on a worker thread.
            analysis = await run_in_threadpool(self.submit, pdf, model)
            location = (
                f"{str(request.base_url).rstrip('/')}{MODELS_PATH}/{model}"
                f"/analyzeResults/{analysis.result_id}?{request.url.query}"
            )
            return Response(status_code=202, headers={"operation-location": location})

        # A plain `def` route: it runs on a worker thread.
        @app.get(f"{MODELS_PATH}/{{model}}/analyzeResults/{{result_id}}")
        def look_up(model: str, result_id: str) -> Response:
            with self._lock:
                self.looks += 1
            analysis = self.analyses.get(result_id)
            if analysis is None or model not in _MODELS:
                return _error(404, "NotFound", "No such analysis.")
            return JSONResponse(self.state(analysis))

        return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="synthdata.layout_standin", description=__doc__
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--mode",
        type=Mode,
        choices=list(Mode),
        default=Mode.OK,
        help="what happens to every analysis (default: ok)",
    )
    parser.add_argument(
        "--no-roles",
        action="store_true",
        help="leave the role out of every paragraph",
    )
    parser.add_argument(
        "--tidy",
        action="store_true",
        help="give the PDF's own paragraphs, and every footer its role",
    )
    parser.add_argument(
        "--classifier-mode",
        type=ClassifierMode,
        choices=list(ClassifierMode),
        default=ClassifierMode.OK,
        help="what the classifier routes do with every call (default: ok)",
    )
    args = parser.parse_args(argv)
    stand_in = LayoutStandIn(args.mode, roles=not args.no_roles, tidy=args.tidy)
    app = stand_in.app()
    # The classifier routes, on the same port. A build reads the training
    # pages from the blob emulator's built-in account, which is no secret;
    # building the client makes no network call.
    ClassifierStandIn(
        blob_container_reader(BlobServiceClient.from_connection_string(EMULATOR)),
        mode=args.classifier_mode,
    ).add_routes(app)
    # Loopback only: it is never reachable from another machine.
    uvicorn.run(app, host="127.0.0.1", port=args.port, server_header=False)


if __name__ == "__main__":
    main()
