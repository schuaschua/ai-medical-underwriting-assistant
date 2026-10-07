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
    RULE_ID_PATTERN,
    is_rule_id,
    rule_ids_defined_in,
)
from synthdata import generate
from synthdata.__main__ import main
from synthdata.cases import CASES
from synthdata.generate import (
    MANUAL_FOLDER,
    RULE_TABLE_FILE,
    build_manual,
    check_text,
    rule_table_for,
    write_all,
)
from synthdata.manual import (
    INVENTED,
    MANUAL_FILE_NAME,
    PARTS,
    example_reading,
    extracted_pages,
    render_manual,
)
from synthdata.manual_model import (
    LATEST_SOURCE_YEAR,
    MAX_PAGES,
    MIN_PAGES,
    Category,
    ManualSpec,
    Measure,
    RuleTable,
    RuleTableImpairment,
    RuleTableRule,
)
from synthdata.manual_rules import MANUAL
from synthdata.model import CaseDefinition
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


def _titled(table: RuleTable, title: str) -> str:
    return next(item.number for item in table.sections if item.title == title)


def _definition(pages: list[str], rule: RuleTableRule) -> str:
    """The paragraph that defines `rule`: from its marker to whatever comes next."""
    page = pages[rule.manual_page - 1]
    start = page.index(f"Rule {rule.rule_id}:")
    following = re.search(RULE_DEFINITION_PATTERN, page[start + 1 :])
    ends = [
        start + 1 + following.start() if following else -1,
        page.find("What does not change the rating.", start),
        page.find(FOOTER, start),
    ]
    return page[start : min(end for end in ends if end > 0)].strip()


def _impairment(table: RuleTable, impairment_id: str) -> RuleTableImpairment:
    return next(i for i in table.impairments if i.impairment_id == impairment_id)


def _rules_of(table: RuleTable, impairment_id: str) -> list[RuleTableRule]:
    return [rule for rule in table.rules if rule.impairment_id == impairment_id]


def _measure(table: RuleTable, rule: RuleTableRule) -> Measure:
    measures = _impairment(table, rule.impairment_id).measures
    return next(m for m in measures if m.key == rule.threshold.measure)


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
# Generate
# ---------------------------------------------------------------------------


def test_story_2_1_generator_writes_the_manual_and_the_rule_table_with_the_cases(
    generated: Path,
) -> None:
    assert _visible(generated / MANUAL_FOLDER) == [MANUAL_FILE_NAME]
    assert _visible(generated / "answer-key") == ["cases", "rule-table.json"]
    assert _visible(generated / "cases") == [case.file_name for case in CASES]


