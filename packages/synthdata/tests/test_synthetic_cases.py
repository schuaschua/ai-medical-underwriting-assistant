"""Stories 1.4, 3.1 and 4.1: the synthetic cases, their answer key, the scored page set and the training set."""

import hashlib
import re
import shutil
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymupdf
import pytest

from contracts.enums import PageType, SystemReason, Verdict
from contracts.query import build_fact_query
from contracts.rules import MEDICAL_PAGE_TYPES, is_medical
from synthdata.cases import CASES
from synthdata.generate import (
    ANSWER_KEY_FOLDER,
    CASES_FOLDER,
    PAGE_SET_FILE,
    RULE_TABLE_FILE,
    TRAINING_FOLDER,
    TRAINING_LIST_FILE,
    answer_key_for,
    check_against_pages,
    check_coverage,
    check_disjoint,
    extracted_page_texts,
    write_all,
)
from synthdata.manual_model import RuleTable
from synthdata.model import (
    LAYOUTS_WITHOUT_TEXT,
    MIN_PAGES_PER_KIND,
    MIN_TRAINING_PAGES_PER_TYPE,
    NON_MEDICAL_LAYOUTS,
    AnswerKeyEntry,
    FactKind,
    FactPlace,
    IdentifierCategory,
    Intent,
    PageKind,
    PageLayout,
    PageSet,
    TrainingSet,
)
from synthdata.render import render_case, render_page

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


def _pictures(pdf: Path) -> list[str]:
    """The SHA-256 of every picture in the file, in page order."""
    with _open(pdf) as document:
        return [
            hashlib.sha256(document.extract_image(image[0])["image"]).hexdigest()
            for page in document
            for image in page.get_images()
        ]


def _picture_sizes(pdf: Path) -> list[tuple[int, int]]:
    """The width and height of every picture in the file, in page order.

    A picture's bytes may differ from one PyMuPDF build to the next; its size, like
    a page's text, does not.
    """
    with _open(pdf) as document:
        return [
            (picture["width"], picture["height"])
            for page in document
            for image in page.get_images()
            if (picture := document.extract_image(image[0]))
        ]


def _files_under(folder: Path) -> list[str]:
    """Every file under `folder` by its path from it, without dot-files such as `.DS_Store`."""
    return sorted(
        str(path.relative_to(folder))
        for path in folder.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    )


def _entry(data_dir: Path, case_id: str) -> AnswerKeyEntry:
    path = data_dir / ANSWER_KEY_FOLDER / f"{case_id}.json"
    return AnswerKeyEntry.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def generated() -> Iterator[Path]:
    """A fresh run of the generator, kept inside the project's gitignored scratch folder."""
    # A folder per run, so that two runs at once do not write over each other.
    folder = REPO_ROOT / ".work" / f"pytest-synthdata-{uuid4().hex}" / "first"
    write_all(folder)
    yield folder
    shutil.rmtree(folder.parent, ignore_errors=True)


CASE_IDS = [case.case_id for case in CASES]
# What the generator writes besides the manual and the rule table, which
# `test_underwriting_manual.py` compares.
CASE_FOLDERS = (CASES_FOLDER, ANSWER_KEY_FOLDER, TRAINING_FOLDER)


def test_story_1_4_regenerating_gives_identical_output_which_is_what_is_committed(
    generated: Path,
) -> None:
    again = generated.parent / "again"
    write_all(again)

    files = [
        f"{folder}/{name}"
        for folder in CASE_FOLDERS
        for name in _files_under(generated / folder)
    ] + [PAGE_SET_FILE]
    assert len(files) > 3 * len(CASE_IDS)
    for other in (again, COMMITTED):
        # Acceptance: a fresh run leaves `data/` unchanged in git, with no file left
        # over from a case or a training page that is no longer generated.
        for folder in CASE_FOLDERS:
            assert _files_under(other / folder) == _files_under(generated / folder)
        for file in files:
            if file.endswith(".json"):
                assert (other / file).read_bytes() == (generated / file).read_bytes()
                continue
            assert _page_texts(other / file) == _page_texts(generated / file), file
            assert _structure(other / file) == _structure(generated / file), file
            assert _picture_sizes(other / file) == _picture_sizes(generated / file)
        # On one machine a rerun is the same picture byte for byte.
    assert [_pictures(again / file) for file in files if file.endswith(".pdf")] == [
        _pictures(generated / file) for file in files if file.endswith(".pdf")
    ]


