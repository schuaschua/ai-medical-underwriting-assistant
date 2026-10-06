"""Base model and shared field types for every contract."""

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


class ContractModel(BaseModel):
    """Base for every payload: unknown fields are rejected and instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be ISO 8601 with a UTC offset")
    return value


# Conventions: timestamps are ISO 8601 UTC. A naive or non-UTC value is rejected.
UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]

# Conventions: confidence and scores are floats from 0 to 1.
UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]
Confidence = UnitFloat
Score = UnitFloat

# Conventions: a loading or debit is an integer percentage (50 means +50%).
Percent = Annotated[int, Field(ge=0, strict=True)]

# At least one character that is not white space. The text is kept as given, never stripped.
NonEmptyStr = Annotated[str, StringConstraints(pattern=r"\S")]

# Every character Unicode treats as a line break.
_LINE_BREAKS = "\\r\\n\\x0b\\x0c\\x85\\u2028\\u2029"
OneLine = Annotated[
    str, StringConstraints(pattern=rf"^[^{_LINE_BREAKS}]*\S[^{_LINE_BREAKS}]*$")
]

# W3C trace context: 32 lower-case hex characters.
TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]

PageNumber = Annotated[int, Field(ge=1)]  # 1-based
Offset = Annotated[int, Field(ge=0)]  # a position in a page's text
Count = Annotated[int, Field(ge=0)]
Milliseconds = Annotated[int, Field(ge=0)]
