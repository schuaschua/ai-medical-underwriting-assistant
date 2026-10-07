"""Story 1.7: `intake` as it really runs, with the Language stand-in where Azure AI Language would be.

Against a real PostgreSQL and the blob emulator: run
`docker compose up --detach --wait` first. No test here calls Azure. The
stand-in speaks the service's REST shape and reads and writes the blob
emulator. Each test has a database and blob containers of its own.

These tests are here and not in `services/intake/tests` because they name the
stand-in's package and read the answer key, which nothing under `services/`
may do (spine AD-17).
"""

import asyncio
import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from azure.storage.blob import ContainerClient
from fastapi.testclient import TestClient

from contracts.errors import ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.intake import (
    PageBoxes,
    PageList,
    PageText,
    RedactionResult,
)
from contracts.text import normalise
from intake.adapters.db import build_database
from intake.adapters.http.app import create_app
from intake.adapters.pdf import read_pages
from intake.adapters.redaction_db import SqlRedactionRepository
from intake.domain.entities import NewPage, PageRecord, Word
from intake.settings import Settings
from synthdata.cases import CASES
from synthdata.language_standin import LanguageStandIn, Mode

pytestmark = pytest.mark.integration

PDF = {"Content-Type": "application/pdf"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = REPOSITORY_ROOT / "data" / "cases"
ANSWER_KEY_DIR = REPOSITORY_ROOT / "data" / "answer-key" / "cases"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TABLES = ("redaction", "page", "page_text", "word_box")


def query(settings: Settings, statement: str, *parameters: Any) -> list[Any]:
    with psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
    ) as connection:
        return connection.execute(statement, parameters).fetchall()


def row_counts(settings: Settings) -> dict[str, int]:
    return {
        table: query(settings, f"SELECT count(*) FROM intake.{table}")[0][0]  # noqa: S608 - a fixed list of table names
        for table in TABLES
    }


def service(settings: Settings, stand_in: LanguageStandIn) -> TestClient:
    """The service as it really runs, with the stand-in where Language would be."""
    app = create_app(settings, language=httpx.ASGITransport(app=stand_in.app()))
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(
    migrated_database: Settings, stand_in: LanguageStandIn
) -> Iterator[TestClient]:
    with service(migrated_database, stand_in) as test_client:
        yield test_client


def upload(client: TestClient, name: str = "case-001.pdf") -> tuple[str, str]:
    response = client.post(
        "/cases", content=(CASES_DIR / name).read_bytes(), headers=PDF
    )
    assert response.status_code == 201
    created = response.json()
    return created["case_id"], created["document_id"]


def redact(client: TestClient, case_id: str) -> RedactionResult:
    response = client.post(f"/cases/{case_id}/redaction", json={})
    assert response.status_code == 200, response.text
    return RedactionResult.model_validate(response.json())


def planted(case_key: str) -> list[str]:
    """Every planted identifier of a case, and every part of a planted name."""
    key = json.loads((ANSWER_KEY_DIR / f"{case_key}.json").read_text())
    values: list[str] = []
    for identifier in key["identifiers"]:
        values.append(identifier["value"])
        if identifier["category"] == "person_name":
            values.extend(identifier["value"].split())
    return values


