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


def original_blob_name(case_id: str, document_id: str) -> str:
    """The blob name of a document's original: always under its case's prefix."""
    return f"{case_id}/{document_id}.pdf"


def new_case_with_document(content: bytes, now: datetime) -> tuple[Case, Document]:
    """Build the records for one accepted upload, with new UUIDv7 ids."""
    case = Case(case_id=new_id(), created_at=now)
    document_id = new_id()
    document = Document(
        document_id=document_id,
        case_id=case.case_id,
        original_blob_name=original_blob_name(case.case_id, document_id),
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        created_at=now,
    )
    return case, document