def test_story_3_1_every_case_has_its_facts_rules_verdict_and_identifiers(
    generated: Path,
) -> None:
    table = RuleTable.model_validate_json(
        (generated / RULE_TABLE_FILE).read_text(encoding="utf-8")
    )
    rules = {rule.rule_id: rule for rule in table.rules}
    entries = {case_id: _entry(generated, case_id) for case_id in CASE_IDS}

    # About 20 cases, at least three of each verdict, over 25 or more impairments.
    assert 18 <= len(entries) <= 23
    verdicts = Counter(entry.expected_verdict.verdict for entry in entries.values())
    assert all(verdicts[verdict] >= 3 for verdict in Verdict), verdicts
    all_met = {
        rule_id for entry in entries.values() for rule_id in entry.expected_rule_ids
    }
    assert len({rules[rule_id].impairment_id for rule_id in all_met}) >= 25

    def answer(case_id: str) -> tuple[tuple[str, ...], Verdict, int | None]:
        expected = entries[case_id].expected_verdict
        return (
            entries[case_id].expected_rule_ids,
            expected.verdict,
            expected.loading_pct,
        )

    def facts_of(case_id: str, kind: FactKind) -> dict[str, tuple[str, ...]]:
        return {
            fact.statement: fact.rule_ids
            for fact in entries[case_id].expected_facts
            if fact.kind is kind
        }

    # The three first cases meet what they always met.
    assert answer("case-001") == (("UW-DM-002",), Verdict.LOADED, 50)
    assert answer("case-002") == (("UW-HT-002", "UW-TOB-001"), Verdict.LOADED, 100)
    assert answer("case-003") == ((), Verdict.STANDARD, None)
    # Two impairments: debits of +25 and +50 add up.
    assert answer("case-008") == (("UW-DM-001", "UW-HT-002"), Verdict.LOADED, 75)
    # Several readings of one measure are rated as the manual says: the most recent
    # HbA1c (reported in mmol/mol), the average systolic pressure of twelve months.
    assert answer("case-018") == (("UW-DM-003",), Verdict.LOADED, 100)
    assert (
        facts_of("case-018", FactKind.READING).items()
        >= {
            "Type 2 diabetes mellitus: HbA1c 8.3 %": ("UW-DM-003",),
            "Type 2 diabetes mellitus: HbA1c 7.6 %": (),
        }.items()
    )
    assert answer("case-019") == (("UW-HT-002", "UW-VTE-002"), Verdict.LOADED, 100)
    # Figures the manual tells the reader to work out are facts of their own.
    assert facts_of("case-019", FactKind.DERIVED) == {
        "systolic blood pressure 145 mmHg": ("UW-HT-002",),
        "Venous thromboembolism: time since the clot 8 months": ("UW-VTE-002",),
    }
    assert facts_of("case-017", FactKind.DERIVED) == {
        "lifetime smoking 40 pack-years": ("UW-TOB-002",)
    }
    # Two rules of one impairment that the manual says add are no conflict.
    assert answer("case-017")[:2] == (
        ("UW-TOB-001", "UW-TOB-002", "UW-COPD-004"),
        Verdict.DECLINE,
    )
    # Referred, each with the reason `verdict` should give: a condition with no
    # rule, and two bands of a measure the manual gives no way to choose between.
    assert {
        case_id: entry.expected_verdict.system_reasons
        for case_id, entry in entries.items()
        if entry.expected_verdict.verdict is Verdict.REFER
    } == {
        "case-011": (SystemReason.NO_MATCHING_RULE,),
        "case-020": (SystemReason.NO_MATCHING_RULE,),
        "case-022": (SystemReason.CONFLICTING_RULES,),
    }
    # A page may say it in other words and another unit than the manual does.
    heart_attack = next(
        fact
        for fact in entries["case-012"].expected_facts
        if fact.kind is FactKind.DIAGNOSIS
    )
    assert heart_attack.statement == "Myocardial infarction, diagnosed 2024-03-11"
    assert heart_attack.places[0].quote == "Heart attack, diagnosed 2024-03-11"
    assert facts_of("case-012", FactKind.READING)["LDL cholesterol 166 mg/dL"] == (
        "UW-LDL-001",
    )

    for case_id, entry in entries.items():
        expected = entry.expected_verdict
        met = [rules[rule_id] for rule_id in entry.expected_rule_ids]
        # The verdict against the rule table's own rows, read from the file.
        if expected.verdict is Verdict.LOADED:
            assert expected.loading_pct == sum(rule.debit_pct or 0 for rule in met)
        if expected.verdict is Verdict.DECLINE:
            assert any(rule.decline for rule in met), case_id
        if expected.verdict is Verdict.STANDARD:
            assert all(rule.debit_pct == 0 for rule in met), case_id

        texts = _page_texts(generated / CASES_FOLDER / entry.file_name)
        assert entry.expected_facts, case_id
        for fact in entry.expected_facts:
            # On each of its pages, and a query as it stands (AD-17).
            for place in fact.places:
                assert _squash(place.quote) in texts[place.page_number - 1], fact
            assert build_fact_query(fact.statement) == fact.statement
        assert {item.category for item in entry.identifiers} == set(IdentifierCategory)
        for item in entry.identifiers:
            found = {
                number: text.count(_squash(item.value))
                for number, text in enumerate(texts, start=1)
                if _squash(item.value) in text
            }
            listed = {each.page_number: each.count for each in item.occurrences}
            assert found == listed, (case_id, item.value)
        for value in entry.may_also_be_redacted:
            assert any(value in text for text in texts), (case_id, value)


