"""The verdict agent's conversation, as the local model stand-in plays it (stories 2.5 and 2.6).

`verdict` runs an agent that is given three tools (`list_facts`,
`search_rules`, `read_rule`) and asks the chat deployment, turn by turn, what
to do next. This module is the stand-in's side of that conversation. It keeps
no state: each move is worked out from the messages of the request alone.

1. No fact listed yet: call `list_facts`.
2. Every finding among the listed facts that has not been searched for: one
   `search_rules` call each, all in one turn. A finding is a reading (a
   label, a number and a unit) or a status; a condition, a treatment or a
   family history is read as context and is not searched for. A statement
   printed on two pages is searched for once. The query is the fact's
   statement, after the conditions the file says were diagnosed.
3. Every rule a search returned whose threshold a fact meets, and that has
   not been read: `read_rule`. Then every rule a read rule points to ("If
   ..., see rule ...") whose condition a listed fact meets: `read_rule`.
4. Nothing left to search or read: the final answer, one reason for each
   rule read that the facts meet, with the effect its "Probable rating" names.

It is not a model, and it knows nothing of medicine. It reads a rule's band
from the words the manual prints after "Threshold:" (below 7.0 %, from 7.0 to
below 8.0 %, of 180 mmHg or more, smoking status: current smoker) and
compares it with the number or the status a fact states under the same
label. It knows four things about the synthetic pages beyond that: that a
blood pressure is written systolic over diastolic, that a family history is
about a relative, that a condition on file is stated with the word
"diagnosed", and that every definition says when its rule applies ("Applies
only to an applicant with type 2 diabetes mellitus on file", "Applies to any
applicant with this reading"). A rule is applied only where that sentence is
met by the case's facts: an HbA1c of 5.2 % is in the band of a diabetes
rule, and the applicant has no diabetes on file. A definition without the
sentence is applied to nobody. And where a definition says which of several
readings counts ("Where the file holds several readings, the most recent of
the last 12 months counts"), the stand-in rates that one alone, so that it
cites one band: a fact's day is the date its statement ends with, and the
last 12 months are counted back from the latest day any fact names. It
converts no units: a reading in another unit than the band's is not read.
Whether a real model reads the manual this way is checked in Azure.

What the stand-in proposes is only a proposal: `verdict`'s domain code
decides what is stored. The flaws below let tests see it refuse.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from contracts.rules import RULE_DEFINITION_PATTERN, RULE_ID_PATTERN
from synthdata.case_parts import months_before
from synthdata.manual_model import (
    APPLIES_ON_FILE,
    APPLIES_TO_ANY,
    APPLIES_UNLESS,
    COUNTS_WORDS,
    NOT_ON_A_READING,
    OF_THE_LAST,
    SEVERAL_READINGS,
)

# The name of the structured output `verdict` asks for, and the three tools
# it gives the agent: what a verdict request is told by.
VERDICT_SCHEMA_NAME = "verdict_output"
LIST_FACTS = "list_facts"
SEARCH_RULES = "search_rules"
READ_RULE = "read_rule"
TOOL_NAMES = frozenset({LIST_FACTS, SEARCH_RULES, READ_RULE})

# How sure the stand-in says it is, and what it says in the `low_confidence`
# flaw: under the service's default floor of 0.70.
CONFIDENCE = 0.9
LOW_CONFIDENCE = 0.6
# A well-formed rule id that the manual does not define and no tool returns.
UNSEEN_RULE_ID = "UW-ZZ-999"
# A well-formed fact id that `list_facts` never answers.
UNLISTED_FACT_ID = "00000000-0000-7000-8000-000000000000"
# What a model writes when it ignores the format it was given.
PROSE_ANSWER = "I would accept this applicant with a loading, I think."
# The longest query a search takes is far longer; a statement is one line.
_MAX_QUERY_CHARS = 500


class Flaw(StrEnum):
    """What is wrong with the conversation, for the tests of `verdict`'s guards."""

    # Never answers: every turn calls `list_facts` again.
    ENDLESS_LOOP = "endless_loop"
    # The answer also cites a rule no tool returned, and a fact never listed.
    UNSEEN_RULE = "unseen_rule"
    # Every reason gives an effect its rule's text does not say.
    WRONG_EFFECT = "wrong_effect"
    # The answer's confidence is `LOW_CONFIDENCE`.
    LOW_CONFIDENCE = "low_confidence"
    # The final answer is prose, not the object that was asked for.
    INVALID_ANSWER = "invalid_answer"


