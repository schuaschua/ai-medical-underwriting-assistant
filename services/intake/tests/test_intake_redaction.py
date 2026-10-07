"""Story 1.7: redaction is the first stage, and only the redacted PDF is ever read.

Unit tests: the redaction command and the page reads, with fakes for Azure AI
Language, Blob Storage, the PDF reader and the database.
"""

import asyncio
import logging
from datetime import datetime, timedelta

import pymupdf
import pytest
from fastapi.testclient import TestClient
from intake_fakes import (
    PDF_BYTES,
    PNG_BYTES,
    FakeLanguage,
    FakeSplitter,
    MemoryCaseFiles,
    MemoryCaseRepository,
    MemoryOriginalStore,
    MemoryRedactionRepository,
)

from contracts.errors import NO_TRACE_ID, DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.intake import (
    PageBoxes,
    PageList,
    PageText,
    RedactionCommand,
    RedactionResult,
)
from intake.domain.entities import new_case_with_document
from intake.domain.ports import RedactionJobError
from intake.domain.redaction import (
    RedactionPorts,
    count_categories,
    redact_document,
)
from intake.settings import DEFAULT_REDACTION_CATEGORIES

PDF = {"Content-Type": "application/pdf"}
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = {"traceparent": f"00-{TRACE_ID}-b7ad6b7169203331-01"}
PAGE_TEXT = "Patient [Person]\nAge 52 years"
# What must never reach a log, a response or the audit record.
SECRETS = ("secret-found-value", "secret-store-detail", PAGE_TEXT, "[Person]")


def upload(client: TestClient, case_pdf: bytes) -> tuple[str, str]:
    created = client.post("/cases", content=case_pdf, headers=PDF).json()
    return created["case_id"], created["document_id"]


def redact(client: TestClient, case_id: str, **body: object) -> RedactionResult:
    response = client.post(f"/cases/{case_id}/redaction", json=body)
    assert response.status_code == 200, response.text
    return RedactionResult.model_validate(response.json())


def error_code(response_json: object) -> str:
    return ErrorBody.model_validate(response_json).error.code.value


def a_case(repository: MemoryCaseRepository, now: datetime) -> tuple[str, str]:
    """A recorded upload, put straight into the fake database."""
    case, document = new_case_with_document(b"%PDF-1.7 original", now)
    repository.cases.append(case)
    repository.documents.append(document)
    return case.case_id, document.document_id


# --- Redact a started case -------------------------------------------------------


def test_story_1_7_redaction_stores_the_redacted_pdf_and_the_pages_and_answers_with_ids_and_counts(
    client: TestClient,
    case_pdf: bytes,
    redactions: MemoryRedactionRepository,
    case_files: MemoryCaseFiles,
    fixed_now: datetime,
) -> None:
    case_id, document_id = upload(client, case_pdf)
    eval_run_id = new_id()

    response = client.post(
        f"/cases/{case_id}/redaction",
        json={"eval_run_id": eval_run_id},
        headers=TRACEPARENT,
    )

    assert response.status_code == 200
    result = RedactionResult.model_validate(response.json())
    assert (result.case_id, result.document_id) == (case_id, document_id)
    assert (result.status.value, result.error_code) == ("done", None)
    # The pages, in document order.
    stored = sorted(redactions.pages.values(), key=lambda p: p.record.page_number)
    assert result.page_ids == [page.record.page_id for page in stored]
    assert [page.record.page_number for page in stored] == [1, 2]
    assert stored[0].text == PAGE_TEXT
    assert len(stored[0].words) == 5
    # A count per category, by the service's category names (AD-8).
    assert result.redaction_counts == {"Person": 2, "PhoneNumber": 1}
    audit = result.audit
    assert audit.action.value == "document.redacted"
    assert (audit.actor_kind.value, audit.actor) == ("ai", "intake:azure-ai-language")
    assert audit.detail == result.redaction_counts
    assert (audit.page_id, audit.ref) == (None, document_id)
    assert (audit.occurred_at, audit.trace_id) == (fixed_now, TRACE_ID)
    assert audit.eval_run_id == eval_run_id
    # Ids and counts only: no page text and no found value (AD-6).
    for secret in SECRETS:
        assert secret not in response.text
    # Everything of the case sits under `<case_id>/` in `cases` (AD-21).
    assert set(case_files.blobs) == {
        f"{case_id}/{document_id}.redacted.pdf",
        f"{case_id}/{document_id}.redaction-result.json",
        *(f"{case_id}/pages/{page_id}.png" for page_id in result.page_ids),
    }
    assert case_files.blobs[f"{case_id}/{document_id}.redacted.pdf"] == PDF_BYTES
    assert case_files.content_types[f"{case_id}/{document_id}.redacted.pdf"] == (
        "application/pdf"
    )