def test_story_3_1_generation_fails_when_a_case_and_its_key_disagree() -> None:
    case = next(case for case in CASES if case.case_id == "case-008")
    rendered = render_case(case)
    entry = answer_key_for(case, rendered)
    texts = extracted_page_texts(rendered.pdf)

    # The case states another verdict than its figures give.
    stated = case.model_copy(update={"intends": Intent(verdict=Verdict.STANDARD)})
    with pytest.raises(ValueError, match="case-008: the case states"):
        answer_key_for(stated, rendered)
    # A fact that is not on its page.
    elsewhere = (FactPlace(page_number=1, quote="HbA1c 9.9 %"),)
    fact = entry.expected_facts[0].model_copy(update={"places": elsewhere})
    moved = entry.model_copy(update={"expected_facts": (fact,)})
    with pytest.raises(ValueError, match="case-008: the fact .* is not found on page"):
        check_against_pages(moved, texts)
    # An identifier that is not found as often as listed.
    with pytest.raises(ValueError, match="case-008: person_name is listed as"):
        check_against_pages(entry, [texts[0], texts[0], *texts[2:]])
    # A diagnosis whose impairment the manual does not have.
    diagnoses = tuple(
        item.model_copy(update={"impairment": "hypertention"})
        for item in case.clinical.diagnoses
    )
    misnamed = case.model_copy(
        update={"clinical": case.clinical.model_copy(update={"diagnoses": diagnoses})}
    )
    with pytest.raises(ValueError, match="case-008: hypertention is no impairment"):
        answer_key_for(misnamed, rendered)
    # Too few cases of a verdict.
    with pytest.raises(ValueError, match="fewer than 3"):
        check_coverage([entry] * 20)
    # A training page that is a scored page.
    with pytest.raises(ValueError, match="shared.pdf: its text is the text of a page"):
        check_disjoint({"shared.pdf": render_page(case, 2)}, {case.file_name: rendered})


