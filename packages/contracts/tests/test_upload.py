"""Story 1.5: the upload rules both `web` and `intake` apply, and the new error codes."""

import pytest

from contracts.enums import CaseStatus
from contracts.errors import HTTP_STATUS, DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.web import Health, Me, UploadedCase
from contracts.upload import (
    MAX_UPLOAD_BYTES,
    PDF_HEADER,
    TOO_LARGE_MESSAGE,
    check_pdf_header,
    check_received_length,
    check_upload,
    check_upload_size,
    parse_declared_length,
)

PDF = PDF_HEADER + b"1.7\n%%EOF\n"


def test_story_1_5_a_pdf_within_the_limit_is_accepted() -> None:
    check_upload(PDF)
    check_upload(PDF_HEADER + b"x" * (MAX_UPLOAD_BYTES - len(PDF_HEADER)))


def test_story_1_5_the_limit_is_ten_megabytes() -> None:
    assert MAX_UPLOAD_BYTES == 10 * 1024 * 1024


def test_story_1_5_an_empty_upload_is_422() -> None:
    with pytest.raises(DomainError) as raised:
        check_upload(b"")

    assert raised.value.code is ErrorCode.VALIDATION_FAILED
    assert raised.value.http_status == 422


def test_story_1_5_one_byte_over_the_limit_is_413() -> None:
    with pytest.raises(DomainError) as raised:
        check_upload(PDF_HEADER + b"x" * (MAX_UPLOAD_BYTES - len(PDF_HEADER) + 1))

    assert raised.value.code is ErrorCode.FILE_TOO_LARGE
    assert raised.value.http_status == 413
    check_upload_size(MAX_UPLOAD_BYTES)


@pytest.mark.parametrize(
    "content",
    [b"just some text", b"PDF-1.7", b" %PDF-1.7", b"%pdf-1.7", b"%PD", b"PK\x03\x04"],
)
def test_story_1_5_content_without_the_pdf_header_is_415(content: bytes) -> None:
    with pytest.raises(DomainError) as raised:
        check_upload(content)

    assert raised.value.code is ErrorCode.UNSUPPORTED_FILE_TYPE
    assert raised.value.http_status == 415
    with pytest.raises(DomainError):
        check_pdf_header(content)


def test_story_1_5_framework_statuses_have_codes_of_their_own() -> None:
    assert HTTP_STATUS[ErrorCode.METHOD_NOT_ALLOWED] == 405
    assert HTTP_STATUS[ErrorCode.PAYLOAD_TOO_LARGE] == 413
    assert HTTP_STATUS[ErrorCode.UNSUPPORTED_MEDIA_TYPE] == 415
    assert HTTP_STATUS[ErrorCode.TOO_MANY_REQUESTS] == 429


def test_story_1_5_web_payloads_live_in_the_contracts_package() -> None:
    case_id, document_id = new_id(), new_id()

    assert Health().model_dump() == {"status": "ok"}
    assert Me.model_validate({"role": "underwriter"}).role.value == "underwriter"
    uploaded = UploadedCase(
        case_id=case_id, document_id=document_id, status=CaseStatus.RUNNING
    )
    assert uploaded.model_dump(mode="json") == {
        "case_id": case_id,
        "document_id": document_id,
        "status": "running",
    }


@pytest.mark.parametrize("size", [-1, -(10**9)])
def test_story_1_5_a_negative_size_is_422(size: int) -> None:
    with pytest.raises(DomainError) as raised:
        check_upload_size(size)

    assert raised.value.code is ErrorCode.VALIDATION_FAILED


def test_story_1_5_the_too_large_message_names_the_limit_it_enforces() -> None:
    assert TOO_LARGE_MESSAGE == (
        f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
    )
    assert "10 MB" in TOO_LARGE_MESSAGE


@pytest.mark.parametrize(("value", "expected"), [(None, None), ("0", 0), (" 42 ", 42)])
def test_story_1_5_a_plain_content_length_is_read(
    value: str | None, expected: int | None
) -> None:
    assert parse_declared_length(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "-1", "+1", "1.0", "1e3", "ten", "1 2", "\u00b2", "\u0663", "9" * 5000],
)
def test_story_1_5_a_malformed_content_length_is_422(value: str) -> None:
    with pytest.raises(DomainError) as raised:
        parse_declared_length(value)

    assert raised.value.code is ErrorCode.VALIDATION_FAILED


def test_story_1_5_a_body_that_differs_from_its_declared_length_is_422() -> None:
    check_received_length(None, 7)
    check_received_length(7, 7)
    for received in (6, 8):
        with pytest.raises(DomainError) as raised:
            check_received_length(7, received)
        assert raised.value.code is ErrorCode.VALIDATION_FAILED
