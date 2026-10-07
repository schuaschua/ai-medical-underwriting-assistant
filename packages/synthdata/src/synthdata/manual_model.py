"""The underwriting manual's definition (what is rendered) and the rule table (what is recorded).

The rule table is the ground truth: the manual PDF and the rule table file are both
generated from one `ManualSpec`, so they cannot disagree (spine AD-12, AD-17).
"""

from collections.abc import Iterable, Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    model_validator,
)

from contracts.base import ContractModel, NonEmptyStr, OneLine, PageNumber, Percent
from contracts.rules import RuleId, is_rule_id
from synthdata.render import PDF_DATE

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
SectionNumber = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]


def _is_code(value: str) -> str:
    # AD-12: the contracts' pattern decides what the letters of a rule id may be.
    if not is_rule_id(f"UW-{value}-000"):
        raise ValueError("not the letters of a rule id")
    return value


ImpairmentCode = Annotated[str, AfterValidator(_is_code)]

MIN_PAGES, MAX_PAGES = 180, 220
# A source cannot be newer than the document that cites it.
LATEST_SOURCE_YEAR = int(PDF_DATE[2:6])


class Comparison(StrEnum):
    """How a reading is compared with a threshold."""

    BELOW = "below"  # reading < value
    AT_MOST = "at_most"  # reading <= value
    ABOVE = "above"  # reading > value
    AT_LEAST = "at_least"  # reading >= value
    BETWEEN = "between"  # lower to upper; each end says whether it is included
    EQUALS = "equals"  # the reading is the named category


# A band as numbers: lower end, whether it is included, upper end, whether it is
# included. `None` is an open end.
type Interval = tuple[Decimal | None, bool, Decimal | None, bool]


class Threshold(ContractModel):
    """What is measured, the comparison, the value or band, and the unit."""

    measure: Slug
    measure_label: OneLine
    unit: OneLine
    # A ratio, an index or a category is written without its unit.
    unit_printed: bool = True
    comparison: Comparison
    # For below, at_most, above and at_least.
    value: Decimal | None = None
    # For between.
    lower: Decimal | None = None
    lower_inclusive: bool | None = None
    upper: Decimal | None = None
    upper_inclusive: bool | None = None
    # For equals: the category as a value a program compares, e.g. `current_smoker`.
    category: Slug | None = None
    # The same threshold as the manual prints it.
    words: OneLine

    @model_validator(mode="after")
    def _fields_follow_the_comparison(self) -> Self:
        band = (self.lower, self.lower_inclusive, self.upper, self.upper_inclusive)
        given = {
            "value": self.value is not None,
            "band": any(part is not None for part in band),
            "category": self.category is not None,
        }
        if self.comparison is Comparison.BETWEEN:
            needed = "band"
            if any(part is None for part in band):
                raise ValueError("between needs both ends and whether each is included")
            if self.lower is None or self.upper is None or self.lower >= self.upper:
                raise ValueError("the lower end must be below the upper end")
        elif self.comparison is Comparison.EQUALS:
            needed = "category"
        else:
            needed = "value"
        if [name for name, present in given.items() if present] != [needed]:
            raise ValueError(f"{self.comparison} takes `{needed}` and nothing else")
        return self

    def interval(self) -> Interval | None:
        """The band as numbers; `None` for a category."""
        match self.comparison:
            case Comparison.BELOW:
                return (None, False, self.value, False)
            case Comparison.AT_MOST:
                return (None, False, self.value, True)
            case Comparison.ABOVE:
                return (self.value, False, None, False)
            case Comparison.AT_LEAST:
                return (self.value, True, None, False)
            case Comparison.BETWEEN:
                return (
                    self.lower,
                    bool(self.lower_inclusive),
                    self.upper,
                    bool(self.upper_inclusive),
                )
            case Comparison.EQUALS:
                return None

    def ends(self) -> tuple[Decimal, ...]:
        """The numbers that bound the band; none for a category."""
        interval = self.interval()
        if interval is None:
            return ()
        return tuple(end for end in (interval[0], interval[2]) if end is not None)

    def holds(self, reading: Decimal | str) -> bool:
        """Tell whether `reading` (a number, or a category for `equals`) meets the threshold."""
        interval = self.interval()
        if interval is None:
            return reading == self.category
        if isinstance(reading, str):
            return False
        lower, lower_inclusive, upper, upper_inclusive = interval
        if lower is not None and (
            reading < lower or (reading == lower and not lower_inclusive)
        ):
            return False
        return (
            upper is None or reading < upper or (reading == upper and upper_inclusive)
        )

    def overlaps(self, other: "Threshold") -> bool:
        """Tell whether some reading of the same measure meets both thresholds."""
        mine, theirs = self.interval(), other.interval()
        if mine is None or theirs is None:
            return mine is None and theirs is None and self.category == other.category
        lower, lower_inclusive = max(
            (mine[0], mine[1]), (theirs[0], theirs[1]), key=_lower_key
        )
        upper, upper_inclusive = min(
            (mine[2], mine[3]), (theirs[2], theirs[3]), key=_upper_key
        )
        if lower is None or upper is None:
            return True
        return lower < upper or (lower == upper and lower_inclusive and upper_inclusive)

    def amount(self, number: Decimal | str) -> str:
        """A number of this measure with its unit, as the manual prints it."""
        return f"{number} {self.unit}" if self.unit_printed else str(number)

    def reading_words(self, reading: Decimal | str) -> str:
        """One reading of this measure as a record line: `HbA1c 7.4 %`."""
        if isinstance(reading, str):
            if reading != self.category:
                raise ValueError("a category is worded only by its own threshold")
            return self.words
        return f"{self.measure_label} {self.amount(reading)}"


