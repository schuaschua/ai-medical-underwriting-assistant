"""Story 1.7: the local stand-in for Azure AI Language's document redaction.

Unit tests: what it masks and what it keeps, and that it stays a dev tool.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from contracts.text import normalise
from intake.adapters.pdf import read_pages
from intake.settings import DEFAULT_REDACTION_CATEGORIES
from synthdata.cases import CASES
from synthdata.language_standin import (
    CATEGORY_OF,
    Mode,
    main,
    planted_values,
    redact_pdf,
    result_file,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = REPOSITORY_ROOT / "data" / "cases"
ANSWER_KEY_DIR = REPOSITORY_ROOT / "data" / "answer-key" / "cases"


# --- The stand-in's masking --------------------------------------------------------------


def parts_of(identifier: dict[str, Any]) -> list[str]:
    """A planted value and, for a name, each part of it."""
    value = str(identifier["value"])
    if identifier["category"] == "person_name":
        return [value, *value.split()]
    return [value]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_story_1_7_the_stand_in_masks_planted_identifiers_and_keeps_dates_ages_and_medical_terms(
    case: Any,
) -> None:
    pdf = (CASES_DIR / case.file_name).read_bytes()
    key = json.loads((ANSWER_KEY_DIR / f"{case.case_id}.json").read_text())

    redacted, masked = redact_pdf(pdf, DEFAULT_REDACTION_CATEGORIES)

    before = normalise("\n".join(page.text for page in read_pages(pdf, 100)))
    after = normalise("\n".join(page.text for page in read_pages(redacted, 100)))
    for identifier in key["identifiers"]:
        for value in parts_of(identifier):
            assert normalise(value) in before
            assert normalise(value) not in after, value
    # Entity tokens stand where the values were.
    for token in ("[person]", "[address]", "[phonenumber]", "[email]"):
        assert token in after
    assert len(masked) >= len(key["identifiers"])
    # Dates (the date of birth too), ages and medical terms are kept.
    applicant = case.applicant
    assert applicant.date_of_birth.isoformat() in after
    assert f"{case.applicant_age} years" in after
    for kept in ("hba1c", "blood pressure", "synthetic test document"):
        assert (kept in after) == (kept in before)
    assert "hba1c" in after
    # The result file names categories and never a value.
    listed = result_file("1", masked).decode()
    for identifier in key["identifiers"]:
        assert identifier["value"] not in listed
    assert json.loads(listed)["results"]["documents"][0]["entities"][0].keys() == {
        "category",
        "confidenceScore",
    }


def test_story_1_7_the_stand_in_masks_only_the_categories_it_is_asked_for() -> None:
    pdf = (CASES_DIR / "case-001.pdf").read_bytes()

    redacted, masked = redact_pdf(pdf, ("Email",))

    text = "\n".join(page.text for page in read_pages(redacted, 100))
    assert set(masked) == {"Email"}
    assert "@example.com" not in text
    assert "Avery Testwood" in text
    # Whole names come before their parts, so a name is one item.
    values = [value for _, value in planted_values()]
    assert values.index("Avery Testwood") < values.index("Testwood")


def test_story_1_7_the_stand_in_reports_the_categories_the_service_is_asked_for() -> (
    None
):
    # Every category it can report is one the default setting asks for, so
    # nothing it masks locally goes unasked for in the settings.
    assert set(CATEGORY_OF.values()) == set(DEFAULT_REDACTION_CATEGORIES)


# --- The stand-in never ships --------------------------------------------------------


def test_story_1_7_the_stand_in_is_in_no_service_image_and_no_service_imports_it() -> (
    None
):
    for service in ("intake", "workflow", "web"):
        folder = REPOSITORY_ROOT / "services" / service
        assert "synthdata" not in (folder / "pyproject.toml").read_text()
        assert "synthdata" not in (folder / "Dockerfile").read_text()
        for source in (folder / "src").rglob("*.py"):
            assert not re.search(
                r"^\s*(from|import) synthdata", source.read_text(), re.MULTILINE
            )
    # The mode switch exists only in the stand-in.
    assert Mode.OK.value == "ok"


def test_story_1_7_the_stand_in_listens_on_loopback_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, Any] = {}
    monkeypatch.setattr(
        "synthdata.language_standin.uvicorn.run",
        lambda app, **options: started.update(options),
    )

    main(["--port", "5199", "--mode", "hang"])

    # Never reachable from another machine.
    assert (started["host"], started["port"]) == ("127.0.0.1", 5199)