def is_verdict_request(body: Mapping[str, Any]) -> bool:
    """Whether a chat request is a turn of the verdict agent.

    Told by what the request is built from, never by its wording: the name
    of the structured output it asks for, or the three tools it offers.
    """
    try:
        if body["response_format"]["json_schema"]["name"] == VERDICT_SCHEMA_NAME:
            return True
    except (KeyError, TypeError):
        pass
    try:
        names = {tool["function"]["name"] for tool in body["tools"]}
    except (KeyError, TypeError):
        return False
    return names == TOOL_NAMES


# --- reading the manual's words ----------------------------------------------

_NUMBER = r"-?\d+(?:\.\d+)?"
# The forms `manual_model.Measure` words a band in, each after the measure's
# label. A band between two ends is looked for first: "above 40 and below 50"
# would otherwise be read as "above 40".
_BAND_FORMS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"(?P<label>.+?) (?P<open>from|above) (?P<lower>{_NUMBER}) "
        rf"(?P<close>to below|to|and up to|and below) (?P<upper>{_NUMBER})"
        r"(?: (?P<unit>.+))?"
    ),
    re.compile(rf"(?P<label>.+?) of (?P<lower>{_NUMBER})(?: (?P<unit>.+?))? or more"),
    re.compile(rf"(?P<label>.+?) of (?P<upper>{_NUMBER})(?: (?P<unit>.+?))? or less"),
    re.compile(rf"(?P<label>.+?) below (?P<upper>{_NUMBER})(?: (?P<unit>.+))?"),
    re.compile(rf"(?P<label>.+?) above (?P<lower>{_NUMBER})(?: (?P<unit>.+))?"),
)
_STATUS_FORM = re.compile(r"(?P<label>[^:]+): (?P<category>.+)")
_DEFINITION = re.compile(RULE_DEFINITION_PATTERN)
_IMPAIRMENT = re.compile(
    rf"Rule {RULE_ID_PATTERN}: (?P<name>.+?) \(section [0-9.]+\)\."
)
_THRESHOLD = re.compile(r"Threshold: (?P<words>.+?)\. Probable rating:")
_RATING = re.compile(
    r"Probable rating: (?:(?P<decline>decline\b)|(?P<none>no debit\b)"
    r"|a debit of \+? ?(?P<pct>[0-9]{1,4}) ?%)"
)
# One pointer of a definition: "If <when>, see rule <rule_id>", and whether
# the other rule applies in place of this one. A sentence that starts with
# "If" and points nowhere is not taken into the next one.
_POINTER = re.compile(
    rf"\bIf (?P<when>(?:(?!\bIf\b).)+?), see rule (?P<rule_id>{RULE_ID_PATTERN})"
    r"(?P<replaces>, which applies in place of this rule)?"
)
# What a pointer's condition says before the other rule's threshold.
_WHEN_FORMS: tuple[re.Pattern[str], ...] = (
    re.compile(r"the applicant (?:also|instead) has .+? with (?P<words>.+)"),
    re.compile(r"the record also shows (?P<words>.+)"),
)


@dataclass(frozen=True, slots=True)
class Band:
    """A threshold as the manual words it: a measure's label, and a band of numbers or a status."""

    label: str
    lower: Decimal | None = None
    lower_included: bool = False
    upper: Decimal | None = None
    upper_included: bool = False
    unit: str | None = None
    # For a status: the value the threshold names.
    category: str | None = None

    def holds(self, reading: Decimal | str) -> bool:
        """Whether a reading (a number, or a status in the fact's words) is in the band."""
        if self.category is not None:
            return isinstance(reading, str) and _starts_with(reading, self.category)
        if isinstance(reading, str):
            return False
        if self.lower is not None and (
            reading < self.lower or (reading == self.lower and not self.lower_included)
        ):
            return False
        return (
            self.upper is None
            or reading < self.upper
            or (reading == self.upper and self.upper_included)
        )


def _starts_with(text: str, start: str) -> bool:
    """Whether `text` starts with the words of `start`, whatever the capitals."""
    text, start = text.casefold().strip(), start.casefold().strip()
    return text.startswith(start) and not text[len(start) : len(start) + 1].isalnum()