def _lower_key(end: tuple[Decimal | None, bool]) -> tuple[int, Decimal, int]:
    # An open end sorts first; at the same value an excluded end starts later.
    value, inclusive = end
    return (0, Decimal(0), 0) if value is None else (1, value, 0 if inclusive else 1)


def _upper_key(end: tuple[Decimal | None, bool]) -> tuple[int, Decimal, int]:
    # An open end sorts last; at the same value an excluded end stops earlier.
    value, inclusive = end
    return (1, Decimal(0), 0) if value is None else (0, value, 1 if inclusive else 0)


class Source(ContractModel):
    """The public guideline a threshold rests on, with enough to find it."""

    body: OneLine
    abbreviation: OneLine
    guideline: OneLine
    year: int = Field(ge=1990)
    # Where in the guideline: a section, table or recommendation.
    locator: OneLine

    @model_validator(mode="after")
    def _not_from_the_future(self) -> Self:
        if self.year > LATEST_SOURCE_YEAR:
            raise ValueError(f"a source cannot be dated after {LATEST_SOURCE_YEAR}")
        return self

    @property
    def citation(self) -> str:
        return f"{self.abbreviation}, {self.guideline} ({self.year}), {self.locator}"


class Category(ContractModel):
    value: Slug
    label: OneLine


