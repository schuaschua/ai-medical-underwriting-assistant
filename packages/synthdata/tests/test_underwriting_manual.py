"""Story 2.1: the synthetic underwriting manual and its rule table."""

import re
import shutil
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymupdf
import pytest

from contracts.rules import (
    RULE_DEFINITION_PATTERN,
    is_rule_id,
    rule_ids_defined_in,
)
from synthdata.generate import (
    MANUAL_FOLDER,
    RULE_TABLE_FILE,
    build_manual,
    check_text,
    rule_table_for,
    write_all,
)
from synthdata.manual import (
    MANUAL_FILE_NAME,
    extracted_pages,
    render_manual,
)
from synthdata.manual_model import (
    LATEST_SOURCE_YEAR,
    MAX_PAGES,
    MIN_PAGES,
    ManualSpec,
    RuleTable,
    RuleTableImpairment,
    RuleTableRule,
)
from synthdata.manual_rules import MANUAL
from synthdata.render import FOOTER

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMITTED = REPO_ROOT / "data"
MANUAL_PDF = Path(MANUAL_FOLDER) / MANUAL_FILE_NAME
# A page's words that are not body text: the running title, "Page N" and the footer.
FURNITURE_WORDS = len(MANUAL.title.split()) + 2 + len(FOOTER.split())
# Pages may end short where a section ends. No more than this many pages may hold
# fewer than this many words of body text.
SHORT_PAGE_WORDS, MAX_SHORT_PAGES = 60, 10


def _squash(text: str) -> str:
    return " ".join(text.split())


def _open(pdf: Path) -> pymupdf.Document:
    return pymupdf.open(pdf)  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate it


def _structure(pdf: Path) -> list[tuple[int, float, float]]:
    with _open(pdf) as document:
        return [
            (page.rotation, round(page.rect.width, 2), round(page.rect.height, 2))
            for page in document
        ]


def _visible(folder: Path) -> list[str]:
    return sorted(
        path.name for path in folder.iterdir() if not path.name.startswith(".")
    )


@pytest.fixture(scope="module")
def generated() -> Iterator[Path]:
    """A fresh run of the generator in a folder of its own, inside the gitignored scratch folder."""
    folder = REPO_ROOT / ".work" / f"pytest-manual-{uuid4().hex}"
    write_all(folder)
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture(scope="module")
def table(generated: Path) -> RuleTable:
    path = generated / RULE_TABLE_FILE
    return RuleTable.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pages(generated: Path) -> list[str]:
    """The text of each page as a reader of the PDF gets it, not as the generator wrote it."""
    return extracted_pages((generated / MANUAL_PDF).read_bytes())


@pytest.fixture(scope="module")
def whole(pages: list[str]) -> str:
    return " ".join(pages)


def _section(table: RuleTable, pages: list[str], number: str) -> str:
    """The text of one numbered section, by the pages the rule table records."""
    starts = [item.first_page for item in table.sections]
    index = [item.number for item in table.sections].index(number)
    last = starts[index + 1] - 1 if index + 1 < len(starts) else table.page_count
    return " ".join(pages[starts[index] - 1 : last])


def _impairment(table: RuleTable, impairment_id: str) -> RuleTableImpairment:
    return next(i for i in table.impairments if i.impairment_id == impairment_id)


def _rules_of(table: RuleTable, impairment_id: str) -> list[RuleTableRule]:
    return [rule for rule in table.rules if rule.impairment_id == impairment_id]


def _can_apply_together(
    table: RuleTable, rule: RuleTableRule, other: RuleTableRule
) -> bool:
    """Worked out from the table alone: neither impairment rules the other out, and
    two bands of one measure of one impairment never hold the same reading."""
    if rule.impairment_id == other.impairment_id:
        return rule.threshold.measure != other.threshold.measure
    mine = _impairment(table, rule.impairment_id).applies.not_with
    theirs = _impairment(table, other.impairment_id).applies.not_with
    return other.impairment_id not in mine and rule.impairment_id not in theirs


# ---------------------------------------------------------------------------
# Size and shape
# ---------------------------------------------------------------------------