def band_of(words: str) -> Band | None:
    """The band the words of a threshold name; None when they are in none of the manual's forms."""
    words = " ".join(words.split())
    for form in _BAND_FORMS:
        found = form.fullmatch(words)
        if found is None:
            continue
        parts = found.groupdict()
        lower, upper = parts.get("lower"), parts.get("upper")
        # "from 7.0" and "of 180 or more" include the end; "above 40" does not.
        opened = parts.get("open") or ("from" if " or more" in words else "above")
        # "to 300", "up to 300" and "of 40 or less" include it; "below 8.0" does not.
        closed = parts.get("close") or ("to" if " or less" in words else "to below")
        return Band(
            label=parts["label"],
            lower=Decimal(lower) if lower is not None else None,
            lower_included=lower is not None and opened == "from",
            upper=Decimal(upper) if upper is not None else None,
            upper_included=upper is not None and closed in ("to", "and up to"),
            unit=parts.get("unit"),
        )
    status = _STATUS_FORM.fullmatch(words)
    if status is None:
        return None
    return Band(label=status.group("label"), category=status.group("category"))


@dataclass(frozen=True, slots=True)
class Pointer:
    """One "see rule" of a definition: the rule pointed to, and when it applies."""

    rule_id: str
    band: Band | None
    replaces: bool


@dataclass(frozen=True, slots=True)
class Definition:
    """One rule as a text defines it."""

    rule_id: str
    impairment: str | None
    band: Band | None
    # `debit`, `decline` or `none`, as the contract names an effect; None
    # when the text names no rating.
    effect: str | None
    debit_pct: int | None
    pointers: tuple[Pointer, ...] = ()
    # When the rule applies, as its definition says: the impairment that
    # must be on file (None when a reading is enough), and whether the
    # definition says so at all.
    on_file: str | None = None
    any_reading: bool = False
    # Impairments that rule this one out when they are on file.
    unless: tuple[str, ...] = ()
    # Which of several readings counts, as the definition says (a key of
    # `COUNTS_WORDS`), and within how many months; None when it does not say.
    choose: str | None = None
    within_months: int | None = None


def _pointer(found: re.Match[str]) -> Pointer:
    when = found.group("when")
    band = None
    for form in _WHEN_FORMS:
        condition = form.fullmatch(when)
        if condition is not None:
            band = band_of(condition.group("words"))
            break
    return Pointer(found.group("rule_id"), band, found.group("replaces") is not None)


# The sentence of a definition that says when its rule applies, in the two
# forms the manual prints (`manual.applicability_words`).
_APPLIES = re.compile(
    r"(?:"
    + APPLIES_ON_FILE.format(name=r"(?P<name>[^.:,]+?)")
    + r"|(?P<any>"
    + APPLIES_TO_ANY
    + r"))(?:"
    + APPLIES_UNLESS.format(names=r"(?P<unless>[^.:]+?)")
    + r")?(?:"
    + re.escape(NOT_ON_A_READING)
    + r")?\."
)


def _applicability(words: str) -> dict[str, Any]:
    """When a definition says its rule applies; nothing when it does not say."""
    found = _APPLIES.search(words)
    if found is None:
        return {}
    unless = found.group("unless")
    return {
        "on_file": found.group("name"),
        "any_reading": found.group("any") is not None,
        "unless": tuple(unless.split(" or ")) if unless else (),
    }


# The sentence of a definition that says which of several readings counts
# (`manual_model.ReadingRule.definition_words`). The longer wording is looked
# for first: "the most recent" must not be read out of another one.
_SEVERAL = re.compile(
    re.escape(SEVERAL_READINGS)
    .replace(
        re.escape("{which}"),
        "(?P<which>"
        + "|".join(
            re.escape(words) for words in sorted(COUNTS_WORDS.values(), key=len)[::-1]
        )
        + ")",
    )
    .replace(
        re.escape("{window}"),
        "(?:"
        + re.escape(OF_THE_LAST).replace(re.escape("{months}"), r"(?P<months>\d+)")
        + ")?",
    )
    + r"\."
)


def _reading_rule(words: str) -> dict[str, Any]:
    """Which of several readings a definition says counts; nothing when it does not say."""
    found = _SEVERAL.search(words)
    if found is None:
        return {}
    choose = next(
        key for key, value in COUNTS_WORDS.items() if value == found.group("which")
    )
    months = found.group("months")
    return {"choose": choose, "within_months": int(months) if months else None}