def test_story_2_1_command_line_lists_the_manual_and_the_rule_table(
    generated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = generated / "from-command"

    main(["--data-dir", str(folder)])

    printed = capsys.readouterr().out
    assert str(folder / MANUAL_PDF) in printed
    assert str(folder / RULE_TABLE_FILE) in printed
    assert (folder / MANUAL_PDF).read_bytes() == (generated / MANUAL_PDF).read_bytes()


def test_story_2_1_nothing_is_written_when_the_manual_cannot_be_built(
    generated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> None:
        raise ValueError("UW-DM-001: the manual defines it 2 times")

    monkeypatch.setattr(generate, "build_manual", fail)
    folder = generated / "never-written"

    with pytest.raises(ValueError, match="UW-DM-001"):
        write_all(folder)

    assert not folder.exists()


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


def test_story_2_1_few_pages_are_nearly_empty(pages: list[str]) -> None:
    # The page count is not made of white space: a new page starts only where a
    # section starts, so a short page is the last page of a section.
    body = [len(text.split()) - FURNITURE_WORDS for text in pages]
    short = [number for number, words in enumerate(body, start=1) if words < 60]

    assert (SHORT_PAGE_WORDS, MAX_SHORT_PAGES) == (60, 10)
    assert len(short) <= MAX_SHORT_PAGES, short
    assert sum(body) > 40_000


def test_story_2_1_rule_table_has_about_40_impairments(table: RuleTable) -> None:
    assert 38 <= len(table.impairments) <= 42
    assert {rule.impairment_id for rule in table.rules} == {
        item.impairment_id for item in table.impairments
    }
    numbers = [item.number for item in table.sections]
    assert numbers == [str(n) for n in range(1, len(numbers) + 1)]
    assert [item.title for item in table.sections] == [
        "Introduction",
        *(item.name for item in table.impairments),
        "Glossary",
        "Public sources",
    ]


def test_story_2_1_every_impairment_section_has_its_numbered_parts(
    table: RuleTable, pages: list[str]
) -> None:
    assert list(PARTS.values())[:2] == ["The impairment", "Key questions"]
    assert PARTS[4] == "Probable rating"
    for item in table.impairments:
        text = _section(table, pages, item.section)
        # The section number is printed with every heading.
        assert f" {item.section} {item.name} {item.section}.1 {PARTS[1]} " in text
        positions = [
            text.index(f" {item.section}.{part} {title} ")
            for part, title in PARTS.items()
        ]
        assert positions == sorted(positions), item.name
        overview, questions, evidence, rating, examples = (
            text[start:end]
            for start, end in zip(positions, [*positions[1:], len(text)], strict=True)
        )
        # Prose, a table and cross-references, each in its part.
        assert len(overview.split()) > 80, item.name
        assert f"When this section applies. {item.applies.words}" in overview
        assert questions.count("?") >= 3, item.name
        assert "Pitfalls with this evidence." in evidence, item.name
        assert "Rule id Measure and band Probable rating" in rating, item.name
        assert "What does not change the rating." in rating, item.name
        assert "Common combinations." in examples, item.name
        assert "Related rules." in examples, item.name
        assert "see rule UW-" in text or "point to this one" in text, item.name


def test_story_2_1_a_section_alone_tells_the_measure_the_bands_and_the_ratings(
    table: RuleTable, pages: list[str]
) -> None:
    for item in table.impairments:
        text = _section(table, pages, item.section)
        for rule in _rules_of(table, item.impairment_id):
            # The table row and the definition both give the band and its rating.
            row = f"{rule.rule_id} {rule.threshold.words} {rule.rating_words}"
            assert row in text, rule.rule_id
            assert (
                f"Threshold: {rule.threshold.words}. "
                f"Probable rating: {rule.rating_words}." in text
            ), rule.rule_id
        for measure in item.measures:
            label = (
                f"{measure.label} ({measure.unit})"
                if measure.unit_printed
                else measure.label
            )
            assert label in text[: text.index(f"{item.section}.2 ")], measure.key


def test_story_2_1_every_conversion_the_manual_asks_for_is_given(
    table: RuleTable, pages: list[str]
) -> None:
    converted = 0
    for item in table.impairments:
        text = _section(table, pages, item.section)
        for measure in item.measures:
            if measure.conversion:
                converted += 1
                assert f"{measure.label}: {measure.conversion}" in text, measure.key
        # A section that tells the reader to convert has the conversion in it.
        if re.search(r"[Cc]onvert", text.replace("Converting units.", "")):
            assert "Converting units." in text, item.name
    assert converted > 15


def test_story_2_1_contents_page_gives_the_page_each_section_starts_on(
    table: RuleTable, pages: list[str]
) -> None:
    first = table.sections[0].first_page
    contents = " ".join(pages[table.contents_page - 1 : first - 1]) + " "

    assert " Contents 1 Introduction " in contents
    for item in table.sections:
        assert f" {item.number} {item.title} {item.first_page} " in contents
        assert pages[item.first_page - 1].startswith(
            f"{table.manual_title} Page {item.first_page} {item.number} {item.title} "
        )
    for impairment in table.impairments:
        section = table.sections[int(impairment.section) - 1]
        assert section.first_page == impairment.first_page


def test_story_2_1_manual_says_how_to_read_ratings_and_that_they_are_invented(
    table: RuleTable, pages: list[str], whole: str
) -> None:
    introduction = _section(table, pages, _titled(table, "Introduction"))
    glossary = _section(table, pages, _titled(table, "Glossary"))

    assert "Reading a rating" in introduction
    assert "Combining debits" in introduction
    assert "add the debits of all the rules that apply" in introduction
    assert "Only rules that can apply together are added" in introduction
    assert "no part of its text was taken from any insurer's" in introduction
    # One statement about the edges, the same wherever it is made.
    assert "A few edges are this manual's own" in introduction
    assert "each band's edges follow" not in whole
    assert INVENTED in pages[0] and INVENTED == table.notice
    # Said again beside every rating table.
    assert whole.count(INVENTED) == 1 + len(table.impairments)
    for term in ("Debit", "Decline", "Cross-reference", "HbA1c", "eGFR"):
        assert f" {term} " in glossary
    # Organisations of the case documents are invented names; none belongs here.
    for name in ("Example Mutual", "Specimen Diagnostics"):
        assert name not in whole


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


def _expected_source_words(rule: RuleTableRule) -> str:
    amount = rule.threshold.amount
    own = [amount(e.value) for e in rule.edges if e.origin == "manual"]
    cited = [amount(e.value) for e in rule.edges if e.origin == "source"]
    if not own:
        return f"Source of the threshold: {rule.source.citation}."
    if cited:
        return (
            f"Source of the edge at {cited[0]}: {rule.source.citation}. "
            f"The edge at {own[0]} is this manual's own."
        )
    if len(own) == 1:
        start = (
            f"The edge at {own[0]} is this manual's own and no guideline supplies it."
        )
    else:
        start = (
            f"The edges at {own[0]} and {own[1]} are this manual's own and no "
            "guideline supplies them."
        )
    return (
        f"{start} The source for the measure and the other edges of this "
        f"section: {rule.source.citation}."
    )


def _expected_reference_sentence(reference: Any) -> str:
    sentence = f"If {reference.when}, see rule {reference.rule_id}"
    if reference.relation == "replaces":
        return f"{sentence}, which applies in place of this rule."
    return f"{sentence}."


def test_story_2_1_a_definition_is_exactly_what_the_rule_table_records(
    table: RuleTable, pages: list[str]
) -> None:
    for rule in table.rules:
        # Rebuilt from the table: impairment, section number, band, rating, note,
        # each pointer with its circumstance, and where each edge comes from.
        expected = " ".join(
            [
                f"Rule {rule.rule_id}: {rule.impairment} (section {rule.section}.4).",
                f"Threshold: {rule.threshold.words}.",
                f"Probable rating: {rule.rating_words}.",
                *([rule.note] if rule.note else []),
                *(_expected_reference_sentence(item) for item in rule.references),
                _expected_source_words(rule),
            ]
        )
        assert _definition(pages, rule) == expected, rule.rule_id
        mentioned = re.findall(RULE_ID_PATTERN, expected)
        assert mentioned == [rule.rule_id, *rule.refers_to], rule.rule_id


def test_story_2_1_edges_the_manual_drew_are_not_given_a_source(
    table: RuleTable, pages: list[str]
) -> None:
    own = {
        rule.rule_id: [e.value for e in rule.edges if e.origin == "manual"]
        for rule in table.rules
    }
    by_id = {rule.rule_id: rule for rule in table.rules}

    assert own["UW-DM-002"] == [] and own["UW-DM-003"] == [Decimal("9.0")]
    assert own["UW-DM-004"] == [Decimal("9.0"), Decimal("10.0")]
    assert own["UW-HT-002"] == own["UW-HT-003"] == [Decimal(160)]
    assert sum(bool(edges) for edges in own.values()) >= 14
    for rule_id, edges in own.items():
        definition = _definition(pages, by_id[rule_id])
        assert ("this manual's own" in definition) == bool(edges), rule_id
        assert ("Source of the threshold:" in definition) == (not edges), rule_id
    # An edge has one origin, whichever of its two rules is read.
    for item in table.impairments:
        origins: dict[tuple[str, Decimal], str] = {}
        for rule in _rules_of(table, item.impairment_id):
            for edge in rule.edges:
                key = (rule.threshold.measure, edge.value)
                assert origins.setdefault(key, edge.origin) == edge.origin, rule.rule_id


def test_story_2_1_hypertension_says_it_departs_from_the_cited_classification(
    table: RuleTable, pages: list[str]
) -> None:
    text = _section(table, pages, _impairment(table, "hypertension").section)

    assert "Rating on the systolic reading alone departs from the cited" in text


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


def test_story_2_1_related_rules_give_the_section_of_every_rule_pointed_to(
    table: RuleTable, pages: list[str]
) -> None:
    by_id = {rule.rule_id: rule for rule in table.rules}
    for item in table.impairments:
        text = _section(table, pages, item.section)
        related = text[text.rindex("Related rules.") :]
        pointed_here = False
        for rule in table.rules:
            for reference in rule.references:
                target = by_id[reference.rule_id]
                if rule.impairment_id == item.impairment_id:
                    assert (
                        f"Under rule {rule.rule_id}, if {reference.when}, see rule "
                        f"{target.rule_id} (section {target.section}.4, "
                        f"{target.impairment})." in related
                    ), rule.rule_id
                elif target.impairment_id == item.impairment_id:
                    pointed_here = True
                    assert (
                        f"rule {rule.rule_id} (section {rule.section}.4, "
                        f"{rule.impairment})" in related
                    ), rule.rule_id
        assert ("point to this one" in related) == pointed_here, item.name
    # No section stands alone.
    melanoma = _rules_of(table, "melanoma")
    assert any(rule.references for rule in melanoma)


# ---------------------------------------------------------------------------
# Worked examples and readings on an edge
# ---------------------------------------------------------------------------


def _expected_combination(
    table: RuleTable, rule: RuleTableRule, other: RuleTableRule
) -> str:
    when = rule.references[0].when
    if not _can_apply_together(table, rule, other):
        return (
            f"If {when}, rule {other.rule_id} would apply in place of this one, "
            f"and the probable rating would be {other.rating_words}."
        )
    if rule.decline or other.decline:
        outcome = "the outcome would be decline"
    else:
        assert rule.debit_pct is not None and other.debit_pct is not None
        outcome = f"the two together would give +{rule.debit_pct + other.debit_pct} %"
    return (
        f"If {when}, rule {other.rule_id} would apply as well; its rating is "
        f"{other.rating_words}, and {outcome}."
    )


def test_story_2_1_each_worked_example_says_what_the_rule_table_says(
    table: RuleTable, pages: list[str]
) -> None:
    by_id = {rule.rule_id: rule for rule in table.rules}
    for item in table.impairments:
        text = _section(table, pages, item.section)
        examples = text[text.index(f"{item.section}.5 {PARTS[5]}") :]
        rules = _rules_of(table, item.impairment_id)
        for k, rule in enumerate(rules, start=1):
            start = examples.index(f"Example {k}. ")
            ends = [
                examples.find(marker, start + 1)
                for marker in (f"Example {k + 1}. ", "Readings on an edge:")
            ]
            example = examples[
                start : min(end for end in [*ends, len(examples)] if end > 0)
            ]
            measure = _measure(table, rule)
            reading = example_reading(rule.threshold, measure)
            # The reading is one the measure can take, and it meets this rule only.
            if isinstance(reading, Decimal):
                assert measure.allows(reading), rule.rule_id
            assert [
                r.rule_id
                for r in rules
                if r.threshold.measure == measure.key and r.threshold.holds(reading)
            ] == [rule.rule_id]
            assert f" {rule.threshold.reading_words(reading)}" in example, rule.rule_id
            assert f"rule {rule.rule_id} ({rule.threshold.words})" in example
            assert re.search(
                rf"[Pp]robable rating(:| is) {re.escape(rule.rating_words)}\.", example
            ), rule.rule_id
            if rule.references:
                other = by_id[rule.refers_to[0]]
                assert _expected_combination(table, rule, other) in example, (
                    rule.rule_id
                )
            else:
                assert "would apply" not in example, rule.rule_id
    # Rules that cannot both apply are never added up.
    prediabetes = _section(table, pages, _impairment(table, "prediabetes").section)
    assert "rule UW-DM-001 would apply in place of this one" in prediabetes
    assert "the two together" not in prediabetes
    # Wording that reads as a sentence for a category and for no debit.
    tobacco = _section(table, pages, _impairment(table, "tobacco_use").section)
    assert "rule UW-TOB-001 (smoking status: current smoker)" in tobacco
    assert "This is current smoker" not in tobacco
    assert "(no debit (" not in " ".join(pages)


def test_story_2_1_readings_on_an_edge_name_the_rule_the_table_gives(
    table: RuleTable, pages: list[str]
) -> None:
    for item in table.impairments:
        rules = _rules_of(table, item.impairment_id)
        lines: list[str] = []
        for measure in item.measures:
            same = [r for r in rules if r.threshold.measure == measure.key]
            edges = sorted({edge.value for rule in same for edge in rule.edges})
            for edge in edges:
                met = [rule for rule in same if rule.threshold.holds(edge)]
                gaps = [
                    g
                    for g in item.gaps
                    if g.threshold.measure == measure.key and g.threshold.holds(edge)
                ]
                # Exactly one home for a reading on an edge: a rule or a declared gap.
                assert len(met) + len(gaps) == 1, (item.name, edge)
                where = (
                    f"rule {met[0].rule_id} ({met[0].rating_words})"
                    if met
                    else "no rule of this section"
                )
                lines.append(
                    f"{same[0].threshold.reading_words(edge)} exactly, {where}"
                )
        text = _section(table, pages, item.section)
        if lines:
            assert f"Readings on an edge: {'; '.join(lines)}." in text, item.name
        else:
            assert "Readings on an edge" not in text, item.name


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


def test_story_2_1_every_public_source_is_listed_with_the_sections_that_use_it(
    table: RuleTable, pages: list[str]
) -> None:
    sources = _section(table, pages, _titled(table, "Public sources"))
    for rule in table.rules:
        source = rule.source
        assert (
            f"{source.body} ({source.abbreviation}). {source.guideline}. {source.year}."
            in sources
        ), rule.rule_id
        assert (
            f"section {rule.section} ({rule.impairment}: {source.locator})" in sources
        )


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


def test_story_2_1_a_band_holds_exactly_the_readings_its_words_say() -> None:
    measure = Measure(key="reading", label="reading", unit="mg/dL", meaning="A test.")
    half_open = measure.between("7.0", "8.0")
    open_both = measure.between("40", "50", lower_inclusive=False)
    closed_above = measure.between(
        "1.0", "2.0", lower_inclusive=False, upper_inclusive=True
    )

    assert half_open.words == "reading from 7.0 to below 8.0 mg/dL"
    assert [half_open.holds(Decimal(v)) for v in ("6.9", "7.0", "7.9", "8.0")] == [
        False,
        True,
        True,
        False,
    ]
    assert open_both.words == "reading above 40 and below 50 mg/dL"
    assert not open_both.holds(Decimal(40)) and not open_both.holds(Decimal(50))
    assert closed_above.words == "reading above 1.0 and up to 2.0 mg/dL"
    assert closed_above.holds(Decimal("2.0")) and not closed_above.holds(Decimal("1.0"))
    assert measure.between("1", "2", upper_inclusive=True).words == (
        "reading from 1 to 2 mg/dL"
    )
    assert measure.at_most("40").holds(Decimal(40))
    assert not measure.above("40").holds(Decimal(40))
    assert measure.at_least("180").words == "reading of 180 mg/dL or more"
    assert not measure.below("7.0").holds(Decimal("7.0"))
    assert half_open.reading_words(Decimal("7.4")) == "reading 7.4 mg/dL"
    # Touching bands do not overlap; bands sharing a reading do.
    assert not measure.below("7.0").overlaps(half_open)
    assert not measure.at_most("40").overlaps(open_both)
    assert measure.at_most("7.0").overlaps(half_open)
    assert measure.at_least("7.5").overlaps(half_open)
    assert measure.below("9").overlaps(measure.below("3"))


def test_story_2_1_a_status_is_compared_by_category() -> None:
    status = Measure(
        key="status",
        label="status",
        unit="category",
        unit_printed=False,
        meaning="A test.",
        categories=(
            Category(value="current_smoker", label="current smoker"),
            Category(value="former_smoker", label="former smoker"),
        ),
    )
    current = status.equals("current_smoker")
    number = Measure(key="n", label="n", unit="mg", meaning="A test.").below("1")

    assert current.words == "status: current smoker"
    assert current.holds("current_smoker") and not current.holds("former_smoker")
    assert not current.holds(Decimal(1)) and not number.holds("current_smoker")
    assert current.overlaps(current) and not current.overlaps(number)
    assert current.reading_words("current_smoker") == "status: current smoker"
    with pytest.raises(ValueError, match="its own threshold"):
        current.reading_words("former_smoker")
    with pytest.raises(ValueError, match="not one of its categories"):
        status.equals("never_smoker")
    with pytest.raises(ValueError, match="no number range"):
        Measure.model_validate({**status.model_dump(), "minimum": Decimal(0)})
    with pytest.raises(ValueError, match="listed twice"):
        Measure.model_validate(
            {**status.model_dump(), "categories": status.model_dump()["categories"] * 2}
        )


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


def test_story_2_1_the_definition_the_generator_uses_is_valid() -> None:
    assert ManualSpec.model_validate(MANUAL.model_dump()) == MANUAL


@pytest.mark.parametrize("fault", INVALID)
def test_story_2_1_an_invalid_definition_fails_generation_and_names_what_is_wrong(
    fault: str,
) -> None:
    spec = MANUAL.model_dump()
    change, named = INVALID[fault]
    change(spec)

    with pytest.raises(ValueError, match=named):
        ManualSpec.model_validate(spec)


def _with(**changes: Any) -> ManualSpec:
    return ManualSpec.model_validate({**MANUAL.model_dump(), **changes})


def _first_impairment(**changes: Any) -> ManualSpec:
    first, *rest = MANUAL.model_dump()["impairments"]
    return _with(impairments=[{**first, **changes}, *rest])


def test_story_2_1_layout_that_does_not_fit_fails_generation() -> None:
    word = "underwriting "
    with pytest.raises(ValueError, match="paragraph starting .* is taller than a page"):
        render_manual(_first_impairment(overview=[word * 700]))
    with pytest.raises(ValueError, match="too wide for its place"):
        render_manual(_first_impairment(overview=["x" * 90]))
    with pytest.raises(ValueError, match="runs into"):
        render_manual(_first_impairment(name="Type 2 diabetes mellitus " * 4))
    with pytest.raises(ValueError, match="runs into 'Page 1'"):
        render_manual(_with(title="Synthetic Underwriting Manual " * 4))


def test_story_2_1_a_rating_table_taller_than_a_page_fails_generation() -> None:
    first = MANUAL.impairments[0]
    measure = Measure(
        key="score", label="score", unit="points", meaning="A test.", minimum=Decimal(0)
    )
    template = first.rules[0].model_dump()
    rules = [
        {
            **template,
            "rule_id": f"UW-DM-{index:03d}",
            "threshold": measure.between(str(index), str(index + 1)).model_dump(),
            "see": [],
        }
        for index in range(60)
    ]
    rest = [
        {
            **item.model_dump(),
            "rules": [{**r.model_dump(), "see": []} for r in item.rules],
        }
        for item in MANUAL.impairments[1:]
    ]
    crowded = {
        **first.model_dump(),
        "measures": [measure.model_dump()],
        "rules": rules,
        "own_edges": [],
        "gaps": [
            {"threshold": measure.at_least("60").model_dump(), "meaning": "no rule"}
        ],
    }

    with pytest.raises(ValueError, match="table starting 'UW-DM-000' is taller"):
        render_manual(_with(impairments=[crowded, *rest]))


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


def _table_with(table: RuleTable, target: str, /, **changes: object) -> dict[str, Any]:
    data = table.model_dump()
    next(r for r in data["rules"] if r["rule_id"] == target).update(changes)
    return data


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"refers_to": ["UW-ZZ-999"]}, "UW-DM-002: refers_to against"),
        ({"references": [], "refers_to": []}, None),
        ({"decline": True}, "UW-DM-002"),
        ({"impairment_id": "no_such_impairment"}, "UW-DM-002"),
        ({"section": "9"}, "UW-DM-002"),
        ({"manual_page": 999}, "UW-DM-002: its page is before its section or past"),
        ({"manual_page": 1}, "UW-DM-002: its page is before its section or past"),
        ({"rule_id": "UW-DM-001"}, "UW-DM-001"),
        ({"rule_id": "UW-HT-099"}, "UW-HT-099: not the code of its impairment"),
        ({"edges": []}, "UW-DM-002: edges against"),
        ({"expected_verdict": "loaded"}, "UW-DM-002"),
    ],
)
def test_story_2_1_rule_table_file_rejects_a_malformed_rule(
    table: RuleTable, changes: dict[str, object], named: str | None
) -> None:
    data = _table_with(table, "UW-DM-002", **changes)
    if named is None:
        # Fewer references is still a sound table; the manual check is what compares.
        assert RuleTable.model_validate(data)
        return
    with pytest.raises(ValueError, match=named):
        RuleTable.model_validate(data)


