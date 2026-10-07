"""Story 1.4: the first synthetic case documents and their answer key."""

import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest

from contracts.enums import PageType
from contracts.rules import MEDICAL_PAGE_TYPES
from synthdata.cases import CASES
from synthdata.generate import (
    ANSWER_KEY_FOLDER,
    CASES_FOLDER,
    RULE_TABLE_FILE,
    write_all,
)
from synthdata.model import (
    AnswerKeyEntry,
    IdentifierCategory,
    PageLayout,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMITTED = REPO_ROOT / "data"


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


@pytest.fixture(scope="module")
def generated() -> Iterator[Path]:
    """A fresh run of the generator, kept inside the project's gitignored scratch folder."""
    # A folder per run, so that two runs at once do not write over each other.
    folder = REPO_ROOT / ".work" / f"pytest-synthdata-{uuid4().hex}"
    write_all(folder)
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


CASE_IDS = [case.case_id for case in CASES]


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


def test_story_1_4_committed_output_is_what_the_generator_writes(
    generated: Path,
) -> None:
    # Acceptance: a fresh run leaves `data/answer-key/` unchanged in git.
    for case_id in CASE_IDS:
        key = Path(ANSWER_KEY_FOLDER) / f"{case_id}.json"
        pdf = Path(CASES_FOLDER) / f"{case_id}.pdf"

        assert (COMMITTED / key).read_bytes() == (generated / key).read_bytes()
        assert _page_texts(COMMITTED / pdf) == _page_texts(generated / pdf)
        assert _structure(COMMITTED / pdf) == _structure(generated / pdf)


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


def test_story_1_4_every_planted_identifier_is_on_its_listed_pages_and_no_others(
    generated: Path,
) -> None:
    for case_id in CASE_IDS:
        entry = _entry(generated, case_id)
        texts = _page_texts(generated / CASES_FOLDER / entry.file_name)

        assert {item.category for item in entry.identifiers} == set(IdentifierCategory)
        for item in entry.identifiers:
            found_on = tuple(
                number
                for number, text in enumerate(texts, start=1)
                if _squash(item.value) in text
            )
            assert found_on == item.pages, (case_id, item.value)


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
        'RULES = DATA / "rule-table.json"',
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
