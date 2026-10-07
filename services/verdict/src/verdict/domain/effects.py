"""Read a rule's rating off its text, as a run read it (AD-15).

A guard against a made-up number, not an interpreter of the manual. The
manual prints each rule's definition as one paragraph, `Rule <rule_id>: ...`,
and names its rating in one of three forms: "Probable rating: a debit of +50 %",
"Probable rating: decline" (also "decline as a postponement") and
"Probable rating: no debit, +0 %". Only these are read. A text that names no
rating in one of them bears out no reason.
"""

import re

from contracts.enums import ReasonEffect
from contracts.models.verdict import Reason
from contracts.rules import RULE_DEFINITION_PATTERN
from verdict.domain.entities import Rating

_DEFINITION = re.compile(RULE_DEFINITION_PATTERN)
# Spacing is not held to: a layout reading may print "+50%" or "+ 50 %".
_RATING = re.compile(
    r"Probable\s+rating\s*:\s*"
    r"(?:(?P<decline>decline\b)"
    r"|(?P<none>no\s+debit\b)"
    r"|a\s+debit\s+of\s*\+?\s*(?P<pct>[0-9]{1,4})\s*%)",
    re.IGNORECASE,
)


def definition_of(text: str, rule_id: str) -> str | None:
    """The part of `text` that defines `rule_id`: from its marker to the end of its own paragraph.

    It ends earlier where the next rule's marker stands in the same
    paragraph. A text that holds more than one rule (a `fixed` chunk of row
    `r1`) has its paragraphs a line break apart: what is printed after the
    definition's paragraph, a worked example say, is not the rule's, and a
    rating named there bears out nothing. None when the text does not
    define the rule. A rule that is only mentioned ("see rule UW-HT-002")
    is not defined there.
    """
    for marker in _DEFINITION.finditer(text):
        if marker.group(1) != rule_id:
            continue
        rest = text[marker.end() :].splitlines()[:1]
        own = rest[0] if rest else ""
        following = _DEFINITION.search(own)
        if following is not None:
            own = own[: following.start()]
        return text[marker.start() : marker.end()] + own
    return None


def rating_in(text: str, rule_id: str) -> Rating | None:
    """The rating the text gives the rule; None when it names none in a form that is read."""
    definition = definition_of(text, rule_id)
    if definition is None:
        return None
    found = _RATING.search(definition)
    if found is None:
        return None
    if found.group("decline"):
        return Rating(ReasonEffect.DECLINE)
    if found.group("none"):
        return Rating(ReasonEffect.NONE)
    debit_pct = int(found.group("pct"))
    # "a debit of +0 %" is no debit, however it is worded.
    return (
        Rating(ReasonEffect.DEBIT, debit_pct)
        if debit_pct
        else Rating(ReasonEffect.NONE)
    )


def agreed_effect(reason: Reason, rating: Rating) -> Rating | None:
    """The effect to store for a proposed reason, if the rule's rating bears it out; else None.

    A decline needs a rule that says decline; a debit needs that percentage
    in the rule's rating. A rule whose rating is no debit is effect `none`,
    also when the reason calls it a debit of 0.
    """
    proposed = (
        Rating(ReasonEffect.NONE)
        if reason.effect is ReasonEffect.DEBIT and not reason.debit_pct
        else Rating(reason.effect, reason.debit_pct)
    )
    return rating if proposed == rating else None