def test_story_2_1_rule_table_file_rejects_malformed_references_and_sections(
    table: RuleTable,
) -> None:
    rule = next(rule for rule in table.rules if rule.rule_id == "UW-DM-002")
    reference = rule.references[0].model_dump()
    twice = _table_with(
        table,
        "UW-DM-002",
        references=[reference, reference],
        refers_to=[reference["rule_id"]] * 2,
    )
    with pytest.raises(ValueError, match="UW-DM-002: it refers to UW-HT-002 twice"):
        RuleTable.model_validate(twice)

    data = table.model_dump()
    data["impairments"][3]["gaps"] = []
    with pytest.raises(ValueError, match="build: bmi: an undeclared gap"):
        RuleTable.model_validate(data)

    data = table.model_dump()
    data["impairments"][0]["first_page"] += 1
    with pytest.raises(ValueError, match="type_2_diabetes: not the section"):
        RuleTable.model_validate(data)

    data = table.model_dump()
    data["sections"][1]["first_page"] = data["sections"][0]["first_page"]
    with pytest.raises(ValueError, match="later and later pages"):
        RuleTable.model_validate(data)

    data = table.model_dump()
    data["impairments"][2]["applies"]["not_with"] = ["no_such_impairment"]
    with pytest.raises(ValueError, match="prediabetes: no_such_impairment"):
        RuleTable.model_validate(data)


