"""Work out a case's expected facts, rules and verdict from its figures and the rule table.

Nothing here is typed in per case. The readings are taken from what the case's pages
state, and rated as the manual says:

- An impairment applies on a diagnosis (the case's data names the impairment of each
  diagnosis; nothing is matched by wording) or on a reading alone, and never beside a
  diagnosis that rules it out.
- A reading in another unit the manual converts is converted first, and rounded as
  the bands are written.
- Figures the manual tells the reader to work out are worked out: pack-years from
  cigarettes a day and years smoked, and whole months since a heart attack or a clot.
- Where an impairment's evidence part says which of several readings is rated (the
  most recent, the average, the highest, the lowest, within so many months), that
  reading is rated and the others meet no rule (`ReadingRule`, the same data the
  manual prints).
- The verdict follows the rules `verdict` applies to a run (stories 2.5 and 2.6).
  Two bands of one measure, for which the manual states no way to choose, give
  `refer` with `conflicting_rules`; so does nothing else. A diagnosis the manual
  has no impairment for gives `refer` with `no_matching_rule`. Otherwise a decline
  gives `decline`; otherwise the debits add up, and a sum above zero gives `loaded`;
  otherwise `standard`. Two rules of one impairment on different measures add only
  where one refers to the other; a case that meets two that do not is refused,
  because the manual does not say and `verdict` would send it to a person.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from contracts.enums import SystemReason, Verdict
from contracts.query import build_fact_query
from synthdata.case_parts import months_before
from synthdata.manual_model import (
    ImpairmentSpec,
    ManualSpec,
    Measure,
    ReadingRule,
    RuleSpec,
)
from synthdata.manual_rules import MANUAL
from synthdata.model import (
    CaseDefinition,
    ExpectedFact,
    ExpectedVerdict,
    FactKind,
    FactPlace,
    PageLayout,
)
from synthdata.render import declared_condition, finding_label, finding_value

_SYSTOLIC, _DIASTOLIC = "systolic blood pressure", "diastolic blood pressure"
_BMI, _SMOKING = "body mass index", "smoking status"
_PRESSURE_UNIT, _BMI_UNIT = "mmHg", "kg/m2"
# The manual: "cigarettes a day divided by 20, times the years smoked", rounded down.
_PACK_YEARS = "pack_years"
_SMOKED = re.compile(r"(?P<a_day>\d+) cigarettes a day for (?P<years>\d+) years")
_CIGARETTES_IN_A_PACK = 20
# The manual: whole months from the date of the event to the date of the application.
_MONTHS_SINCE = {
    "myocardial_infarction": "months_since_myocardial_infarction",
    "venous_thromboembolism": "months_since_venous_thromboembolism",
}


def squash(text: str) -> str:
    """Text with every run of white space made one space: how a quote is looked for."""
    return " ".join(text.split())


def whole_months(earlier: date, later: date) -> int:
    """Whole months from `earlier` to `later`."""
    months = (later.year - earlier.year) * 12 + later.month - earlier.month
    return months - (later.day < earlier.day)


@dataclass(frozen=True, slots=True)
class _Stated:
    """A reading as a page states it: under a label, in the page's own unit."""

    layout: PageLayout
    quote: str
    label: str
    value: str
    unit: str | None = None
    taken_on: date | None = None
    # A reading the manual must have a measure for; other tests are just not facts.
    must_be_a_measure: bool = False


@dataclass(slots=True)
class _Reading:
    """One reading of one of the manual's measures, in the manual's unit."""

    measure: Measure
    # As the rule table compares it: a number, or the value of a category.
    reading: Decimal | str
    # As the manual would word it: `HbA1c 7.4 %`.
    words: str
    taken_on: date
    places: list[FactPlace]
    derived: bool = False
    rule_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Expected:
    """What the rule table says of one case."""

    facts: tuple[ExpectedFact, ...]
    rule_ids: tuple[str, ...]
    verdict: ExpectedVerdict


