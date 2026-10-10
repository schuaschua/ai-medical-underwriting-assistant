"""The review record of the manual: what a reader with the sources should check, and what they found.

The manual's ratings are invented. Its citations, the band edges it says a guideline
draws, its unit conversions and its reading rules claim more, and were written without
the sources at hand. `manual-review.yaml` lists every one of them with a review
record. The list is worked out here from the same `ManualSpec` the manual is printed
from, so the file can neither miss an item nor keep one the manual has lost, and a
correction recorded in it is applied each time the manual is generated.
"""

from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, Self

import yaml
from pydantic import (
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from contracts.base import ContractModel, OneLine
from contracts.rules import RuleId
from synthdata.manual import section_plan
from synthdata.manual_model import (
    ImpairmentSpec,
    ManualSpec,
    Measure,
    ReadingRule,
    Slug,
    Source,
)
from synthdata.manual_sources import named_sources

REVIEW_FILE: Final = Path(__file__).with_name("manual-review.yaml")
SYNC_COMMAND: Final = "uv run python -m synthdata review --sync"

ItemId = Annotated[str, StringConstraints(pattern=r"^[a-z_]+(\.[A-Za-z0-9_-]+)+$")]


class Kind(StrEnum):
    """What an item is."""

    CITATION = "citation"
    THRESHOLD = "threshold"
    CONVERSION = "conversion"
    READING_RULE = "reading_rule"


class Status(StrEnum):
    UNREVIEWED = "unreviewed"
    # The source says what the manual says.
    VERIFIED = "verified"
    # The source says something else, which `corrected_value` holds.
    CORRECTED = "corrected"


# A sentence or a number as text, or named parts (a citation, a reading rule).
type Parts = dict[str, str | int | None]
# A number is text here: YAML would read 7.0 as a number and drop the zero the
# manual prints, so a number without quotes is refused.
type Value = str | Parts


_SENTENCE: Final = TypeAdapter[str](OneLine)


class Where(ContractModel):
    """Where the manual prints an item."""

    # Section numbers, with the part where one part prints it: `2.4`.
    sections: tuple[OneLine, ...]
    impairments: tuple[Slug, ...]
    rule_ids: tuple[RuleId, ...] = ()


class ReviewItem(ContractModel):
    """One thing to check, as the manual states it, and what a reviewer recorded."""

    # Never changes while the manual has the item, whatever its value becomes.
    id: ItemId
    kind: Kind
    what: OneLine
    where: Where
    # What the manual's own definition states, before any correction.
    value: Value
    # For a band edge: each guideline the manual says draws it.
    cited: tuple[OneLine, ...] = ()
    status: Status = Status.UNREVIEWED
    corrected_value: Value | None = None
    # What the reviewer checked it against.
    source: OneLine | None = None
    reviewed_by: OneLine | None = None
    reviewed_on: date | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _record_is_whole(self) -> Self:
        signed = (self.source, self.reviewed_by, self.reviewed_on)
        try:
            if self.status is Status.UNREVIEWED:
                if any(part is not None for part in (*signed, self.corrected_value)):
                    raise ValueError(
                        "it is unreviewed and yet has a source, a reviewer, a date "
                        "or a corrected value"
                    )
                return self
            if any(part is None for part in signed):
                raise ValueError(
                    f"{self.status} needs source, reviewed_by and reviewed_on"
                )
            if self.status is Status.VERIFIED and self.corrected_value is not None:
                raise ValueError("a verified item has no corrected_value")
            if self.status is Status.CORRECTED:
                if self.corrected_value is None or self.corrected_value == self.value:
                    raise ValueError(
                        "a corrected item needs a corrected_value other than its value"
                    )
                self.corrected()
        except ValueError as error:
            raise ValueError(f"{self.id}: {error}") from error
        return self

    def corrected(self) -> Source | Decimal | str | ReadingRule:
        """The corrected value as what it is; fails when it has not the shape of its kind."""
        value = self.corrected_value
        try:
            match self.kind:
                case Kind.CITATION:
                    return Source.model_validate(value)
                case Kind.READING_RULE:
                    measure = self.id.rsplit(".", 1)[-1]
                    return ReadingRule.model_validate(
                        {
                            **(value if isinstance(value, dict) else {}),
                            "measure": measure,
                        }
                    )
                case Kind.THRESHOLD:
                    return Decimal(str(value))
                case Kind.CONVERSION:
                    return _SENTENCE.validate_python(value)
        except (ValidationError, InvalidOperation) as error:
            raise ValueError(
                f"its corrected_value has not the shape of its value: {error}"
            ) from error

    @property
    def checked(self) -> bool:
        return self.status is not Status.UNREVIEWED

    def stated(self) -> dict[str, Any]:
        """What the manual states of the item: everything but the review record."""
        return self.model_dump(
            include={"id", "kind", "what", "where", "value", "cited"}
        )


class Review(ContractModel):
    """The whole review file."""

    items: tuple[ReviewItem, ...]

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        twice = [
            name
            for name, count in Counter(i.id for i in self.items).items()
            if count > 1
        ]
        if twice:
            raise ValueError(f"listed twice: {', '.join(twice)}")
        return self

    def progress(self) -> tuple[int, int]:
        """How many items a reviewer has checked (verified or corrected), and how many there are."""
        return sum(item.checked for item in self.items), len(self.items)


class ReviewOutOfDate(ValueError):
    """The review file does not list what the manual holds."""


# ---------------------------------------------------------------------------
# The items, from the manual's definition
# ---------------------------------------------------------------------------


def _citations(spec: ManualSpec, sections: dict[str, str]) -> list[ReviewItem]:
    names: dict[Source, str] = {}
    for name, source in named_sources().items():
        if names.setdefault(source, name) != name:
            raise ValueError(f"the sources {names[source]} and {name} are one citation")
    last = str(len(sections) + 3)
    users: dict[Source, list[ImpairmentSpec]] = {}
    for impairment in spec.impairments:
        for rule in impairment.rules:
            if rule.source not in names:
                raise ValueError(
                    f"{rule.rule_id}: its source is not one of `manual_sources`"
                )
            found = users.setdefault(rule.source, [])
            if impairment not in found:
                found.append(impairment)
    return [
        ReviewItem(
            id=f"citation.{names[source]}",
            kind=Kind.CITATION,
            what=(
                f"The manual cites {source.body} ({source.abbreviation}), "
                f"{source.guideline} ({source.year}), for: {source.locator}."
            ),
            where=Where(
                # Part 1 names the guideline, each definition in part 4 cites it,
                # and the last section lists it.
                sections=(
                    *(
                        f"{sections[item.impairment_id]}.{part}"
                        for item in impairments
                        for part in (1, 4)
                    ),
                    last,
                ),
                impairments=tuple(item.impairment_id for item in impairments),
                rule_ids=tuple(
                    rule.rule_id
                    for item in impairments
                    for rule in item.rules
                    if rule.source == source
                ),
            ),
            value=source.model_dump(),
        )
        for source, impairments in users.items()
    ]


def _thresholds(impairment: ImpairmentSpec, section: str) -> list[ReviewItem]:
    """Each end of a band that the manual says a guideline draws, once per edge."""
    items: list[ReviewItem] = []
    for measure in impairment.measures:
        rules = [r for r in impairment.rules if r.threshold.measure == measure.key]
        ends = sorted({end for rule in rules for end in rule.threshold.ends()})
        for end in ends:
            if end in impairment.own_edges:
                continue
            # The band that stops at the edge names the item; failing that, the one
            # that starts there. Rule ids never change, so neither does the item's.
            below = [r for r in rules if (i := r.threshold.interval()) and i[2] == end]
            above = [r for r in rules if (i := r.threshold.interval()) and i[0] == end]
            name = f"{below[0].rule_id}.upper" if below else f"{above[0].rule_id}.lower"
            touching = [*below, *above]
            bands = " ".join(f"{r.rule_id}: {r.threshold.words}." for r in touching)
            items.append(
                ReviewItem(
                    id=f"threshold.{name}",
                    kind=Kind.THRESHOLD,
                    what=(
                        f"{impairment.name}: a band edge at "
                        f"{touching[0].threshold.reading_words(end)}. {bands}"
                    ),
                    where=Where(
                        sections=(f"{section}.4",),
                        impairments=(impairment.impairment_id,),
                        rule_ids=tuple(r.rule_id for r in touching),
                    ),
                    value=str(end),
                    cited=tuple(dict.fromkeys(r.source.citation for r in touching)),
                )
            )
    return items


def _conversions(spec: ManualSpec, sections: dict[str, str]) -> list[ReviewItem]:
    """Each conversion sentence, once per measure however many sections print it."""
    users: dict[str, list[ImpairmentSpec]] = {}
    measures: dict[str, Measure] = {}
    for impairment in spec.impairments:
        for measure in impairment.measures:
            if measure.conversion:
                measures[measure.key] = measure
                users.setdefault(measure.key, []).append(impairment)
    return [
        ReviewItem(
            id=f"conversion.{key}",
            kind=Kind.CONVERSION,
            # As the manual prints it under "Converting units."
            what=f"Converting units. {measure.label}: {measure.conversion}",
            where=Where(
                sections=tuple(
                    f"{sections[item.impairment_id]}.3" for item in users[key]
                ),
                impairments=tuple(item.impairment_id for item in users[key]),
            ),
            value=str(measure.conversion),
        )
        for key, measure in measures.items()
    ]


def _reading_rules(impairment: ImpairmentSpec, section: str) -> list[ReviewItem]:
    labels = {measure.key: measure.label for measure in impairment.measures}
    return [
        ReviewItem(
            id=f"reading_rule.{impairment.impairment_id}.{choice.measure}",
            kind=Kind.READING_RULE,
            what=f"{impairment.name}. {choice.words(labels[choice.measure])}",
            where=Where(
                # Part 3 states it, and so does the definition of each rule of the measure.
                sections=(f"{section}.3", f"{section}.4"),
                impairments=(impairment.impairment_id,),
                rule_ids=tuple(
                    rule.rule_id
                    for rule in impairment.rules
                    if rule.threshold.measure == choice.measure
                ),
            ),
            value=choice.model_dump(exclude={"measure"}),
        )
        for choice in impairment.reading_rules
    ]


def review_items(spec: ManualSpec) -> tuple[ReviewItem, ...]:
    """Every item of `spec` a reviewer should check, each unreviewed, in the manual's order by kind."""
    _, sections = section_plan(spec)
    placed = [(item, sections[item.impairment_id]) for item in spec.impairments]
    return (
        *_citations(spec, sections),
        *(found for item, section in placed for found in _thresholds(item, section)),
        *_conversions(spec, sections),
        *(found for item, section in placed for found in _reading_rules(item, section)),
    )


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------

_HEADER: Final = """\
# The review record of the synthetic underwriting manual.
#
# The manual's ratings (its debits and its declines) are invented and are not listed
# here. Listed here is everything in the manual that claims to follow the real world,
# or that an expert was asked to judge:
#
#   citation      a guideline of a public body, as the manual cites it: the body, its
#                 abbreviation, the guideline, its year and what it is cited for
#   threshold     an edge of a band that the manual says the cited guideline draws
#   conversion    a sentence that converts a figure from another unit
#   reading_rule  which reading counts when a file holds several of one measure
#                 (invented with the manual; a medical expert is to confirm them)
#
# Every item was written by a program that could not read the sources. `unreviewed`
# means exactly that: nobody has checked it.
#
# To record a check, change only the last six fields of the item:
#
#   status           unreviewed; verified (the source says what `value` says); or
#                    corrected (the source says something else)
#   corrected_value  only when corrected: what the manual should say, in the same
#                    shape as `value`. Write a number in quotes: "6.5"
#   source           what you checked it against: the document, its year and the
#                    place in it
#   reviewed_by      your name
#   reviewed_on      the date, as YYYY-MM-DD
#   note             anything else, in your own words
#
# Leave id, kind, what, where, value and cited as they are. They say what the manual
# states, and the build fails when they differ from it.
#
# What a correction changes when the manual is generated again
# (`uv run python -m synthdata`, from the repository root):
#
#   citation      Applied. It changes text only: the citation in part 1 and in each
#                 rule's definition of the sections named, the list of public
#                 sources, and `source` of those rules in the rule table. Pages may
#                 move. No band, rating or expected verdict changes.
#   conversion    Applied when it changes text only. For HbA1c in mmol/mol and LDL
#                 cholesterol in mmol/L the cases' readings are converted with the
#                 sentence's numbers; a correction that changes those numbers is
#                 refused, as below.
#   threshold     Not applied. A corrected edge changes a rule's band, so the rule
#                 table, and may change the expected verdict of a case; the section's
#                 own wording states the number too. The generator refuses to run and
#                 names the rules and the cases. A developer then changes the
#                 manual's definition, its wording and the cases, and runs
#                 `uv run python -m synthdata review --sync`, which records the item
#                 as verified at its new value.
#   reading_rule  Not applied, for the same reason: it decides which reading of a case
#                 is rated. Refused in the same way.
#
# `uv run python -m synthdata review` prints what is left to check. The page footer of
# the manual counts the items that are verified or corrected.
#
# Not listed, because the manual states them only in running text: numbers in a
# section's prose (how old a reading may be, how a figure is worked out) and the
# range of readings a measure can take. Read the section when you check its items.
"""


def load_review(path: Path = REVIEW_FILE) -> Review:
    """Read the review file; fails, naming the item, on a record that is not whole."""
    return Review.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def dump_review(review: Review) -> str:
    """The review file's text: the header, then every item with a blank line before it."""
    body = yaml.safe_dump(
        review.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
        # A value stays on one line, so that a line can be searched for.
        width=10_000,
    )
    return _HEADER + "\n" + body.replace("\n- id:", "\n\n- id:")


def check_listed(spec: ManualSpec, review: Review) -> None:
    """Fail unless `review` lists exactly the items `spec` holds, each as the manual states it."""
    wanted = {item.id: item for item in review_items(spec)}
    listed = {item.id: item for item in review.items}
    faults = [
        *(
            f"{name}: the manual holds it and the file does not list it"
            for name in wanted
            if name not in listed
        ),
        *(
            f"{name}: the file lists it and the manual no longer holds it"
            for name in listed
            if name not in wanted
        ),
        *(
            f"{name}: the file does not state it as the manual does"
            for name, item in listed.items()
            if name in wanted and item.stated() != wanted[name].stated()
        ),
    ]
    if faults:
        raise ReviewOutOfDate(
            f"{REVIEW_FILE.name} is not the list of what the manual holds. "
            f"Run `{SYNC_COMMAND}`. " + "; ".join(faults)
        )


def synced(spec: ManualSpec, review: Review) -> tuple[Review, tuple[str, ...]]:
    """`review` brought in line with `spec`, and what changed, one line each.

    A record is kept while the manual states the item as before. Where the manual now
    states what a correction asked for, the item becomes verified at that value.
    Where it states anything else, the record was of another value and starts again.
    """
    listed = {item.id: item for item in review.items}
    items: list[ReviewItem] = []
    changes = [f"{name}: removed, the manual no longer holds it" for name in listed]
    for wanted in review_items(spec):
        old = listed.get(wanted.id)
        if old is None:
            changes.append(f"{wanted.id}: added, unreviewed")
            items.append(wanted)
            continue
        changes.remove(f"{wanted.id}: removed, the manual no longer holds it")
        record = old.model_dump(exclude=set(wanted.stated()))
        if old.value != wanted.value:
            if old.status is Status.CORRECTED and old.corrected_value == wanted.value:
                was = f"Corrected in the manual's definition from {old.value!r}."
                record |= {
                    "status": Status.VERIFIED,
                    "corrected_value": None,
                    "note": f"{old.note} {was}" if old.note else was,
                }
                changes.append(
                    f"{wanted.id}: the manual now states the correction; verified"
                )
            else:
                record = {}
                changes.append(
                    f"{wanted.id}: the manual states another value; unreviewed again"
                )
        elif old.stated() != wanted.stated():
            changes.append(f"{wanted.id}: its description follows the manual")
        items.append(ReviewItem.model_validate({**wanted.stated(), **record}))
    return Review(items=tuple(items)), tuple(changes)


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------


class Refusal(ContractModel):
    """A correction the generator does not apply, and what it would change."""

    item: ReviewItem
    # The measure whose readings the correction decides on.
    measure: Slug
    measure_label: OneLine
    # For a conversion: the other units whose readings it converts.
    units: tuple[OneLine, ...] = ()
    why: OneLine


def _measure(spec: ManualSpec, key: str) -> Measure:
    return next(m for item in spec.impairments for m in item.measures if m.key == key)


def _with_conversion(measure: Measure, sentence: str) -> Measure | None:
    """`measure` with another conversion sentence; `None` when its numbers no longer hold."""
    try:
        return Measure.model_validate({**measure.model_dump(), "conversion": sentence})
    except ValidationError:
        return None


def refusals(spec: ManualSpec, review: Review) -> tuple[Refusal, ...]:
    """The corrections that would change a band or which reading is rated: none is applied."""
    found: list[Refusal] = []
    for item in review.items:
        if item.status is not Status.CORRECTED:
            continue
        change = f"from {item.value!r} to {item.corrected_value!r}"
        if item.kind is Kind.THRESHOLD:
            rule = spec.rule(item.where.rule_ids[0])
            found.append(
                Refusal(
                    item=item,
                    measure=rule.threshold.measure,
                    measure_label=rule.threshold.measure_label,
                    why=(
                        f"it moves a band edge {change}, which changes the band of "
                        f"{' and '.join(item.where.rule_ids)}"
                    ),
                )
            )
        elif item.kind is Kind.READING_RULE:
            measure = _measure(spec, item.id.rsplit(".", 1)[-1])
            found.append(
                Refusal(
                    item=item,
                    measure=measure.key,
                    measure_label=measure.label,
                    why=f"it changes which {measure.label} reading is rated, {change}",
                )
            )
        elif item.kind is Kind.CONVERSION:
            measure = _measure(spec, item.id.rsplit(".", 1)[-1])
            if _with_conversion(measure, str(item.corrected_value)) is None:
                units = " and ".join(other.unit for other in measure.other_units)
                found.append(
                    Refusal(
                        item=item,
                        measure=measure.key,
                        measure_label=measure.label,
                        units=tuple(other.unit for other in measure.other_units),
                        why=(
                            "its sentence no longer holds the numbers a reading in "
                            f"{units} is converted with (`other_units` of the measure)"
                        ),
                    )
                )
    return tuple(found)


def with_corrections(spec: ManualSpec, review: Review) -> ManualSpec:
    """`spec` with the corrections that change text only: citations and conversion sentences.

    The corrections `refusals` lists are left out here; the generator stops on them.
    """
    refused = {refusal.item.id for refusal in refusals(spec, review)}
    names = named_sources()
    sources: dict[Source, Source] = {}
    conversions: dict[str, str] = {}
    for item in review.items:
        if item.status is not Status.CORRECTED or item.id in refused:
            continue
        fixed = item.corrected()
        if isinstance(fixed, Source):
            sources[names[item.id.removeprefix("citation.")]] = fixed
        elif isinstance(fixed, str):
            conversions[item.id.removeprefix("conversion.")] = fixed
    if not sources and not conversions:
        return spec
    impairments = tuple(
        impairment.model_copy(
            update={
                "measures": tuple(
                    measure.model_copy(update={"conversion": conversions[measure.key]})
                    if measure.key in conversions
                    else measure
                    for measure in impairment.measures
                ),
                "rules": tuple(
                    rule.model_copy(
                        update={"source": sources.get(rule.source, rule.source)}
                    )
                    for rule in impairment.rules
                ),
            }
        )
        for impairment in spec.impairments
    )
    # Validated again as a whole: a corrected citation is a source like any other.
    return ManualSpec.model_validate(
        spec.model_copy(update={"impairments": impairments}).model_dump()
    )


# ---------------------------------------------------------------------------
# What is left
# ---------------------------------------------------------------------------


def report(spec: ManualSpec, review: Review) -> str:
    """The counts by kind and status, the items nobody has checked, and the corrections refused."""
    checked, total = review.progress()
    counts = Counter((item.kind, item.status) for item in review.items)
    lines = [
        f"{REVIEW_FILE.name}: {checked} of {total} items checked against their sources.",
        "",
        f"{'kind':<14}" + "".join(f"{status:>12}" for status in Status) + f"{'all':>8}",
    ]
    for kind in Kind:
        row = [counts[kind, status] for status in Status]
        lines.append(
            f"{kind:<14}" + "".join(f"{n:>12}" for n in row) + f"{sum(row):>8}"
        )
    column = [sum(counts[kind, status] for kind in Kind) for status in Status]
    lines.append(f"{'all':<14}" + "".join(f"{n:>12}" for n in column) + f"{total:>8}")
    left = [item for item in review.items if not item.checked]
    lines += ["", f"Unreviewed ({len(left)}):"]
    lines += [f"  {item.id}  {item.what}" for item in left]
    refused = refusals(spec, review)
    if refused:
        lines += ["", f"Corrections the generator refuses ({len(refused)}):"]
        lines += [f"  {refusal.item.id}: {refusal.why}" for refusal in refused]
    return "\n".join(lines)