def test_story_2_1_manual_has_about_200_pages_each_with_a_text_layer_and_the_footer(
    generated: Path, table: RuleTable
) -> None:
    with _open(generated / MANUAL_PDF) as document:
        assert (MIN_PAGES, MAX_PAGES) == (180, 220)
        assert MIN_PAGES <= document.page_count <= MAX_PAGES
        assert document.page_count == table.page_count
        for number, page in enumerate(document, start=1):
            text = _squash(page.get_text())
            assert len(page.get_text("words")) > FURNITURE_WORDS, number
            assert text.endswith(FOOTER), number
            assert text.startswith(f"{table.manual_title} Page {number} "), number
            assert page.get_images() == [], number


# ---------------------------------------------------------------------------
# Each rule defined once; what a definition says
# ---------------------------------------------------------------------------


def test_story_2_1_every_rule_is_defined_exactly_once_in_the_manual(
    table: RuleTable, whole: str
) -> None:
    rule_ids = [rule.rule_id for rule in table.rules]

    assert len(rule_ids) == len(set(rule_ids)) > 100
    assert all(is_rule_id(rule_id) for rule_id in rule_ids)
    assert re.findall(RULE_DEFINITION_PATTERN, whole) == rule_ids
    assert rule_ids_defined_in(whole) == rule_ids
    for rule_id in rule_ids:
        # Every mention that is not the definition fails the definition pattern.
        assert len(re.findall(rf"{rule_id}:", whole)) == 1, rule_id
        # The table row, the definition and the worked example at least.
        assert len(re.findall(rf"{rule_id}\b", whole)) >= 3, rule_id


# ---------------------------------------------------------------------------
# Cross-references
# ---------------------------------------------------------------------------


def test_story_2_1_a_reference_states_the_band_of_the_rule_it_points_to(
    table: RuleTable,
) -> None:
    by_id = {rule.rule_id: rule for rule in table.rules}
    crossing = 0

    assert sum(bool(rule.references) for rule in table.rules) > 40
    for rule in table.rules:
        assert rule.refers_to == tuple(item.rule_id for item in rule.references)
        assert len(set(rule.refers_to)) == len(rule.refers_to), rule.rule_id
        for reference in rule.references:
            target = by_id[reference.rule_id]
            assert target.rule_id != rule.rule_id
            # A reader who follows the pointer lands on a rule that applies: the
            # circumstance is the target's own band, nothing wider.
            assert reference.when.endswith(target.threshold.words), rule.rule_id
            together = _can_apply_together(table, rule, target)
            assert reference.relation == ("adds" if together else "replaces")
            assert ("instead" in reference.when) == (not together), rule.rule_id
            crossing += target.impairment_id != rule.impairment_id
    # Some rules point into another impairment's section, and one pair replaces.
    assert crossing > 30
    assert by_id["UW-PD-001"].references[0].relation == "replaces"
    assert by_id["UW-PD-001"].references[0].when == (
        "the applicant instead has type 2 diabetes with HbA1c below 7.0 %"
    )
    assert by_id["UW-DM-003"].references[0].when == (
        "the applicant also has chronic kidney disease with eGFR from 45 to "
        "below 60 mL/min/1.73 m2"
    )


# ---------------------------------------------------------------------------
# Rule fields, bands and declared gaps
# ---------------------------------------------------------------------------


def test_story_2_1_every_rule_has_its_fields(table: RuleTable) -> None:
    for rule in table.rules:
        threshold = rule.threshold
        assert rule.rule_id.startswith("UW-") and is_rule_id(rule.rule_id)
        assert rule.impairment and threshold.measure and threshold.unit
        assert threshold.words.startswith(threshold.measure_label)
        # A debit or decline: never both, never neither. A debit is a whole number.
        assert rule.decline != (rule.debit_pct is not None), rule.rule_id
        assert rule.debit_pct is None or type(rule.debit_pct) is int
        assert not rule.postponement or rule.decline, rule.rule_id
        source = rule.source
        assert source.body and source.guideline and len(source.locator) > 10
        assert 1990 <= source.year <= LATEST_SOURCE_YEAR, rule.rule_id
        assert [edge.value for edge in rule.edges] == list(threshold.ends())
    ratings = {rule.rating_words for rule in table.rules}
    assert {
        "decline",
        "decline as a postponement",
        "no debit, +0 %",
        "a debit of +50 %",
    } <= ratings
    postponed = {rule.rule_id for rule in table.rules if rule.postponement}
    assert {"UW-MI-001", "UW-VTE-001", "UW-ANA-002"} <= postponed
    assert any(rule.note for rule in table.rules)