def test_story_4_1_scored_page_set_has_every_kind_of_page_with_its_label(
    generated: Path,
) -> None:
    page_set = PageSet.model_validate_json(
        (generated / PAGE_SET_FILE).read_text(encoding="utf-8")
    )
    pages = [
        (document, page) for document in page_set.documents for page in document.pages
    ]

    for document, page in pages:
        # Medical pages are taken from the case set, with the label the case gives.
        entry = _entry(generated, document.case_id)
        assert page.model_dump(exclude={"kind"}) == (
            entry.pages[page.page_number - 1].model_dump()
        )
        assert page.is_medical == is_medical(page.page_type)
    by_kind = {
        kind: {page.layout for _, page in pages if page.kind is kind}
        for kind in PageKind
    }
    assert {page.page_type for _, page in pages if page.kind is PageKind.MEDICAL} == (
        MEDICAL_PAGE_TYPES
    )
    assert by_kind[PageKind.NON_MEDICAL] == NON_MEDICAL_LAYOUTS
    assert {PageLayout.BLANK, PageLayout.HANDWRITTEN_NOTE} <= by_kind[PageKind.EDGE]
    # Enough of each non-medical and edge kind for a score to mean something.
    kinds = Counter(str(page.layout) for _, page in pages)
    kinds["turned"] = sum(bool(p.rotation or p.drawn_rotation) for _, p in pages)
    for kind in (*NON_MEDICAL_LAYOUTS, *LAYOUTS_WITHOUT_TEXT, "turned"):
        assert kinds[str(kind)] >= MIN_PAGES_PER_KIND, kind

    # The mixed file: its pages are labelled one by one.
    mixed = next(doc for doc in page_set.documents if doc.case_id == "case-002")
    assert mixed.mixed
    assert [page.page_type for page in mixed.pages] == [
        PageType.APPLICATION_FORM,
        PageType.ATTENDING_PHYSICIAN_STATEMENT,
        PageType.LAB_REPORT,
        PageType.INVOICE,
        PageType.OTHER,
        PageType.OTHER,
    ]
    assert not next(
        doc for doc in page_set.documents if doc.case_id == "case-001"
    ).mixed

    # `Any`: PyMuPDF does not annotate what is read from a page.
    def page_of(layout: PageLayout, **traits: int) -> Any:
        document, page = next(
            (document, page)
            for document, page in pages
            if page.layout is layout
            and all(getattr(page, name) == value for name, value in traits.items())
        )
        opened = _open(generated / CASES_FOLDER / document.file_name)
        return opened[page.page_number - 1]

    blank = page_of(PageLayout.BLANK)
    assert blank.get_text().strip() == ""
    assert blank.get_drawings() == [] and blank.get_images() == []
    # Turned by the PDF's flag (story 1.4): drawn upright, shown on its side.
    flagged = page_of(PageLayout.LAB_REPORT, rotation=90)
    assert flagged.rotation == 90 and flagged.rect.width > flagged.rect.height
    assert "Laboratory Report" in flagged.get_text()
    assert page_of(PageLayout.LAB_REPORT, rotation=180).rotation == 180
    # Truly turned: no flag, and every line of text runs up the page.
    turned = page_of(PageLayout.LAB_REPORT, drawn_rotation=90)
    assert turned.rotation == 0 and turned.rect.width > turned.rect.height
    directions = {
        line["dir"]
        for block in turned.get_text("dict")["blocks"]
        for line in block.get("lines", [])
    }
    assert directions == {(0.0, -1.0)}
    assert "Laboratory Report" in turned.get_text()
    # The handwritten note is a picture: no text layer at all.
    note = page_of(PageLayout.HANDWRITTEN_NOTE)
    assert note.get_text().strip() == "" and len(note.get_images()) == 1


