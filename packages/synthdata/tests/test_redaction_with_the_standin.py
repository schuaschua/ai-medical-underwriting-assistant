"""Story 1.7: `intake` as it really runs, with stand-ins where its two Azure AI services would be.

The Language stand-in where Azure AI Language would be, and the Document
Intelligence stand-in where the read model would be. The Language stand-in
writes what the real service writes: a redacted PDF whose every page is one
picture, with no text in it but the masks' labels. So the page text these
tests see can only have come from the read model's reading of that PDF: a
service that read the PDF's text layer instead would find no word of the page.

Against a real PostgreSQL and the blob emulator: run
`docker compose up --detach --wait` first. No test here calls Azure. The
stand-ins speak the services' REST shapes; the Language one reads and writes
the blob emulator. Each test has a database and blob containers of its own.

These tests are here and not in `services/intake/tests` because they name the
stand-in's package and read the answer key, which nothing under `services/`
may do (spine AD-17).
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pymupdf
import pytest
from azure.storage.blob import ContainerClient
from fastapi.testclient import TestClient

from contracts.models.intake import (
    PageBoxes,
    PageList,
    PageText,
    RedactionResult,
)
from contracts.text import MASK_TOKEN_PATTERN, find_quote, normalise
from intake.adapters.http.app import create_app
from intake.settings import Settings
from synthdata.cases import CASES
from synthdata.language_standin import MASK_LABEL_OF, LanguageStandIn
from synthdata.layout_standin import LayoutStandIn

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


def service(
    settings: Settings, stand_in: LanguageStandIn, reader: LayoutStandIn
) -> TestClient:
    """The service as it really runs, with the stand-ins where Language and the read model would be."""
    app = create_app(
        settings,
        language=httpx.ASGITransport(app=stand_in.app()),
        read=httpx.ASGITransport(app=reader.app()),
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(
    migrated_database: Settings,
    stand_in: LanguageStandIn,
    layout_stand_in: LayoutStandIn,
) -> Iterator[TestClient]:
    with service(migrated_database, stand_in, layout_stand_in) as test_client:
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


def answer_key(case_key: str) -> dict[str, Any]:
    key: dict[str, Any] = json.loads((ANSWER_KEY_DIR / f"{case_key}.json").read_text())
    return key


def planted(case_key: str) -> list[str]:
    """Every planted identifier of a case, and every part of a planted name."""
    key = answer_key(case_key)
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
    layout_stand_in: LayoutStandIn,
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
    # Each mask is one token of the contracts' shape, named by its category:
    # no label of the picture (`PER5`) is left in the text.
    assert "[person]" in stored
    tokens = set(re.findall(MASK_TOKEN_PATTERN, "\n".join(texts)))
    assert tokens and tokens <= {f"[{name}]" for name in result.redaction_counts}
    labels = "|".join(MASK_LABEL_OF.values())
    assert not re.search(rf"\b(?:{labels})\d", "\n".join(texts))
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

    # Every quote of the answer key is found in the stored text of its
    # page, and the boxes of its words lie on that page.
    places = [
        place
        for fact in answer_key(case.case_id)["expected_facts"]
        for place in fact["places"]
    ]
    assert places
    for place in places:
        page_id = result.page_ids[place["page_number"] - 1]
        found = find_quote(texts[place["page_number"] - 1], place["quote"])
        assert found is not None, place
        quoted = PageBoxes.model_validate(
            client.get(
                f"/pages/{page_id}/boxes",
                params={"quote_start": found.start, "quote_end": found.end},
            ).json()
        )
        assert len(quoted.boxes) == len(place["quote"].split())
        for box in quoted.boxes:
            assert 0 <= box.x0 < box.x1 <= quoted.page_width
            assert 0 <= box.y0 < box.y1 <= quoted.page_height

    # The file route serves the redacted PDF: the document of record. As the
    # real service writes it, every page is one picture and the file's text
    # layer holds nothing but the masks' labels with their numbers.
    file = client.get(f"/documents/{document_id}/file")
    assert file.headers["content-type"] == "application/pdf"
    assert file.content != original
    with pymupdf.open(stream=file.content, filetype="pdf") as redacted:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        assert redacted.page_count == len(case.pages)
        layer = [str(page.get_text()).split() for page in redacted]
        assert all(len(page.get_images()) == 1 for page in redacted)
    assert any(layer)
    for word in (word for page in layer for word in page):
        assert re.fullmatch(rf"(?:{labels})\d+", word), word
    # So the page text was read from the pictures: the read model was sent
    # that redacted PDF, once, and never the original.
    assert layout_stand_in.read == [file.content]
    # What sits in `cases`: the files of record and nothing else. The result
    # file's text is searched for the planted values; the PDF is checked
    # through its text layer above and its read text, not byte by byte.
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
    # The service's own result file holds every value it found, in clear:
    # the stand-in's does too. No blob left in `cases` holds a planted
    # value, and the result file kept is names and counts only.
    for name, content in blobs.items():
        for value in planted(case.case_id):
            assert value.encode() not in content, (name, value)
    listed = json.loads(blobs[f"{case_id}/{document_id}.redaction-result.json"])
    assert listed["redaction_counts"] == result.redaction_counts
    assert listed["entities"]
    assert {key for entity in listed["entities"] for key in entity} == {
        "type",
        "entityId",
        "mask",
    }
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