# ---------------------------------------------------------------------------
# Rerun and the committed files
# ---------------------------------------------------------------------------


def test_story_2_1_regenerating_gives_the_same_manual_and_rule_table() -> None:
    first, second = render_manual(MANUAL), render_manual(MANUAL)

    # Byte for byte in one process, so nothing in the file depends on the run.
    assert first.pdf == second.pdf
    assert first.page_texts == second.page_texts
    assert (
        rule_table_for(MANUAL, first).model_dump_json()
        == rule_table_for(MANUAL, second).model_dump_json()
    )


def test_story_2_1_committed_manual_and_rule_table_are_what_the_generator_writes(
    generated: Path, pages: list[str]
) -> None:
    # Acceptance: a fresh run leaves `data/` unchanged in git.
    assert (COMMITTED / RULE_TABLE_FILE).read_bytes() == (
        generated / RULE_TABLE_FILE
    ).read_bytes()
    assert extracted_pages((COMMITTED / MANUAL_PDF).read_bytes()) == pages
    assert _structure(COMMITTED / MANUAL_PDF) == _structure(generated / MANUAL_PDF)


def test_story_2_1_committed_folders_hold_nothing_but_the_generated_files() -> None:
    assert _visible(COMMITTED / MANUAL_FOLDER) == [MANUAL_FILE_NAME]
    assert _visible(COMMITTED / "answer-key") == ["cases", "rule-table.json"]
    # The manual is what gets indexed; the answers stay out of its folder.
    assert RULE_TABLE_FILE.startswith("answer-key/")