def definitions_in(text: str) -> list[Definition]:
    """Every rule a text defines, read from its words. A rule that is only mentioned is not one."""
    # A layout reading breaks a paragraph into lines; the words are the same.
    text = " ".join(text.split())
    markers = list(_DEFINITION.finditer(text))
    definitions: list[Definition] = []
    for place, marker in enumerate(markers):
        end = markers[place + 1].start() if place + 1 < len(markers) else len(text)
        words = text[marker.start() : end]
        impairment = _IMPAIRMENT.match(words)
        threshold = _THRESHOLD.search(words)
        rating = _RATING.search(words)
        effect: str | None = None
        debit_pct: int | None = None
        if rating is not None:
            if rating.group("decline"):
                effect = "decline"
            elif rating.group("none") or int(rating.group("pct")) == 0:
                effect = "none"
            else:
                effect, debit_pct = "debit", int(rating.group("pct"))
        definitions.append(
            Definition(
                rule_id=marker.group(1),
                impairment=impairment.group("name") if impairment else None,
                band=band_of(threshold.group("words")) if threshold else None,
                **_applicability(words),
                **_reading_rule(words),
                effect=effect,
                debit_pct=debit_pct,
                pointers=tuple(_pointer(found) for found in _POINTER.finditer(words)),
            )
        )
    return definitions


# --- reading a fact ----------------------------------------------------------

_BLOOD_PRESSURE = re.compile(
    rf"\bblood pressure:? (?P<systolic>{_NUMBER})/(?P<diastolic>{_NUMBER}) mmHg",
    re.IGNORECASE,
)
# A reading: a label, a number (or two, for a blood pressure) and a unit, and
# perhaps the day it was taken. A diagnosis or a treatment has a comma or
# words before its first number, and is not one.
_READING = re.compile(
    rf"[A-Za-z][^:,]*?:? {_NUMBER}(?:/{_NUMBER})? [^,]+?(?: on \d{{4}}-\d{{2}}-\d{{2}})?"
)
_STATUS = re.compile(r"[A-Za-z][^:,]* status: .+", re.IGNORECASE)
_FAMILY_HISTORY = "family history"
# How the synthetic pages state a diagnosis: the condition, then its date.
_DIAGNOSED = ", diagnosed "
# Between a label and its value: "HbA1c 7.4", "Latest HbA1c: 7.4",
# "Body mass index (BMI): 28.7".
_BEFORE_A_VALUE = r"\s*(?:\([^)]*\))?\s*:?\s*"


def _spelled_out(statement: str) -> str:
    """A statement with a blood pressure named as its two readings: systolic over diastolic."""
    return _BLOOD_PRESSURE.sub(
        lambda found: (
            f"systolic blood pressure {found.group('systolic')} mmHg, "
            f"diastolic blood pressure {found.group('diastolic')} mmHg"
        ),
        statement,
    )


def condition_in(statement: str) -> str | None:
    """The condition a fact says was diagnosed: "Condition: Essential hypertension, diagnosed ..."."""
    if is_about_a_relative(statement):
        return None
    name, diagnosed, _ = statement.partition(_DIAGNOSED)
    if not diagnosed:
        return None
    return name.rpartition(": ")[2].strip() or None


def query_for(statement: str, facts: Sequence[Mapping[str, Any]]) -> str:
    """The words of a search about one fact: the conditions on file, then the fact's statement.

    A reading is searched for as the reading of an applicant with those
    conditions: "Type 2 diabetes mellitus: HbA1c 7.4 %". The manual has a
    band of HbA1c for more than one impairment, and the statement alone does
    not say whose bands are wanted.
    """
    conditions = list(
        dict.fromkeys(
            condition
            for fact in facts
            if (condition := condition_in(fact["statement"])) is not None
        )
    )
    query = f"{', '.join(conditions)}: {statement}" if conditions else statement
    return query[:_MAX_QUERY_CHARS]


def is_about_a_relative(statement: str) -> bool:
    """Whether a fact is a family history: what it names is not the applicant's."""
    return statement.casefold().lstrip().startswith(_FAMILY_HISTORY)


def is_finding(statement: str) -> bool:
    """Whether a fact is worth a search: a reading with its unit, or a status."""
    if is_about_a_relative(statement):
        return False
    return bool(_READING.fullmatch(statement) or _STATUS.fullmatch(statement))