# --- Redact a started case, and the planted identifiers -------------------------------


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_story_1_7_a_synthetic_case_is_redacted_and_no_planted_identifier_is_in_any_page_text(
    client: TestClient,
    migrated_database: Settings,
    cases_container: ContainerClient,
    originals: ContainerClient,
    stand_in: LanguageStandIn,
    case: Any,
) -> None:
    original = (CASES_DIR / case.file_name).read_bytes()
    case_id, document_id = upload(client, case.file_name)

    result = redact(client, case_id)

    assert (result.status.value, result.document_id) == ("done", document_id)
    assert len(result.page_ids) == len(case.pages)
    # Counts per category, by the service's names; they add up to what was masked.
    assert result.redaction_counts.keys() <= {
        "Person",
        "Address",
        "PhoneNumber",
        "Email",
        "USSocialSecurityNumber",
        "PolicyNumber",
    }
    assert result.redaction_counts["Person"] >= 2
    assert result.audit.detail == result.redaction_counts
    assert result.audit.actor == "intake:azure-ai-language"

    pages = PageList.model_validate(client.get(f"/cases/{case_id}/pages").json())
    assert [page.page_id for page in pages.pages] == result.page_ids
    assert [page.page_number for page in pages.pages] == list(
        range(1, len(case.pages) + 1)
    )
    texts = [
        PageText.model_validate(client.get(f"/pages/{page_id}/text").json()).text
        for page_id in result.page_ids
    ]
    # The redaction check of AD-17: after the contracts normalisation, no
    # planted identifier and no part of a planted name is in any page text.
    stored = normalise("\n".join(texts))
    for value in planted(case.case_id):
        assert normalise(value) not in stored, value
    assert "[person]" in stored
    # Dates (the date of birth too), ages and medical terms are still there.
    assert case.applicant.date_of_birth.isoformat() in stored
    assert f"{case.applicant_age} years" in stored
    assert "hba1c" in stored
    assert "synthetic test document" in stored

    # A box per word, with offsets into the stored text, and a PNG thumbnail.
    for page_id, text in zip(result.page_ids, texts, strict=True):
        boxes = PageBoxes.model_validate(client.get(f"/pages/{page_id}/boxes").json())
        assert [text[box.char_start : box.char_end] for box in boxes.boxes] == (
            text.split()
        )
        thumbnail = client.get(f"/pages/{page_id}/thumbnail")
        assert thumbnail.headers["content-type"] == "image/png"
        assert thumbnail.content.startswith(PNG_SIGNATURE)

    # The file route serves the redacted PDF: the document of record.
    file = client.get(f"/documents/{document_id}/file")
    assert file.headers["content-type"] == "application/pdf"
    assert file.content != original
    served = normalise("\n".join(page.text for page in read_pages(file.content, 100)))
    assert served == stored
    # What sits in `cases`: the files of record and nothing else. The result
    # file's text is searched for the planted values; the PDF is checked
    # through its extracted text above, not byte by byte.
    blobs = {
        name: cases_container.download_blob(name).readall()
        for name in cases_container.list_blob_names()
    }
    assert set(blobs) == {
        f"{case_id}/{document_id}.redacted.pdf",
        f"{case_id}/{document_id}.redaction-result.json",
        *(f"{case_id}/pages/{page_id}.png" for page_id in result.page_ids),
    }
    assert blobs[f"{case_id}/{document_id}.redacted.pdf"] == file.content
    listed = blobs[f"{case_id}/{document_id}.redaction-result.json"].decode()
    for value in planted(case.case_id):
        assert value not in listed
    # The original is where it was, unchanged; the job was told its address.
    (original_name,) = originals.list_blob_names()
    assert originals.download_blob(original_name).readall() == original
    (job,) = stand_in.submitted
    (document,) = job["analysisInput"]["documents"]
    assert document["source"]["location"] == f"{originals.url}/{original_name}"
    assert document["target"]["location"] == f"{cases_container.url}/{case_id}"
    # The rows: one key row, done; one text per page; a box per word.
    assert query(
        migrated_database,
        "SELECT status, redacted_blob_name, finished_at IS NOT NULL FROM intake.redaction",
    ) == [("done", f"{case_id}/{document_id}.redacted.pdf", True)]
    counts = row_counts(migrated_database)
    assert (counts["page"], counts["page_text"]) == (len(case.pages), len(case.pages))
    assert counts["word_box"] == sum(len(text.split()) for text in texts)


def test_story_1_7_a_repeat_after_the_end_calls_language_no_more_and_adds_no_rows(
    client: TestClient,
    migrated_database: Settings,
    cases_container: ContainerClient,
    stand_in: LanguageStandIn,
) -> None:
    case_id, _ = upload(client)
    first = client.post(f"/cases/{case_id}/redaction", json={})
    rows_before = row_counts(migrated_database)
    blobs_before = set(cases_container.list_blob_names())

    again = client.post(f"/cases/{case_id}/redaction", json={})

    assert (again.status_code, again.json()) == (200, first.json())
    assert len(stand_in.submitted) == 1
    assert row_counts(migrated_database) == rows_before
    assert set(cases_container.list_blob_names()) == blobs_before


