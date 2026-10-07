"""A local stand-in for Document Intelligence's layout model (story 2.2).

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

As a process it also serves the routes of the classifier stand-in
(`synthdata.classifier_standin`, story 4.2): in Azure one Document
Intelligence account answers both, so locally one port does. A build reads
its training pages from the blob emulator of compose.yaml.

Run it: `uv run python -m synthdata.layout_standin` (see README, 'Run locally').
"""

import argparse
import base64
import binascii
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
from fastapi.responses import JSONResponse, Response

from synthdata.classifier_standin import ClassifierStandIn, blob_container_reader
from synthdata.classifier_standin import Mode as ClassifierMode
from synthdata.language_standin import EMULATOR

MODELS_PATH = "/documentintelligence/documentModels"
LAYOUT_MODEL = "prebuilt-layout"
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


def analyze_pdf(pdf: bytes, roles: bool = True) -> dict[str, Any]:
    """The `analyzeResult` of the layout model for a PDF, as the stand-in reads it."""
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
                for paragraph in _paragraphs_of(_lines_of(block)):
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
                    role = _role(paragraph, page_number, page.rect.height)
                    if roles and role is not None:
                        entry["role"] = role
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


@dataclass
class LayoutStandIn:
    """The stand-in's state and its HTTP app. Tests look at what it was asked."""

    mode: Mode = Mode.OK
    # Whether paragraphs carry a role. The real service may leave roles out.
    roles: bool = True
    # What a throttled submit is told to wait; none by default, so that a
    # test or a local run fails at once instead of waiting.
    retry_after_seconds: int = 0
    analyses: dict[str, Analysis] = field(default_factory=dict)
    # How many analyses were submitted, accepted or not, and how many looks
    # at a result there were.
    submits: int = 0
    looks: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def submit(self, body: dict[str, Any]) -> Analysis | None:
        """Take an analysis; None if the body does not carry a document's bytes."""
        source = body.get("base64Source")
        if not isinstance(source, str) or not source:
            return None
        try:
            pdf = base64.b64decode(source, validate=True)
        except (binascii.Error, ValueError):
            return None
        analysis = Analysis(uuid.uuid4().hex, "running", _now())
        if self.mode is Mode.FAIL:
            analysis.status = "failed"
        elif self.mode is not Mode.HANG:
            try:
                analysis.result = analyze_pdf(pdf, self.roles)
                analysis.status = "succeeded"
            except Exception:  # noqa: BLE001 - whatever went wrong, the analysis is reported as failed
                analysis.status = "failed"
        with self._lock:
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
        """The HTTP app: the service's two routes for one analysis."""
        app = FastAPI(
            title="layout-stand-in", docs_url=None, redoc_url=None, openapi_url=None
        )

        # Plain `def` routes: reading the PDF blocks, so it runs on a worker thread.
        @app.post(f"{MODELS_PATH}/{{model}}:analyze")
        def submit(model: str, request: Request, body: dict[str, Any]) -> Response:
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
            if model != LAYOUT_MODEL:
                return _error(404, "ModelNotFound", "No such model.")
            analysis = self.submit(body)
            if analysis is None:
                return _error(
                    400, "InvalidRequest", "Send the document as base64Source."
                )
            location = (
                f"{str(request.base_url).rstrip('/')}{MODELS_PATH}/{model}"
                f"/analyzeResults/{analysis.result_id}?{request.url.query}"
            )
            return Response(status_code=202, headers={"operation-location": location})

        @app.get(f"{MODELS_PATH}/{{model}}/analyzeResults/{{result_id}}")
        def look_up(model: str, result_id: str) -> Response:
            with self._lock:
                self.looks += 1
            analysis = self.analyses.get(result_id)
            if analysis is None or model != LAYOUT_MODEL:
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
        "--classifier-mode",
        type=ClassifierMode,
        choices=list(ClassifierMode),
        default=ClassifierMode.OK,
        help="what the classifier routes do with every call (default: ok)",
    )
    args = parser.parse_args(argv)
    stand_in = LayoutStandIn(args.mode, roles=not args.no_roles)
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