def test_story_2_1_every_reading_meets_one_rule_or_one_declared_gap(
    table: RuleTable, pages: list[str]
) -> None:
    step = Decimal("0.01")
    declared = 0
    for item in table.impairments:
        rules = _rules_of(table, item.impairment_id)
        text = _section(table, pages, item.section)
        assert "Readings that meet no rule." in text, item.name
        for gap in item.gaps:
            declared += 1
            # Printed with the rating table, with what the gap means.
            assert f"{gap.threshold.words}: {gap.meaning}." in text, item.name
        if not item.gaps:
            assert "None: every reading these measures can take" in text, item.name
        for measure in item.measures:
            parts = [
                *(r.threshold for r in rules if r.threshold.measure == measure.key),
                *(g.threshold for g in item.gaps if g.threshold.measure == measure.key),
            ]
            if measure.categories:
                for category in measure.categories:
                    assert sum(p.holds(category.value) for p in parts) == 1
                continue
            ends = {end for part in parts for end in part.ends()}
            limits = {measure.minimum, measure.maximum} - {None}
            readings = {
                end + shift for end in ends for shift in (-step, Decimal(0), step)
            } | {limit for limit in limits if limit is not None}
            for reading in readings:
                if measure.allows(reading):
                    met = sum(part.holds(reading) for part in parts)
                    assert met == 1, (item.name, measure.key, reading)
    assert declared >= 20
    build = _impairment(table, "build")
    assert [gap.threshold.words for gap in build.gaps] == [
        "body mass index from 18.5 to below 35.0 kg/m2"
    ]


# ---------------------------------------------------------------------------
# What fails generation
# ---------------------------------------------------------------------------


def _item(spec: dict[str, Any], impairment_id: str) -> dict[str, Any]:
    return next(
        item for item in spec["impairments"] if item["impairment_id"] == impairment_id
    )


def _rule(spec: dict[str, Any], rule_id: str) -> dict[str, Any]:
    return next(
        rule
        for item in spec["impairments"]
        for rule in item["rules"]
        if rule["rule_id"] == rule_id
    )


Change = Callable[[dict[str, Any]], None]


def _change(target: str, /, **changes: object) -> Change:
    def apply(spec: dict[str, Any]) -> None:
        _rule(spec, target).update(changes)

    return apply


def _in_threshold(target: str, /, **changes: object) -> Change:
    def apply(spec: dict[str, Any]) -> None:
        _rule(spec, target)["threshold"].update(changes)

    return apply


def _in_impairment(target: str, /, **changes: object) -> Change:
    def apply(spec: dict[str, Any]) -> None:
        _item(spec, target).update(changes)

    return apply


def _drop(target: str, field: str, inner: str) -> Change:
    def apply(spec: dict[str, Any]) -> None:
        del _rule(spec, target)[field][inner]

    return apply


def _second_measure(**changes: object) -> Change:
    def apply(spec: dict[str, Any]) -> None:
        item = _item(spec, "type_2_diabetes")
        item["measures"] = [*item["measures"], {**item["measures"][0], **changes}]

    return apply


def _in_measure(impairment_id: str, **changes: object) -> Change:
    def apply(spec: dict[str, Any]) -> None:
        item = _item(spec, impairment_id)
        item["measures"] = [{**item["measures"][0], **changes}, *item["measures"][1:]]

    return apply


def _repeat_glossary_term(spec: dict[str, Any]) -> None:
    spec["glossary"] = [
        *spec["glossary"],
        {"term": spec["glossary"][0]["term"].upper(), "meaning": "Something else."},
    ]


def _not_with(other: str) -> Change:
    def apply(spec: dict[str, Any]) -> None:
        _item(spec, "prediabetes")["applies"]["not_with"] = [other]

    return apply


