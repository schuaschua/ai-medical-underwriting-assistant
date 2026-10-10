"""Lay the underwriting manual out as a PDF with a real text layer.

The page design serves the stories that read the manual later: each rule's definition
is one paragraph that starts `Rule <rule_id>:`, carries its section number and is
never split across pages, and every section heading carries its number.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from functools import cache

import pymupdf

from synthdata.manual_model import (
    APPLIES_ON_FILE,
    APPLIES_TO_ANY,
    APPLIES_UNLESS,
    NOT_ON_A_READING,
    ImpairmentSpec,
    ManualSpec,
    Measure,
    Reference,
    RuleSpec,
    Source,
    Threshold,
    edges_of,
)
from synthdata.render import PDF_DATE

MANUAL_FILE_NAME = "underwriting-manual.pdf"
# The headings of an impairment's section; the number is the part's number.
PARTS: dict[int, str] = {
    1: "The impairment",
    2: "Key questions",
    3: "Evidence and readings",
    4: "Probable rating",
    5: "Worked examples and related rules",
}
INVENTED = (
    "The debit percentages and the decisions to decline are invented for this "
    "synthetic manual. The thresholds follow the public source named in each rule, "
    "except the edges a rule marks as this manual's own."
)
NO_RULE = "no rule of this section"


def footer_lines(checked: int, total: int) -> tuple[str, str]:
    """The two lines at the foot of every page of the manual.

    Not the cases' footer: a case's figures are all invented, the manual's thresholds
    are not. `checked` of `total` is the state of the review file, so the footer says
    how far the thresholds, citations and conversions have been checked.
    """
    return (
        "SYNTHETIC TEST DOCUMENT. Its ratings (debits and declines) are invented.",
        (
            "Its clinical thresholds follow public guidelines: "
            f"{checked} of {total} items checked against their sources."
        ),
    )


# A5 in points: the format of a desk handbook.
_WIDTH, _HEIGHT = 420.0, 595.0
_LEFT, _RIGHT, _TOP = 46.0, 374.0, 64.0
_HEADER_Y = 34.0
_FOOTER_Y = _HEIGHT - 30
_FOOTER_SIZE = 7.0
# Body text stops this far above the first footer line.
_FLOOR = _FOOTER_Y - 20
_REGULAR, _BOLD = "helv", "hebo"
_BODY = 10.5
_SMALL = 9.0
# Two texts on one line (a contents entry and its page) keep this much between them.
_CLEAR = 12.0

_JOBS = (
    "a librarian",
    "a bus driver",
    "a pastry cook",
    "a surveyor",
    "a school caretaker",
    "a florist",
    "a payroll clerk",
    "a joiner",
    "a dental technician",
    "a translator",
    "a market gardener",
)
_DOCUMENTS = (
    "attending physician's statement",
    "most recent clinic letter",
    "report on file",
    "specialist's letter",
)


@dataclass(frozen=True)
class RenderedManual:
    pdf: bytes
    # What was written on each page, one string per page.
    page_texts: tuple[str, ...]
    # What every page's text ends with.
    footer: str
    # The 1-based page the contents start on.
    contents_page: int
    # Every numbered section in order: its title and the 1-based page it starts on.
    section_titles: dict[str, str]
    section_pages: dict[str, int]
    # The section number of each impairment, by `impairment_id`.
    sections: dict[str, str]
    # The 1-based page that prints each rule's definition.
    rule_pages: dict[str, int]


def extracted_pages(pdf: bytes) -> list[str]:
    """The text a reader of the PDF gets from each page, white space squashed."""
    with pymupdf.open(stream=pdf, filetype="pdf") as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        return [" ".join(page.get_text().split()) for page in document]


def _leading(size: float) -> float:
    return round(size * 1.4, 1)


@cache
def _word_width(word: str, font: str, size: float) -> float:
    # Measuring is the slow part of the layout, and the same words come back all the time.
    return float(pymupdf.get_text_length(word, fontname=font, fontsize=size))


def _text_width(text: str, font: str, size: float) -> float:
    """The width of `text`: these fonts have no kerning, so it is the sum of its words."""
    words = text.split(" ")
    spaces = (len(words) - 1) * _word_width(" ", font, size)
    return sum(_word_width(word, font, size) for word in words) + spaces


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    used = 0.0
    space = _word_width(" ", font, size)
    for word in text.split():
        length = _word_width(word, font, size)
        if length > width:
            raise ValueError(f"{word!r} is too wide for its place on the page")
        if current and used + space + length > width:
            lines.append(" ".join(current))
            current, used = [word], length
        else:
            used += (space if current else 0.0) + length
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


@dataclass
class _Book:
    """The manual being written page by page; it remembers every string it draws."""

    title: str
    footer: tuple[str, ...]
    document: pymupdf.Document = field(
        default_factory=lambda: pymupdf.open()  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    )
    written: list[list[str]] = field(default_factory=list)
    # The 1-based page each numbered section starts on.
    section_pages: dict[str, int] = field(default_factory=dict)
    # Everything on a page is drawn through one shape: one content stream per page
    # keeps the file small and the layout quick.
    _shape: pymupdf.Shape | None = None
    _y: float = _TOP

    @property
    def page_number(self) -> int:
        return len(self.written)

    def new_page(self) -> None:
        self._commit()
        page = self.document.new_page(width=_WIDTH, height=_HEIGHT)
        self._shape = page.new_shape()  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        self.written.append([])
        self._pair(_HEADER_Y, self.title, f"Page {self.page_number}", 7.5)
        self._rule(_HEADER_Y + 6, 0.4)
        self._y = _TOP

    def _commit(self) -> None:
        if self._shape is not None:
            # Drawn last, so that the footer is also last in the page's text.
            for index, line in enumerate(self.footer):
                y = _FOOTER_Y + index * _leading(_FOOTER_SIZE)
                self._draw(_LEFT, y, line, _FOOTER_SIZE, _REGULAR)
            self._shape.commit()
            self._shape = None

    def _on_page(self) -> pymupdf.Shape:
        if self._shape is None:
            raise ValueError("no page has been started")
        return self._shape

    def _draw(self, x: float, y: float, text: str, size: float, font: str) -> None:
        if x + _text_width(text, font, size) > _RIGHT + 0.5:
            raise ValueError(f"{text!r} is too wide for its place on the page")
        self._on_page().insert_text((x, y), text, fontsize=size, fontname=font)
        self.written[-1].append(text)

    def _pair(self, y: float, left: str, right: str, size: float) -> None:
        """One text at the left margin and one at the right, which must not meet."""
        x = _RIGHT - _text_width(right, _REGULAR, size)
        if _LEFT + _text_width(left, _REGULAR, size) + _CLEAR > x:
            raise ValueError(f"{left!r} runs into {right!r} on the same line")
        self._draw(_LEFT, y, left, size, _REGULAR)
        self._draw(x, y, right, size, _REGULAR)

    def _rule(self, y: float, width: float) -> None:
        shape = self._on_page()
        shape.draw_line((_LEFT, y), (_RIGHT, y))
        shape.finish(width=width)

    def _fit(self, height: float, what: str) -> None:
        """Move to a new page unless `height` fits here; fail if no page could hold it."""
        if height > _FLOOR - _TOP:
            raise ValueError(f"{what} is taller than a page")
        if self._shape is None or self._y + height > _FLOOR:
            self.new_page()

    def space(self, points: float) -> None:
        self._y += points

    def paragraph(
        self,
        text: str,
        *,
        size: float = _BODY,
        bold: bool = False,
        indent: float = 0.0,
        after: float = 6.0,
        keep_with: float = 0.0,
    ) -> None:
        """Wrapped text that is never split across pages.

        `keep_with` is room that must also be free below it, for a heading.
        """
        font = _BOLD if bold else _REGULAR
        lines = _wrap(text, font, size, _RIGHT - _LEFT - indent)
        self._fit(
            (len(lines) - 1) * _leading(size) + keep_with,
            f"the paragraph starting {text[:40]!r}",
        )
        for line in lines:
            self._draw(_LEFT + indent, self._y, line, size, font)
            self._y += _leading(size)
        self._y += after

    def heading(self, text: str, size: float) -> None:
        if self._y > _TOP:
            self._y += 6
        self.paragraph(
            text, size=size, bold=True, after=4, keep_with=4 * _leading(_BODY)
        )

    def label(self, text: str) -> None:
        """A small bold heading inside a part, kept with the paragraph below it."""
        self.paragraph(text, bold=True, after=2, keep_with=3 * _leading(_BODY))

    def table(
        self,
        columns: Sequence[float],
        header: Sequence[str],
        rows: Sequence[Sequence[str]],
    ) -> None:
        """A table with wrapped cells, kept on one page."""
        step = _leading(_SMALL)
        limits = (*columns[1:], _RIGHT)

        def cells(row: Sequence[str], font: str) -> list[list[str]]:
            return [
                _wrap(cell, font, _SMALL, limit - x - 6)
                for x, cell, limit in zip(columns, row, limits, strict=True)
            ]

        head = cells(header, _BOLD)
        body = [cells(row, _REGULAR) for row in rows]
        height = sum(max(map(len, row)) * step + 5 for row in (head, *body))
        self._fit(height, f"the table starting {rows[0][0]!r}")
        for row, font in ((head, _BOLD), *((item, _REGULAR) for item in body)):
            for x, lines in zip(columns, row, strict=True):
                for index, line in enumerate(lines):
                    self._draw(x, self._y + index * step, line, _SMALL, font)
            self._y += max(map(len, row)) * step
            self._rule(self._y - step + 4, 0.7 if font == _BOLD else 0.3)
            self._y += 5
        self._y += 6

    def section(self, number: int, title: str) -> None:
        """Start a numbered section on a new page, its number printed with the heading."""
        self.new_page()
        self.section_pages[str(number)] = self.page_number
        self.heading(f"{number} {title}", 16)

    def entry(self, left: str, right: str) -> None:
        """A contents line: the section on the left, its page on the right."""
        self._fit(0, "a contents line")
        self._pair(self._y, left, right, _BODY)
        self._y += _leading(_BODY)

    def finish(self) -> tuple[bytes, tuple[str, ...]]:
        self._commit()
        self.document.set_metadata(
            {
                "title": self.title,
                "subject": "Synthetic test document. Every rating is invented.",
                "creator": "synthdata",
                "producer": "synthdata",
                "creationDate": PDF_DATE,
                "modDate": PDF_DATE,
            }
        )
        # `no_new_id` leaves out the random file id, the last thing that would differ per run.
        pdf: bytes = self.document.tobytes(  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            deflate=True, no_new_id=True
        )
        self.document.close()  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        return pdf, tuple("\n".join(page) for page in self.written)


# ---------------------------------------------------------------------------
# Wording: every sentence about a rule is built here from the rule itself
# ---------------------------------------------------------------------------


def reference_words(rule_id: str) -> str:
    """How a rule is mentioned outside its definition; it never matches the definition pattern."""
    return f"see rule {rule_id}"


def reference_sentence(reference: Reference) -> str:
    """One pointer as the definition prints it."""
    sentence = f"If {reference.when}, {reference_words(reference.rule_id)}"
    if reference.relation == "replaces":
        return f"{sentence}, which applies in place of this rule."
    return f"{sentence}."


def source_words(rule: RuleSpec, own: Sequence[Decimal]) -> str:
    """Where each end of the band comes from: the cited source, or this manual."""
    edges = edges_of(rule.threshold, own)
    amounts = {
        origin: [rule.threshold.amount(e.value) for e in edges if e.origin == origin]
        for origin in ("source", "manual")
    }
    mine = " and ".join(amounts["manual"])
    plural = len(amounts["manual"]) > 1
    if not amounts["manual"]:
        return f"Source of the threshold: {rule.source.citation}."
    if not amounts["source"]:
        return (
            f"The edge{'s' if plural else ''} at {mine} {'are' if plural else 'is'} "
            "this manual's own and no guideline supplies "
            f"{'them' if plural else 'it'}. The source for the measure and the "
            f"other edges of this section: {rule.source.citation}."
        )
    return (
        f"Source of the edge at {' and '.join(amounts['source'])}: "
        f"{rule.source.citation}. The edge at {mine} is this manual's own."
    )


def applicability_words(spec: ManualSpec, impairment: ImpairmentSpec) -> str:
    """The one sentence of a definition that says when the impairment's rules apply.

    A definition is read on its own, as a search returns it: without this
    sentence a healthy reading would lie inside the band of an impairment
    that needs a diagnosis.
    """
    names = {item.impairment_id: item.name for item in spec.impairments}
    unless = (
        APPLIES_UNLESS.format(
            names=" or ".join(names[other] for other in impairment.applies.not_with)
        )
        if impairment.applies.not_with
        else ""
    )
    if impairment.applies.basis == "diagnosis":
        return (
            APPLIES_ON_FILE.format(name=impairment.name)
            + unless
            + NOT_ON_A_READING
            + "."
        )
    return APPLIES_TO_ANY + unless + "."


def definition_words(spec: ManualSpec, rule: RuleSpec, section: str) -> str:
    """The one paragraph that defines `rule`: complete without the rest of the section."""
    impairment = spec.impairment_of(rule.rule_id)
    parts = [
        f"Rule {rule.rule_id}: {impairment.name} (section {section}.4).",
        applicability_words(spec, impairment),
        f"Threshold: {rule.threshold.words}.",
        f"Probable rating: {rule.rating_words}.",
    ]
    if rule.note:
        parts.append(rule.note)
    # A definition is read on its own: which of several readings counts is said here
    # too, from the data that part 3 of the section is printed from.
    choice = impairment.reading_rule(rule.threshold.measure)
    if choice is not None:
        parts.append(choice.definition_words())
    parts += [reference_sentence(reference) for reference in spec.references(rule)]
    parts.append(source_words(rule, impairment.own_edges))
    return " ".join(parts)


def example_reading(threshold: Threshold, measure: Measure) -> Decimal | str:
    """A reading that meets `threshold`, for a worked example: the same one every run.

    It stays inside the range of readings the measure can take.
    """
    interval = threshold.interval()
    if interval is None:
        return str(threshold.category)
    lower, _, upper, _ = interval
    ends = threshold.ends()
    step = Decimal(1).scaleb(min(int(end.as_tuple().exponent) for end in ends))
    if lower is not None and upper is not None:
        reading = ((lower + upper) / 2).quantize(step, rounding=ROUND_HALF_EVEN)
    else:
        edge = ends[0]
        margin = max(step, (abs(edge) * Decimal("0.08")).quantize(step))
        reading = edge + margin if upper is None else edge - margin
        if measure.maximum is not None:
            reading = min(reading, measure.maximum)
        if measure.minimum is not None:
            reading = max(reading, measure.minimum)
    if not threshold.holds(reading) or not measure.allows(reading):
        raise ValueError(f"no example reading for {threshold.words!r}")
    return reading


def combination_words(rule: RuleSpec, other: RuleSpec, reference: Reference) -> str:
    """What the ratings of two rules come to, for a worked example."""
    if reference.relation == "replaces":
        return (
            f"If {reference.when}, rule {other.rule_id} would apply in place of "
            f"this one, and the probable rating would be {other.rating_words}."
        )
    if rule.decline or other.decline:
        outcome = "the outcome would be decline"
    else:
        total = (rule.debit_pct or 0) + (other.debit_pct or 0)
        outcome = f"the two together would give +{total} %"
    return (
        f"If {reference.when}, rule {other.rule_id} would apply as well; its "
        f"rating is {other.rating_words}, and {outcome}."
    )


def _example(
    spec: ManualSpec, impairment: ImpairmentSpec, rule: RuleSpec, section: int, k: int
) -> str:
    threshold = rule.threshold
    measure = next(m for m in impairment.measures if m.key == threshold.measure)
    # Invented applicants, varied by arithmetic so that every run writes the same ones.
    age = 31 + (section * 7 + k * 11) % 30
    job = _JOBS[(section * 3 + k * 5) % len(_JOBS)]
    document = _DOCUMENTS[(section + k) % len(_DOCUMENTS)]
    reading = threshold.reading_words(example_reading(threshold, measure))
    met = f"rule {rule.rule_id} ({threshold.words})"
    opening = (
        (
            f"An applicant aged {age}, {job}, has {impairment.phrase}. The "
            f"{document} records {reading}. That meets {met}, so the probable "
            f"rating is {rule.rating_words}."
        ),
        (
            f"The {document} for {job} aged {age} with {impairment.phrase} records "
            f"{reading}. This is covered by {met}. Probable rating: "
            f"{rule.rating_words}."
        ),
        (
            f"Take {job} of {age} with {impairment.phrase}. On file, from the "
            f"{document}: {reading}. The threshold met is that of {met}, and the "
            f"probable rating is {rule.rating_words}."
        ),
    )[(section + 2 * k) % 3]
    sentences = [f"Example {k}. {opening}"]
    for reference in spec.references(rule)[:1]:
        sentences.append(
            combination_words(rule, spec.rule(reference.rule_id), reference)
        )
    return " ".join(sentences)


def edge_lines(impairment: ImpairmentSpec) -> list[str]:
    """For every edge of every band, the rule a reading exactly on it meets."""
    lines: list[str] = []
    for measure in impairment.measures:
        rules = [r for r in impairment.rules if r.threshold.measure == measure.key]
        for edge in sorted({end for r in rules for end in r.threshold.ends()}):
            met = [r for r in rules if r.threshold.holds(edge)]
            reading = rules[0].threshold.reading_words(edge)
            lines.append(
                f"{reading} exactly, rule {met[0].rule_id} ({met[0].rating_words})"
                if met
                else f"{reading} exactly, {NO_RULE}"
            )
    return lines


def _sources(impairment: ImpairmentSpec) -> list[Source]:
    return list(dict.fromkeys(rule.source for rule in impairment.rules))


def _measured(impairment: ImpairmentSpec) -> str:
    measures = [
        f"{measure.label} ({measure.unit})" if measure.unit_printed else measure.label
        for measure in impairment.measures
    ]
    grounded = "; ".join(
        f"{source.body}, {source.guideline} ({source.year})"
        for source in _sources(impairment)
    )
    return (
        f"What is measured. The rating in this section depends on "
        f"{' and on '.join(measures)}. The thresholds are grounded in: {grounded}."
    )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def _front_matter(book: _Book, spec: ManualSpec) -> None:
    book.new_page()
    book.space(90)
    book.paragraph(spec.title, size=24, bold=True, after=10)
    book.paragraph(
        "Medical impairments, key questions and probable ratings for life insurance",
        size=12,
        after=40,
    )
    for text in (
        "A synthetic document, generated for a software demonstration.",
        (
            "No insurer or reinsurer wrote, uses or endorses this manual, and none of "
            "its wording comes from any insurer's or reinsurer's manual."
        ),
        INVENTED,
        "It must not be used to assess any real person.",
    ):
        book.paragraph(text, after=10)


def _contents(
    book: _Book, titles: Mapping[str, str], pages: Mapping[str, int] | None
) -> int:
    book.new_page()
    first = book.page_number
    book.heading("Contents", 16)
    book.space(6)
    for number, title in titles.items():
        # On the first pass the pages are not known yet; the line takes the same room.
        book.entry(f"{number}  {title}", str(pages[number]) if pages else "0")
    return first


def _introduction(book: _Book, spec: ManualSpec) -> None:
    book.section(1, "Introduction")
    for index, (title, paragraphs) in enumerate(spec.introduction, start=1):
        book.heading(f"1.{index} {title}", 12)
        for text in paragraphs:
            book.paragraph(text)


def _impairment(
    book: _Book,
    spec: ManualSpec,
    impairment: ImpairmentSpec,
    number: int,
    place: Mapping[str, str],
    rule_pages: dict[str, int],
) -> None:
    def part(index: int) -> None:
        book.heading(f"{number}.{index} {PARTS[index]}", 12)

    book.section(number, impairment.name)
    part(1)
    for text in impairment.overview:
        book.paragraph(text)
    book.paragraph(f"When this section applies. {impairment.applies.words}")
    book.paragraph(_measured(impairment))

    part(2)
    for index, item in enumerate(impairment.questions, start=1):
        book.paragraph(
            f"{index}. {item.question}",
            bold=True,
            after=2,
            keep_with=2 * _leading(_BODY),
        )
        book.paragraph(item.why, indent=12)

    part(3)
    for text in impairment.evidence:
        book.paragraph(text)
    labels = {measure.key: measure.label for measure in impairment.measures}
    for choice in impairment.reading_rules:
        book.paragraph(choice.words(labels[choice.measure]))
    book.label("Pitfalls with this evidence.")
    for text in impairment.pitfalls:
        book.paragraph(text)
    conversions = [m for m in impairment.measures if m.conversion]
    if conversions:
        book.label("Converting units.")
        for measure in conversions:
            book.paragraph(f"{measure.label}: {measure.conversion}")

    part(4)
    book.paragraph(
        f"The table lists every rule of this section; each is defined in full below "
        f"it. {INVENTED}"
    )
    book.table(
        (_LEFT, 122, 288),
        ("Rule id", "Measure and band", "Probable rating"),
        [
            (rule.rule_id, rule.threshold.words, rule.rating_words)
            for rule in impairment.rules
        ],
    )
    book.label("Readings that meet no rule.")
    if not impairment.gaps:
        book.paragraph(
            "None: every reading these measures can take falls in one of the bands above."
        )
    for gap in impairment.gaps:
        book.paragraph(f"{gap.threshold.words}: {gap.meaning}.", after=3)
    book.paragraph(f"Note on the bands. {impairment.rating_note}")
    for rule in impairment.rules:
        book.paragraph(definition_words(spec, rule, str(number)), after=8)
        rule_pages[rule.rule_id] = book.page_number
    book.label("What does not change the rating.")
    for text in impairment.unchanged:
        book.paragraph(text)

    part(5)
    for k, rule in enumerate(impairment.rules, start=1):
        book.paragraph(_example(spec, impairment, rule, number, k))
    edges = edge_lines(impairment)
    if edges:
        book.paragraph(f"Readings on an edge: {'; '.join(edges)}.")
    book.label("Common combinations.")
    for text in impairment.combinations:
        book.paragraph(text)
    outward = [
        f"Under rule {rule.rule_id}, if {reference.when}, "
        f"{reference_words(reference.rule_id)} ({place[reference.rule_id]})."
        for rule in impairment.rules
        for reference in spec.references(rule)
    ]
    own = {rule.rule_id for rule in impairment.rules}
    inward = [
        f"rule {other.rule_id} ({place[other.rule_id]})"
        for other in spec.rules()
        if other.rule_id not in own and own.intersection(other.see)
    ]
    book.label("Related rules.")
    if not outward and not inward:
        book.paragraph(
            "No rule of this section points to another rule, and none points here."
        )
    for text in outward:
        book.paragraph(text, after=3)
    if inward:
        book.paragraph(
            f"Rules of other sections that point to this one: {'; '.join(inward)}."
        )


def _glossary(book: _Book, spec: ManualSpec, number: int) -> None:
    book.section(number, "Glossary")
    for item in spec.glossary:
        book.paragraph(item.term, bold=True, after=1, keep_with=2 * _leading(_BODY))
        book.paragraph(item.meaning, indent=12)


def _source_list(
    book: _Book, spec: ManualSpec, number: int, sections: Mapping[str, str]
) -> None:
    book.section(number, "Public sources")
    book.paragraph(
        "The thresholds in this manual rest on the public guidelines below, apart "
        "from the edges that a rule marks as the manual's own. The ratings rest on "
        "nothing: they are invented."
    )
    users: dict[tuple[str, str, int], list[str]] = {}
    bodies: dict[tuple[str, str, int], str] = {}
    for impairment in spec.impairments:
        for source in _sources(impairment):
            key = (source.abbreviation, source.guideline, source.year)
            bodies[key] = source.body
            users.setdefault(key, []).append(
                f"section {sections[impairment.impairment_id]} "
                f"({impairment.name}: {source.locator})"
            )
    for key in sorted(users):
        abbreviation, guideline, year = key
        book.paragraph(
            f"{bodies[key]} ({abbreviation}). {guideline}. {year}.",
            bold=True,
            after=2,
            keep_with=2 * _leading(_BODY),
        )
        book.paragraph(f"Relied on in {'; '.join(users[key])}.", indent=12)


def section_plan(spec: ManualSpec) -> tuple[dict[str, str], dict[str, str]]:
    """The title of every numbered section, and each impairment's section number."""
    # Section 1 is the introduction; the impairments follow in the order of the table.
    sections = {
        item.impairment_id: str(index)
        for index, item in enumerate(spec.impairments, start=2)
    }
    last = len(sections) + 1
    titles = {
        "1": "Introduction",
        **{sections[item.impairment_id]: item.name for item in spec.impairments},
        str(last + 1): "Glossary",
        str(last + 2): "Public sources",
    }
    return titles, sections


