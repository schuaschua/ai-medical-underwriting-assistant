"""What one run has seen: the facts it listed and the rules it found or read (AD-15).

The state is the run's memory of its own tool calls. It decides which rule a
`read_rule` may name, and afterwards which proposed reasons can be stored:
nothing the run did not see is ever cited.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from contracts.models.extraction import Fact
from contracts.models.retrieval import RuleText, SearchItem


@dataclass(slots=True)
class RunState:
    """One run's memory. Only the tools write it."""

    # The facts `list_facts` answered in this run, by id.
    facts_listed: dict[str, Fact] = field(default_factory=dict)
    # Each rule's text as the run saw it: the chunk a search returned, or
    # the text a read answered, which replaces it. On row `r6` the first
    # chunk a search returned for the rule, which stands as read.
    rule_texts: dict[str, str] = field(default_factory=dict)
    impairments: dict[str, str] = field(default_factory=dict)
    returned_by_search: set[str] = field(default_factory=set)
    read: set[str] = field(default_factory=set)
    # The rules a rule read in this run refers to.
    referred_to: set[str] = field(default_factory=set)
    # The facts a search of this run was made about.
    facts_searched: set[str] = field(default_factory=set)
    # How many tool calls the run has made, refused and failed ones included.
    steps: int = 0
    # Set when a tool call was not made because the run was at its step
    # limit, or when the run's time budget stopped the agent.
    step_limit_reached: bool = False

    def listed(self, facts: Iterable[Fact]) -> None:
        """Note the facts a `list_facts` call answered."""
        for fact in facts:
            self.facts_listed[fact.fact_id] = fact

    def found(self, items: Iterable[SearchItem], *, as_read: bool = False) -> list[str]:
        """Note the rules a search returned; answer their ids, in rank order, once each.

        With `as_read` (row `r6`, where the run makes no rule read) a rule
        counts as read by the search that returned its chunk, and that
        chunk's text is what an effect is checked against.
        """
        rule_ids: list[str] = []
        for item in items:
            for rule_id in item.rule_ids:
                self.returned_by_search.add(rule_id)
                # A text the run read stands; a search does not replace it.
                if rule_id not in self.read:
                    self.rule_texts[rule_id] = item.text
                if as_read:
                    self.read.add(rule_id)
                self.impairments[rule_id] = item.impairment
                if rule_id not in rule_ids:
                    rule_ids.append(rule_id)
        return rule_ids

    def was_read(self, rule: RuleText) -> None:
        """Note a rule the run read, and the rules its text refers to."""
        self.read.add(rule.rule_id)
        self.rule_texts[rule.rule_id] = rule.text
        self.impairments[rule.rule_id] = rule.impairment
        self.referred_to.update(rule.reference_rule_ids)

    def may_read(self, rule_id: str) -> bool:
        """AD-15: a rule may be read only if a search of this run returned it, or a rule read in this run refers to it."""
        return rule_id in self.returned_by_search or rule_id in self.referred_to

    def saw_rule(self, rule_id: str) -> bool:
        """Whether a search of this run returned the rule, or the run read it."""
        return rule_id in self.rule_texts

    def looked_things_up(self) -> bool:
        """Whether the run listed the case's facts and searched the manual for at least one of them."""
        return bool(self.facts_listed) and bool(
            self.facts_searched & self.facts_listed.keys()
        )