def test_story_4_1_training_set_has_five_pages_per_type_and_shares_none_with_the_scored_set(
    generated: Path,
) -> None:
    training = TrainingSet.model_validate_json(
        (generated / TRAINING_LIST_FILE).read_text(encoding="utf-8")
    )
    folder = generated / TRAINING_FOLDER
    listed = sorted(page.file for page in training.pages)

    assert listed == [name for name in _files_under(folder) if name.endswith(".pdf")]
    counts = Counter(page.page_type for page in training.pages)
    assert all(
        counts[page_type] >= MIN_TRAINING_PAGES_PER_TYPE for page_type in PageType
    )
    scored_texts: set[str] = set()
    scored_pictures: set[str] = set()
    for case_id in CASE_IDS:
        pdf = generated / CASES_FOLDER / f"{case_id}.pdf"
        scored_texts.update(_page_texts(pdf))
        scored_pictures.update(_pictures(pdf))
    # A page without text is compared by its picture.
    scored_texts.discard("")
    planted = {
        _squash(item.value)
        for case_id in CASE_IDS
        for item in _entry(generated, case_id).identifiers
    }
    with_a_picture = 0
    shapes = set()
    for page in training.pages:
        # One page each, under the folder of its page type (AD-13).
        assert page.file.startswith(f"{page.page_type}/")
        (text,) = _page_texts(folder / page.file)
        pictures = _pictures(folder / page.file)
        ((rotation, width, height),) = _structure(folder / page.file)
        shapes.add((page.layout is PageLayout.BLANK, rotation, width > height))
        assert text not in scored_texts, page.file
        assert not scored_pictures & set(pictures), page.file
        # Different people: no identifier of a case is on a training page.
        assert not [value for value in planted if value in text], page.file
        with_a_picture += bool(pictures)
    assert with_a_picture >= 1
    # The edge kinds of the scored set are trained on too: a blank page, a page
    # turned by the PDF's flag, and one drawn turned.
    assert {(True, 0, False), (False, 180, False), (False, 0, True)} <= shapes


def _files(folder: str) -> list[Path]:
    skipped = {".venv", "node_modules", "__pycache__"}
    return [
        path
        for path in (REPO_ROOT / folder).rglob("*")
        if path.is_file() and not skipped & set(path.parts)
    ]


# AD-17: what names the answers. The folder holds the per-case entries, the rule
# table (story 2.1) and the page set's labels (story 4.1); each file's name is
# caught on its own too. Speaking of a rule table or a page set in general is not
# naming the file.
ANSWER_KEY_NAMES = re.compile(
    rb"answer[-_ ]?key|case-[0-9]{3}\.json|"
    + b"|".join(
        re.escape(Path(file).name).encode() for file in (RULE_TABLE_FILE, PAGE_SET_FILE)
    ),
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

    # The same scan over stand-ins for files under `services/`: one for each file of
    # the answer key (stories 2.1, 3.1 and 4.1), and one that names none of them.
    scratch = REPO_ROOT / ".work" / f"pytest-guard-{uuid4().hex}"
    folder = scratch / "services" / "retrieval"
    folder.mkdir(parents=True)
    offenders = []
    for number, line in enumerate(
        (
            'RULES = DATA / "rule-table.json"',
            'LABELS = DATA / "page-set.json"',
            'EXPECTED = DATA / "case-004.json"',
            'KEY = DATA / "answer-key"',
        )
    ):
        offenders.append(folder / f"offender_{number}.py")
        offenders[-1].write_text(f"{line}\n", encoding="utf-8")
    innocent = folder / "innocent.py"
    # Naming the manual, a case or the training pages, or speaking of a rule table
    # or a page set, is not naming the answers.
    innocent.write_text(
        'MANUAL = "underwriting-manual.pdf"\n# one chunk per row of the rule table\n'
        'CASE = "case-004.pdf"\nTRAINING = "classifier-training"\n# the scored page set\n',
        encoding="utf-8",
    )
    try:
        assert _naming_the_answer_key([*offenders, innocent]) == [
            str(offender.relative_to(REPO_ROOT)) for offender in offenders
        ]
    finally:
        shutil.rmtree(scratch)
