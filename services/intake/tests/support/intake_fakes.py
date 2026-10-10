"""In-memory stand-ins for `intake`'s stores, with switches that make them misbehave.

Unit tests use them in place of PostgreSQL and Blob Storage (coding-style
rule 23). They live with the tests, on pytest's `pythonpath`, so the service
package and its image hold no test code.
"""

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from intake.adapters.pdf import cut_page
from intake.domain.entities import (
    Case,
    Document,
    JobOutput,
    LayerWord,
    NewPage,
    PageRecord,
    PageSheet,
    ReadPage,
    ReadWord,
    Redaction,
    Word,
)
from intake.domain.ports import DocumentTextEmpty, DuplicateUpload, RedactionJobError
from intake.domain.redaction import RedactionPorts


class StoreDown(Exception):
    """What a failing stand-in raises. Its message must never reach a log or a body."""

    def __init__(self) -> None:
        super().__init__("secret-store-detail host=10.0.0.1")


async def _never() -> None:
    await asyncio.Event().wait()


@dataclass
class MemoryOriginalStore:
    blobs: dict[str, bytes] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    fail_put: bool = False
    fail_delete: bool = False
    # Never answers, like a storage call that hangs.
    hang_put: bool = False
    # Set once a call has started, so a test knows when to interrupt it.
    entered: asyncio.Event = field(default_factory=asyncio.Event)

    async def put(self, blob_name: str, content: bytes) -> None:
        self.entered.set()
        if self.fail_put:
            raise StoreDown
        if self.hang_put:
            await _never()
        self.blobs[blob_name] = content

    async def delete(self, blob_name: str) -> None:
        if self.fail_delete:
            raise StoreDown
        self.deleted.append(blob_name)
        self.blobs.pop(blob_name, None)


@dataclass
class MemoryCaseRepository:
    cases: list[Case] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    # The insert fails and nothing is stored.
    fail: bool = False
    # The rows are stored, then the call fails: a commit whose answer was lost.
    commit_then_fail: bool = False
    # The call never answers, before or after the rows are stored.
    hang: bool = False
    commit_then_hang: bool = False
    # The database cannot be asked whether a row exists.
    fail_exists: bool = False
    # The database cannot be asked for an earlier upload with the same key.
    fail_find: bool = False
    # One entry per lookup, first to last; "ok" or nothing means it answers.
    find_script: list[str] = field(default_factory=list)
    finds: int = 0
    # An upload with the same key is recorded by another call, just before
    # this one's insert: the race the unique rule settles.
    racing: tuple[Case, Document] | None = None
    entered: asyncio.Event = field(default_factory=asyncio.Event)

    async def add(self, case: Case, document: Document) -> None:
        self.entered.set()
        if self.fail:
            raise StoreDown
        if self.hang:
            await _never()
        if self.racing is not None:
            self.cases.append(self.racing[0])
            self.documents.append(self.racing[1])
            self.racing = None
        if document.idempotency_key is not None and any(
            item.idempotency_key == document.idempotency_key for item in self.documents
        ):
            # As the database's unique rule does: neither row is inserted.
            raise DuplicateUpload
        self.cases.append(case)
        self.documents.append(document)
        if self.commit_then_fail:
            raise StoreDown
        if self.commit_then_hang:
            await _never()

    async def find_by_idempotency_key(self, idempotency_key: str) -> Document | None:
        self.finds += 1
        # What this lookup does, if the test scripted it: "fail", "empty" or "hang".
        scripted = self.find_script.pop(0) if self.find_script else None
        if self.fail_find or scripted == "fail":
            raise StoreDown
        if scripted == "empty":
            return None
        if scripted == "hang":
            await _never()
        for item in self.documents:
            if item.idempotency_key == idempotency_key:
                return item
        return None

    async def document_exists(self, document_id: str) -> bool:
        if self.fail_exists:
            raise StoreDown
        return any(item.document_id == document_id for item in self.documents)


@dataclass
class MemorySchemaRevision:
    revision: str | None
    fail: bool = False

    async def current(self) -> str | None:
        if self.fail:
            raise StoreDown
        return self.revision


# --- Redaction and pages (story 1.7) -------------------------------------------

PNG_BYTES = b"\x89PNG\r\n\x1a\n-synthetic-thumbnail"
PDF_BYTES = b"%PDF-1.7 synthetic redacted file"
PICTURES_BYTES = b"%PDF-1.7 the original's pages as pictures"


# The label the service draws for each category.
MASK_LABELS = {"Person": "PER", "PhoneNumber": "PHN", "Email": "EML", "Address": "ADR"}


def result_file(*categories: str) -> bytes:
    """A redaction result file as the service lays it out: one entry per entity found."""
    return json.dumps(
        {
            "id": "1",
            "entities": [
                {
                    "type": category,
                    "entityId": str(number),
                    "mask": MASK_LABELS[category],
                    # `text` is what a real file holds: it must never be copied.
                    "text": "secret-found-value",
                }
                for number, category in enumerate(categories, start=1)
            ],
            "warnings": [],
        }
    ).encode()