def test_story_1_7_only_the_redaction_call_is_told_where_the_original_is(
    client: TestClient,
    case_pdf: bytes,
    store: MemoryOriginalStore,
    language: FakeLanguage,
    splitter: FakeSplitter,
    case_files: MemoryCaseFiles,
    repository: MemoryCaseRepository,
) -> None:
    case_id, _ = upload(client, case_pdf)

    redact(client, case_id)

    original_name = repository.documents[0].original_blob_name
    # The one use of the original: its place is handed to the redaction service.
    assert language.started == [(original_name, case_id, DEFAULT_REDACTION_CATEGORIES)]
    # The store of originals has no read at all, and nothing read the
    # original's blob from anywhere else.
    assert not hasattr(store, "read")
    assert original_name not in case_files.reads
    # The page split was given the redacted PDF and nothing else.
    assert splitter.given == [PDF_BYTES]
    assert splitter.given[0] != case_pdf


# --- Idempotency -------------------------------------------------------------------


def test_story_1_7_a_repeat_after_the_end_answers_the_stored_result_and_does_no_work(
    client: TestClient,
    case_pdf: bytes,
    language: FakeLanguage,
    splitter: FakeSplitter,
    redactions: MemoryRedactionRepository,
    case_files: MemoryCaseFiles,
) -> None:
    case_id, _ = upload(client, case_pdf)
    first = client.post(f"/cases/{case_id}/redaction", json={})
    pages_before, blobs_before = dict(redactions.pages), dict(case_files.blobs)

    again = client.post(f"/cases/{case_id}/redaction", json={})

    assert again.status_code == 200
    assert again.json() == first.json()
    # No second Language call, no second split, no new rows or files.
    assert len(language.started) == 1
    assert len(splitter.given) == 1
    assert redactions.pages == pages_before
    assert case_files.blobs == blobs_before


def test_story_1_7_a_repeat_while_running_is_409_in_progress(
    client: TestClient,
    case_pdf: bytes,
    ports: RedactionPorts,
    language: FakeLanguage,
    redactions: MemoryRedactionRepository,
    fixed_now: datetime,
) -> None:
    case_id, document_id = upload(client, case_pdf)
    # The key row of a redaction that is under way.
    asyncio.run(redactions.begin(case_id, document_id, fixed_now))

    response = client.post(f"/cases/{case_id}/redaction", json={})

    assert response.status_code == 409
    assert error_code(response.json()) == "in_progress"
    assert language.started == []
    assert redactions.pages == {}


# --- Reads -------------------------------------------------------------------------


def test_story_1_7_before_redaction_the_page_list_is_empty_and_the_file_is_not_redacted(
    client: TestClient, case_pdf: bytes
) -> None:
    case_id, document_id = upload(client, case_pdf)

    pages = client.get(f"/cases/{case_id}/pages")
    file = client.get(f"/documents/{document_id}/file")

    assert pages.status_code == 200
    assert PageList.model_validate(pages.json()).pages == []
    assert file.status_code == 409
    assert error_code(file.json()) == "not_redacted"
    assert b"%PDF" not in file.content


def test_story_1_7_after_redaction_pages_text_boxes_thumbnail_and_file_are_served(
    client: TestClient, case_pdf: bytes
) -> None:
    case_id, document_id = upload(client, case_pdf)
    result = redact(client, case_id)
    first, second = result.page_ids

    pages = PageList.model_validate(client.get(f"/cases/{case_id}/pages").json())
    text = PageText.model_validate(client.get(f"/pages/{first}/text").json())
    boxes = PageBoxes.model_validate(client.get(f"/pages/{first}/boxes").json())
    thumbnail = client.get(f"/pages/{first}/thumbnail")
    file = client.get(f"/documents/{document_id}/file")

    assert [(page.page_id, page.page_number) for page in pages.pages] == [
        (first, 1),
        (second, 2),
    ]
    assert {(page.case_id, page.document_id) for page in pages.pages} == {
        (case_id, document_id)
    }
    assert (text.page_number, text.text) == (1, PAGE_TEXT)
    # A box per word, with offsets into that text.
    assert [text.text[box.char_start : box.char_end] for box in boxes.boxes] == (
        PAGE_TEXT.split()
    )
    assert (boxes.page_width, boxes.page_height) == (595.0, 842.0)
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/png"
    assert thumbnail.content == PNG_BYTES
    assert file.status_code == 200
    assert file.headers["content-type"] == "application/pdf"
    # The redacted PDF, not the upload.
    assert file.content == PDF_BYTES
    assert file.content != case_pdf
    # A page with no words has an empty text and no boxes.
    assert client.get(f"/pages/{second}/text").json()["text"] == ""
    assert client.get(f"/pages/{second}/boxes").json()["boxes"] == []