class Measure(ContractModel):
    """Something a rule compares: a reading, a score, a count of months, a status."""

    key: Slug
    label: OneLine
    unit: OneLine
    unit_printed: bool = True
    # One sentence for the glossary.
    meaning: OneLine
    # The readings that can occur at all; an open end has no limit. Bands and declared
    # gaps cover this range, and worked examples stay inside it.
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    # For a status: every value it can take. A measure has categories or numbers.
    categories: tuple[Category, ...] = ()
    # How to turn another unit a document may use into this one.
    conversion: OneLine | None = None

    @model_validator(mode="after")
    def _numbers_or_categories(self) -> Self:
        if self.categories and (self.minimum is not None or self.maximum is not None):
            raise ValueError(f"{self.key}: a status has no number range")
        values = [item.value for item in self.categories]
        if len(values) != len(set(values)):
            raise ValueError(f"{self.key}: a category is listed twice")
        return self

    def allows(self, number: Decimal) -> bool:
        return (self.minimum is None or number >= self.minimum) and (
            self.maximum is None or number <= self.maximum
        )

    def _threshold(
        self, comparison: Comparison, band: str, **parts: object
    ) -> Threshold:
        return Threshold.model_validate(
            {
                "measure": self.key,
                "measure_label": self.label,
                "unit": self.unit,
                "unit_printed": self.unit_printed,
                "comparison": comparison,
                "words": f"{self.label} {band}",
                **parts,
            }
        )

    def _amount(self, text: str) -> str:
        return f"{text} {self.unit}" if self.unit_printed else text

    def below(self, value: str) -> Threshold:
        return self._threshold(
            Comparison.BELOW, f"below {self._amount(value)}", value=Decimal(value)
        )

    def at_most(self, value: str) -> Threshold:
        return self._threshold(
            Comparison.AT_MOST,
            f"of {self._amount(value)} or less",
            value=Decimal(value),
        )

    def above(self, value: str) -> Threshold:
        return self._threshold(
            Comparison.ABOVE, f"above {self._amount(value)}", value=Decimal(value)
        )

    def at_least(self, value: str) -> Threshold:
        return self._threshold(
            Comparison.AT_LEAST,
            f"of {self._amount(value)} or more",
            value=Decimal(value),
        )

    def between(
        self,
        lower: str,
        upper: str,
        *,
        lower_inclusive: bool = True,
        upper_inclusive: bool = False,
    ) -> Threshold:
        """A band; by default the lower end is in it and the upper end is not."""
        start = f"from {lower}" if lower_inclusive else f"above {lower}"
        if upper_inclusive:
            end = f"to {upper}" if lower_inclusive else f"and up to {upper}"
        else:
            end = f"to below {upper}" if lower_inclusive else f"and below {upper}"
        return self._threshold(
            Comparison.BETWEEN,
            f"{start} {self._amount(end)}",
            lower=Decimal(lower),
            lower_inclusive=lower_inclusive,
            upper=Decimal(upper),
            upper_inclusive=upper_inclusive,
        )

    def equals(self, category: str) -> Threshold:
        label = next(
            (item.label for item in self.categories if item.value == category), None
        )
        if label is None:
            raise ValueError(f"{self.key}: {category} is not one of its categories")
        return Threshold.model_validate(
            {
                "measure": self.key,
                "measure_label": self.label,
                "unit": self.unit,
                "unit_printed": self.unit_printed,
                "comparison": Comparison.EQUALS,
                "words": f"{self.label}: {label}",
                "category": category,
            }
        )


def band_words(threshold: Threshold) -> str:
    """The threshold's words without the name of the measure."""
    band = threshold.words.removeprefix(threshold.measure_label).lstrip(": ")
    return band.removeprefix("of ")


class Edge(ContractModel):
    """One end of a rule's band and where it comes from."""

    value: Decimal
    # `source`: the cited guideline draws this line. `manual`: this manual drew it.
    origin: Literal["source", "manual"]


class Gap(ContractModel):
    """Readings of a measure that meet no rule, and what that means."""

    threshold: Threshold
    meaning: OneLine


class Applicability(ContractModel):
    """What must be true of an applicant for an impairment's rules to apply."""

    # `diagnosis`: the condition, event or treatment is on file. `reading`: a reading
    # of the measure is enough, with or without a diagnosis.
    basis: Literal["diagnosis", "reading"]
    words: OneLine
    # Impairments whose diagnosis rules this one out.
    not_with: tuple[Slug, ...] = ()


