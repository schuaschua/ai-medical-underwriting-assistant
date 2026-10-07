"""Decide what is stored of a run: the reasons, the system reasons, the verdict and the loading (AD-15, AD-10).

The agent proposes; this module decides. A proposed reason is kept only if
the run saw its rule and listed every one of its facts, and only if its
effect is what the rule's text says; one with a debit or a decline only if
the run read the rule. The rules that refer a case, the verdict
and the loading are worked out here from what was kept. The agent's own
verdict word is read and thrown away. The prompt decides none of this.
"""

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from contracts.enums import ReasonEffect, SystemReason, Verdict
from contracts.models.verdict import Reason, VerdictOutput
from verdict.domain.effects import agreed_effect, rating_in
from verdict.domain.entities import Suggestion
from verdict.domain.state import RunState

DEFAULT_CONFIDENCE_FLOOR = 0.70

# The system reasons the agent may report. Every other one is set by code
# alone, whatever the agent says.
_REPORTED_BY_THE_AGENT = frozenset(
    {SystemReason.NO_MATCHING_RULE, SystemReason.CONFLICTING_RULES}
)
_REASONS_FIELD = "reasons"
_EFFECTS_WITHOUT_A_FIGURE = (ReasonEffect.NONE.value, ReasonEffect.DECLINE.value)

# Why a proposed reason was not kept; codes for the log.
MALFORMED = "malformed"
RULE_NOT_SEEN = "rule_not_seen"
FACT_NOT_LISTED = "fact_not_listed"
RULE_NOT_READ = "rule_not_read"
RATING_NOT_READ = "rating_not_read"
EFFECT_NOT_IN_RULE = "effect_not_in_rule"


class InvalidModelOutput(Exception):
    """The agent's final answer is not a `VerdictOutput`: no part of it is stored."""


class AnswerCutOff(InvalidModelOutput):
    """The model stopped at its token limit: what it gave is the start of an answer."""


@dataclass(frozen=True, slots=True)
class Proposal:
    """The agent's final answer, read: what it proposes, and nothing it decides."""

    confidence: float
    reasons: tuple[Reason, ...]
    # Only the system reasons the agent may report.
    reported: frozenset[SystemReason]
    # How many entries of `reasons` were left out because they are no reason
    # at all: a field missing, an id that is none, a debit without a figure.
    malformed: int = 0
    # How many of those named a debit or a decline as their effect, or none
    # that can be read.
    malformed_weighty: int = 0

    @property
    def proposed(self) -> int:
        """How many reasons the answer held in all."""
        return len(self.reasons) + self.malformed


@dataclass(frozen=True, slots=True)
class KeptReasons:
    """What the keep-or-drop rule made of the proposed reasons."""

    reasons: tuple[Reason, ...]
    # Why reasons were dropped, by cause; for the log, never stored.
    dropped: Counter[str] = field(default_factory=Counter)
    # How many of the dropped reasons proposed a debit or a decline.
    dropped_weighty: int = 0


def _reason_of(entry: object) -> Reason | None:
    """One entry of the answer's reasons as a `Reason`; None if it cannot be one."""
    if (
        isinstance(entry, dict)
        and entry.get("effect") in _EFFECTS_WITHOUT_A_FIGURE
        # "No debit" is often written as a debit of 0 beside the effect.
        # `False` equals 0 in Python and is no figure.
        and entry.get("debit_pct") == 0
        and entry.get("debit_pct") is not False
    ):
        entry = {**entry, "debit_pct": None}
    try:
        return Reason.model_validate(entry)
    except ValidationError:
        return None


def read_proposal(answer: str) -> Proposal:
    """Read the agent's final answer; `InvalidModelOutput` unless it has the contract's shape.

    The answer must be one JSON object with the fields of the contracts'
    `VerdictOutput` and no other. Within its list of reasons a single entry
    that is no reason is left out and counted, as a reason that cites the
    unseen is: one bad entry does not fail the run, and the case with it.
    The verdict word is checked and not kept.
    """
    try:
        given = json.loads(answer)
    except (ValueError, RecursionError):
        # Not JSON, or nested without end. Not raised from the parser's
        # error: that one holds the answer.
        raise InvalidModelOutput from None
    if not isinstance(given, dict) or not isinstance(given.get(_REASONS_FIELD), list):
        raise InvalidModelOutput
    entries = given[_REASONS_FIELD]
    read = [_reason_of(entry) for entry in entries]
    reasons = tuple(reason for reason in read if reason is not None)
    # An entry that is no reason and does not plainly say "no effect" is
    # taken for one that would have weighed.
    weighty = sum(
        reason is None
        and not (
            isinstance(entry, dict) and entry.get("effect") == ReasonEffect.NONE.value
        )
        for entry, reason in zip(entries, read, strict=True)
    )
    try:
        output = VerdictOutput.model_validate({**given, _REASONS_FIELD: []})
    except ValidationError:
        raise InvalidModelOutput from None
    return Proposal(
        confidence=output.confidence,
        reasons=reasons,
        reported=frozenset(output.system_reasons) & _REPORTED_BY_THE_AGENT,
        malformed=len(read) - len(reasons),
        malformed_weighty=weighty,
    )


def _weighs(reason: Reason) -> bool:
    """Whether a reason proposes a debit above zero or a decline."""
    return reason.effect is ReasonEffect.DECLINE or bool(reason.debit_pct)