# What is wrong, and what the error must name: the rule wherever one is at fault.
INVALID: dict[str, tuple[Change, str]] = {
    "debit and decline": (_change("UW-DM-002", decline=True), "UW-DM-002"),
    "neither debit nor decline": (_change("UW-DM-002", debit_pct=None), "UW-DM-002"),
    "debit that is not a whole number": (
        _change("UW-DM-002", debit_pct=12.5),
        "UW-DM-002",
    ),
    "negative debit": (_change("UW-DM-002", debit_pct=-25), "UW-DM-002"),
    "postponement that is not a decline": (
        _change("UW-DM-002", postponement=True),
        "UW-DM-002.*only a decline",
    ),
    "id against the pattern": (_change("UW-DM-002", rule_id="UW-D-2"), "UW-D-2"),
    "id used twice": (_change("UW-DM-002", rule_id="UW-DM-001"), "UW-DM-001"),
    "id with another impairment's code": (
        _change("UW-DM-002", rule_id="UW-HT-099"),
        "UW-HT-099.*starts UW-DM-",
    ),
    "bands that overlap": (
        _in_threshold("UW-DM-002", lower=Decimal("6.5")),
        "UW-DM-002",
    ),
    "category used twice": (
        _in_threshold("UW-TOB-003", category="current_smoker"),
        "UW-TOB-003",
    ),
    "reference to an unknown rule": (
        _change("UW-DM-001", see=["UW-ZZ-999"]),
        "UW-DM-001.*UW-ZZ-999",
    ),
    "reference to itself": (_change("UW-DM-001", see=["UW-DM-001"]), "UW-DM-001"),
    "reference listed twice": (
        _change("UW-DM-001", see=["UW-ALB-001", "UW-ALB-001"]),
        "UW-DM-001.*twice",
    ),
    "source without its body": (_drop("UW-DM-003", "source", "body"), "UW-DM-003"),
    "source dated in the future": (
        lambda spec: _rule(spec, "UW-DM-003")["source"].update(
            year=LATEST_SOURCE_YEAR + 1
        ),
        f"(?s)UW-DM-003.*after {LATEST_SOURCE_YEAR}",
    ),
    "threshold without a unit": (_drop("UW-DM-003", "threshold", "unit"), "UW-DM-003"),
    "band without an end": (_in_threshold("UW-DM-003", upper=None), "UW-DM-003"),
    "band with its ends the wrong way round": (
        _in_threshold("UW-DM-003", upper=Decimal("7.5")),
        "UW-DM-003",
    ),
    "band that also has a single value": (
        _in_threshold("UW-DM-003", value=Decimal("8.5")),
        "UW-DM-003",
    ),
    "band end outside the measure's range": (
        _in_threshold("UW-DM-005", value=Decimal("25.0")),
        "UW-DM-005.*outside the measure's range",
    ),
    "measure the impairment does not have": (
        _in_threshold("UW-DM-003", measure="egfr"),
        "UW-DM-003.*not a measure",
    ),
    "unit that is not the measure's": (
        _in_threshold("UW-DM-003", unit="mmol/mol"),
        "UW-DM-003.*not the measure's",
    ),
    "gap between bands that nobody declared": (
        _in_impairment("build", gaps=[]),
        "build: bmi: an undeclared gap",
    ),
    "readings below the first band undeclared": (
        _in_impairment("raised_ldl_cholesterol", gaps=[]),
        "raised_ldl_cholesterol: ldl_cholesterol: readings below 160",
    ),
    "readings above the last band undeclared": (
        _in_impairment("anaemia", gaps=[]),
        "anaemia: haemoglobin: readings above 11.0",
    ),
    "category with no rule and no gap": (
        _in_impairment("tobacco_use", gaps=[]),
        "tobacco_use: smoking_status: every category",
    ),
    "measure no rule uses": (
        _second_measure(key="glucose", label="glucose"),
        "type_2_diabetes: no rule uses the measure glucose",
    ),
    "measure listed twice": (_second_measure(), "type_2_diabetes: a measure is listed"),
    "measure defined differently elsewhere": (
        _in_measure("type_1_diabetes", meaning="Something else."),
        "type_1_diabetes: hba1c is defined differently",
    ),
    "edge of its own that is no band's end": (
        _in_impairment("type_2_diabetes", own_edges=[Decimal("9.5")]),
        "type_2_diabetes: 9.5 is not an end",
    ),
    "ruled out by an impairment that does not exist": (
        _not_with("no_such_impairment"),
        "prediabetes: no_such_impairment is not another impairment",
    ),
    "ruled out by itself": (_not_with("prediabetes"), "prediabetes: prediabetes"),
    "code that is not the letters of a rule id": (
        _in_impairment("type_2_diabetes", code="D1"),
        "letters of a rule id",
    ),
    "two impairments with one name": (
        _in_impairment("type_1_diabetes", name="Type 2 diabetes mellitus"),
        "share one name",
    ),
    "impairment with no rule": (_in_impairment("gout", rules=[]), "rules"),
    "glossary term with two meanings": (_repeat_glossary_term, "glossary has two"),
}