def reading_of(statement: str, band: Band) -> Decimal | str | None:
    """What a fact states for the measure of `band`: a number, a status in its words, or nothing.

    The measure's label must be in the statement, and for a number the unit
    of the band must follow it: no unit is converted and nothing is guessed.
    """
    if is_about_a_relative(statement):
        return None
    text = _spelled_out(statement)
    label = re.search(
        rf"(?<![A-Za-z0-9]){re.escape(band.label)}(?![A-Za-z0-9])", text, re.IGNORECASE
    )
    if label is None:
        return None
    rest = text[label.end() :]
    if band.category is not None:
        value = re.match(_BEFORE_A_VALUE, rest)
        return rest[value.end() if value else 0 :] or None
    number = re.match(rf"{_BEFORE_A_VALUE}(?P<number>{_NUMBER})(?P<after>.*)", rest)
    if number is None:
        return None
    if band.unit is not None and not _starts_with(number.group("after"), band.unit):
        return None
    return Decimal(number.group("number"))


def meets(statement: str, band: Band | None) -> bool:
    """Whether a fact states a reading that lies in the band."""
    if band is None:
        return False
    reading = reading_of(statement, band)
    return reading is not None and band.holds(reading)


# --- the conversation so far -------------------------------------------------


@dataclass(slots=True)
class Conversation:
    """What the messages of one request say has happened: the tool calls and their answers."""

    # The facts `list_facts` answered; None until it has.
    facts: list[dict[str, Any]] | None = None
    # The facts a search was made for, answered or refused.
    searched: set[str] = field(default_factory=set)
    # Every rule definition a search returned, by the fact it was made for.
    found: dict[str, list[Definition]] = field(default_factory=dict)
    # The rules a read was asked for, answered or refused.
    asked: set[str] = field(default_factory=set)
    # The rules read, as their text defines them, and what each may be followed to.
    read: dict[str, Definition] = field(default_factory=dict)
    references: dict[str, list[str]] = field(default_factory=dict)
    tool_calls: int = 0

    def definitions(self) -> Iterable[Definition]:
        """Every definition the conversation has shown, the read ones first."""
        yield from self.read.values()
        for definitions in self.found.values():
            yield from definitions


def _text_of(content: object) -> str:
    """A message's content as text, whether it is a string or a list of text parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def conversation_of(messages: Sequence[Mapping[str, Any]]) -> Conversation:
    """Read the tool calls of a request's messages, and what each was answered."""
    seen = Conversation()
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                calls[str(call.get("id"))] = (
                    str(function.get("name")),
                    _json_object(str(function.get("arguments") or "{}")),
                )
                seen.tool_calls += 1
        elif role == "tool":
            name, arguments = calls.get(str(message.get("tool_call_id")), ("", {}))
            _note(seen, name, arguments, _json_object(_text_of(message.get("content"))))
    return seen


def _note(
    seen: Conversation, name: str, arguments: dict[str, Any], answer: dict[str, Any]
) -> None:
    if name == LIST_FACTS and isinstance(answer.get("facts"), list):
        seen.facts = [fact for fact in answer["facts"] if _is_fact(fact)]
    elif name == SEARCH_RULES:
        fact_id = arguments.get("fact_id")
        if not isinstance(fact_id, str):
            return
        # A refused search is not made again.
        seen.searched.add(fact_id)
        rules = answer.get("rules")
        for rule in rules if isinstance(rules, list) else []:
            if isinstance(rule, dict) and isinstance(rule.get("text"), str):
                seen.found.setdefault(fact_id, []).extend(definitions_in(rule["text"]))
    elif name == READ_RULE:
        rule_id = arguments.get("rule_id")
        if not isinstance(rule_id, str):
            return
        # Nor is a refused read.
        seen.asked.add(rule_id)
        if answer.get("rule_id") != rule_id or not isinstance(answer.get("text"), str):
            return
        for definition in definitions_in(answer["text"]):
            if definition.rule_id == rule_id:
                seen.read[rule_id] = definition
        references = answer.get("reference_rule_ids")
        seen.references[rule_id] = [
            item for item in references or [] if isinstance(item, str)
        ]


def _is_fact(fact: object) -> bool:
    return (
        isinstance(fact, dict)
        and isinstance(fact.get("fact_id"), str)
        and isinstance(fact.get("statement"), str)
    )


# --- which rule a fact meets -------------------------------------------------