def keep_reasons(proposed: Sequence[Reason], state: RunState) -> KeptReasons:
    """Keep the proposed reasons the run can bear out; count the rest (AD-15).

    Kept only if the rule was returned by a search of this run or read in
    it, every fact was listed in it, and the effect agrees with the rule's
    text as the run read it. A debit or a decline needs the rule to have
    been read in this run, and is checked against the text that read
    answered: the chunk a search returned bears out no rating but "no
    debit". A rule proposed twice is kept once, with the facts of both: its
    debit counts once.
    """
    kept: dict[str, Reason] = {}
    dropped: Counter[str] = Counter()
    weighty = 0

    def drop(reason: Reason, cause: str) -> None:
        nonlocal weighty
        dropped[cause] += 1
        weighty += _weighs(reason)

    for reason in proposed:
        if not state.saw_rule(reason.rule_id):
            drop(reason, RULE_NOT_SEEN)
            continue
        if any(fact_id not in state.facts_listed for fact_id in reason.fact_ids):
            drop(reason, FACT_NOT_LISTED)
            continue
        rating = rating_in(state.rule_texts[reason.rule_id], reason.rule_id)
        if rating is None:
            drop(reason, RATING_NOT_READ)
            continue
        if reason.rule_id not in state.read and (
            _weighs(reason) or rating.effect is not ReasonEffect.NONE
        ):
            drop(reason, RULE_NOT_READ)
            continue
        effect = agreed_effect(reason, rating)
        if effect is None:
            drop(reason, EFFECT_NOT_IN_RULE)
            continue
        earlier = kept.get(reason.rule_id)
        fact_ids = list(
            dict.fromkeys([*(earlier.fact_ids if earlier else []), *reason.fact_ids])
        )
        kept[reason.rule_id] = Reason(
            rule_id=reason.rule_id,
            fact_ids=fact_ids,
            effect=effect.effect,
            debit_pct=effect.debit_pct,
        )
    return KeptReasons(tuple(kept.values()), dropped, weighty)


def rules_conflict(reasons: Sequence[Reason], state: RunState) -> bool:
    """Whether two kept reasons cite different rules of one impairment, whatever their facts.

    The rules of an impairment are the bands of its measure: an applicant
    meets one band, so two of them cannot both apply and their debits are
    never added up.
    """
    impairments = [
        state.impairments[reason.rule_id]
        for reason in reasons
        if reason.rule_id in state.impairments
    ]
    return len(set(impairments)) < len(impairments)


def cites_unverified_quote(reasons: Sequence[Reason], state: RunState) -> bool:
    """Whether a reason with a debit or a decline rests on a fact whose quote is not verified."""
    return any(
        not state.facts_listed[fact_id].quote_verified
        for reason in reasons
        if reason.effect is not ReasonEffect.NONE
        for fact_id in reason.fact_ids
    )


def verdict_of(
    reasons: Sequence[Reason], system_reasons: Sequence[SystemReason]
) -> tuple[Verdict, int | None]:
    """The verdict and the loading, from the kept reasons and the system reasons alone.

    Any system reason gives `refer`. Otherwise a decline gives `decline`;
    otherwise debits give `loaded`, with the loading their sum; otherwise
    `standard`.
    """
    if system_reasons:
        return Verdict.REFER, None
    if any(reason.effect is ReasonEffect.DECLINE for reason in reasons):
        return Verdict.DECLINE, None
    loading_pct = sum(reason.debit_pct or 0 for reason in reasons)
    if loading_pct > 0:
        return Verdict.LOADED, loading_pct
    return Verdict.STANDARD, None


def _suggestion(
    reasons: tuple[Reason, ...],
    found: set[SystemReason],
    confidence: float | None,
    dropped: int = 0,
) -> Suggestion:
    # In the order the contracts list them, so a run reads the same every time.
    system_reasons = tuple(reason for reason in SystemReason if reason in found)
    verdict, loading_pct = verdict_of(reasons, system_reasons)
    return Suggestion(
        verdict=verdict,
        loading_pct=loading_pct,
        confidence=confidence,
        reasons=reasons,
        system_reasons=system_reasons,
        dropped=dropped,
    )


def without_facts() -> Suggestion:
    """A case with no fact: there is nothing a rule could match, and the agent is not asked."""
    return _suggestion((), {SystemReason.NO_MATCHING_RULE}, None)


def stopped_at_the_step_limit() -> Suggestion:
    """A run its step limit stopped: it gave no answer, so nothing is proposed and nothing kept."""
    return _suggestion((), {SystemReason.STEP_LIMIT}, None)


def decide(
    proposal: Proposal,
    state: RunState,
    *,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
) -> tuple[Suggestion, KeptReasons]:
    """What is stored for a run whose agent gave an answer; and what the keep-or-drop rule did.

    The system reasons: `unverified_quote` when a kept debit or decline
    cites a fact whose quote is not verified; `low_confidence` when the
    agent's confidence is under the floor (at the floor it is not), and
    when the run did not list the facts or searched the manual for none of
    them, whatever confidence it claims; `no_matching_rule` when the agent
    reports it, when it proposed reasons of which none could be kept, and
    when a proposed debit or decline was dropped (what is left would be a
    lighter verdict than the agent meant); `conflicting_rules` when the
    agent reports it, or kept reasons cite different rules of one
    impairment.
    """
    kept = keep_reasons(proposal.reasons, state)
    found = set(proposal.reported)
    if cites_unverified_quote(kept.reasons, state):
        found.add(SystemReason.UNVERIFIED_QUOTE)
    if proposal.confidence < confidence_floor or not state.looked_things_up():
        found.add(SystemReason.LOW_CONFIDENCE)
    if (
        (proposal.proposed and not kept.reasons)
        or kept.dropped_weighty
        or proposal.malformed_weighty
    ):
        found.add(SystemReason.NO_MATCHING_RULE)
    if rules_conflict(kept.reasons, state):
        found.add(SystemReason.CONFLICTING_RULES)
    dropped = proposal.malformed + sum(kept.dropped.values())
    return _suggestion(kept.reasons, found, proposal.confidence, dropped), kept
