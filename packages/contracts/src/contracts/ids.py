"""UUIDv7 ids: generation and the validated string type (spine Consistency Conventions)."""

import re
import secrets
import time
from typing import Annotated

from pydantic import StringConstraints

UUID7_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_UUID7_RE = re.compile(UUID7_PATTERN)

# A UUIDv7 in canonical lower-case form. One spelling only, so an id compares
# equal as a string in the database, the API and the SPA.
Uuid7Str = Annotated[str, StringConstraints(pattern=UUID7_PATTERN)]

CaseId = Uuid7Str
DocumentId = Uuid7Str
PageId = Uuid7Str
FactId = Uuid7Str
VerdictRunId = Uuid7Str
EvalRunId = Uuid7Str
# AD-8: the owning records an audit `ref` points at have ids of their own.
ClassificationId = Uuid7Str
FactSetId = Uuid7Str
DecisionId = Uuid7Str

_TIMESTAMP_BITS = 48
_RANDOM_BITS = 74


def new_id(unix_ms: int | None = None) -> str:
    """Return a new UUIDv7 string; `unix_ms` lets a caller inject the clock."""
    # Python 3.13 has no uuid.uuid7 (added in 3.14), so the RFC 9562 layout is built here.
    timestamp = time.time_ns() // 1_000_000 if unix_ms is None else unix_ms
    if not 0 <= timestamp < 1 << _TIMESTAMP_BITS:
        raise ValueError("unix_ms does not fit the 48-bit UUIDv7 timestamp")
    random_bits = secrets.randbits(_RANDOM_BITS)
    rand_a = random_bits >> 62
    rand_b = random_bits & ((1 << 62) - 1)
    value = (timestamp << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    text = f"{value:032x}"
    return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"


def is_uuid7(value: str) -> bool:
    """Tell whether `value` is a canonical lower-case UUIDv7 string."""
    return _UUID7_RE.fullmatch(value) is not None
