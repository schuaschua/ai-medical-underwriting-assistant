"""Story 1.5: the upload rules both `web` and `intake` apply, and the new error codes."""

import pytest

from contracts.errors import DomainError, ErrorCode
from contracts.upload import (
    MAX_UPLOAD_BYTES,
    PDF_HEADER,
    check_pdf_header,
    check_received_length,
    check_upload,
    check_upload_size,
    parse_declared_length,
    parse_idempotency_key,
)

PDF = PDF_HEADER + b"1.7\n%%EOF\n"


def refused(call: object, *arguments: object) -> DomainError:
    with pytest.raises(DomainError) as raised:
        call(*arguments)  # type: ignore[operator]  # any of the checks of this module
    return raised.value


def test_story_1_5_the_size_limit_is_ten_megabytes_and_holds_to_the_byte() -> None:
    assert MAX_UPLOAD_BYTES == 10 * 1024 * 1024
    check_upload(PDF)
    check_upload(PDF_HEADER + b"x" * (MAX_UPLOAD_BYTES - len(PDF_HEADER)))
    check_upload_size(MAX_UPLOAD_BYTES)

    over = refused(
        check_upload, PDF_HEADER + b"x" * (MAX_UPLOAD_BYTES - len(PDF_HEADER) + 1)
    )
    assert over.code is ErrorCode.FILE_TOO_LARGE
    assert over.http_status == 413
    empty = refused(check_upload, b"")
    assert empty.code is ErrorCode.VALIDATION_FAILED
    assert empty.http_status == 422
    assert refused(check_upload_size, -1).code is ErrorCode.VALIDATION_FAILED


def test_story_1_5_content_without_the_pdf_header_is_415() -> None:
    for content in (b"just some text", b"PDF-1.7", b" %PDF-1.7", b"%pdf-1.7", b"%PD"):
        error = refused(check_upload, content)
        assert error.code is ErrorCode.UNSUPPORTED_FILE_TYPE
        assert error.http_status == 415
        assert refused(check_pdf_header, content).http_status == 415


def test_story_1_5_a_content_length_is_read_strictly_and_must_match_the_body() -> None:
    assert parse_declared_length(None) is None
    assert parse_declared_length("0") == 0
    assert parse_declared_length(" 42 ") == 42
    for value in ("", "-1", "+1", "1.0", "1e3", "ten", "1 2", "\u0663", "9" * 5000):
        error = refused(parse_declared_length, value)
        assert error.code is ErrorCode.VALIDATION_FAILED, value

    check_received_length(None, 7)
    check_received_length(7, 7)
    for received in (6, 8):
        error = refused(check_received_length, 7, received)
        assert error.code is ErrorCode.VALIDATION_FAILED


def test_story_1_6_an_idempotency_key_is_kept_as_sent_or_refused_without_echo() -> None:
    assert parse_idempotency_key(None) is None
    for key in (
        "3f2b8a52-6c1d-4c43-9d0e-0a8f5a1b2c3d",  # what a browser's randomUUID gives
        "a" * 16,
        "A_b-9" * 12 + "abcd",
    ):
        assert parse_idempotency_key(key) == key
    for key in ("", " ", "short", "a" * 65, "has space in the key 123", "é" * 20):
        error = refused(parse_idempotency_key, key)
        assert error.code is ErrorCode.VALIDATION_FAILED
        assert error.http_status == 422
        if key.strip():
            assert key not in error.message