def test_story_1_7_before_redaction_the_real_service_lists_no_pages_and_has_no_file(
    client: TestClient,
) -> None:
    case_id, document_id = upload(client)

    pages = client.get(f"/cases/{case_id}/pages")
    file = client.get(f"/documents/{document_id}/file")

    assert PageList.model_validate(pages.json()).pages == []
    assert file.status_code == 409
    assert ErrorBody.model_validate(file.json()).error.code is ErrorCode.NOT_REDACTED
    # Unknown ids are 404, each.
    for path in (
        f"/cases/{new_id()}/pages",
        f"/pages/{new_id()}/text",
        f"/pages/{new_id()}/boxes",
        f"/pages/{new_id()}/thumbnail",
        f"/documents/{new_id()}/file",
    ):
        assert client.get(path).status_code == 404, path


def test_story_1_7_an_offset_range_selects_the_boxes_of_the_words_it_touches(
    client: TestClient,
) -> None:
    case_id, _ = upload(client)
    page_id = redact(client, case_id).page_ids[0]
    text = client.get(f"/pages/{page_id}/text").json()["text"]
    quote = "Life Insurance Application Form"
    start = text.index(quote)

    response = client.get(
        f"/pages/{page_id}/boxes",
        params={"quote_start": start, "quote_end": start + len(quote)},
    )

    boxes = PageBoxes.model_validate(response.json()).boxes
    assert [text[box.char_start : box.char_end] for box in boxes] == quote.split()


# --- Failure -----------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [Mode.FAIL, Mode.REJECT, Mode.UNREADABLE])
def test_story_1_7_a_stand_in_told_to_fail_leaves_no_page_and_the_original_unread(
    client: TestClient,
    migrated_database: Settings,
    cases_container: ContainerClient,
    originals: ContainerClient,
    stand_in: LanguageStandIn,
    mode: Mode,
) -> None:
    stand_in.mode = mode
    case_id, document_id = upload(client)

    result = redact(client, case_id)

    assert (result.status.value, result.error_code) == (
        "failed",
        ErrorCode.REDACTION_FAILED,
    )
    assert result.page_ids == []
    assert (result.audit.action.value, result.audit.page_id) == ("stage.failed", None)
    # No page exists, in the database or in `cases`.
    counts = row_counts(migrated_database)
    assert (counts["page"], counts["page_text"], counts["word_box"]) == (0, 0, 0)
    assert list(cases_container.list_blob_names()) == []
    assert query(
        migrated_database, "SELECT status, redacted_blob_name FROM intake.redaction"
    ) == [("failed", None)]
    assert (
        PageList.model_validate(client.get(f"/cases/{case_id}/pages").json()).pages
        == []
    )
    # The file is not there, and the original is never served in its place.
    file = client.get(f"/documents/{document_id}/file")
    assert file.status_code == 409
    assert b"%PDF" not in file.content
    assert len(list(originals.list_blob_names())) == 1
    # A repeat is answered from the stored result.
    assert redact(client, case_id) == result
    assert len(stand_in.submitted) == (0 if mode is Mode.REJECT else 1)


def test_story_1_7_a_stand_in_that_hangs_is_cancelled_at_the_deadline(
    migrated_database: Settings,
    cases_container: ContainerClient,
    stand_in: LanguageStandIn,
) -> None:
    stand_in.mode = Mode.HANG
    # The stage's own deadline, made short: the stand-in never ends the job.
    settings = migrated_database.model_copy(update={"redaction_deadline_seconds": 0.5})

    with service(settings, stand_in) as client:
        case_id, _ = upload(client)
        result = redact(client, case_id)

    assert (result.status.value, result.error_code) == (
        "failed",
        ErrorCode.STAGE_TIMEOUT,
    )
    # The Language job was cancelled, and nothing was created.
    (job_id,) = stand_in.jobs
    assert stand_in.cancelled == [job_id]
    assert stand_in.jobs[job_id].status == "cancelled"
    assert row_counts(migrated_database) == {
        "redaction": 1,
        "page": 0,
        "page_text": 0,
        "word_box": 0,
    }
    assert list(cases_container.list_blob_names()) == []
    assert query(migrated_database, "SELECT status, job_id FROM intake.redaction") == [
        ("failed", job_id)
    ]


