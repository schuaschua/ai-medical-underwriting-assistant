"""Checks an upload as it arrives, so it can be passed on without being kept (AD-3)."""

from collections.abc import AsyncIterator

from fastapi import Request
from starlette.requests import ClientDisconnect

from contracts.errors import DomainError, ErrorCode
from contracts.operations import PDF
from contracts.upload import (
    PDF_HEADER,
    check_pdf_header,
    check_received_length,
    check_upload_size,
    parse_declared_length,
)
from web.adapters.http.errors import UNSUPPORTED_MEDIA_TYPE_MESSAGE

INTERRUPTED_MESSAGE = "The upload was interrupted."


def declared_length(request: Request) -> int | None:
    """The body size the request declares, checked against the upload rules."""
    # Anything but plain digits is refused with 422.
    size = parse_declared_length(request.headers.get("content-length"))
    if size is not None:
        # Refused before a byte of the body is read.
        check_upload_size(size)
    return size


async def _body(request: Request) -> AsyncIterator[bytes]:
    try:
        async for chunk in request.stream():
            yield chunk
    except ClientDisconnect:
        # The browser went away mid-upload; nobody is left to read the answer.
        raise DomainError(ErrorCode.VALIDATION_FAILED, INTERRUPTED_MESSAGE) from None


async def checked_pdf(request: Request, declared: int | None) -> AsyncIterator[bytes]:
    """Check the declared type and the file's first bytes; return the whole body as a stream.

    The size and type rules are the contracts package's, the same ones
    `intake` applies again. The returned stream raises `file_too_large` if the
    body runs past the limit, and `validation_failed` if it is not the
    `declared` length; either ends the call to `intake` before it stores
    anything.
    """
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != PDF:
        raise DomainError(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE, UNSUPPORTED_MEDIA_TYPE_MESSAGE
        )
    body = _body(request)
    # Enough of the start to tell a PDF by its content, not its name.
    start = b""
    async for chunk in body:
        start += chunk
        if len(start) >= len(PDF_HEADER):
            break
    check_upload_size(len(start))
    check_pdf_header(start)

    async def stream() -> AsyncIterator[bytes]:
        sent = len(start)
        yield start
        async for chunk in body:
            sent += len(chunk)
            check_upload_size(sent)
            if declared is not None and sent > declared:
                check_received_length(declared, sent)
            if chunk:
                yield chunk
        # A body cut short is not the file that was sent.
        check_received_length(declared, sent)

    return stream()
