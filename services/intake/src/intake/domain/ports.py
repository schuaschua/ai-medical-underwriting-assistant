"""What upload, redaction and page reads need from the outside world; adapters provide it."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from intake.domain.entities import (
    Case,
    Document,
    JobOutput,
    NewPage,
    PageRecord,
    PageSheet,
    ReadPage,
    Redaction,
    Word,
)


class OriginalStore(Protocol):
    """The `originals` container (AD-21). It has no read: nothing serves an original."""

    async def put(self, blob_name: str, content: bytes) -> None:
        """Store a new original; fail rather than overwrite an existing one."""
        ...

    async def delete(self, blob_name: str) -> None:
        """Remove an original that no record points to.

        A write of that blob that is still under way is waited for first, so
        the blob is gone when this returns.
        """
        ...


class DuplicateUpload(Exception):
    """The upload's idempotency key is already recorded: another call got there first."""


class CaseRepository(Protocol):
    async def add(self, case: Case, document: Document) -> None:
        """Insert both rows, or neither.

        Raises `DuplicateUpload`, with neither row inserted, if a document
        with the same idempotency key exists.
        """
        ...

    async def find_by_idempotency_key(self, idempotency_key: str) -> Document | None:
        """The document an earlier upload with this key recorded, if there is one."""
        ...

    async def document_exists(self, document_id: str) -> bool:
        """Whether the document's row is in the database."""
        ...


# --- Redaction (AD-21) ---------------------------------------------------------


class RedactionJobError(Exception):
    """The redaction job did not come to a usable end.

    `reason` is a short code for the log, never a message of the service's:
    such a message could hold what was found in the document.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DocumentTextEmpty(RedactionJobError):
    """The redaction service found no text in the document at all, and so redacted nothing.

    A blank document, or one whose text is only in its pictures (a scan,
    handwriting), which the service does not read.
    """


class OriginalPages(Protocol):
    """The one read of an original by `intake` itself (owner's decision of 2026-10-10).

    Used only for a document the redaction service said has no text: its
    pages are then turned into pictures and looked at by the read model, to
    tell a blank document from one with text in its pictures. Nothing else
    reads an original, and no route serves one (AD-21).
    """

    async def read(self, blob_name: str) -> bytes: ...


class RedactionService(Protocol):
    """Azure AI Language's document PII redaction, with the entity mask.

    The one reader of an original: it is handed the original's place in the
    `originals` container and writes what it makes to the `cases` container.
    """

    async def start(
        self, original_blob_name: str, case_id: str, categories: Sequence[str]
    ) -> str:
        """Submit the job; return its id. Raises `RedactionJobError` if it is refused."""
        ...

    async def output(self, job_id: str, case_id: str) -> JobOutput:
        """Wait for the job to end and say where its files are.

        Raises `DocumentTextEmpty` if the service says the document has no
        text. Raises `RedactionJobError` if the job fails, is cancelled or names no
        usable files, or files outside the case's own prefix `<case_id>/`. It waits as long as the job runs: the caller sets the deadline.
        """
        ...

    async def cancel(self, job_id: str) -> None:
        """Ask the service to stop the job; one that has ended is not an error."""
        ...


class CaseFiles(Protocol):
    """The `cases` container: redacted PDFs, result files and thumbnails (AD-21)."""

    async def read(self, blob_name: str) -> bytes: ...

    async def put(self, blob_name: str, content: bytes, content_type: str) -> None:
        """Store a file, in the place of any file of that name."""
        ...

    async def delete(self, blob_name: str) -> None:
        """Remove a file; one that is already gone is not an error."""
        ...

    async def delete_all(self, prefix: str) -> None:
        """Remove every file whose name starts with `prefix`."""
        ...


class PageSplitter(Protocol):
    async def split(self, pdf: bytes) -> list[PageSheet]:
        """A redacted PDF page by page, in document order: sizes, pictures, mask labels.

        Not the page text: the redacted PDF holds none (see `PageReader`).
        """
        ...

    async def one_page(self, pdf: bytes, page_number: int) -> bytes | None:
        """One page of a PDF as a PDF of its own; None when it has no such page."""
        ...

    async def pictures(self, pdf: bytes) -> bytes:
        """A PDF as a PDF of pictures: one picture per page, no text layer.

        The shape of the redacted file the redaction service writes.
        """
        ...


class PageReader(Protocol):
    """Document Intelligence's read model: the text of a redacted PDF, by OCR (AD-14).

    The redaction service writes each page as one picture, so the page text
    and the word places come from this reading. It is only ever given the
    redacted PDF (AD-21).
    """

    async def read(self, pdf: bytes) -> list[ReadPage]:
        """Read a whole PDF once; its pages in document order.

        Raises `RedactionJobError` if the service cannot be reached, the
        analysis fails or its answer is not usable. It waits as long as the
        analysis runs: the caller sets the deadline.
        """
        ...


class RedactionRepository(Protocol):
    """The key row of a redaction and what a finished one stores (AD-6)."""

    async def document_of_case(self, case_id: str) -> Document | None:
        """The case's document, or None if `intake` does not hold the case."""
        ...

    async def begin(
        self, case_id: str, document_id: str, started_at: datetime
    ) -> Redaction | None:
        """Insert the case's key row as running, before any work.

        Returns None when this call inserted it; otherwise the row that was
        there already, and nothing is changed.
        """
        ...

    async def note_job(self, case_id: str, job_id: str) -> None:
        """Keep the Language job's id on the key row, so it can be cancelled later."""
        ...

    async def finish(
        self,
        case_id: str,
        result_json: str,
        pages: Sequence[NewPage],
        redacted_blob_name: str | None,
    ) -> str:
        """Store the result, and the pages with their text and words, in one transaction.

        Nothing is written unless the key row is still running. Returns the
        result the row holds afterwards: this one, or the one stored before it.
        """
        ...


class PageRepository(Protocol):
    """Reads of what a finished redaction stored (AD-14)."""

    async def pages_of_case(self, case_id: str) -> list[PageRecord] | None:
        """The case's pages in order; empty until redaction is done; None for an unknown case."""
        ...

    async def page(self, page_id: str) -> PageRecord | None: ...

    async def page_text(self, page_id: str) -> str | None: ...

    async def page_words(self, page_id: str) -> list[Word]:
        """The page's words in reading order."""
        ...

    async def redacted_blob_of(self, document_id: str) -> tuple[bool, str | None]:
        """Whether the document is known, and where its redacted PDF is once there is one."""
        ...
