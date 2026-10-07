"""Story 1.4: the first synthetic case documents and their answer key."""

import json
import re
import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymupdf
import pytest
from pydantic import ValidationError

from contracts.enums import PageType
from contracts.rules import MEDICAL_PAGE_TYPES
from synthdata.__main__ import main
from synthdata.cases import CASES
from synthdata.generate import (
    ANSWER_KEY_FOLDER,
    CASES_FOLDER,
    RULE_TABLE_FILE,
    write_all,
)
from synthdata.model import (
    AnswerKeyEntry,
    CaseDefinition,
    IdentifierCategory,
    PageLayout,
    PageSpec,
)
from synthdata.render import render_case

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMITTED = REPO_ROOT / "data"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _squash(text: str) -> str:
    return " ".join(text.split())


def _open(pdf: Path) -> pymupdf.Document:
    return pymupdf.open(pdf)  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate it


def _page_texts(pdf: Path) -> list[str]:
    with _open(pdf) as document:
        return [_squash(page.get_text()) for page in document]


def _structure(pdf: Path) -> list[tuple[int, float, float]]:
    """Each page's rotation, width and height; the length is the page count."""
    with _open(pdf) as document:
        return [
            (page.rotation, round(page.rect.width, 2), round(page.rect.height, 2))
            for page in document
        ]


def _visible(folder: Path) -> list[str]:
    """File names in `folder`, without dot-files such as `.DS_Store`."""
    return sorted(
        path.name for path in folder.iterdir() if not path.name.startswith(".")
    )


def _entry(data_dir: Path, case_id: str) -> AnswerKeyEntry:
    path = data_dir / ANSWER_KEY_FOLDER / f"{case_id}.json"
    return AnswerKeyEntry.model_validate_json(path.read_text(encoding="utf-8"))


def _case(case_id: str) -> CaseDefinition:
    return next(case for case in CASES if case.case_id == case_id)


@pytest.fixture(scope="module")
def generated() -> Iterator[Path]:
    """A fresh run of the generator, kept inside the project's gitignored scratch folder."""
    # A folder per run, so that two runs at once do not write over each other.
    folder = REPO_ROOT / ".work" / f"pytest-synthdata-{uuid4().hex}"
    write_all(folder)
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


CASE_IDS = [case.case_id for case in CASES]


def test_story_1_4_generator_writes_a_pdf_and_an_answer_key_per_case(
    generated: Path,
) -> None:
    pdfs = sorted(path.name for path in (generated / CASES_FOLDER).iterdir())
    keys = sorted(path.name for path in (generated / ANSWER_KEY_FOLDER).iterdir())

    assert len(pdfs) >= 3
    assert pdfs == [f"{case_id}.pdf" for case_id in CASE_IDS]
    assert keys == [f"{case_id}.json" for case_id in CASE_IDS]


