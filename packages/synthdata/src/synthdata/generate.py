"""Build the answer key and write it, the case PDFs, the training pages and the manual to disk."""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path

import pymupdf

from contracts.enums import Verdict
from contracts.rules import RULE_DEFINITION_PATTERN, is_medical, rule_ids_defined_in
from synthdata.cases import CASES
from synthdata.expected import expected_for, squash
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
    LAYOUTS_WITHOUT_TEXT,
    NON_MEDICAL_LAYOUTS,
    PAGE_TYPE_OF_LAYOUT,
    AnswerKeyEntry,
    CaseDefinition,
    ExpectedPage,
    IdentifierCategory,
    PageCount,
    PageKind,
    PageSet,
    PageSetDocument,
    PageSetPage,
    PlantedIdentifier,
    TrainingPage,
    TrainingSet,
)
from synthdata.render import FOOTER, RenderedCase, render_case, render_page
from synthdata.training import TRAINING_SUBJECTS

CASES_FOLDER = "cases"
# AD-17: the answer key has its own folder, which no service code reads.
ANSWER_KEY_FOLDER = "answer-key/cases"
MANUAL_FOLDER = "manual"
# AD-17: the rule table is part of the answer key. The manual is what gets indexed.
RULE_TABLE_FILE = "answer-key/rule-table.json"
# AD-17: the labels of the classifiers' scored pages are part of the answer key too.
PAGE_SET_FILE = "answer-key/page-set.json"
# AD-13: what a classifier is trained on. Its labels are not answers: the folder of a
# page is its page type, and the list of the pages is beside them.
TRAINING_FOLDER = "classifier-training"
TRAINING_LIST_FILE = "classifier-training/pages.json"
MIN_CASES_PER_VERDICT = 3


def extracted_page_texts(pdf: bytes) -> tuple[str, ...]:
    """The text a reader gets out of each page of a PDF, white space squashed."""
    with pymupdf.open(stream=pdf, filetype="pdf") as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        return tuple(squash(page.get_text()) for page in document)


def _may_also_be_redacted(
    case: CaseDefinition, texts: Sequence[str]
) -> tuple[str, ...]:
    """What redaction may mask besides the planted identifiers without being wrong.

    What a recogniser of names, addresses, organisations and numbers may reasonably
    take for one: each part of a name, each part of an address, organisations and
    names that read like one (a recipe's source), the occupation, the nationality,
    places, and reference numbers including the passport's. Dates, ages and medical
    terms are never listed: redaction keeps them (AD-21). What the real service
    masks is checked in Azure.
    """
    applicant, physician, clinical = case.applicant, case.physician, case.clinical
    candidates = [
        *applicant.name.split(),
        *physician.name.split(),
        f"Dr {physician.name}",
        physician.clinic_name,
        clinical.lab_name,
        clinical.lab_specimen_reference,
        case.insurer,
        applicant.occupation,
        # The street, the town and the postcode, from "<street>, <town>, <postcode>".
        *applicant.address.split(", "),
        *physician.clinic_address.split(", "),
    ]
    if case.invoice is not None:
        candidates += [case.invoice.issuer, case.invoice.invoice_number]
    if case.payslip is not None:
        candidates.append(case.payslip.employer)
    if case.utility_bill is not None:
        candidates.append(case.utility_bill.supplier)
    if case.passport is not None:
        passport = case.passport
        candidates += [
            passport.number,
            passport.country,
            passport.authority,
            passport.nationality,
            passport.place_of_birth,
        ]
    if case.recipe is not None:
        candidates.append(case.recipe.source)
    planted = {value for _, value in case.identifiers()}
    return tuple(
        sorted(
            {
                value
                for value in candidates
                if value not in planted and any(value in text for text in texts)
            }
        )
    )


