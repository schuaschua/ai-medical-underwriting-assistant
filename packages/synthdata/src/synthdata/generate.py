"""Build the answer key and write it, the case PDFs and the manual to disk."""

import re
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path

from contracts.rules import RULE_DEFINITION_PATTERN, is_medical, rule_ids_defined_in
from synthdata.cases import CASES
from synthdata.manual import (
    INVENTED,
    MANUAL_FILE_NAME,
    RenderedManual,
    extracted_pages,
    reference_sentence,
    render_manual,
    section_plan,
)
from synthdata.manual_model import (
    MAX_PAGES,
    MIN_PAGES,
    ManualSection,
    ManualSpec,
    RuleTable,
    RuleTableImpairment,
    RuleTableRule,
    edges_of,
)
from synthdata.manual_rules import MANUAL
from synthdata.model import (
    PAGE_TYPE_OF_LAYOUT,
    AnswerKeyEntry,
    CaseDefinition,
    ExpectedPage,
    PlantedIdentifier,
)
from synthdata.render import FOOTER, RenderedCase, render_case

CASES_FOLDER = "cases"
# AD-17: the answer key has its own folder, which no service code reads.
ANSWER_KEY_FOLDER = "answer-key/cases"
MANUAL_FOLDER = "manual"
# AD-17: the rule table is part of the answer key. The manual is what gets indexed.
RULE_TABLE_FILE = "answer-key/rule-table.json"


def answer_key_for(case: CaseDefinition, rendered: RenderedCase) -> AnswerKeyEntry:
    """Record what was planted where, from the text the renderer wrote on each page."""
    identifiers: list[PlantedIdentifier] = []
    for category, value in case.identifiers():
        pages = tuple(
            number
            for number, text in enumerate(rendered.page_texts, start=1)
            if value in text
        )
        if not pages:
            raise ValueError(f"{case.case_id}: {category} is not on any page")
        identifiers.append(
            PlantedIdentifier(category=category, value=value, pages=pages)
        )
    pages_expected = tuple(
        ExpectedPage(
            page_number=number,
            page_type=PAGE_TYPE_OF_LAYOUT[spec.layout],
            is_medical=is_medical(PAGE_TYPE_OF_LAYOUT[spec.layout]),
            layout=spec.layout,
            rotation=spec.rotation,
        )
        for number, spec in enumerate(case.pages, start=1)
    )
    return AnswerKeyEntry(
        case_id=case.case_id,
        file_name=case.file_name,
        summary=case.summary,
        pages=pages_expected,
        identifiers=tuple(identifiers),
    )


def check_text(
    spec: ManualSpec, rule_pages: Mapping[str, int], pages: Sequence[str]
) -> None:
    """Fail unless the manual's text says exactly what the rule table says.

    `pages` is the text of each page with white space squashed. Checked: the size, a
    text layer with the page number and the footer on every page, one definition per
    rule (AD-12) on the page recorded for it, and in that definition its section and
    every cross-reference.
    """
    if not MIN_PAGES <= len(pages) <= MAX_PAGES:
        raise ValueError(
            f"the manual has {len(pages)} pages, outside {MIN_PAGES} to {MAX_PAGES}"
        )
    for number, text in enumerate(pages, start=1):
        if f"Page {number} " not in text or not text.endswith(FOOTER):
            raise ValueError(
                f"page {number} has no text layer with its page number and the footer"
            )
    whole = " ".join(pages)
    wanted = [rule.rule_id for rule in spec.rules()]
    defined = re.findall(RULE_DEFINITION_PATTERN, whole)
    for rule_id in wanted:
        if defined.count(rule_id) != 1:
            raise ValueError(
                f"{rule_id}: the manual defines it {defined.count(rule_id)} times"
            )
    if rule_ids_defined_in(whole) != wanted:
        raise ValueError("the manual defines a rule the table lacks, or out of order")
    _, sections = section_plan(spec)
    for rule in spec.rules():
        impairment = spec.impairment_of(rule.rule_id)
        page = pages[rule_pages[rule.rule_id] - 1]
        marker = (
            f"Rule {rule.rule_id}: {impairment.name} "
            f"(section {sections[impairment.impairment_id]}.4)."
        )
        if marker not in page:
            raise ValueError(
                f"{rule.rule_id}: its definition with its section is not on page "
                f"{rule_pages[rule.rule_id]}"
            )
        start = page.index(marker)
        following = re.search(RULE_DEFINITION_PATTERN, page[start + 1 :])
        end = start + 1 + following.start() if following else len(page)
        for reference in spec.references(rule):
            if reference_sentence(reference) not in page[start:end]:
                raise ValueError(
                    f"{rule.rule_id}: its definition does not mention {reference.rule_id}"
                )