def _stated(case: CaseDefinition) -> list[_Stated]:
    """Every reading the medical pages print, in the order they print them."""
    clinical = case.clinical
    form, statement, lab = (
        PageLayout.APPLICATION_FORM,
        PageLayout.ATTENDING_PHYSICIAN_STATEMENT,
        PageLayout.LAB_REPORT,
    )
    bmi = f"{clinical.bmi}"

    def build_and_smoking(layout: PageLayout) -> list[_Stated]:
        return [
            _Stated(
                layout,
                f"Body mass index (BMI) {bmi} {_BMI_UNIT}",
                _BMI,
                bmi,
                _BMI_UNIT,
            ),
            _Stated(
                layout,
                f"Smoking status {clinical.smoking_status}",
                _SMOKING,
                clinical.smoking_status,
            ),
        ]

    said = build_and_smoking(form)
    for reading in clinical.blood_pressure:
        row = (
            f"{reading.taken_on.isoformat()} {reading.systolic_mmhg} "
            f"{reading.diastolic_mmhg}"
        )
        said += [
            _Stated(
                statement,
                row,
                label,
                f"{value}",
                _PRESSURE_UNIT,
                reading.taken_on,
            )
            for label, value in (
                (_SYSTOLIC, reading.systolic_mmhg),
                (_DIASTOLIC, reading.diastolic_mmhg),
            )
        ]
    said += build_and_smoking(statement)
    latest = clinical.lab_results[0]
    said.append(
        _Stated(
            statement,
            f"Latest {latest.test} {latest.value} {latest.unit} on "
            f"{clinical.lab_collected_on.isoformat()}",
            latest.test,
            latest.value,
            latest.unit,
            clinical.lab_collected_on,
        )
    )
    said += [
        _Stated(
            statement,
            f"{finding_label(finding)} {finding_value(finding)}",
            finding.label,
            finding.value,
            finding.unit,
            finding.taken_on,
            must_be_a_measure=True,
        )
        for finding in clinical.findings
    ]
    said += [
        _Stated(
            lab,
            f"{result.test} {result.value} {result.unit}",
            result.test,
            result.value,
            result.unit,
            clinical.lab_collected_on,
        )
        for result in clinical.lab_results
    ]
    return said


def measures_by_label(manual: ManualSpec) -> dict[str, Measure]:
    """Every measure of the manual by its label, whatever the capitals.

    Fails when two different measures would be found under one label: a page's
    reading could then be taken for either.
    """
    found: dict[str, Measure] = {}
    for impairment in manual.impairments:
        for measure in impairment.measures:
            if found.setdefault(measure.label.casefold(), measure) != measure:
                raise ValueError(
                    f"two measures of the manual are labelled {measure.label!r}"
                )
    return found


def _decimals(manual: ManualSpec, measure: Measure) -> int:
    """How many decimal places the bands of a measure are written with."""
    ends = [
        end
        for impairment in manual.impairments
        for threshold in (
            *(rule.threshold for rule in impairment.rules),
            *(gap.threshold for gap in impairment.gaps),
        )
        if threshold.measure == measure.key
        for end in threshold.ends()
    ]
    return max((-int(end.as_tuple().exponent) for end in ends), default=0)


def _rounded(number: Decimal, places: int) -> Decimal:
    return number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _words(measure: Measure, number: Decimal) -> str:
    words = f"{measure.label} {number}"
    return f"{words} {measure.unit}" if measure.unit_printed else words


def _reading(
    where: str, manual: ManualSpec, measure: Measure, stated: _Stated
) -> tuple[Decimal | str, str]:
    """A stated value as the rule table compares it, and as the manual would word it."""
    value = stated.value
    if measure.categories:
        for category in measure.categories:
            start = category.label.casefold()
            if (
                value.casefold().startswith(start)
                and not value[len(start) :][:1].isalnum()
            ):
                return category.value, f"{measure.label}: {category.label}"
        raise ValueError(f"{where}: {value!r} is no status of {measure.label}")
    number = Decimal(value)
    wanted = measure.unit if measure.unit_printed else None
    if stated.unit != wanted:
        other = next(
            (item for item in measure.other_units if item.unit == stated.unit), None
        )
        if other is None:
            raise ValueError(
                f"{where}: {measure.label} is given in {stated.unit}, which the "
                f"manual does not convert to {wanted}"
            )
        number = _rounded(other.convert(number), _decimals(manual, measure))
    if not measure.allows(number):
        raise ValueError(f"{where}: {measure.label} {number} cannot occur")
    return number, _words(measure, number)


def _applies(impairment: ImpairmentSpec, on_file: set[str]) -> bool:
    if on_file & set(impairment.applies.not_with):
        return False
    return impairment.applies.basis == "reading" or impairment.impairment_id in on_file


def _chosen(
    where: str,
    manual: ManualSpec,
    rule: ReadingRule,
    readings: list[_Reading],
) -> _Reading:
    """The one reading an impairment rates, of several of one measure."""
    numbers = {
        id(item): item.reading for item in readings if isinstance(item.reading, Decimal)
    }
    if len(numbers) != len(readings):
        raise ValueError(f"{where}: a status cannot be chosen among by a reading rule")
    if rule.choose == "most_recent":
        latest = max(item.taken_on for item in readings)
        newest = [item for item in readings if item.taken_on == latest]
        if len(newest) > 1:
            raise ValueError(
                f"{where}: two readings of {newest[0].measure.label} on {latest}"
            )
        return newest[0]
    if rule.choose == "highest":
        return max(readings, key=lambda item: numbers[id(item)])
    if rule.choose == "lowest":
        return min(readings, key=lambda item: numbers[id(item)])
    if len(set(numbers.values())) == 1:
        return readings[0]
    measure = readings[0].measure
    average = _rounded(
        sum(numbers.values(), Decimal(0)) / len(numbers), _decimals(manual, measure)
    )
    return _Reading(
        measure=measure,
        reading=average,
        words=_words(measure, average),
        taken_on=max(item.taken_on for item in readings),
        places=[place for item in readings for place in item.places],
        derived=True,
    )


