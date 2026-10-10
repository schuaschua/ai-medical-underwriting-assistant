"""Read what redaction stored: pages, their text and words, thumbnails and the file (AD-14).

Every read here is of the redacted PDF or of something made from it: the
page text and the words are what the read model read from that PDF. No
function reads an original (AD-21).
"""

import logging
from collections.abc import Sequence

from contracts.errors import DomainError, ErrorCode
from contracts.models.intake import (
    Page,
    PageBoxes,
    PageBoxesQuery,
    PageList,
    PageText,
    WordBox,
)
from intake.domain.entities import PageRecord, Word
from intake.domain.ports import CaseFiles, PageRepository, PageSplitter

logger = logging.getLogger(__name__)

UNKNOWN_CASE_MESSAGE = "That case could not be found."
UNKNOWN_PAGE_MESSAGE = "That page could not be found."
UNKNOWN_DOCUMENT_MESSAGE = "That document could not be found."
NOT_REDACTED_MESSAGE = "The document has not been redacted."
FILE_UNAVAILABLE_MESSAGE = "The file could not be read. Please try again."


def words_in_range(
    words: Sequence[Word], quote_start: int | None, quote_end: int | None
) -> list[Word]:
    """The words a range of the page text touches; every word when no range is given.

    A word belongs to the range `[quote_start, quote_end)` if any of its
    characters does, so a quote that starts or ends inside a word still
    highlights that word.
    """
    if quote_start is None or quote_end is None:
        return list(words)
    return [
        word
        for word in words
        if word.char_start < quote_end and word.char_end > quote_start
    ]


async def _known_page(page_id: str, repository: PageRepository) -> PageRecord:
    page = await repository.page(page_id)
    if page is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_PAGE_MESSAGE)
    return page


async def list_pages(case_id: str, *, repository: PageRepository) -> PageList:
    """The case's pages in document order; empty until redaction is done."""
    pages = await repository.pages_of_case(case_id)
    if pages is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    return PageList(
        case_id=case_id,
        pages=[
            Page(
                page_id=page.page_id,
                case_id=page.case_id,
                document_id=page.document_id,
                page_number=page.page_number,
            )
            for page in pages
        ],
    )


async def read_page_text(page_id: str, *, repository: PageRepository) -> PageText:
    """The one stored text of a page, as the read model read it from the redacted PDF."""
    page = await _known_page(page_id, repository)
    text = await repository.page_text(page_id)
    if text is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_PAGE_MESSAGE)
    return PageText(page_id=page_id, page_number=page.page_number, text=text)


async def read_page_boxes(
    page_id: str, query: PageBoxesQuery, *, repository: PageRepository
) -> PageBoxes:
    """The page's word boxes: all of them, or those an offset range touches."""
    page = await _known_page(page_id, repository)
    words = words_in_range(
        await repository.page_words(page_id), query.quote_start, query.quote_end
    )
    return PageBoxes(
        page_id=page_id,
        page_number=page.page_number,
        page_width=page.width,
        page_height=page.height,
        boxes=[
            WordBox(
                char_start=word.char_start,
                char_end=word.char_end,
                x0=word.x0,
                y0=word.y0,
                x1=word.x1,
                y1=word.y1,
            )
            for word in words
        ],
    )


async def _read_file(files: CaseFiles, blob_name: str, what: str, of: str) -> bytes:
    try:
        return await files.read(blob_name)
    except Exception as error:
        # security rule 31: an id and the error's type; its message can hold a URL.
        logger.error(
            "file not read: what=%s id=%s type=%s", what, of, type(error).__qualname__
        )
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, FILE_UNAVAILABLE_MESSAGE
        ) from error


async def read_page_thumbnail(
    page_id: str, *, repository: PageRepository, files: CaseFiles
) -> bytes:
    """The page's thumbnail, a PNG made from the redacted PDF."""
    page = await _known_page(page_id, repository)
    return await _read_file(files, page.thumbnail_blob_name, "thumbnail", page_id)


async def read_document_file(
    document_id: str, *, repository: PageRepository, files: CaseFiles
) -> bytes:
    """The redacted PDF of a document; `not_redacted` until there is one.

    There is no other file to answer with: the original is never served (AD-21).
    """
    known, blob_name = await repository.redacted_blob_of(document_id)
    if not known:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_DOCUMENT_MESSAGE)
    if blob_name is None:
        raise DomainError(ErrorCode.NOT_REDACTED, NOT_REDACTED_MESSAGE)
    return await _read_file(files, blob_name, "document", document_id)


async def read_page_file(
    document_id: str,
    page_number: int,
    *,
    repository: PageRepository,
    files: CaseFiles,
    splitter: PageSplitter,
) -> bytes:
    """One page of a document's redacted PDF, as a one-page PDF (story 4.2).

    For the classifier that is asked one page at a time as a document. Like
    the whole file it is `not_redacted` until redaction is done, and it is
    cut from the redacted PDF and from nothing else (AD-21).
    """
    whole = await read_document_file(document_id, repository=repository, files=files)
    try:
        page = await splitter.one_page(whole, page_number)
    except Exception as error:
        # security rule 31: ids and the error's type, never its message.
        logger.error(
            "file not read: what=page_file id=%s page_number=%d type=%s",
            document_id,
            page_number,
            type(error).__qualname__,
        )
        raise DomainError(
            ErrorCode.UPSTREAM_UNAVAILABLE, FILE_UNAVAILABLE_MESSAGE
        ) from error
    if page is None:
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_PAGE_MESSAGE)
    return page