def test_story_4_2_one_page_of_the_redacted_file_is_served_as_a_one_page_pdf(
    client: TestClient, case_pdf: bytes, language: FakeLanguage
) -> None:
    # The redacted file is a real PDF here: the synthetic case, whose second
    # page is the physician's statement.
    language.redacted = case_pdf
    case_id, document_id = upload(client, case_pdf)
    path = f"/documents/{document_id}/pages/2/file"

    before = client.get(path)

    assert before.status_code == 409
    assert error_code(before.json()) == "not_redacted"
    assert b"%PDF" not in before.content

    redact(client, case_id)
    served = client.get(path)

    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"
    with (
        pymupdf.open(stream=case_pdf, filetype="pdf") as whole,  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        pymupdf.open(stream=served.content, filetype="pdf") as single,  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    ):
        assert single.page_count == 1 < whole.page_count
        # That page and no other: its text, as the whole file has it.
        assert single[0].get_text() == whole[1].get_text()
        assert single[0].get_text() != whole[0].get_text()
        beyond = whole.page_count + 1
    # A page the file does not have, a document nobody holds, a number that
    # is no page number.
    for unknown in (
        f"/documents/{document_id}/pages/{beyond}/file",
        f"/documents/{new_id()}/pages/1/file",
    ):
        response = client.get(unknown)
        assert response.status_code == 404, unknown
        assert error_code(response.json()) == "not_found"
    assert client.get(f"/documents/{document_id}/pages/0/file").status_code == 422


# --- Failure -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("behaviour", "cancelled"), [("reject", []), ("fail", ["job-1"])]
)
def test_story_1_7_a_failed_redaction_creates_no_pages_and_answers_failed(
    client: TestClient,
    case_pdf: bytes,
    language: FakeLanguage,
    redactions: MemoryRedactionRepository,
    case_files: MemoryCaseFiles,
    splitter: FakeSplitter,
    behaviour: str,
    cancelled: list[str],
) -> None:
    case_id, document_id = upload(client, case_pdf)
    language.behaviour = behaviour

    result = redact(client, case_id)

    assert (result.status.value, result.error_code) == (
        "failed",
        ErrorCode.REDACTION_FAILED,
    )
    assert (result.page_ids, result.redaction_counts) == ([], {})
    audit = result.audit
    # A case-level `stage.failed` record: no page, no detail.
    assert (audit.action.value, audit.page_id, audit.detail) == (
        "stage.failed",
        None,
        None,
    )
    assert (audit.actor, audit.ref) == ("intake:azure-ai-language", document_id)
    assert language.cancelled == cancelled
    # No pages, nothing left in `cases`, and no fallback to the original.
    assert redactions.pages == {}
    assert case_files.blobs == {}
    assert splitter.given == []
    assert (
        PageList.model_validate(client.get(f"/cases/{case_id}/pages").json()).pages
        == []
    )
    file = client.get(f"/documents/{document_id}/file")
    assert (file.status_code, error_code(file.json())) == (409, "not_redacted")
    # A repeat is answered with the stored failure, and does no work.
    assert redact(client, case_id) == result
    assert len(language.started) == 1


def test_story_1_7_when_the_deadline_passes_the_job_is_cancelled_and_the_result_is_stage_timeout(
    ports: RedactionPorts,
    repository: MemoryCaseRepository,
    redactions: MemoryRedactionRepository,
    language: FakeLanguage,
    case_files: MemoryCaseFiles,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case_id, _ = a_case(repository, fixed_now)
    language.behaviour = "hang"

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            redact_document(
                case_id,
                RedactionCommand(),
                ports=ports,
                categories=DEFAULT_REDACTION_CATEGORIES,
                # The fake never answers, so the deadline is what ends the call.
                deadline_seconds=0.05,
                stale_margin_seconds=1,
                now=lambda: fixed_now,
            )
        )

    assert (result.status.value, result.error_code) == (
        "failed",
        ErrorCode.STAGE_TIMEOUT,
    )
    assert result.audit.trace_id == NO_TRACE_ID
    assert language.cancelled == ["job-1"]
    assert redactions.pages == {}
    assert case_files.blobs == {}
    assert redactions.redactions[case_id].result_json == result.model_dump_json()
    assert (
        f"redaction failed: case_id={case_id} error_code=stage_timeout "
        "reason=deadline job=cancelled stored=failed"
    ) in caplog.text


# --- A key row left behind -----------------------------------------------------------


