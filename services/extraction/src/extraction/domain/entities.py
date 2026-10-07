"""What `extraction` keeps and works with: the key row and the page it reads (AD-6, AD-14)."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class FactSetKey:
    """The idempotency key of an extract command (AD-6)."""

    case_id: str
    page_id: str


@dataclass(frozen=True, slots=True)
class KeyRow:
    """The key row of one page's fact set: one per key, whatever is repeated."""

    fact_set_id: str
    key: FactSetKey
    started_at: datetime
    # The stored stage result, as JSON, once the extraction has ended; None
    # while it runs.
    result_json: str | None

    @property
    def running(self) -> bool:
        return self.result_json is None


@dataclass(frozen=True, slots=True)
class ModelAnswer:
    """What the model gave for one page: its text, and why it stopped (AD-16)."""

    # As the model gave it; the domain parses it. Empty when it gave none.
    text: str
    # The model's finish reason as a short code (`stop`, `length`,
    # `content_filter`), `none` when it named none, `other` for anything
    # that is not a plain code. `length` means the answer was cut off at
    # the token limit.
    finish_reason: str


@dataclass(frozen=True, slots=True)
class PageReading:
    """One page as `intake` holds it after redaction: its number and its text (AD-14)."""

    # 1-based, as `intake` numbers the pages of the document.
    page_number: int
    # The one stored reading of the page. Quotes are checked against this
    # text and no other, and offsets index it.
    text: str