def sheet(*layer_words: LayerWord) -> PageSheet:
    """One page of the redacted PDF as the splitter gives it: A4, a picture, its mask labels."""
    return PageSheet(
        width=595.0, height=842.0, thumbnail=PNG_BYTES, layer_words=layer_words
    )


# The mask of the first page: the label `PER` with its raised number, as the
# redacted PDF's text layer holds them, in PDF points.
MASK_ON_PAGE = (
    LayerWord("PER", 144.0, 72.0, 165.0, 84.0),
    LayerWord("1", 165.0, 69.0, 169.0, 76.0),
)


def read_page(page_number: int, *lines: tuple[ReadWord, ...]) -> ReadPage:
    """One page as the read model reads it: A4 in inches, not turned."""
    return ReadPage(
        page_number=page_number, angle=0.0, width=8.2639, height=11.6944, lines=lines
    )


def first_page_read() -> ReadPage:
    """`Patient` and the mask, then `Age 52 years`: the mask read as `PER` and `1`."""
    return read_page(
        1,
        (
            ReadWord("Patient", 1.0, 1.0, 1.8, 1.17),
            ReadWord("PER", 2.0, 1.0, 2.29, 1.17),
            ReadWord("1", 2.29, 0.96, 2.35, 1.06),
        ),
        (
            ReadWord("Age", 1.0, 1.4, 1.4, 1.57),
            ReadWord("52", 2.0, 1.4, 2.2, 1.57),
            ReadWord("years", 2.25, 1.4, 2.7, 1.57),
        ),
    )


@dataclass
class MemoryCaseFiles:
    """Stands in for the `cases` container."""

    blobs: dict[str, bytes] = field(default_factory=dict)
    content_types: dict[str, str] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)
    fail_read: bool = False
    fail_put: bool = False
    fail_delete_all: bool = False
    fail_delete: bool = False

    async def read(self, blob_name: str) -> bytes:
        self.reads.append(blob_name)
        if self.fail_read or blob_name not in self.blobs:
            raise StoreDown
        return self.blobs[blob_name]

    async def put(self, blob_name: str, content: bytes, content_type: str) -> None:
        if self.fail_put:
            raise StoreDown
        self.blobs[blob_name] = content
        self.content_types[blob_name] = content_type

    async def delete(self, blob_name: str) -> None:
        if self.fail_delete:
            raise StoreDown
        self.blobs.pop(blob_name, None)

    async def delete_all(self, prefix: str) -> None:
        if self.fail_delete_all:
            raise StoreDown
        for name in [name for name in self.blobs if name.startswith(prefix)]:
            del self.blobs[name]


@dataclass
class FakeLanguage:
    """Stands in for Azure AI Language: it writes its files to the fake `cases` container.

    It is handed only the original's name, as the real service is handed its
    address; `started` shows that this is the one place the name went.
    """

    files: MemoryCaseFiles
    # What the job finds, by category; one entry per item.
    found: tuple[str, ...] = ("Person", "Person", "PhoneNumber")
    # "reject": refused at submit. "fail": the job fails. "hang": it never ends.
    # "unreadable": the result file is not JSON. "no_files": it names no output.
    behaviour: str = "ok"
    # The redacted PDF the job writes.
    redacted: bytes = PDF_BYTES
    fail_cancel: bool = False
    # The cancel never answers.
    hang_cancel: bool = False
    started: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    waiting: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(
        self, original_blob_name: str, case_id: str, categories: Sequence[str]
    ) -> str:
        self.started.append((original_blob_name, case_id, tuple(categories)))
        if self.behaviour == "reject":
            raise RedactionJobError("submit_status_400")
        return f"job-{len(self.started)}"

    async def output(self, job_id: str, case_id: str) -> JobOutput:
        self.waiting.set()
        if self.behaviour == "hang":
            await _never()
        if self.behaviour == "fail":
            raise RedactionJobError("job_failed")
        if self.behaviour == "call_timeout":
            # One call of the adapter gave up, well before the deadline.
            raise TimeoutError
        if self.behaviour == "text_empty":
            # The service found no text in the document, and wrote no file.
            raise DocumentTextEmpty("job_document_text_empty")
        if self.behaviour == "no_files":
            raise RedactionJobError("job_output_incomplete")
        # Where the service puts its files: a folder of its own for the job.
        output = JobOutput(
            redacted_blob_name=f"{case_id}/{job_id}/out/original.pdf",
            result_blob_name=f"{case_id}/{job_id}/out/original.result.json",
        )
        self.files.blobs[output.redacted_blob_name] = self.redacted
        self.files.blobs[output.result_blob_name] = (
            b"not json" if self.behaviour == "unreadable" else result_file(*self.found)
        )
        return output

    async def cancel(self, job_id: str) -> None:
        if self.fail_cancel:
            raise StoreDown
        if self.hang_cancel:
            await _never()
        self.cancelled.append(job_id)


