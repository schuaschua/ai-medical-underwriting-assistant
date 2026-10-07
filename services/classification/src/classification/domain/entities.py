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