def test_story_1_7_a_repeat_while_the_job_runs_is_409_and_does_no_work(
    migrated_database: Settings, stand_in: LanguageStandIn
) -> None:
    stand_in.mode = Mode.HANG
    settings = migrated_database.model_copy(update={"redaction_deadline_seconds": 3.0})

    with service(settings, stand_in) as client, ThreadPoolExecutor(1) as pool:
        case_id, _ = upload(client)
        first = pool.submit(client.post, f"/cases/{case_id}/redaction", json={})
        # Until the first call has submitted its job.
        limit = time.monotonic() + 10
        while not stand_in.jobs and time.monotonic() < limit:
            time.sleep(0.02)  # waits for another thread's HTTP call, not for a clock
        again = client.post(f"/cases/{case_id}/redaction", json={})
        ended = first.result(30)

    assert again.status_code == 409
    assert ErrorBody.model_validate(again.json()).error.code is ErrorCode.IN_PROGRESS
    assert len(stand_in.submitted) == 1
    assert RedactionResult.model_validate(ended.json()).error_code is (
        ErrorCode.STAGE_TIMEOUT
    )


def test_story_1_7_a_key_row_left_running_by_a_dead_process_is_settled_as_failed(
    client: TestClient, migrated_database: Settings, stand_in: LanguageStandIn
) -> None:
    case_id, document_id = upload(client)
    # A process began the redaction an hour ago and died.
    with psycopg.connect(
        host=migrated_database.database_host,
        port=migrated_database.database_port,
        dbname=migrated_database.database_name,
        user=migrated_database.database_user,
        autocommit=True,
    ) as connection:
        connection.execute(
            "INSERT INTO intake.redaction (case_id, document_id, status, started_at) "
            "VALUES (%s, %s, 'running', now() - interval '1 hour')",
            (case_id, document_id),
        )

    result = redact(client, case_id)

    assert (result.status.value, result.error_code) == (
        "failed",
        ErrorCode.STAGE_TIMEOUT,
    )
    assert stand_in.submitted == []
    assert query(migrated_database, "SELECT status FROM intake.redaction") == [
        ("failed",)
    ]
    assert redact(client, case_id) == result


def test_story_1_7_redaction_of_a_case_intake_does_not_hold_is_404(
    client: TestClient, migrated_database: Settings, stand_in: LanguageStandIn
) -> None:
    response = client.post(f"/cases/{new_id()}/redaction", json={})

    assert response.status_code == 404
    assert ErrorBody.model_validate(response.json()).error.code is ErrorCode.NOT_FOUND
    assert row_counts(migrated_database)["redaction"] == 0
    assert stand_in.submitted == []


# --- The stand-in as a process -----------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_story_1_7_the_stand_in_runs_as_a_process_and_the_service_reaches_it_over_http(
    migrated_database: Settings,
) -> None:
    port = _free_port()
    process = subprocess.Popen(  # noqa: S603 - this interpreter and fixed arguments
        [sys.executable, "-m", "synthdata.language_standin", "--port", str(port)],
        cwd=REPOSITORY_ROOT,
    )
    try:
        limit = time.monotonic() + 30
        while time.monotonic() < limit:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(0.1)  # waits for a process to listen, not for a clock
        settings = migrated_database.model_copy(
            update={"language_endpoint": f"http://127.0.0.1:{port}"}
        )
        # No transport handed in: the real HTTP client, as in the local start.
        with TestClient(create_app(settings), raise_server_exceptions=False) as client:
            case_id, _ = upload(client)
            result = redact(client, case_id)
            text = client.get(f"/pages/{result.page_ids[0]}/text").json()["text"]
    finally:
        process.terminate()
        process.wait(timeout=10)

    assert result.status.value == "done"
    assert len(result.page_ids) == 3
    assert "Avery Testwood" not in text
    assert "[Person]" in text


# --- The settings reach the routes -----------------------------------------------------


def test_story_1_7_the_category_setting_reaches_the_language_job(
    migrated_database: Settings, stand_in: LanguageStandIn
) -> None:
    settings = migrated_database.model_copy(
        update={"redaction_categories": ["Email", "PhoneNumber"]}
    )

    with service(settings, stand_in) as client:
        case_id, _ = upload(client)
        result = redact(client, case_id)
        text = client.get(f"/pages/{result.page_ids[0]}/text").json()["text"]

    (job,) = stand_in.submitted
    assert job["tasks"][0]["parameters"]["piiCategories"] == ["Email", "PhoneNumber"]
    assert set(result.redaction_counts) == {"Email", "PhoneNumber"}
    # What was not asked for is still there.
    assert "Avery Testwood" in text