def _tampered(old: str, new: str, rule_id: str = "UW-DM-001") -> list[str]:
    rendered, _ = build_manual()
    pages = extracted_pages(rendered.pdf)
    page = rendered.rule_pages[rule_id] - 1
    assert old in pages[page]
    pages[page] = pages[page].replace(old, new)
    return pages


def test_story_2_1_generation_fails_when_the_manual_and_the_table_disagree() -> None:
    rendered, table = build_manual()
    rule_pages = rendered.rule_pages
    good = extracted_pages(rendered.pdf)

    def check(pages: list[str]) -> None:
        check_text(MANUAL, rule_pages, pages)

    check(good)
    with pytest.raises(ValueError, match="UW-DM-001.*defines it 2 times"):
        check(_tampered("Rule UW-DM-002:", "Rule UW-DM-002: Rule UW-DM-001:"))
    with pytest.raises(ValueError, match="UW-DM-001.*defines it 0 times"):
        check(_tampered("Rule UW-DM-001:", "As for UW-DM-001,"))
    with pytest.raises(ValueError, match="a rule the table lacks"):
        check(_tampered("Rule UW-DM-001:", "Rule UW-XX-001: x. Rule UW-DM-001:"))
    with pytest.raises(ValueError, match="UW-DM-001.*does not mention UW-ALB-001"):
        check(_tampered("see rule UW-ALB-001", "see the kidney rules"))
    with pytest.raises(ValueError, match="UW-DM-001: its definition with its section"):
        check(_tampered("mellitus (section 2.4).", "mellitus."))
    with pytest.raises(ValueError, match="UW-DM-001: its definition with its section"):
        check_text(MANUAL, {**rule_pages, "UW-DM-001": 1}, good)
    with pytest.raises(ValueError, match="100 pages"):
        check(good[:100])
    # A page with no text layer, or one that lost its footer or its number.
    for broken in ("", good[4].removesuffix(FOOTER), good[4].replace("Page 5 ", "")):
        with pytest.raises(ValueError, match="page 5 has no text layer"):
            check([*good[:4], broken, *good[5:]])
    with pytest.raises(ValueError, match="page_count"):
        RuleTable.model_validate({**table.model_dump(), "page_count": 100})
    # A reference between two rules of one impairment says that their ratings add
    # (`verdict` reads it so), which two bands of one measure never do.
    spec = MANUAL.model_dump()
    _change("UW-DM-001", see=["UW-DM-002"])(spec)
    with pytest.raises(ValueError, match="UW-DM-001: it refers to UW-DM-002, another"):
        ManualSpec.model_validate(spec)
    # Which of several readings is rated is printed from the same data the answer
    # key is worked out with.
    whole = " ".join(good)
    chosen = [
        rule.words(measure.label)
        for item in MANUAL.impairments
        for rule in item.reading_rules
        for measure in item.measures
        if measure.key == rule.measure
    ]
    assert len(chosen) >= 9 and all(words in whole for words in chosen)
    # And in the definition of every rule of such a measure, which is read alone.
    for item in MANUAL.impairments:
        for rule in item.rules:
            choice = item.reading_rule(rule.threshold.measure)
            definition = good[rule_pages[rule.rule_id] - 1]
            start = definition.index(f"Rule {rule.rule_id}:")
            own = definition[start:].split("Source of the", 1)[0]
            said = "Where the file holds several readings" in own
            assert said == (choice is not None), rule.rule_id
            assert choice is None or choice.definition_words() in own


# ---------------------------------------------------------------------------
# Rerun and the committed files
# ---------------------------------------------------------------------------


def test_story_2_1_regenerating_gives_the_same_manual_and_rule_table_which_are_what_is_committed(
    generated: Path, pages: list[str]
) -> None:
    first, second = render_manual(MANUAL), render_manual(MANUAL)

    # Byte for byte in one process, so nothing in the file depends on the run.
    assert first.pdf == second.pdf
    assert first.page_texts == second.page_texts
    assert (
        rule_table_for(MANUAL, first).model_dump_json()
        == rule_table_for(MANUAL, second).model_dump_json()
    )

    # Acceptance: a fresh run leaves `data/` unchanged in git.
    assert (COMMITTED / RULE_TABLE_FILE).read_bytes() == (
        generated / RULE_TABLE_FILE
    ).read_bytes()
    assert extracted_pages((COMMITTED / MANUAL_PDF).read_bytes()) == pages
    assert _structure(COMMITTED / MANUAL_PDF) == _structure(generated / MANUAL_PDF)