def test_story_1_7_a_stale_running_row_is_settled_as_failed_once_it_is_past_its_deadline(
    ports: RedactionPorts,
    repository: MemoryCaseRepository,
    redactions: MemoryRedactionRepository,
    language: FakeLanguage,
    case_files: MemoryCaseFiles,
    fixed_now: datetime,
) -> None:
    case_id, document_id = a_case(repository, fixed_now)
    # A process began the redaction, submitted the job, wrote a file and died.
    asyncio.run(redactions.begin(case_id, document_id, fixed_now))
    asyncio.run(redactions.note_job(case_id, "job-of-the-dead-process"))
    case_files.blobs[f"{case_id}/left-behind.pdf"] = PDF_BYTES

    def at(seconds: float) -> RedactionResult:
        return asyncio.run(
            redact_document(
                case_id,
                RedactionCommand(),
                ports=ports,
                categories=DEFAULT_REDACTION_CATEGORIES,
                deadline_seconds=180,
                stale_margin_seconds=60,
                # The injected clock is moved; nothing waits.
                now=lambda: fixed_now + timedelta(seconds=seconds),
            )
        )

    # Within the deadline plus the margin it may still be running.
    with pytest.raises(DomainError) as refused:
        at(240)
    assert refused.value.code is ErrorCode.IN_PROGRESS
    assert language.cancelled == []

    result = at(241)

    assert (result.status.value, result.error_code) == (
        "failed",
        ErrorCode.STAGE_TIMEOUT,
    )
    # Its job is told to stop, its files go, and no work is started.
    assert language.cancelled == ["job-of-the-dead-process"]
    assert language.started == []
    assert case_files.blobs == {}
    assert redactions.pages == {}
    # From now on the case answers with that stored result.
    assert at(10_000) == result


# --- What is counted, and what is logged ---------------------------------------------


def test_story_1_7_counts_come_from_category_names_and_never_from_values() -> None:
    file = (
        b'{"results": {"documents": [{"entities": ['
        b'{"category": "Person", "text": "secret-found-value", "offset": 3},'
        b'{"category": "Person", "text": "secret-found-value"},'
        b'{"category": "Email"}]}]}, "entities": [{"category": "Address"}]}'
    )

    assert count_categories(file) == {"Address": 1, "Email": 1, "Person": 2}
    assert count_categories(b'{"results": {"documents": []}}') == {}

    for unreadable in (
        b"not json",
        b"[]",
        # A category that is not a name could be a found value: it is not copied.
        b'{"entities": [{"category": "Avery Testwood"}]}',
        b'{"entities": [{"category": 7}]}',
        b'{"entities": ["Person"]}',
    ):
        with pytest.raises(RedactionJobError) as raised:
            count_categories(unreadable)
        assert "Avery" not in raised.value.reason


def test_story_1_7_logs_carry_ids_codes_counts_and_timings_only(
    client: TestClient, case_pdf: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        case_id, document_id = upload(client, case_pdf)
        result = redact(client, case_id)
        client.get(f"/pages/{result.page_ids[0]}/text")

    assert (
        f"document redacted: case_id={case_id} document_id={document_id} pages=2 "
        "items=3 categories=2 duration_ms="
    ) in caplog.text
    for secret in SECRETS:
        assert secret not in caplog.text
    # No blob address either, in anything the service itself logs.
    own = [r.getMessage() for r in caplog.records if r.name.startswith("intake")]
    assert own
    assert not any("http" in message or "blob" in message for message in own)


# --- The failure path itself -------------------------------------------------------


def test_story_1_7_boxes_can_be_asked_for_an_offset_range(
    client: TestClient, case_pdf: bytes
) -> None:
    case_id, _ = upload(client, case_pdf)
    page_id = redact(client, case_id).page_ids[0]
    start = PAGE_TEXT.index("[Person]")

    # From inside `[Person]` to inside `Age`: both words are touched.
    response = client.get(
        f"/pages/{page_id}/boxes",
        params={"quote_start": start + 2, "quote_end": start + 10},
    )

    boxes = PageBoxes.model_validate(response.json()).boxes
    assert [PAGE_TEXT[box.char_start : box.char_end] for box in boxes] == [
        "[Person]",
        "Age",
    ]
    # Half a range, or one that runs backwards, is refused.
    for params in (
        {"quote_start": 3},
        {"quote_end": 3},
        {"quote_start": 5, "quote_end": 5},
        {"quote_start": -1, "quote_end": 5},
    ):
        refused = client.get(f"/pages/{page_id}/boxes", params=params)
        assert refused.status_code == 422, params
        assert error_code(refused.json()) == "validation_failed"


def test_story_1_7_an_unknown_case_page_or_document_is_404(
    client: TestClient, case_pdf: bytes
) -> None:
    upload(client, case_pdf)
    unknown = new_id()

    for path in (
        f"/cases/{unknown}/pages",
        f"/pages/{unknown}/text",
        f"/pages/{unknown}/boxes",
        f"/pages/{unknown}/thumbnail",
        f"/documents/{unknown}/file",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert error_code(response.json()) == "not_found"
    # An id that is no UUIDv7 is refused before anything is looked up.
    assert client.get("/pages/not-an-id/text").status_code == 422