@dataclass
class FakeSplitter:
    """Stands in for the PDF adapter; it notes every file it was given."""

    pages: list[PageSheet] = field(
        default_factory=lambda: [sheet(*MASK_ON_PAGE), sheet()]
    )
    fail: bool = False
    given: list[bytes] = field(default_factory=list)
    # Every file it was asked to turn into pictures.
    drawn: list[bytes] = field(default_factory=list)

    async def split(self, pdf: bytes) -> list[PageSheet]:
        self.given.append(pdf)
        if self.fail:
            raise StoreDown
        return self.pages

    async def one_page(self, pdf: bytes, page_number: int) -> bytes | None:
        # The real cut: a test that asks for a page gives the fake a real PDF.
        return cut_page(pdf, page_number)

    async def pictures(self, pdf: bytes) -> bytes:
        self.drawn.append(pdf)
        return PICTURES_BYTES


@dataclass
class FakeOriginalPages:
    """The one read of an original; it notes every blob it was asked for."""

    store: MemoryOriginalStore
    reads: list[str] = field(default_factory=list)

    async def read(self, blob_name: str) -> bytes:
        self.reads.append(blob_name)
        return self.store.blobs[blob_name]


@dataclass
class FakeReader:
    """Stands in for Document Intelligence's read model; it notes every file it was given."""

    pages: list[ReadPage] = field(
        default_factory=lambda: [first_page_read(), read_page(2)]
    )
    # "fail": the analysis fails. "hang": it never ends.
    behaviour: str = "ok"
    given: list[bytes] = field(default_factory=list)

    async def read(self, pdf: bytes) -> list[ReadPage]:
        self.given.append(pdf)
        if self.behaviour == "hang":
            await _never()
        if self.behaviour == "fail":
            raise RedactionJobError("read_failed")
        return self.pages


@dataclass
class MemoryRedactionRepository:
    """Keeps to the repository's contract: one key row per case, a result stored once."""

    cases: MemoryCaseRepository
    redactions: dict[str, Redaction] = field(default_factory=dict)
    pages: dict[str, NewPage] = field(default_factory=dict)
    redacted_blobs: dict[str, str] = field(default_factory=dict)
    fail_finish: bool = False
    # The first `finish` fails, later ones work: a database that comes back.
    fail_finish_once: bool = False
    # `finish` waits for this before it writes, when set: a slow database.
    hold_finish: asyncio.Event | None = None
    finishing: asyncio.Event = field(default_factory=asyncio.Event)
    begins: int = 0

    async def document_of_case(self, case_id: str) -> Document | None:
        for document in self.cases.documents:
            if document.case_id == case_id:
                return document
        return None

    async def begin(
        self, case_id: str, document_id: str, started_at: datetime
    ) -> Redaction | None:
        self.begins += 1
        if case_id in self.redactions:
            return self.redactions[case_id]
        self.redactions[case_id] = Redaction(case_id, document_id, started_at, None)
        return None

    async def note_job(self, case_id: str, job_id: str) -> None:
        self.redactions[case_id] = replace(self.redactions[case_id], job_id=job_id)

    async def finish(
        self,
        case_id: str,
        result_json: str,
        pages: Sequence[NewPage],
        redacted_blob_name: str | None,
    ) -> str:
        if self.fail_finish:
            raise StoreDown
        if self.fail_finish_once:
            self.fail_finish_once = False
            raise StoreDown
        self.finishing.set()
        if self.hold_finish is not None:
            await self.hold_finish.wait()
        row = self.redactions[case_id]
        if row.result_json is not None:
            return row.result_json
        self.redactions[case_id] = replace(row, result_json=result_json)
        for page in pages:
            self.pages[page.record.page_id] = page
        if redacted_blob_name is not None:
            self.redacted_blobs[row.document_id] = redacted_blob_name
        return result_json

    async def pages_of_case(self, case_id: str) -> list[PageRecord] | None:
        if not any(case.case_id == case_id for case in self.cases.cases):
            return None
        return sorted(
            (
                page.record
                for page in self.pages.values()
                if page.record.case_id == case_id
            ),
            key=lambda record: record.page_number,
        )

    async def page(self, page_id: str) -> PageRecord | None:
        page = self.pages.get(page_id)
        return page.record if page is not None else None

    async def page_text(self, page_id: str) -> str | None:
        page = self.pages.get(page_id)
        return page.text if page is not None else None

    async def page_words(self, page_id: str) -> list[Word]:
        page = self.pages.get(page_id)
        return list(page.words) if page is not None else []

    async def redacted_blob_of(self, document_id: str) -> tuple[bool, str | None]:
        known = any(item.document_id == document_id for item in self.cases.documents)
        return known, self.redacted_blobs.get(document_id)


def memory_redaction(cases: MemoryCaseRepository | None = None) -> dict[str, Any]:
    """The redaction dependencies of the routes, all in memory: `Dependencies(**...)`."""
    files = MemoryCaseFiles()
    repository = MemoryRedactionRepository(cases or MemoryCaseRepository())
    return {
        "redaction": RedactionPorts(
            repository=repository,
            language=FakeLanguage(files),
            files=files,
            splitter=FakeSplitter(),
            reader=FakeReader(),
            originals=FakeOriginalPages(MemoryOriginalStore()),
        ),
        "pages": repository,
    }