def answer_key_for(case: CaseDefinition, rendered: RenderedCase) -> AnswerKeyEntry:
    """The known right answers for one case, checked against the PDF that was rendered.

    What was planted where is taken from the text the renderer wrote on each page;
    the facts, the rules and the verdict are worked out from the case's figures and
    the rule table (`expected_for`). Fails, naming the case, when the PDF's own text
    does not bear the entry out.
    """
    identifiers: list[PlantedIdentifier] = []
    for category, value in case.identifiers():
        occurrences = tuple(
            PageCount(page_number=number, count=text.count(value))
            for number, text in enumerate(rendered.page_texts, start=1)
            if value in text
        )
        if not occurrences:
            raise ValueError(f"{case.case_id}: {category} is not on any page")
        identifiers.append(
            PlantedIdentifier(
                category=category,
                value=value,
                pages=tuple(item.page_number for item in occurrences),
                occurrences=occurrences,
            )
        )
    pages_expected = tuple(
        ExpectedPage(
            page_number=number,
            page_type=PAGE_TYPE_OF_LAYOUT[spec.layout],
            is_medical=is_medical(PAGE_TYPE_OF_LAYOUT[spec.layout]),
            layout=spec.layout,
            rotation=spec.rotation,
            drawn_rotation=spec.drawn_rotation,
            has_text_layer=spec.layout not in LAYOUTS_WITHOUT_TEXT,
        )
        for number, spec in enumerate(case.pages, start=1)
    )
    expected = expected_for(case)
    entry = AnswerKeyEntry(
        case_id=case.case_id,
        file_name=case.file_name,
        summary=case.summary,
        pages=pages_expected,
        identifiers=tuple(identifiers),
        may_also_be_redacted=_may_also_be_redacted(case, rendered.page_texts),
        expected_facts=expected.facts,
        expected_rule_ids=expected.rule_ids,
        expected_verdict=expected.verdict,
    )
    check_against_pages(entry, extracted_page_texts(rendered.pdf))
    return entry


def check_against_pages(entry: AnswerKeyEntry, page_texts: Sequence[str]) -> None:
    """Fail unless the text of the case's pages bears its answer-key entry out.

    `page_texts` is the text of each page with white space squashed. Checked: every
    page has a text layer or none, as listed; every planted identifier is found on
    each page exactly as often as listed, and on no other; every expected fact's
    quote is on its page.
    """
    where = entry.case_id
    if len(page_texts) != len(entry.pages):
        raise ValueError(f"{where}: {len(page_texts)} pages, not {len(entry.pages)}")
    for page, text in zip(entry.pages, page_texts, strict=True):
        if page.has_text_layer != bool(text):
            raise ValueError(
                f"{where}: page {page.page_number} has "
                f"{'a' if text else 'no'} text layer, against its entry"
            )
    for item in entry.identifiers:
        listed = {each.page_number: each.count for each in item.occurrences}
        found = {
            number: text.count(squash(item.value))
            for number, text in enumerate(page_texts, start=1)
            if squash(item.value) in text
        }
        if found != listed:
            raise ValueError(
                f"{where}: {item.category} is listed as {listed} (page: count) and "
                f"found as {found}"
            )
    for fact in entry.expected_facts:
        for place in fact.places:
            if squash(place.quote) not in page_texts[place.page_number - 1]:
                raise ValueError(
                    f"{where}: the fact {fact.statement!r} is not found on page "
                    f"{place.page_number}"
                )


def check_coverage(entries: Sequence[AnswerKeyEntry]) -> None:
    """Fail unless the case set has enough cases of each of the four verdicts."""
    counts = Counter(entry.expected_verdict.verdict for entry in entries)
    for verdict in Verdict:
        if counts[verdict] < MIN_CASES_PER_VERDICT:
            raise ValueError(
                f"the case set has {counts[verdict]} cases with the verdict "
                f"{verdict}, fewer than {MIN_CASES_PER_VERDICT}"
            )


def _kind_of(page: ExpectedPage) -> PageKind:
    if not page.has_text_layer or page.rotation or page.drawn_rotation:
        return PageKind.EDGE
    return PageKind.MEDICAL if page.is_medical else PageKind.NON_MEDICAL


def page_set_for(entries: Sequence[AnswerKeyEntry]) -> PageSet:
    """The classifiers' scored pages: every page of every case, each with its label.

    The whole case set is the page set: every case but the first has a non-medical or
    an edge page, and the runner uploads whole files.
    """
    documents: list[PageSetDocument] = []
    for entry in entries:
        pages = entry.pages
        documents.append(
            PageSetDocument(
                case_id=entry.case_id,
                file_name=entry.file_name,
                mixed=any(page.is_medical for page in pages)
                and any(page.layout in NON_MEDICAL_LAYOUTS for page in pages),
                pages=tuple(
                    PageSetPage(**page.model_dump(), kind=_kind_of(page))
                    for page in pages
                ),
            )
        )
    return PageSet(documents=tuple(documents))


