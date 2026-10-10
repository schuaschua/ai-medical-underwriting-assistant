"""What `classification` keeps and works with: the key row and the page it reads (AD-6, AD-13)."""

from dataclasses import dataclass
from datetime import datetime

from contracts.enums import ClassifierContender


@dataclass(frozen=True, slots=True)
class ClassificationKey:
    """The idempotency key of a classify command (AD-6)."""

    case_id: str
    page_id: str
    contender: ClassifierContender


@dataclass(frozen=True, slots=True)
class KeyRow:
    """The key row of one classification: one per key, whatever is repeated."""

    classification_id: str
    key: ClassificationKey
    started_at: datetime
    # The stored stage result, as JSON, once the classification has ended;
    # None while it runs.
    result_json: str | None

    @property
    def running(self) -> bool:
        return self.result_json is None


@dataclass(frozen=True, slots=True)
class PageContent:
    """One page as `intake` holds it after redaction: its text and its picture (AD-14)."""

    text: str
    # PNG: the page's thumbnail.
    image: bytes


@dataclass(frozen=True, slots=True)
class ClassifierAnswer:
    """What the Document Intelligence classifier answered for one document (story 4.2).

    As the service gave it: the document type it named, which the domain
    holds against the contracts' page types, and its own confidence in it.
    None where the answer named none.
    """

    doc_type: str | None
    confidence: float | None


@dataclass(frozen=True, slots=True)
class ListedPage:
    """One entry of the list of prepared training pages: a page the pipeline redacted."""

    # The blob's name in the `classifier-training` container.
    file: str
    page_type: str
    # The hex MD5 of the redacted file as the preparing tool wrote it.
    md5: str
    # The hex MD5 of the page's layout result (`<file>.ocr.json`), which the
    # real service needs beside every page; None where none was made.
    ocr_md5: str | None = None


@dataclass(frozen=True, slots=True)
class StoredBlob:
    """One blob the `classifier-training` container holds, beside the list itself."""

    name: str
    # The hex MD5 of its content; None for a blob the list does not name,
    # whose content nobody needs to know.
    md5: str | None