# ---------------------------------------------------------------------------
# The rules cover what the existing cases hold
# ---------------------------------------------------------------------------


def _lab(case: CaseDefinition, test: str) -> Decimal:
    return next(
        Decimal(item.value) for item in case.clinical.lab_results if item.test == test
    )


def _readings(case: CaseDefinition) -> dict[str, Decimal | str]:
    """A case's own figures, by the measure each one is a reading of."""
    clinical = case.clinical
    systolic = [reading.systolic_mmhg for reading in clinical.blood_pressure]
    readings: dict[str, Decimal | str] = {
        "hba1c": _lab(case, "HbA1c"),
        "systolic_blood_pressure": Decimal(round(sum(systolic) / len(systolic))),
        "diastolic_blood_pressure": Decimal(
            max(reading.diastolic_mmhg for reading in clinical.blood_pressure)
        ),
        "bmi": clinical.bmi,
        "ldl_cholesterol": _lab(case, "LDL cholesterol"),
        "egfr": _lab(case, "eGFR"),
        "smoking_status": "never_smoker",
        "pack_years": Decimal(0),
    }
    smoking = re.match(
        r"Current smoker, (\d+) cigarettes a day for (\d+) years",
        clinical.smoking_status,
    )
    if smoking:
        per_day, years = map(int, smoking.groups())
        readings["smoking_status"] = "current_smoker"
        readings["pack_years"] = Decimal(per_day * years // 20)
    else:
        assert clinical.smoking_status == "Never smoked"
    return readings


def _rules_met(table: RuleTable, case: CaseDefinition) -> set[str]:
    """The rules a case meets, by the applicability and thresholds the table records."""
    conditions = [item.condition.lower() for item in case.clinical.diagnoses]
    diagnosed = {
        item.impairment_id
        for item in table.impairments
        if any(item.name.lower() in c or c in item.name.lower() for c in conditions)
    }
    met: set[str] = set()
    for item in table.impairments:
        applies = item.applies
        if applies.basis == "diagnosis" and item.impairment_id not in diagnosed:
            continue
        if diagnosed.intersection(applies.not_with):
            continue
        for measure in item.measures:
            reading = _readings(case).get(measure.key)
            if reading is None:
                continue
            rules = [
                rule.rule_id
                for rule in _rules_of(table, item.impairment_id)
                if rule.threshold.measure == measure.key
                and rule.threshold.holds(reading)
            ]
            gaps = [
                g
                for g in item.gaps
                if g.threshold.measure == measure.key and g.threshold.holds(reading)
            ]
            # Where a section applies, a reading is never left unaccounted for.
            assert len(rules) + len(gaps) == 1, (case.case_id, measure.key)
            met.update(rules)
    return met


@pytest.mark.parametrize(
    ("case_id", "expected"),
    [
        ("case-001", {"UW-DM-002"}),
        ("case-002", {"UW-HT-002", "UW-TOB-001"}),
        ("case-003", set()),
    ],
)
def test_story_2_1_rules_cover_what_the_existing_cases_hold(
    table: RuleTable, case_id: str, expected: set[str]
) -> None:
    case = next(case for case in CASES if case.case_id == case_id)

    assert _rules_met(table, case) == expected


def test_story_2_1_the_table_says_when_a_rule_applies(table: RuleTable) -> None:
    # HbA1c 6.0 % is inside a band of three impairments; applicability tells them apart.
    reading = Decimal("6.0")
    holding = {
        rule.impairment_id
        for rule in table.rules
        if rule.threshold.measure == "hba1c" and rule.threshold.holds(reading)
    }
    assert holding == {"type_2_diabetes", "type_1_diabetes", "prediabetes"}
    basis = {i: _impairment(table, i).applies for i in holding}
    assert (
        basis["type_2_diabetes"].basis == basis["type_1_diabetes"].basis == "diagnosis"
    )
    assert basis["prediabetes"].basis == "reading"
    assert set(basis["prediabetes"].not_with) == {"type_2_diabetes", "type_1_diabetes"}
    assert basis["type_2_diabetes"].not_with == ("type_1_diabetes",)
    assert {item.applies.basis for item in table.impairments} == {
        "diagnosis",
        "reading",
    }


def test_story_2_1_demo_path_rules_read_as_the_case_documents_do(
    table: RuleTable, pages: list[str]
) -> None:
    by_id = {rule.rule_id: rule for rule in table.rules}

    assert by_id["UW-DM-002"].impairment == "Type 2 diabetes mellitus"
    assert by_id["UW-DM-002"].threshold.words == "HbA1c from 7.0 to below 8.0 %"
    assert by_id["UW-HT-002"].threshold.words == (
        "systolic blood pressure from 140 to below 160 mmHg"
    )
    assert by_id["UW-TOB-001"].threshold.words == "smoking status: current smoker"
    assert "see rule UW-TOB-001" in _definition(pages, by_id["UW-HT-002"])