def verdict_of(
    rules: list[RuleSpec], conflicting: bool, unknown_conditions: bool
) -> ExpectedVerdict:
    """The verdict for the rules a case meets, by the rules of stories 2.5 and 2.6."""
    found: set[SystemReason] = set()
    if unknown_conditions:
        found.add(SystemReason.NO_MATCHING_RULE)
    if conflicting:
        found.add(SystemReason.CONFLICTING_RULES)
    if found:
        return ExpectedVerdict(
            verdict=Verdict.REFER,
            # In the order the contracts list them, as `verdict` stores them.
            system_reasons=tuple(reason for reason in SystemReason if reason in found),
        )
    if any(rule.decline for rule in rules):
        return ExpectedVerdict(verdict=Verdict.DECLINE)
    loading_pct = sum(rule.debit_pct or 0 for rule in rules)
    if loading_pct > 0:
        return ExpectedVerdict(verdict=Verdict.LOADED, loading_pct=loading_pct)
    return ExpectedVerdict(verdict=Verdict.STANDARD)


def expected_for(case: CaseDefinition, manual: ManualSpec = MANUAL) -> Expected:
    """The facts, the rules and the verdict the rule table gives `case`.

    Fails, naming the case, on a reading the manual cannot place, on a diagnosed
    impairment the case gives no reading for, on two rules the manual does not say
    how to combine, and when the case states another verdict, loading or set of
    rules than its figures give.
    """
    where = case.case_id
    by_label = measures_by_label(manual)
    by_key = {measure.key: measure for measure in by_label.values()}
    impairments = {item.impairment_id: item for item in manual.impairments}
    pages: dict[PageLayout, list[int]] = {}
    for number, spec in enumerate(case.pages, start=1):
        pages.setdefault(spec.layout, []).append(number)

    def places(layout: PageLayout, quote: str) -> list[FactPlace]:
        return [
            FactPlace(page_number=number, quote=quote)
            for number in pages.get(layout, [])
        ]

    # The conditions on file, each with the impairment the case's data names.
    on_file: set[str] = set()
    unknown = False
    facts: list[ExpectedFact] = []
    readings: dict[tuple[str, str, date, bool], _Reading] = {}

    def note(reading: _Reading) -> _Reading:
        key = (
            reading.measure.key,
            reading.words,
            reading.taken_on,
            reading.derived,
        )
        kept = readings.setdefault(key, reading)
        if kept is not reading:
            kept.places += [
                place for place in reading.places if place not in kept.places
            ]
        return kept

    for item in case.clinical.diagnoses:
        if item.impairment is None:
            unknown = True
            name = item.condition
        elif item.impairment not in impairments:
            raise ValueError(
                f"{where}: {item.impairment} is no impairment of the manual"
            )
        else:
            on_file.add(item.impairment)
            name = impairments[item.impairment].name
        day = item.diagnosed_on.isoformat()
        stated_at = [
            *places(PageLayout.APPLICATION_FORM, declared_condition(item)),
            *places(
                PageLayout.ATTENDING_PHYSICIAN_STATEMENT,
                f"{item.condition} {day} {item.treatment}",
            ),
        ]
        facts.append(
            ExpectedFact(
                kind=FactKind.DIAGNOSIS,
                statement=f"{name}, diagnosed {day}",
                places=tuple(stated_at),
            )
        )
        since = _MONTHS_SINCE.get(item.impairment or "")
        if since is not None:
            months = Decimal(whole_months(item.diagnosed_on, case.document_date))
            note(
                _Reading(
                    measure=by_key[since],
                    reading=months,
                    words=_words(by_key[since], months),
                    taken_on=case.document_date,
                    places=stated_at,
                    derived=True,
                )
            )

    for stated in _stated(case):
        stated_at = places(stated.layout, stated.quote)
        if not stated_at:
            continue
        measure = by_label.get(stated.label.casefold())
        if measure is None:
            if stated.must_be_a_measure:
                raise ValueError(
                    f"{where}: the manual has no measure called {stated.label!r}"
                )
            continue
        value, words = _reading(where, manual, measure, stated)
        note(
            _Reading(
                measure=measure,
                reading=value,
                words=words,
                taken_on=stated.taken_on or case.document_date,
                places=stated_at,
            )
        )
        smoked = _SMOKED.search(stated.value) if stated.label == _SMOKING else None
        if smoked is not None:
            pack_years = Decimal(
                int(smoked["a_day"]) * int(smoked["years"]) // _CIGARETTES_IN_A_PACK
            )
            note(
                _Reading(
                    measure=by_key[_PACK_YEARS],
                    reading=pack_years,
                    words=_words(by_key[_PACK_YEARS], pack_years),
                    taken_on=case.document_date,
                    places=stated_at,
                    derived=True,
                )
            )

    applicable = [item for item in manual.impairments if _applies(item, on_file)]
    met: dict[str, RuleSpec] = {}
    conflicting = False
    for impairment in applicable:
        by_measure: dict[str, set[str]] = {}
        for measure in impairment.measures:
            rated = [
                item for item in readings.values() if item.measure.key == measure.key
            ]
            choice = impairment.reading_rule(measure.key)
            if choice is not None:
                if choice.within_months is not None:
                    oldest = months_before(case.document_date, choice.within_months)
                    rated = [item for item in rated if item.taken_on >= oldest]
                # The average of several readings is a figure of its own.
                rated = [note(_chosen(where, manual, choice, rated))] if rated else []
            for reading in rated:
                by_measure.setdefault(measure.key, set())
                for rule in impairment.rules:
                    if rule.threshold.measure == measure.key and rule.threshold.holds(
                        reading.reading
                    ):
                        met[rule.rule_id] = rule
                        by_measure[measure.key].add(rule.rule_id)
                        if rule.rule_id not in reading.rule_ids:
                            reading.rule_ids.append(rule.rule_id)
        if (
            impairment.applies.basis == "diagnosis"
            and impairment.impairment_id in on_file
            and not by_measure
        ):
            raise ValueError(
                f"{where}: {impairment.name} is on file, and the case gives no "
                "reading its rules could be met by"
            )
        # Two bands of one measure, and nothing in the manual to choose by.
        conflicting = conflicting or any(len(ids) > 1 for ids in by_measure.values())
        ids = [rule_id for found in by_measure.values() for rule_id in found]
        for first in ids:
            for second in ids:
                if (
                    met[first].threshold.measure != met[second].threshold.measure
                    and second not in met[first].see
                    and first not in met[second].see
                ):
                    raise ValueError(
                        f"{where}: {first} and {second} are both met, and the manual "
                        "does not say that their ratings add"
                    )

    for reading in readings.values():
        # A band of a diagnosed impairment is searched for as that applicant's reading:
        # the manual has a band of HbA1c for more than one impairment.
        owners = [
            impairment
            for impairment in applicable
            if impairment.applies.basis == "diagnosis"
            and any(item.key == reading.measure.key for item in impairment.measures)
        ]
        statement = (
            f"{owners[0].name}: {reading.words}" if len(owners) == 1 else reading.words
        )
        kind = FactKind.DERIVED if reading.derived else FactKind.READING
        same = next(
            (
                index
                for index, fact in enumerate(facts)
                if (fact.kind, fact.statement) == (kind, statement)
            ),
            None,
        )
        earlier = facts.pop(same) if same is not None else None
        facts.append(
            ExpectedFact(
                kind=kind,
                statement=statement,
                measure=reading.measure.key,
                rule_ids=tuple(
                    dict.fromkeys(
                        [*(earlier.rule_ids if earlier else ()), *reading.rule_ids]
                    )
                ),
                places=tuple(
                    sorted(
                        {*(earlier.places if earlier else ()), *reading.places},
                        key=lambda place: (place.page_number, place.quote),
                    )
                ),
            )
        )
    for fact in facts:
        if build_fact_query(fact.statement) != fact.statement:
            raise ValueError(f"{where}: {fact.statement!r} is not a query as it stands")
    in_order = [rule for rule in manual.rules() if rule.rule_id in met]
    verdict = verdict_of(in_order, conflicting, unknown)
    rule_ids = tuple(rule.rule_id for rule in in_order)
    intends = case.intends
    if intends is None:
        raise ValueError(f"{where}: a case must state the verdict it is meant to reach")
    stated_verdict = (intends.verdict, intends.loading_pct, sorted(intends.rule_ids))
    worked_out = (verdict.verdict, verdict.loading_pct, sorted(rule_ids))
    if stated_verdict != worked_out:
        raise ValueError(
            f"{where}: the case states {stated_verdict} and its figures give "
            f"{worked_out} by the rule table"
        )
    # In the order of the pages, and on a page in the order found.
    facts.sort(key=lambda fact: min(place.page_number for place in fact.places))
    return Expected(facts=tuple(facts), rule_ids=rule_ids, verdict=verdict)
