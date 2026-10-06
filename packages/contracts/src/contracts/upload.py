"""The upload rules, once, for the two services that check them (spine AD-3).

`web` checks an upload before passing it on and `intake` checks it again
before storing it; both call these functions so the rules cannot drift.
"""

import re

from contracts.errors import DomainError, ErrorCode

# AD-3: an upload is at most 10 MB, the redaction service's limit.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Every PDF file starts with these bytes.
PDF_HEADER = b"%PDF-"

EMPTY_MESSAGE = "The file is empty."
TOO_LARGE_MESSAGE = f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
BAD_LENGTH_MESSAGE = "The request's Content-Length is not valid."
LENGTH_MISMATCH_MESSAGE = "The file did not arrive whole."
# No real size needs more digits; a longer value is refused unread.
_MAX_LENGTH_DIGITS = 18
NOT_A_PDF_MESSAGE = "Only PDF files can be uploaded."

# The header that makes an upload safe to retry: the browser picks one key per
# upload attempt and sends the same key again when it retries, and `intake`
# answers a repeat with the case the first call created.
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
BAD_IDEMPOTENCY_KEY_MESSAGE = "The request's Idempotency-Key is not valid."
KEY_REUSED_MESSAGE = "This upload key was already used for a different file."
# A UUID fits, with or without hyphens; nothing that needs escaping does.
_IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")


def check_upload_size(size: int) -> None:
    """Raise `validation_failed` for an empty upload, `file_too_large` for one over the limit."""
    if size <= 0:
        raise DomainError(ErrorCode.VALIDATION_FAILED, EMPTY_MESSAGE)
    if size > MAX_UPLOAD_BYTES:
        raise DomainError(ErrorCode.FILE_TOO_LARGE, TOO_LARGE_MESSAGE)


def parse_declared_length(value: str | None) -> int | None:
    """Read a `Content-Length` header value; anything but plain digits is `validation_failed`."""
    if value is None:
        return None
    text = value.strip()
    if not (text.isascii() and text.isdigit() and len(text) <= _MAX_LENGTH_DIGITS):
        raise DomainError(ErrorCode.VALIDATION_FAILED, BAD_LENGTH_MESSAGE)
    return int(text)


def parse_idempotency_key(value: str | None) -> str | None:
    """Read an `Idempotency-Key` header value; a malformed one is `validation_failed`.

    A missing header is allowed: such an upload is simply not safe to retry.
    """
    if value is None:
        return None
    if _IDEMPOTENCY_KEY_RE.fullmatch(value) is None:
        # The value itself stays out of the message and the logs.
        raise DomainError(ErrorCode.VALIDATION_FAILED, BAD_IDEMPOTENCY_KEY_MESSAGE)
    return value


def check_received_length(declared: int | None, received: int) -> None:
    """Raise `validation_failed` when a body is not the size its request declared."""
    if declared is not None and declared != received:
        raise DomainError(ErrorCode.VALIDATION_FAILED, LENGTH_MISMATCH_MESSAGE)


def check_pdf_header(first_bytes: bytes) -> None:
    """Raise `unsupported_file_type` unless the content starts as a PDF does.

    The file's name and declared type are not evidence: only the content is.
    """
    if not first_bytes.startswith(PDF_HEADER):
        raise DomainError(ErrorCode.UNSUPPORTED_FILE_TYPE, NOT_A_PDF_MESSAGE)


def check_upload(content: bytes) -> None:
    """Apply every upload rule to a whole file: not empty, at most 10 MB, a PDF."""
    check_upload_size(len(content))
    check_pdf_header(content[: len(PDF_HEADER)])
