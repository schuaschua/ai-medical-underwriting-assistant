"""PDF adapter: the one reading of each page of a redacted PDF (AD-14).

The page text, a box for every word of it and a thumbnail, all from the same
file. It is only ever given the redacted PDF (AD-21).
"""

import asyncio

import pymupdf
from opentelemetry import trace

from intake.domain.entities import PageReading, Word
from intake.domain.ports import RedactionJobError
from intake.settings import APP_ID

tracer = trace.get_tracer(APP_ID)

# The limits when a caller names none; the service passes its settings.
MAX_PAGES = 200
THUMBNAIL_MAX_HEIGHT_PX = 1280

# The fields of one entry of PyMuPDF's "words" extraction.
_X0, _Y0, _X1, _Y1, _TEXT, _BLOCK, _LINE = range(7)


def _clamp(value: float, limit: float) -> float:
    return min(max(value, 0.0), limit)


def read_page(
    page: pymupdf.Page,
    thumbnail_width_px: int,
    thumbnail_max_height_px: int = THUMBNAIL_MAX_HEIGHT_PX,
) -> PageReading:
    """Read one page: words in reading order, joined into the page's text.

    Words of a line are joined by a space and lines by a line break, and each
    word keeps the offsets of its characters in that text. Boxes are given
    as the page is shown: for a rotated page they are turned with it.
    """
    shown = page.rect
    # PyMuPDF reports text where it sits on the unrotated sheet.
    to_shown = page.rotation_matrix
    parts: list[str] = []
    words: list[Word] = []
    length = 0
    line: tuple[int, int] | None = None
    for entry in page.get_text("words", sort=True):  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        text = str(entry[_TEXT])
        if not text:
            continue
        this_line = (int(entry[_BLOCK]), int(entry[_LINE]))
        if parts:
            separator = " " if this_line == line else "\n"
            parts.append(separator)
            length += len(separator)
        line = this_line
        box = pymupdf.Rect(entry[_X0], entry[_Y0], entry[_X1], entry[_Y1]) * to_shown  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        box.normalize()
        words.append(
            Word(
                char_start=length,
                char_end=length + len(text),
                x0=_clamp(box.x0, shown.width),
                y0=_clamp(box.y0, shown.height),
                x1=_clamp(box.x1, shown.width),
                y1=_clamp(box.y1, shown.height),
            )
        )
        parts.append(text)
        length += len(text)
    # As wide as asked, unless that makes a very long page too high.
    zoom = min(thumbnail_width_px / shown.width, thumbnail_max_height_px / shown.height)
    picture = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    return PageReading(
        text="".join(parts),
        width=shown.width,
        height=shown.height,
        words=tuple(words),
        thumbnail=picture.tobytes("png"),  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    )


def read_pages(
    pdf: bytes,
    thumbnail_width_px: int,
    thumbnail_max_height_px: int = THUMBNAIL_MAX_HEIGHT_PX,
    max_pages: int = MAX_PAGES,
) -> list[PageReading]:
    """Read every page of a PDF, in document order. A file that is not a PDF raises.

    A document of more than `max_pages` pages is refused before any page is
    rendered: the redaction then fails with that reason.
    """
    with pymupdf.open(stream=pdf, filetype="pdf") as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        if document.page_count > max_pages:
            raise RedactionJobError("too_many_pages")
        return [
            read_page(page, thumbnail_width_px, thumbnail_max_height_px)
            for page in document
        ]


class PdfPageSplitter:
    def __init__(
        self,
        thumbnail_width_px: int,
        thumbnail_max_height_px: int = THUMBNAIL_MAX_HEIGHT_PX,
        max_pages: int = MAX_PAGES,
    ) -> None:
        self._limits = (thumbnail_width_px, thumbnail_max_height_px, max_pages)

    async def split(self, pdf: bytes) -> list[PageReading]:
        with tracer.start_as_current_span("intake.pdf.split_pages"):
            # Rendering is CPU work: off the event loop.
            return await asyncio.to_thread(read_pages, pdf, *self._limits)