def _named(impairment: str | None, facts: Sequence[Mapping[str, Any]]) -> bool:
    """Whether a fact about the applicant names the impairment: it is on file."""
    if not impairment:
        return False
    name = impairment.casefold()
    return any(
        name in fact["statement"].casefold()
        for fact in facts
        if not is_about_a_relative(fact["statement"])
    )


def _applies(
    rule: Definition, facts: Sequence[Mapping[str, Any]], seen: Conversation
) -> bool:
    """Whether a rule whose band a fact meets is the applicant's to meet.

    By the sentence of its own definition, and by nothing else: the
    impairment it names must be on file, or a reading is enough; and no
    impairment that rules it out may be on file. A definition that does not
    say when it applies is applied to nobody.
    """
    if rule.band is None or any(_named(other, facts) for other in rule.unless):
        return False
    if rule.on_file is not None:
        return _named(rule.on_file, facts)
    return rule.any_reading


_ON_A_DAY = re.compile(r" on (\d{4}-\d{2}-\d{2})$")


def _day_of(statement: str) -> date | None:
    """The day a fact says its reading was taken: the date its statement ends with."""
    found = _ON_A_DAY.search(statement.strip())
    return date.fromisoformat(found.group(1)) if found else None


def _counting(
    rule: Definition, band: Band, facts: Sequence[Mapping[str, Any]]
) -> list[str]:
    """The facts whose reading counts by the rule's own sentence, if it is in the band.

    The readings of the band's measure are taken from every fact, those older
    than the sentence allows are left out, and the one it names is rated: the
    most recent, the highest, the lowest, or the average of them all. A
    reading with no day is taken as of the latest day any fact names.
    """
    days = [day for fact in facts if (day := _day_of(fact["statement"])) is not None]
    today = max(days, default=date.min)
    stated = [
        (fact["fact_id"], reading, _day_of(fact["statement"]) or today)
        for fact in facts
        if isinstance(reading := reading_of(fact["statement"], band), Decimal)
    ]
    if rule.within_months is not None and days:
        oldest = months_before(today, rule.within_months)
        stated = [item for item in stated if item[2] >= oldest]
    if not stated:
        return []
    readings = [reading for _, reading, _ in stated]
    if rule.choose == "average":
        places = max(
            -int(end.as_tuple().exponent)
            for end in (band.lower, band.upper)
            if end is not None
        )
        counted = (sum(readings, Decimal(0)) / len(readings)).quantize(
            Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP
        )
        return [fact_id for fact_id, _, _ in stated] if band.holds(counted) else []
    if rule.choose == "most_recent":
        latest = max(day for _, _, day in stated)
        counted = next(reading for _, reading, day in stated if day == latest)
    else:
        counted = max(readings) if rule.choose == "highest" else min(readings)
    if not band.holds(counted):
        return []
    return [fact_id for fact_id, reading, _ in stated if reading == counted]


def _facts_meeting(
    rule: Definition, facts: Sequence[Mapping[str, Any]], seen: Conversation
) -> list[str]:
    """The ids of the facts a rule applies to; none when it does not apply."""
    if rule.band is None or not _applies(rule, facts, seen):
        return []
    if rule.choose is not None:
        return _counting(rule, rule.band, facts)
    return [fact["fact_id"] for fact in facts if meets(fact["statement"], rule.band)]


def _to_search(seen: Conversation) -> list[dict[str, Any]]:
    """The findings not searched for yet, one for each statement."""
    statements = {
        fact["statement"]
        for fact in seen.facts or []
        if fact["fact_id"] in seen.searched
    }
    pending: list[dict[str, Any]] = []
    for fact in seen.facts or []:
        statement = fact["statement"]
        if is_finding(statement) and statement not in statements:
            statements.add(statement)
            pending.append(fact)
    return pending


def _to_read(seen: Conversation) -> list[str]:
    """The rules to read next: found and met, or pointed to by a read rule whose condition is met."""
    facts = seen.facts or []
    by_id = {fact["fact_id"]: fact for fact in facts}
    wanted: list[str] = []
    for fact_id, definitions in seen.found.items():
        fact = by_id.get(fact_id)
        if fact is None:
            continue
        for rule in definitions:
            # By the rule's own sentence the reading that counts may be another
            # fact's, or the average of several: the rule is read all the same.
            if (
                rule.choose is not None or meets(fact["statement"], rule.band)
            ) and _facts_meeting(rule, facts, seen):
                wanted.append(rule.rule_id)
    for rule_id, rule in seen.read.items():
        # A rule the facts do not meet is not followed.
        if not _facts_meeting(rule, facts, seen):
            continue
        for pointer in rule.pointers:
            if pointer.rule_id in seen.references.get(rule_id, []) and any(
                meets(fact["statement"], pointer.band) for fact in facts
            ):
                wanted.append(pointer.rule_id)
    return [rule_id for rule_id in dict.fromkeys(wanted) if rule_id not in seen.asked]