class _Rated(ContractModel):
    """What a rule in the manual and its row in the rule table have in common."""

    rule_id: RuleId
    threshold: Threshold
    # Exactly one of the two: a debit (a whole percentage; 0 is "no debit") or decline.
    debit_pct: Percent | None = None
    decline: bool = False
    # A decline that invites a fresh application later.
    postponement: bool = False
    # One more sentence of the definition, if the band needs it.
    note: OneLine | None = None
    source: Source

    @model_validator(mode="wrap")
    @classmethod
    def _name_the_rule(
        cls, data: Any, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        # Whatever is wrong with a rule, the error says which rule it is.
        try:
            rule = handler(data)
            if rule.decline and rule.debit_pct is not None:
                raise ValueError("a rule cannot be both a debit and decline")
            if not rule.decline and rule.debit_pct is None:
                raise ValueError("a rule needs a debit or decline")
            if rule.postponement and not rule.decline:
                raise ValueError("only a decline can be a postponement")
        except ValueError as error:
            rule_id = (
                data.get("rule_id")
                if isinstance(data, dict)
                else getattr(data, "rule_id", None)
            )
            raise ValueError(f"{rule_id} is not a valid rule: {error}") from error
        return rule

    @property
    def rating_words(self) -> str:
        if self.decline:
            return "decline as a postponement" if self.postponement else "decline"
        if self.debit_pct == 0:
            return "no debit, +0 %"
        return f"a debit of +{self.debit_pct} %"


def check_rules(
    groups: Iterable[tuple[str, Sequence[_Rated]]],
    references: Iterable[tuple[str, str]],
) -> None:
    """Fail on a repeated id, overlapping bands of one measure, or a bad reference.

    `groups` are the rules of each impairment; `references` are (from, to) pairs.
    """
    seen: set[str] = set()
    for impairment, rules in groups:
        if not rules:
            raise ValueError(f"{impairment}: an impairment needs at least one rule")
        for index, rule in enumerate(rules):
            if rule.rule_id in seen:
                raise ValueError(f"{rule.rule_id}: this rule id is used twice")
            seen.add(rule.rule_id)
            _check_overlap(rule, rules[:index])
    pairs: set[tuple[str, str]] = set()
    for source, target in references:
        if target not in seen:
            raise ValueError(f"{source}: it refers to {target}, which is not a rule")
        if target == source:
            raise ValueError(f"{source}: a rule cannot refer to itself")
        if (source, target) in pairs:
            raise ValueError(f"{source}: it refers to {target} twice")
        pairs.add((source, target))


def _check_overlap(rule: _Rated, earlier: Sequence[_Rated]) -> None:
    for other in earlier:
        if (
            other.threshold.measure == rule.threshold.measure
            and other.threshold.overlaps(rule.threshold)
        ):
            raise ValueError(
                f"{rule.rule_id}: its band overlaps the band of "
                f"{other.rule_id} ({rule.threshold.measure})"
            )


def check_measures(
    impairment: str,
    measures: Sequence[Measure],
    rules: Sequence[_Rated],
    gaps: Sequence[Gap],
) -> None:
    """Fail unless the rules and the declared gaps of each measure cover its whole range.

    Every reading that can occur then meets exactly one rule or falls in exactly one
    gap whose meaning is written down.
    """
    keys = [measure.key for measure in measures]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{impairment}: a measure is listed twice")
    for name, threshold in (
        *((rule.rule_id, rule.threshold) for rule in rules),
        *((f"{impairment}: a gap", gap.threshold) for gap in gaps),
    ):
        measure = next((m for m in measures if m.key == threshold.measure), None)
        if measure is None:
            raise ValueError(
                f"{name}: {threshold.measure} is not a measure of {impairment}"
            )
        if (
            threshold.measure_label,
            threshold.unit,
            threshold.unit_printed,
        ) != (measure.label, measure.unit, measure.unit_printed):
            raise ValueError(f"{name}: its label or unit is not the measure's")
        if not all(measure.allows(end) for end in threshold.ends()):
            raise ValueError(
                f"{name}: an end of its band is outside the measure's range"
            )
    for index, rule in enumerate(rules):
        # Named by rule here; the cover check below could only name the measure.
        _check_overlap(rule, rules[:index])
    for measure in measures:
        used = [r.threshold for r in rules if r.threshold.measure == measure.key]
        if not used:
            raise ValueError(f"{impairment}: no rule uses the measure {measure.key}")
        declared = [g.threshold for g in gaps if g.threshold.measure == measure.key]
        _check_cover(impairment, measure, [*used, *declared])


def _check_cover(impairment: str, measure: Measure, parts: list[Threshold]) -> None:
    where = f"{impairment}: {measure.key}"
    if measure.categories:
        covered = sorted(str(part.category) for part in parts)
        if covered != sorted(item.value for item in measure.categories):
            raise ValueError(
                f"{where}: every category needs one rule or one declared gap"
            )
        return
    intervals = sorted(
        (interval for part in parts if (interval := part.interval()) is not None),
        key=lambda interval: _lower_key((interval[0], interval[1])),
    )
    if len(intervals) != len(parts):
        raise ValueError(f"{where}: a category on a measure of numbers")
    lower, lower_inclusive, upper, upper_inclusive = intervals[0]
    if lower is not None and not (lower == measure.minimum and lower_inclusive):
        raise ValueError(f"{where}: readings below {lower} are in no band and no gap")
    for lower, lower_inclusive, next_upper, next_inclusive in intervals[1:]:
        if upper is None or lower != upper or lower_inclusive == upper_inclusive:
            raise ValueError(
                f"{where}: an undeclared gap or an overlap at {lower}; declare what "
                "readings between the bands mean"
            )
        upper, upper_inclusive = next_upper, next_inclusive
    if upper is not None and not (upper == measure.maximum and upper_inclusive):
        raise ValueError(f"{where}: readings above {upper} are in no band and no gap")


def edges_of(threshold: Threshold, own: Iterable[Decimal]) -> tuple[Edge, ...]:
    """Each end of a band with its origin; `own` are the ends this manual drew."""
    mine = set(own)
    return tuple(
        Edge(value=end, origin="manual" if end in mine else "source")
        for end in threshold.ends()
    )


# ---------------------------------------------------------------------------
# Manual definition
# ---------------------------------------------------------------------------


class RuleSpec(_Rated):
    # The rules this rule's definition points to. The circumstance printed with each
    # is worked out from the rule pointed to, so it cannot disagree with it.
    see: tuple[RuleId, ...] = ()


class KeyQuestion(ContractModel):
    question: OneLine
    why: OneLine


class GlossaryTerm(ContractModel):
    term: OneLine
    meaning: OneLine


class Reference(ContractModel):
    """A pointer from one rule to another, with the circumstance that makes it matter."""

    rule_id: RuleId
    # `adds`: both rules can apply and their ratings combine. `replaces`: the rule
    # pointed to applies in place of this one.
    relation: Literal["adds", "replaces"]
    # Completes "If ..., see rule <rule_id>": the rule's own impairment and band.
    when: OneLine


class ImpairmentSpec(ContractModel):
    """One impairment's section of the manual. All wording is written for this project."""

    impairment_id: Slug
    # The letters in this impairment's rule ids: `UW-<code>-001`.
    code: ImpairmentCode
    name: OneLine
    # The name as it reads in the middle of a sentence.
    phrase: OneLine
    applies: Applicability
    measures: tuple[Measure, ...] = Field(min_length=1)
    overview: tuple[OneLine, ...] = Field(min_length=1)
    questions: tuple[KeyQuestion, ...] = Field(min_length=1)
    evidence: tuple[OneLine, ...] = Field(min_length=1)
    pitfalls: tuple[OneLine, ...] = Field(min_length=1)
    rating_note: OneLine
    # Ends of bands that no guideline supplies: this manual drew them.
    own_edges: tuple[Decimal, ...] = ()
    gaps: tuple[Gap, ...] = ()
    unchanged: tuple[OneLine, ...] = Field(min_length=1)
    combinations: tuple[OneLine, ...] = Field(min_length=1)
    rules: tuple[RuleSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rules_belong_here(self) -> Self:
        for rule in self.rules:
            if not rule.rule_id.startswith(f"UW-{self.code}-"):
                raise ValueError(
                    f"{rule.rule_id}: a rule of {self.impairment_id} starts UW-{self.code}-"
                )
        check_measures(self.impairment_id, self.measures, self.rules, self.gaps)
        ends = {end for rule in self.rules for end in rule.threshold.ends()}
        for edge in self.own_edges:
            if edge not in ends:
                raise ValueError(
                    f"{self.impairment_id}: {edge} is not an end of any of its bands"
                )
        return self


class ManualSpec(ContractModel):
    """Everything the manual and the rule table are generated from."""

    title: OneLine
    introduction: tuple[tuple[OneLine, tuple[OneLine, ...]], ...] = Field(min_length=1)
    impairments: tuple[ImpairmentSpec, ...] = Field(min_length=1)
    glossary: tuple[GlossaryTerm, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _table_is_sound(self) -> Self:
        for field in ("impairment_id", "code", "name"):
            values = [getattr(item, field) for item in self.impairments]
            if len(values) != len(set(values)):
                raise ValueError(f"two impairments share one {field}")
        check_rules(
            ((item.impairment_id, item.rules) for item in self.impairments),
            ((rule.rule_id, target) for rule in self.rules() for target in rule.see),
        )
        known = {item.impairment_id for item in self.impairments}
        measures: dict[str, Measure] = {}
        for item in self.impairments:
            for other in item.applies.not_with:
                if other not in known or other == item.impairment_id:
                    raise ValueError(
                        f"{item.impairment_id}: {other} is not another impairment"
                    )
            for measure in item.measures:
                # One key or one label means one thing across the manual.
                for name in (measure.key, measure.label.casefold()):
                    if measures.setdefault(name, measure) != measure:
                        raise ValueError(
                            f"{item.impairment_id}: {name} is defined differently "
                            "in another impairment"
                        )
        terms = [item.term.casefold() for item in self.glossary]
        if len(terms) != len(set(terms)):
            raise ValueError("the glossary has two meanings for one term")
        return self

    def rules(self) -> tuple[RuleSpec, ...]:
        """Every rule, in the order the manual prints them."""
        return tuple(rule for item in self.impairments for rule in item.rules)

    def rule(self, rule_id: str) -> RuleSpec:
        return next(rule for rule in self.rules() if rule.rule_id == rule_id)

    def impairment_of(self, rule_id: str) -> ImpairmentSpec:
        return next(
            item
            for item in self.impairments
            if any(rule.rule_id == rule_id for rule in item.rules)
        )

    def reference(self, rule: RuleSpec, target_id: str) -> Reference:
        """The pointer from `rule` to `target_id`, worded from the rule pointed to."""
        home, target = self.impairment_of(rule.rule_id), self.rule(target_id)
        other = self.impairment_of(target_id)
        words = target.threshold.words
        if other is home:
            replaces = target.threshold.measure == rule.threshold.measure
            when = (
                f"the reading is instead {words}"
                if replaces
                else f"the record also shows {words}"
            )
        else:
            replaces = (
                other.impairment_id in home.applies.not_with
                or home.impairment_id in other.applies.not_with
            )
            also = "instead" if replaces else "also"
            when = f"the applicant {also} has {other.phrase} with {words}"
        return Reference(
            rule_id=target_id, relation="replaces" if replaces else "adds", when=when
        )

    def references(self, rule: RuleSpec) -> tuple[Reference, ...]:
        return tuple(self.reference(rule, target) for target in rule.see)


# ---------------------------------------------------------------------------
# Rule table file (part of the answer key)
# ---------------------------------------------------------------------------


class ManualSection(ContractModel):
    number: SectionNumber
    title: OneLine
    first_page: PageNumber


class RuleTableImpairment(ContractModel):
    impairment_id: Slug
    code: ImpairmentCode
    name: OneLine
    # The manual section that holds this impairment, and the page it starts on.
    section: SectionNumber
    first_page: PageNumber
    # When its rules apply at all; a threshold alone does not say.
    applies: Applicability
    measures: tuple[Measure, ...] = Field(min_length=1)
    # The readings of each measure that meet no rule, and what that means.
    gaps: tuple[Gap, ...] = ()


class RuleTableRule(_Rated):
    impairment_id: Slug
    impairment: OneLine
    section: SectionNumber
    # The 1-based page of the manual PDF that prints this rule's definition.
    manual_page: PageNumber
    # Where each end of the band comes from.
    edges: tuple[Edge, ...] = ()
    # The rules this rule's definition points to, each with its circumstance.
    references: tuple[Reference, ...] = ()
    # The same rules as a plain list; never rules it defines.
    refers_to: tuple[RuleId, ...] = ()


class RuleTable(ContractModel):
    """The known right rules of the manual (spine AD-12, AD-17). Never read by a service."""

    manual_file: NonEmptyStr
    manual_title: OneLine
    page_count: int = Field(ge=MIN_PAGES, le=MAX_PAGES)
    # Said in the file itself, so that nobody takes the ratings for a real insurer's.
    notice: OneLine
    contents_page: PageNumber
    # Every numbered section, in order, with the page it starts on.
    sections: tuple[ManualSection, ...] = Field(min_length=1)
    impairments: tuple[RuleTableImpairment, ...] = Field(min_length=1)
    rules: tuple[RuleTableRule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _table_is_sound(self) -> Self:
        pages = [self.contents_page, *(item.first_page for item in self.sections)]
        if pages != sorted(set(pages)) or pages[-1] > self.page_count:
            raise ValueError(
                "sections must start on later and later pages of the manual"
            )
        placed = {item.number: item for item in self.sections}
        known = {item.impairment_id: item for item in self.impairments}
        if len(known) != len(self.impairments):
            raise ValueError("two impairments share one impairment_id")
        for rule in self.rules:
            impairment = known.get(rule.impairment_id)
            if impairment is None:
                raise ValueError(f"{rule.rule_id}: unknown impairment")
            if (rule.impairment, rule.section) != (impairment.name, impairment.section):
                raise ValueError(
                    f"{rule.rule_id}: name or section against its impairment"
                )
            if not rule.rule_id.startswith(f"UW-{impairment.code}-"):
                raise ValueError(f"{rule.rule_id}: not the code of its impairment")
            if not impairment.first_page <= rule.manual_page <= self.page_count:
                raise ValueError(
                    f"{rule.rule_id}: its page is before its section or past the "
                    "end of the manual"
                )
            if rule.refers_to != tuple(item.rule_id for item in rule.references):
                raise ValueError(f"{rule.rule_id}: refers_to against its references")
            if [edge.value for edge in rule.edges] != list(rule.threshold.ends()):
                raise ValueError(f"{rule.rule_id}: edges against the ends of its band")
        for item in self.impairments:
            section = placed.get(item.section)
            if section is None or (section.title, section.first_page) != (
                item.name,
                item.first_page,
            ):
                raise ValueError(
                    f"{item.impairment_id}: not the section of that number"
                )
            for other in item.applies.not_with:
                if other not in known or other == item.impairment_id:
                    raise ValueError(
                        f"{item.impairment_id}: {other} is not another impairment"
                    )
            check_measures(
                item.impairment_id,
                item.measures,
                [r for r in self.rules if r.impairment_id == item.impairment_id],
                item.gaps,
            )
        check_rules(
            (
                (
                    item.impairment_id,
                    [r for r in self.rules if r.impairment_id == item.impairment_id],
                )
                for item in self.impairments
            ),
            (
                (rule.rule_id, target)
                for rule in self.rules
                for target in rule.refers_to
            ),
        )
        return self