def test_story_1_7_the_stale_margin_setting_reaches_the_redaction(
    migrated_database: Settings, stand_in: LanguageStandIn
) -> None:
    def left_running(client: TestClient, minutes: int) -> str:
        case_id, document_id = upload(client)
        query(
            migrated_database,
            "INSERT INTO intake.redaction (case_id, document_id, status, started_at) "
            "VALUES (%s, %s, 'running', now() - make_interval(mins => %s)) "
            "RETURNING case_id::text",
            case_id,
            document_id,
            minutes,
        )
        return case_id

    # Ten minutes old: stale with the default margin, not with an hour's.
    patient = migrated_database.model_copy(
        update={"redaction_stale_margin_seconds": 3600.0}
    )
    with service(patient, stand_in) as client:
        waiting = client.post(f"/cases/{left_running(client, 10)}/redaction", json={})
    with service(migrated_database, stand_in) as client:
        settled = redact(client, left_running(client, 10))

    assert waiting.status_code == 409
    assert settled.error_code is ErrorCode.STAGE_TIMEOUT


# --- The repository's own guard, in PostgreSQL --------------------------------------------


def test_story_1_7_a_result_is_stored_only_while_the_key_row_is_running(
    client: TestClient, migrated_database: Settings
) -> None:
    case_id, document_id = upload(client)
    page_id = new_id()
    page = NewPage(
        record=PageRecord(
            page_id=page_id,
            case_id=case_id,
            document_id=document_id,
            page_number=1,
            width=595.0,
            height=842.0,
            thumbnail_blob_name=f"{case_id}/pages/{page_id}.png",
        ),
        text="A word",
        words=(Word(0, 1, 1.0, 1.0, 2.0, 2.0), Word(2, 6, 3.0, 1.0, 4.0, 2.0)),
    )

    async def scenario() -> tuple[Any, ...]:
        database = build_database(migrated_database)
        repository = SqlRedactionRepository(database)
        try:
            started = datetime.now(UTC)
            first = await repository.begin(case_id, document_id, started)
            await repository.note_job(case_id, "job-1")
            again = await repository.begin(case_id, document_id, started)
            failed = await repository.finish(case_id, '{"r": "failed"}', (), None)
            # An earlier attempt comes back late with a done result and a page.
            late = await repository.finish(
                case_id, '{"r": "done"}', [page], f"{case_id}/x.pdf"
            )
            return first, again, failed, late
        finally:
            await database.dispose()

    first, again, failed, late = asyncio.run(scenario())

    assert first is None
    # The row that was there already, with the job's id for a later cancel.
    assert (again.case_id, again.job_id, again.running) == (case_id, "job-1", True)
    # The failed result stands and is what the late call is answered with.
    assert (failed, late) == ('{"r": "failed"}', '{"r": "failed"}')
    assert query(
        migrated_database, "SELECT status, redacted_blob_name FROM intake.redaction"
    ) == [("failed", None)]
    assert row_counts(migrated_database) == {
        "redaction": 1,
        "page": 0,
        "page_text": 0,
        "word_box": 0,
    }


def test_story_1_7_a_nul_in_page_text_is_stored_as_a_replacement_character(
    client: TestClient, migrated_database: Settings
) -> None:
    case_id, document_id = upload(client)
    page_id = new_id()
    page = NewPage(
        record=PageRecord(
            page_id=page_id,
            case_id=case_id,
            document_id=document_id,
            page_number=1,
            width=595.0,
            height=842.0,
            thumbnail_blob_name=f"{case_id}/pages/{page_id}.png",
        ),
        text="A\x00B word",
        words=(Word(0, 3, 1.0, 1.0, 2.0, 2.0), Word(4, 8, 3.0, 1.0, 4.0, 2.0)),
    )

    async def scenario() -> None:
        database = build_database(migrated_database)
        repository = SqlRedactionRepository(database)
        try:
            await repository.begin(case_id, document_id, datetime.now(UTC))
            await repository.finish(case_id, "{}", [page], f"{case_id}/x.pdf")
        finally:
            await database.dispose()

    asyncio.run(scenario())

    text = client.get(f"/pages/{page_id}/text").json()["text"]
    boxes = client.get(f"/pages/{page_id}/boxes").json()["boxes"]
    # One character for one, so the words' offsets still hold.
    assert text == "A\ufffdB word"
    assert [text[box["char_start"] : box["char_end"]] for box in boxes] == [
        "A\ufffdB",
        "word",
    ]