def test_story_1_4_command_line_writes_into_the_folder_it_is_given(
    generated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = generated / "from-command"

    main(["--data-dir", str(folder)])

    assert len(list((folder / CASES_FOLDER).glob("*.pdf"))) == len(CASES)
    assert len(list((folder / ANSWER_KEY_FOLDER).glob("*.json"))) == len(CASES)
    assert "case-001.pdf" in capsys.readouterr().out


def test_story_1_4_command_line_writes_into_data_under_the_current_folder(
    generated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = generated / "as-repository-root"
    folder.mkdir()
    monkeypatch.chdir(folder)

    main([])

    assert _visible(folder / "data" / "cases") == [f"{c}.pdf" for c in CASE_IDS]
    assert _visible(folder / "data" / "answer-key" / "cases") == [
        f"{c}.json" for c in CASE_IDS
    ]


def test_story_1_4_regenerating_gives_identical_answer_keys_and_pdf_text(
    generated: Path,
) -> None:
    again = generated / "again"
    write_all(again)

    for case_id in CASE_IDS:
        key = Path(ANSWER_KEY_FOLDER) / f"{case_id}.json"
        pdf = Path(CASES_FOLDER) / f"{case_id}.pdf"
        assert (again / key).read_bytes() == (generated / key).read_bytes()
        assert _page_texts(again / pdf) == _page_texts(generated / pdf)
        assert _structure(again / pdf) == _structure(generated / pdf)


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_story_1_4_committed_output_is_what_the_generator_writes(
    generated: Path, case_id: str
) -> None:
    # Acceptance: a fresh run leaves `data/answer-key/` unchanged in git.
    key = Path(ANSWER_KEY_FOLDER) / f"{case_id}.json"
    pdf = Path(CASES_FOLDER) / f"{case_id}.pdf"

    assert (COMMITTED / key).read_bytes() == (generated / key).read_bytes()
    assert _page_texts(COMMITTED / pdf) == _page_texts(generated / pdf)
    assert _structure(COMMITTED / pdf) == _structure(generated / pdf)


def test_story_1_4_committed_folders_hold_nothing_but_the_generated_cases() -> None:
    pdfs = _visible(COMMITTED / CASES_FOLDER)
    keys = _visible(COMMITTED / ANSWER_KEY_FOLDER)

    assert pdfs == [f"{case_id}.pdf" for case_id in CASE_IDS]
    assert keys == [f"{case_id}.json" for case_id in CASE_IDS]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_story_1_4_pdf_is_small_and_text_only(generated: Path, case_id: str) -> None:
    path = generated / CASES_FOLDER / f"{case_id}.pdf"
    entry = _entry(generated, case_id)

    assert path.stat().st_size <= MAX_UPLOAD_BYTES
    with _open(path) as document:
        assert 1 <= document.page_count <= 10
        assert document.page_count == len(entry.pages)
        for page, expected in zip(document, entry.pages, strict=True):
            assert page.get_images() == []
            if expected.layout is not PageLayout.BLANK:
                assert len(page.get_text("words")) > 20


def test_story_1_4_medical_only_case_has_only_the_three_medical_page_types(
    generated: Path,
) -> None:
    pages = _entry(generated, "case-001").pages

    assert {page.page_type for page in pages} == MEDICAL_PAGE_TYPES
    assert all(page.is_medical for page in pages)


def test_story_1_4_mixed_case_has_medical_pages_and_the_three_non_medical_ones(
    generated: Path,
) -> None:
    entry = _entry(generated, "case-002")
    texts = _page_texts(generated / CASES_FOLDER / entry.file_name)
    non_medical = {page.layout: page for page in entry.pages if not page.is_medical}

    assert {page.page_type for page in entry.pages if page.is_medical} == (
        MEDICAL_PAGE_TYPES
    )
    assert set(non_medical) == {
        PageLayout.INVOICE,
        PageLayout.PAYSLIP,
        PageLayout.UTILITY_BILL,
    }
    assert non_medical[PageLayout.INVOICE].page_type is PageType.INVOICE
    assert non_medical[PageLayout.PAYSLIP].page_type is PageType.OTHER
    assert non_medical[PageLayout.UTILITY_BILL].page_type is PageType.OTHER
    for layout, heading in (
        (PageLayout.INVOICE, "Invoice number"),
        (PageLayout.PAYSLIP, "Net pay"),
        (PageLayout.UTILITY_BILL, "Electricity Bill"),
    ):
        assert heading in texts[non_medical[layout].page_number - 1]


def test_story_1_4_edge_case_has_a_blank_page_and_a_page_rotated_90_degrees(
    generated: Path,
) -> None:
    entry = _entry(generated, "case-003")
    blank = next(page for page in entry.pages if page.layout is PageLayout.BLANK)
    rotated = next(page for page in entry.pages if page.rotation == 90)

    assert (blank.page_type, blank.is_medical) == (PageType.OTHER, False)
    with _open(generated / CASES_FOLDER / entry.file_name) as document:
        blank_page = document[blank.page_number - 1]
        assert blank_page.get_text().strip() == ""
        assert blank_page.get_drawings() == []
        rotated_page = document[rotated.page_number - 1]
        assert rotated_page.rotation == 90
        # Shown on its side: the upright A4 sheet is now wider than it is tall.
        assert rotated_page.rect.width > rotated_page.rect.height
        assert "Laboratory Report" in rotated_page.get_text()
        assert [page.rotation for page in document].count(0) == len(entry.pages) - 1


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_story_1_4_every_planted_identifier_is_on_its_listed_pages_and_no_others(
    generated: Path, case_id: str
) -> None:
    entry = _entry(generated, case_id)
    texts = _page_texts(generated / CASES_FOLDER / entry.file_name)

    assert {item.category for item in entry.identifiers} == set(IdentifierCategory)
    for item in entry.identifiers:
        found_on = tuple(
            number
            for number, text in enumerate(texts, start=1)
            if _squash(item.value) in text
        )
        assert found_on == item.pages, item.value


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_story_1_4_every_applicant_and_physician_identifier_is_in_the_answer_key(
    generated: Path, case_id: str
) -> None:
    case = _case(case_id)
    listed = {item.value for item in _entry(generated, case_id).identifiers}

    assert listed == {
        case.applicant.name,
        case.applicant.address,
        case.applicant.phone_number,
        case.applicant.email_address,
        case.applicant.identity_number,
        case.applicant.policy_number,
        case.physician.name,
        case.physician.clinic_address,
        case.physician.clinic_phone_number,
    }


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_story_1_4_identifiers_are_obviously_fictional(case_id: str) -> None:
    case = _case(case_id)

    assert case.applicant.email_address.endswith("@example.com")
    # 555-0100 to 555-0199 is the block reserved for fiction.
    for phone in (case.applicant.phone_number, case.physician.clinic_phone_number):
        assert re.fullmatch(r"\(\d{3}\) 555-01\d{2}", phone)
    # No identity number scheme issues area 000.
    assert case.applicant.identity_number.startswith("000-")
    assert case.applicant.policy_number.startswith("POL-SYN-")
    for address in (case.applicant.address, case.physician.clinic_address):
        assert ", ZZ 000" in address


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_story_1_4_medical_pages_keep_dates_ages_and_medical_terms(
    generated: Path, case_id: str
) -> None:
    case = _case(case_id)
    entry = _entry(generated, case_id)
    texts = _page_texts(generated / CASES_FOLDER / entry.file_name)
    planted = " ".join(item.value for item in entry.identifiers)
    date_of_birth = case.applicant.date_of_birth.isoformat()
    age = f"{case.applicant_age} years"

    for page in entry.pages:
        if not page.is_medical:
            continue
        text = texts[page.page_number - 1]
        assert date_of_birth in text
        assert age in text
        assert re.search(r"HbA1c|Blood pressure|Body mass index", text)
    assert date_of_birth not in planted
    assert age not in planted
    assert not ISO_DATE.search(planted)
    for term in ("HbA1c", "diabetes", "hypertension", "cholesterol", "mmHg"):
        assert term.lower() not in planted.lower()


def test_story_1_4_clinical_content_is_internally_consistent(generated: Path) -> None:
    diabetic, hypertensive = _case("case-001"), _case("case-002")
    diabetic_text = " ".join(_page_texts(generated / CASES_FOLDER / "case-001.pdf"))
    hypertensive_text = " ".join(_page_texts(generated / CASES_FOLDER / "case-002.pdf"))

    assert "Type 2 diabetes mellitus" in diabetic_text
    assert "HbA1c 7.4 % 4.0 to 5.6 H" in diabetic_text
    assert "Essential hypertension" in hypertensive_text
    assert "2026-08-10 152 96" in hypertensive_text
    assert "Current smoker" in hypertensive_text
    # 78 kg at 1.65 m and 98 kg at 1.78 m.
    assert str(diabetic.clinical.bmi) == "28.7"
    assert str(hypertensive.clinical.bmi) == "30.9"
    assert "28.7 kg/m2" in diabetic_text
    assert "30.9 kg/m2" in hypertensive_text
    assert diabetic.applicant_age == 52
    assert hypertensive.applicant_age == 57


def test_story_1_4_age_counts_whole_years_to_the_document_date() -> None:
    case = _case("case-001")
    day_before_birthday = case.model_copy(update={"document_date": date(2026, 3, 17)})
    birthday = case.model_copy(update={"document_date": date(2026, 3, 18)})

    assert day_before_birthday.applicant_age == 51
    assert birthday.applicant_age == 52


def _valid_entry(generated: Path) -> dict[str, Any]:
    path = generated / ANSWER_KEY_FOLDER / "case-002.json"
    entry: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return entry


def _drop_policy_number(entry: dict[str, Any]) -> None:
    entry["identifiers"] = [
        item for item in entry["identifiers"] if item["category"] != "policy_number"
    ]


def _repeat_first_identifier(entry: dict[str, Any]) -> None:
    entry["identifiers"].append(dict(entry["identifiers"][0]))


def _set(path: list[str | int], value: object) -> Any:
    def change(entry: dict[str, Any]) -> None:
        target: Any = entry
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = value

    return change


MALFORMED = {
    "unknown page type": _set(["pages", 0, "page_type"], "payslip"),
    "medical label against the mapping": _set(["pages", 0, "is_medical"], False),
    "page type against the layout": _set(["pages", 4, "page_type"], "invoice"),
    "page number missing": _set(["pages", 1, "page_number"], 7),
    "unknown identifier category": _set(["identifiers", 0, "category"], "birthday"),
    "identifier on no page": _set(["identifiers", 0, "pages"], []),
    "identifier on a page that does not exist": _set(["identifiers", 0, "pages"], [9]),
    "identifier pages out of order": _set(["identifiers", 0, "pages"], [2, 1]),
    "identifier page repeated": _set(["identifiers", 0, "pages"], [1, 1]),
    "identifier listed twice": _repeat_first_identifier,
    "identifier value with a line break": _set(
        ["identifiers", 0, "value"], "Jordan\nSamplewick"
    ),
    "empty identifier value": _set(["identifiers", 0, "value"], " "),
    "category with no identifier": _drop_policy_number,
    "file name of another case": _set(["file_name"], "case-001.pdf"),
    "rotation that is not a quarter turn": _set(["pages", 0, "rotation"], 45),
    "unknown field": _set(["expected_verdict"], "standard"),
}


def test_story_1_4_answer_key_entry_accepts_what_the_generator_wrote(
    generated: Path,
) -> None:
    assert AnswerKeyEntry.model_validate(_valid_entry(generated)).case_id == "case-002"


@pytest.mark.parametrize("fault", MALFORMED)
def test_story_1_4_answer_key_entry_rejects_a_malformed_entry(
    generated: Path, fault: str
) -> None:
    entry = _valid_entry(generated)
    MALFORMED[fault](entry)

    with pytest.raises(ValidationError):
        AnswerKeyEntry.model_validate(entry)


def test_story_1_4_case_definition_rejects_a_page_with_no_content_to_draw() -> None:
    case = _case("case-001")

    with pytest.raises(ValidationError):
        CaseDefinition.model_validate(
            {**case.model_dump(), "pages": [PageSpec(layout=PageLayout.INVOICE)]}
        )


def test_story_1_4_case_definition_rejects_two_identifiers_with_one_value() -> None:
    case = _case("case-001")
    physician = {**case.physician.model_dump(), "name": case.applicant.name}

    with pytest.raises(ValidationError):
        CaseDefinition.model_validate({**case.model_dump(), "physician": physician})


def test_story_1_4_renderer_refuses_a_table_too_long_for_the_page() -> None:
    case = _case("case-001")
    clinical = case.clinical.model_copy(
        update={"lab_results": case.clinical.lab_results * 8}
    )

    with pytest.raises(ValueError, match="page is full"):
        render_case(case.model_copy(update={"clinical": clinical}))


def test_story_1_4_renderer_refuses_a_value_too_wide_for_the_page() -> None:
    case = _case("case-001")
    applicant = case.applicant.model_copy(
        update={"address": f"{case.applicant.address}, " * 4}
    )

    with pytest.raises(ValueError, match="too wide"):
        render_case(case.model_copy(update={"applicant": applicant}))


def _files(folder: str) -> list[Path]:
    skipped = {".venv", "node_modules", "__pycache__"}
    return [
        path
        for path in (REPO_ROOT / folder).rglob("*")
        if path.is_file() and not skipped & set(path.parts)
    ]


# AD-17: what names the answers. The folder holds the per-case entries and, since
# story 2.1, the rule table; the rule table's file name is caught on its own too.
# Speaking of a rule table in general is not naming the file.
ANSWER_KEY_NAMES = re.compile(
    rb"answer[-_ ]?key|" + re.escape(Path(RULE_TABLE_FILE).name).encode(),
    re.IGNORECASE,
)


def _naming_the_answer_key(paths: list[Path]) -> list[str]:
    return [
        str(path.relative_to(REPO_ROOT))
        for path in paths
        if ANSWER_KEY_NAMES.search(path.read_bytes())
    ]


def test_story_1_4_no_service_or_contracts_code_refers_to_the_answer_key() -> None:
    # AD-17: only the eval runner and the generator may know where the answers are,
    # and the generator knows, so a service may not import it either.
    generator = re.compile(rb"\bsynthdata\b")
    contracts, services = _files("packages/contracts"), _files("services")

    # Guards against the scan passing because it looked at nothing.
    assert any(path.name == "enums.py" for path in contracts)
    assert _naming_the_answer_key(contracts + services) == []
    assert [
        str(path.relative_to(REPO_ROOT))
        for path in services
        if generator.search(path.read_bytes())
    ] == []


@pytest.mark.parametrize(
    "line",
    [
        f'RULES = "data/{RULE_TABLE_FILE}"',
        'RULES = DATA / "rule-table.json"',
        'RULES = json.loads((DATA / "answer-key" / name).read_text())',
        "from evaluation import answer_key",
    ],
)
def test_story_2_1_guard_catches_service_code_that_names_the_rule_table(
    line: str,
) -> None:
    # The same scan as the test above, over a stand-in for a file under `services/`.
    scratch = REPO_ROOT / ".work" / f"pytest-guard-{uuid4().hex}"
    folder = scratch / "services" / "retrieval"
    folder.mkdir(parents=True)
    offender, innocent = folder / "offender.py", folder / "innocent.py"
    offender.write_text(f"{line}\n", encoding="utf-8")
    # Naming the manual, or speaking of a rule table, is not naming the answers.
    innocent.write_text(
        'MANUAL = "underwriting-manual.pdf"\n# one chunk per row of the rule table\n',
        encoding="utf-8",
    )
    try:
        assert _naming_the_answer_key([offender, innocent]) == [
            str(offender.relative_to(REPO_ROOT))
        ]
    finally:
        shutil.rmtree(scratch)