def training_pages() -> list[tuple[TrainingPage, RenderedCase]]:
    """Every page of the training set as a one-page PDF, with where it is written."""
    built: list[tuple[TrainingPage, RenderedCase]] = []
    for subject in TRAINING_SUBJECTS:
        for number, spec in enumerate(subject.pages, start=1):
            page_type = PAGE_TYPE_OF_LAYOUT[spec.layout]
            built.append(
                (
                    TrainingPage(
                        # The page number keeps two pages of one layout apart.
                        file=(
                            f"{page_type}/{subject.case_id}-p{number:02d}-"
                            f"{spec.layout}.pdf"
                        ),
                        page_type=page_type,
                        layout=spec.layout,
                    ),
                    render_page(subject, number),
                )
            )
    return built


def check_people_differ(
    cases: Sequence[CaseDefinition], subjects: Sequence[CaseDefinition]
) -> None:
    """Fail when two people of the cases and the training set share a name or an identifier."""
    seen: dict[str, str] = {}
    for person in (*cases, *subjects):
        for category, value in person.identifiers():
            # The physician and the practice count too: every case has its own.
            other = seen.setdefault(value, person.case_id)
            if other != person.case_id:
                raise ValueError(
                    f"{person.case_id} and {other} share the {category} {value!r}"
                )
        parts = {
            part
            for category, value in person.identifiers()
            if category is IdentifierCategory.PERSON_NAME
            for part in value.split()
        }
        for part in parts:
            other = seen.setdefault(f"name part {part}", person.case_id)
            if other != person.case_id:
                raise ValueError(
                    f"{person.case_id} and {other} share the name {part!r}"
                )


def check_disjoint(
    training: Mapping[str, RenderedCase], scored: Mapping[str, RenderedCase]
) -> None:
    """Fail when a training page is also a scored page (AD-13).

    A page is the same page when its text is the same, white space aside, or when it
    holds the same picture, byte for byte. A blank page has neither: there is nothing
    of a case on it, so both sets may have one.
    """
    texts = {
        squash(text): name
        for name, rendered in scored.items()
        for text in rendered.page_texts
        if squash(text)
    }
    pictures = {
        digest: name
        for name, rendered in scored.items()
        for digests in rendered.page_pictures
        for digest in digests
    }
    for file, rendered in training.items():
        for text, digests in zip(
            rendered.page_texts, rendered.page_pictures, strict=True
        ):
            if squash(text) in texts:
                raise ValueError(
                    f"{file}: its text is the text of a page of {texts[squash(text)]}"
                )
            for digest in digests:
                if digest in pictures:
                    raise ValueError(
                        f"{file}: its picture is a picture of {pictures[digest]}"
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
            reading_rules=item.reading_rules,
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
    """Write the cases, the answer key, the training pages, the manual and the rule table.

    Everything is built and checked first, so a failure leaves the folder as it was.
    Returns the files written.
    """
    manual, rule_table = build_manual()
    built = [(case, render_case(case)) for case in CASES]
    entries = [answer_key_for(case, rendered) for case, rendered in built]
    check_coverage(entries)
    page_set = page_set_for(entries)
    training = training_pages()
    training_set = TrainingSet(pages=tuple(page for page, _ in training))
    check_people_differ(CASES, TRAINING_SUBJECTS)
    check_disjoint(
        {page.file: rendered for page, rendered in training},
        {case.file_name: rendered for case, rendered in built},
    )
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
    page_set_path = data_dir / PAGE_SET_FILE
    page_set_path.write_text(
        page_set.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    written.append(page_set_path)
    for page, rendered in training:
        page_path = data_dir / TRAINING_FOLDER / page.file
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_bytes(rendered.pdf)
        written.append(page_path)
    training_list_path = data_dir / TRAINING_LIST_FILE
    training_list_path.write_text(
        training_set.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    written.append(training_list_path)
    manual_path = data_dir / MANUAL_FOLDER / MANUAL_FILE_NAME
    table_path = data_dir / RULE_TABLE_FILE
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_bytes(manual.pdf)
    table_path.write_text(rule_table.model_dump_json(indent=2) + "\n", encoding="utf-8")
    written += [manual_path, table_path]
    return written
