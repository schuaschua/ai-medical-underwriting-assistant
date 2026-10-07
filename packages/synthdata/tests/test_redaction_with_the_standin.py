"""Story 1.7: `intake` as it really runs, with the Language stand-in where Azure AI Language would be.

Against a real PostgreSQL and the blob emulator: run
`docker compose up --detach --wait` first. No test here calls Azure. The
stand-in speaks the service's REST shape and reads and writes the blob
emulator. Each test has a database and blob containers of its own.

These tests are here and not in `services/intake/tests` because they name the
stand-in's package and read the answer key, which nothing under `services/`
may do (spine AD-17).
"""

import json
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from azure.storage.blob import ContainerClient
from fastapi.testclient import TestClient

from contracts.models.intake import (
    PageBoxes,
    PageList,
    PageText,
    RedactionResult,
)
from contracts.text import normalise
from intake.adapters.http.app import create_app
from intake.adapters.pdf import read_pages
from intake.settings import Settings
from synthdata.cases import CASES
from synthdata.language_standin import LanguageStandIn

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


@pytest.mark.parametrize("case", CASES[:2], ids=lambda case: case.case_id)
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


# --- The stand-in as a process -----------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