def check_manual(spec: ManualSpec, rendered: RenderedManual) -> None:
    """Check the text a reader gets out of the PDF, not the strings the renderer kept."""
    check_text(spec, rendered.rule_pages, extracted_pages(rendered.pdf))


def rule_table_for(spec: ManualSpec, rendered: RenderedManual) -> RuleTable:
    """Record every rule with where the rendered manual defines it."""
    check_manual(spec, rendered)
    impairments = tuple(
        RuleTableImpairment(
            impairment_id=item.impairment_id,
            code=item.code,
            name=item.name,
            section=rendered.sections[item.impairment_id],
            first_page=rendered.section_pages[rendered.sections[item.impairment_id]],
            applies=item.applies,
            measures=item.measures,
            gaps=item.gaps,
        )
        for item in spec.impairments
    )
    rules = tuple(
        RuleTableRule(
            rule_id=rule.rule_id,
            impairment_id=item.impairment_id,
            impairment=item.name,
            section=rendered.sections[item.impairment_id],
            manual_page=rendered.rule_pages[rule.rule_id],
            threshold=rule.threshold,
            debit_pct=rule.debit_pct,
            decline=rule.decline,
            postponement=rule.postponement,
            note=rule.note,
            source=rule.source,
            edges=edges_of(rule.threshold, item.own_edges),
            references=spec.references(rule),
            refers_to=rule.see,
        )
        for item in spec.impairments
        for rule in item.rules
    )
    return RuleTable(
        manual_file=MANUAL_FILE_NAME,
        manual_title=spec.title,
        page_count=len(rendered.page_texts),
        notice=INVENTED,
        contents_page=rendered.contents_page,
        sections=tuple(
            ManualSection(
                number=number, title=title, first_page=rendered.section_pages[number]
            )
            for number, title in rendered.section_titles.items()
        ),
        impairments=impairments,
        rules=rules,
    )


@cache
def build_manual() -> tuple[RenderedManual, RuleTable]:
    """The manual PDF and its rule table, both from the one definition in `manual_rules`."""
    rendered = render_manual(MANUAL)
    return rendered, rule_table_for(MANUAL, rendered)


def write_all(data_dir: Path) -> list[Path]:
    """Write the cases, their answer keys, the manual and the rule table under `data_dir`.

    Everything is built and checked first, so a failure leaves the folder as it was.
    Returns the files written.
    """
    manual, rule_table = build_manual()
    built = [(case, render_case(case)) for case in CASES]
    entries = [answer_key_for(case, rendered) for case, rendered in built]
    cases_dir = data_dir / CASES_FOLDER
    key_dir = data_dir / ANSWER_KEY_FOLDER
    cases_dir.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for (case, rendered), entry in zip(built, entries, strict=True):
        pdf_path = cases_dir / case.file_name
        key_path = key_dir / f"{case.case_id}.json"
        pdf_path.write_bytes(rendered.pdf)
        key_path.write_text(entry.model_dump_json(indent=2) + "\n", encoding="utf-8")
        written += [pdf_path, key_path]
    manual_path = data_dir / MANUAL_FOLDER / MANUAL_FILE_NAME
    table_path = data_dir / RULE_TABLE_FILE
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_bytes(manual.pdf)
    table_path.write_text(rule_table.model_dump_json(indent=2) + "\n", encoding="utf-8")
    written += [manual_path, table_path]
    return written