def _compose(
    spec: ManualSpec, pages: Mapping[str, int] | None, footer: tuple[str, ...]
) -> tuple[_Book, int, dict[str, int]]:
    titles, sections = section_plan(spec)
    # Where a mention sends the reader: the part that holds the rule's definition.
    place = {
        rule.rule_id: f"section {sections[item.impairment_id]}.4, {item.name}"
        for item in spec.impairments
        for rule in item.rules
    }
    book = _Book(title=spec.title, footer=footer)
    rule_pages: dict[str, int] = {}
    _front_matter(book, spec)
    contents_page = _contents(book, titles, pages)
    _introduction(book, spec)
    for impairment in spec.impairments:
        number = int(sections[impairment.impairment_id])
        _impairment(book, spec, impairment, number, place, rule_pages)
    _glossary(book, spec, len(sections) + 2)
    _source_list(book, spec, len(sections) + 3, sections)
    return book, contents_page, rule_pages


def render_manual(spec: ManualSpec, footer: tuple[str, ...]) -> RenderedManual:
    """Render the manual, with `footer` (see `footer_lines`) at the foot of every page.

    The same definition always gives the same text, and the same bytes on the same
    PyMuPDF build. It is laid out twice: the contents page needs the page numbers
    that only a first layout can tell.
    """
    draft, _, _ = _compose(spec, None, footer)
    section_pages = dict(draft.section_pages)
    draft.finish()
    book, contents_page, rule_pages = _compose(spec, section_pages, footer)
    if book.section_pages != section_pages:
        raise ValueError("the contents page changed the page numbers it lists")
    pdf, page_texts = book.finish()
    titles, sections = section_plan(spec)
    return RenderedManual(
        pdf=pdf,
        page_texts=page_texts,
        footer=" ".join(footer),
        contents_page=contents_page,
        section_titles=titles,
        section_pages=section_pages,
        sections=sections,
        rule_pages=rule_pages,
    )
