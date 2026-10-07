"""The error catalogue, the error body and the domain error (spine Consistency Conventions)."""

import re
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from contracts.base import ContractModel, NonEmptyStr, TraceId


class ErrorCode(StrEnum):
    """Every `error.code` a service may return."""

    VALIDATION_FAILED = "validation_failed"
    # AD-9: the `X-Demo-Role` header is missing or not a demo role.
    INVALID_ROLE = "invalid_role"
    # AD-9: the role is known but the route is not open to it.
    ROLE_NOT_ALLOWED = "role_not_allowed"
    # AD-10: a human-reserved action came from a non-human actor.
    ACTOR_NOT_HUMAN = "actor_not_human"
    NOT_FOUND = "not_found"
    # The path exists but does not take the request's method.
    METHOD_NOT_ALLOWED = "method_not_allowed"
    # Upload rules (AD-3): the file is over 10 MB, or is not a PDF.
    FILE_TOO_LARGE = "file_too_large"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    # A request the framework or a proxy refused before any upload rule ran.
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    TOO_MANY_REQUESTS = "too_many_requests"
    # AD-6: the same stage command is still running.
    IN_PROGRESS = "in_progress"
    # Operations table: the document file is asked for before redaction is done.
    NOT_REDACTED = "not_redacted"
    # AD-10: the page is not awaiting that decision.
    NOT_AWAITING_DECISION = "not_awaiting_decision"
    # Operations table: a verdict run is asked for before every page is terminal.
    PAGES_NOT_TERMINAL = "pages_not_terminal"
    # AD-15: `read_rule` was given a rule id not seen earlier in the run.
    RULE_NOT_SEEN = "rule_not_seen"
    # AD-11: the ladder row is a real one, but this build cannot answer with
    # it yet. An unknown row is `validation_failed`.
    RETRIEVER_NOT_AVAILABLE = "retriever_not_available"
    # AD-6: the stage passed its own deadline.
    STAGE_TIMEOUT = "stage_timeout"
    STAGE_FAILED = "stage_failed"
    REDACTION_FAILED = "redaction_failed"
    # Conventions (AI output): a model response failed validation.
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    # AD-16: the model gateway gave up after its retries.
    MODEL_UNAVAILABLE = "model_unavailable"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    INTERNAL_ERROR = "internal_error"


# One mapping so every service's adapter answers a code with the same HTTP status.
HTTP_STATUS: Mapping[ErrorCode, int] = MappingProxyType(
    {
        ErrorCode.VALIDATION_FAILED: 422,
        ErrorCode.INVALID_ROLE: 400,
        ErrorCode.ROLE_NOT_ALLOWED: 403,
        ErrorCode.ACTOR_NOT_HUMAN: 403,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.METHOD_NOT_ALLOWED: 405,
        ErrorCode.FILE_TOO_LARGE: 413,
        ErrorCode.UNSUPPORTED_FILE_TYPE: 415,
        ErrorCode.PAYLOAD_TOO_LARGE: 413,
        ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
        ErrorCode.TOO_MANY_REQUESTS: 429,
        ErrorCode.IN_PROGRESS: 409,
        ErrorCode.NOT_REDACTED: 409,
        ErrorCode.NOT_AWAITING_DECISION: 409,
        ErrorCode.PAGES_NOT_TERMINAL: 409,
        ErrorCode.RULE_NOT_SEEN: 409,
        ErrorCode.RETRIEVER_NOT_AVAILABLE: 409,
        ErrorCode.STAGE_TIMEOUT: 504,
        ErrorCode.STAGE_FAILED: 500,
        ErrorCode.REDACTION_FAILED: 502,
        ErrorCode.INVALID_MODEL_OUTPUT: 502,
        ErrorCode.MODEL_UNAVAILABLE: 503,
        ErrorCode.UPSTREAM_UNAVAILABLE: 502,
        ErrorCode.INTERNAL_ERROR: 500,
    }
)


class ErrorDetail(ContractModel):
    code: ErrorCode
    # A plain sentence: no stack trace, SQL or path.
    message: NonEmptyStr
    trace_id: TraceId


class ErrorBody(ContractModel):
    """The one error shape: `{"error": {"code", "message", "trace_id"}}`."""

    error: ErrorDetail


_TRACE_ID_RE = re.compile(r"[0-9a-f]{32}")
# W3C trace context: the all-zero id means "no trace".
NO_TRACE_ID = "0" * 32


class DomainError(Exception):
    """Raised by domain code; adapters map it to an `ErrorBody` (coding-style rule 10)."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        if not message.strip():
            raise ValueError("a domain error needs a message")
        # Both arguments go to Exception so the error can be pickled and copied.
        super().__init__(code, message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return self.message

    @property
    def http_status(self) -> int:
        """The HTTP status every adapter uses for this error's code."""
        return HTTP_STATUS[self.code]

    def to_body(self, trace_id: str | None) -> ErrorBody:
        """Build the response body; a missing or malformed trace id becomes all zeros."""
        # Building the error response must never fail on its own input.
        if trace_id is None or _TRACE_ID_RE.fullmatch(trace_id) is None:
            trace_id = NO_TRACE_ID
        return ErrorBody(
            error=ErrorDetail(code=self.code, message=self.message, trace_id=trace_id)
        )
