"""The case and its document, as `intake` records them at upload (spine, Core entities)."""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from contracts.ids import new_id


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Document:
    """The uploaded PDF. Its file name is not kept: it can hold a person's name."""

    document_id: str
    case_id: str
    # Where the original sits in the `originals` container (AD-21).
    original_blob_name: str
    size_bytes: int
    # Hex digest of the original, so a stored copy can be checked against the upload.
    sha256: str
    created_at: datetime
    # The key the browser sent with the upload, if any: a repeat of the upload
    # with the same key is answered with this document's case.
    idempotency_key: str | None = None


def content_hash(content: bytes) -> str:
    """The hex SHA-256 digest of a file's content."""
    return hashlib.sha256(content).hexdigest()


def original_blob_name(case_id: str, document_id: str) -> str:
    """The blob name of a document's original: always under its case's prefix."""
    return f"{case_id}/{document_id}.pdf"


def new_case_with_document(
    content: bytes, now: datetime, idempotency_key: str | None = None
) -> tuple[Case, Document]:
    """Build the records for one accepted upload, with new UUIDv7 ids."""
    case = Case(case_id=new_id(), created_at=now)
    document_id = new_id()
    document = Document(
        document_id=document_id,
        case_id=case.case_id,
        original_blob_name=original_blob_name(case.case_id, document_id),
        size_bytes=len(content),
        sha256=content_hash(content),
        created_at=now,
        idempotency_key=idempotency_key,
    )
    return case, document


# --- Redaction and pages (AD-21, AD-14) ----------------------------------------


def redacted_blob_name(case_id: str, document_id: str) -> str:
    """The blob name of the redacted PDF, the document of record (AD-21)."""
    return f"{case_id}/{document_id}.redacted.pdf"


def result_blob_name(case_id: str, document_id: str) -> str:
    """The blob name of the redaction service's result file."""
    return f"{case_id}/{document_id}.redaction-result.json"


def thumbnail_blob_name(case_id: str, page_id: str) -> str:
    """The blob name of a page's thumbnail."""
    return f"{case_id}/pages/{page_id}.png"


def case_prefix(case_id: str) -> str:
    """Everything a case has in the `cases` container sits under this prefix."""
    return f"{case_id}/"


@dataclass(frozen=True, slots=True)
class JobOutput:
    """Where a finished redaction job left its files, as names in the `cases` container."""

    redacted_blob_name: str
    result_blob_name: str


@dataclass(frozen=True, slots=True)
class Word:
    """One word of a page's text and where it sits on the page (AD-14)."""

    # Offsets into the page text: `text[char_start:char_end]` is the word.
    char_start: int
    char_end: int
    # PDF points, origin at the top-left corner of the page as it is shown.
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True, slots=True)
class LayerWord:
    """One word of the redacted PDF's own text layer, where it sits on the page.

    The redaction service writes every page as one picture; the only text it
    leaves in the file is the label of each mask and the number of what was
    masked there. PDF points, as the page is shown.
    """

    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True, slots=True)
class PageSheet:
    """One page of the redacted PDF as a sheet: its size, its picture, its mask labels."""

    width: float
    height: float
    # PNG.
    thumbnail: bytes
    # In the order the file holds them: a label, then its number.
    layer_words: tuple[LayerWord, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadWord:
    """One word as the read model gives it, in the read's own unit and page size."""

    content: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True, slots=True)
class ReadPage:
    """One page as the read model read it from the redacted PDF (OCR)."""

    # 1-based, in document order.
    page_number: int
    # How the page's content is turned, clockwise, in degrees.
    angle: float
    width: float
    height: float
    # In reading order, whatever the angle; each line's words in reading order.
    lines: tuple[tuple[ReadWord, ...], ...]


@dataclass(frozen=True, slots=True)
class MaskNames:
    """What the redaction's result file says of its masks: names, never found values."""

    # The category of each entity, by its mask's label and the number it carries.
    category_of_entity: dict[tuple[str, str], str]
    # The category a label stands for, where every entity with it has the same one.
    category_of_label: dict[str, str]
    labels: frozenset[str]


@dataclass(frozen=True, slots=True)
class PageReading:
    """One page's text and words, built from the read model's answer (AD-14)."""

    text: str
    words: tuple[Word, ...]
    # Counts for the log: mask tokens written, and masks no read word lay on.
    masks: int = 0
    masks_unread: int = 0


@dataclass(frozen=True, slots=True)
class PageRecord:
    """A page as `intake` stores it; its text and words are kept beside it."""

    page_id: str
    case_id: str
    document_id: str
    # 1-based, in document order.
    page_number: int
    width: float
    height: float
    thumbnail_blob_name: str


@dataclass(frozen=True, slots=True)
class NewPage:
    """A page about to be stored, with the one reading of it."""

    record: PageRecord
    text: str
    words: tuple[Word, ...]


@dataclass(frozen=True, slots=True)
class Redaction:
    """The key row of a case's redaction (AD-6): one per case, whatever is repeated."""

    case_id: str
    document_id: str
    started_at: datetime
    # The stored result, as JSON, once the redaction has ended; None while it runs.
    result_json: str | None
    # The Language job, once it was submitted.
    job_id: str | None = None

    @property
    def running(self) -> bool:
        return self.result_json is None