def reasons_of(seen: Conversation) -> list[dict[str, Any]]:
    """One reason for each rule read that the facts meet, with the effect its text names."""
    facts = seen.facts or []
    met = {
        rule_id: fact_ids
        for rule_id, rule in seen.read.items()
        if rule.effect is not None and (fact_ids := _facts_meeting(rule, facts, seen))
    }
    # A rule that another one met applies in place of is left out.
    replaced = {
        rule_id
        for rule_id in met
        for pointer in seen.read[rule_id].pointers
        if pointer.replaces and pointer.rule_id in met
    }
    return [
        {
            "rule_id": rule_id,
            "fact_ids": fact_ids,
            "effect": seen.read[rule_id].effect,
            "debit_pct": seen.read[rule_id].debit_pct,
        }
        for rule_id, fact_ids in met.items()
        if rule_id not in replaced
    ]


# --- the next move -----------------------------------------------------------


def _call(number: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"call_{number}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _calls(
    seen: Conversation, name: str, arguments: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """An assistant message that asks for tools, each call with an id of its own."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            _call(seen.tool_calls + place, name, each)
            for place, each in enumerate(arguments, start=1)
        ],
    }


def _wrong(reason: dict[str, Any]) -> dict[str, Any]:
    """The reason with an effect its rule does not name: twice the debit, or a debit for none."""
    if reason["effect"] == "debit":
        return {**reason, "debit_pct": reason["debit_pct"] * 2}
    return {**reason, "effect": "debit", "debit_pct": 50}


def _verdict_word(reasons: Sequence[Mapping[str, Any]]) -> str:
    if any(reason["effect"] == "decline" for reason in reasons):
        return "decline"
    if any(reason["effect"] == "debit" for reason in reasons):
        return "loaded"
    return "standard"


def final_answer(seen: Conversation, flaw: Flaw | None = None) -> str:
    """The content of the agent's last message: the proposal, or what a flaw makes of it."""
    if flaw is Flaw.INVALID_ANSWER:
        return PROSE_ANSWER
    reasons = reasons_of(seen)
    if flaw is Flaw.WRONG_EFFECT:
        reasons = [_wrong(reason) for reason in reasons]
    # The word is the stand-in's own reading; the service never stores it.
    verdict = _verdict_word(reasons)
    if flaw is Flaw.UNSEEN_RULE:
        facts = seen.facts or []
        shown = next((rule.rule_id for rule in seen.definitions()), None)
        if facts:
            reasons.append(
                {
                    "rule_id": UNSEEN_RULE_ID,
                    "fact_ids": [facts[0]["fact_id"]],
                    "effect": "debit",
                    "debit_pct": 50,
                }
            )
        if shown is not None:
            reasons.append(
                {
                    "rule_id": shown,
                    "fact_ids": [UNLISTED_FACT_ID],
                    "effect": "debit",
                    "debit_pct": 50,
                }
            )
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": LOW_CONFIDENCE if flaw is Flaw.LOW_CONFIDENCE else CONFIDENCE,
            "reasons": reasons,
            "system_reasons": [],
        }
    )


def next_message(body: Mapping[str, Any], flaw: Flaw | None = None) -> dict[str, Any]:
    """The assistant's next message in a verdict conversation: tool calls, or the final answer."""
    messages = body.get("messages")
    seen = conversation_of(messages if isinstance(messages, list) else [])
    if seen.facts is None or flaw is Flaw.ENDLESS_LOOP:
        return _calls(seen, LIST_FACTS, [{}])
    searches = [
        {
            "query": query_for(fact["statement"], seen.facts),
            "fact_id": fact["fact_id"],
        }
        for fact in _to_search(seen)
    ]
    if searches:
        return _calls(seen, SEARCH_RULES, searches)
    reads = [{"rule_id": rule_id} for rule_id in _to_read(seen)]
    if reads:
        return _calls(seen, READ_RULE, reads)
    return {"role": "assistant", "content": final_answer(seen, flaw)}
