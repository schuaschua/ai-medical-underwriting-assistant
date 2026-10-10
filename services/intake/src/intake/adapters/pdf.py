"""PDF adapter: each page of a redacted PDF as a sheet (AD-14).

The page's size, a thumbnail and the words of the file's own text layer; and
one page of that file as a PDF of its own (story 4.2). It is only ever given
the redacted PDF (AD-21).

It does not give the page text. The redaction service writes every page as
one picture, and the only text it leaves in the file is the label of each
mask with its number (seen in the Azure session of 2026-10-10). The page
text is read from the pictures by the read model (`adapters/read.py`); the
layer's words say where the masks are and nothing else.
"""

import asyncio

import pymupdf
from opentelemetry import trace

from intake.adapters.telemetry import adapter_span
from intake.domain.entities import LayerWord, PageSheet
from intake.domain.ports import RedactionJobError
from intake.settings import APP_ID

tracer = trace.get_tracer(APP_ID)

# The limits when a caller names none; the service passes its settings.
MAX_PAGES = 200
THUMBNAIL_MAX_HEIGHT_PX = 1280
# Dots per inch of a page drawn as a picture: what the redaction service uses.
PICTURE_DPI = 200

# The fields of one entry of PyMuPDF's "words" extraction.
_X0, _Y0, _X1, _Y1, _TEXT = range(5)


def read_page(
    page: pymupdf.Page,
    thumbnail_width_px: int,
    thumbnail_max_height_px: int = THUMBNAIL_MAX_HEIGHT_PX,
) -> PageSheet:
    """One page as a sheet: its size as shown, its picture, its text layer's words.

    The words are given in the order the file holds them, a mask's label
    before its number, and where they are on the page as it is shown: for a
    rotated page they are turned with it.
    """
    shown = page.rect
    # PyMuPDF reports text where it sits on the unrotated sheet.
    to_shown = page.rotation_matrix
    words: list[LayerWord] = []
    for entry in page.get_text("words"):  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        text = str(entry[_TEXT])
        if not text:
            continue
        box = pymupdf.Rect(entry[_X0], entry[_Y0], entry[_X1], entry[_Y1]) * to_shown  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        box.normalize()
        words.append(LayerWord(text, box.x0, box.y0, box.x1, box.y1))
    # As wide as asked, unless that makes a very long page too high.
    zoom = min(thumbnail_width_px / shown.width, thumbnail_max_height_px / shown.height)
    picture = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    return PageSheet(
        width=shown.width,
        height=shown.height,
        thumbnail=picture.tobytes("png"),  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        layer_words=tuple(words),
    )


def read_pages(
    pdf: bytes,
    thumbnail_width_px: int,
    thumbnail_max_height_px: int = THUMBNAIL_MAX_HEIGHT_PX,
    max_pages: int = MAX_PAGES,
) -> list[PageSheet]:
    """Every page of a PDF as a sheet, in document order. A file that is not a PDF raises.

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


def cut_page(pdf: bytes, page_number: int) -> bytes | None:
    """One page of a PDF as a one-page PDF; None when the file has no such page.

    The page is copied as it is: its picture, its mask labels and its
    rotation. Nothing is drawn again, so what redaction masked stays masked
    and nothing it removed comes back.
    """
    with pymupdf.open(stream=pdf, filetype="pdf") as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        if not 1 <= page_number <= document.page_count:
            return None
        with pymupdf.open() as single:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            single.insert_pdf(
                document, from_page=page_number - 1, to_page=page_number - 1
            )
            # Unused objects are dropped: the other pages' content does not
            # travel with the one page.
            return bytes(single.tobytes(garbage=3, deflate=True))


def as_pictures(pdf: bytes, max_pages: int = MAX_PAGES) -> bytes:
    """A PDF as a PDF of pictures: each page drawn as one picture, as it is shown.

    The shape of the file the redaction service writes: no text layer, and a
    turned page comes out upright with its content turned. A document of
    more than `max_pages` pages is refused before any page is drawn.
    """
    with (
        pymupdf.open(stream=pdf, filetype="pdf") as document,  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        pymupdf.open() as pictures,  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    ):
        if document.page_count > max_pages:
            raise RedactionJobError("too_many_pages")
        for page in document:
            picture = page.get_pixmap(dpi=PICTURE_DPI, alpha=False)
            sheet = pictures.new_page(width=page.rect.width, height=page.rect.height)
            sheet.insert_image(sheet.rect, pixmap=picture)
        return bytes(pictures.tobytes(garbage=3, deflate=True))


class PdfPageSplitter:
    def __init__(
        self,
        thumbnail_width_px: int,
        thumbnail_max_height_px: int = THUMBNAIL_MAX_HEIGHT_PX,
        max_pages: int = MAX_PAGES,
    ) -> None:
        self._limits = (thumbnail_width_px, thumbnail_max_height_px, max_pages)

    async def split(self, pdf: bytes) -> list[PageSheet]:
        with adapter_span(tracer, "intake.pdf.split_pages"):
            # Rendering is CPU work: off the event loop.
            return await asyncio.to_thread(read_pages, pdf, *self._limits)

    async def one_page(self, pdf: bytes, page_number: int) -> bytes | None:
        with adapter_span(tracer, "intake.pdf.cut_page"):
            return await asyncio.to_thread(cut_page, pdf, page_number)

    async def pictures(self, pdf: bytes) -> bytes:
        with adapter_span(tracer, "intake.pdf.as_pictures"):
            return await asyncio.to_thread(as_pictures, pdf, self._limits[2])
