"""Who may decide what about a page: the one mapping (AD-9, AD-10).

Keep, discard, accept and deny are a person's to say. For each decision this
names the demo role that may make it, the page status it needs, the status it
leaves the page in, and the audit action that reports it. `workflow` enforces
it in its domain code; nothing else holds a copy.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from contracts.audit import AuditAction
from contracts.enums import Decision, DemoRole, PageStatus


@dataclass(frozen=True, slots=True)
class DecisionRule:
    """What one decision takes and gives."""

    # The one role that may make the decision.
    role: DemoRole
    # The status a page must have for the decision to be made.
    awaits: PageStatus
    # The status the decision leaves the page in.
    leaves: PageStatus
    action: AuditAction


DECISION_RULES: Mapping[Decision, DecisionRule] = MappingProxyType(
    {
        # The customer answers for a page the gate was sure is not medical.
        Decision.KEEP: DecisionRule(
            role=DemoRole.CUSTOMER,
            awaits=PageStatus.AWAITING_CUSTOMER,
            leaves=PageStatus.AWAITING_TRIAGE,
            action=AuditAction.PAGE_KEPT,
        ),
        Decision.DISCARD: DecisionRule(
            role=DemoRole.CUSTOMER,
            awaits=PageStatus.AWAITING_CUSTOMER,
            leaves=PageStatus.DISCARDED,
            action=AuditAction.PAGE_DISCARDED,
        ),
        # The underwriter answers for a page in triage.
        Decision.ACCEPT: DecisionRule(
            role=DemoRole.UNDERWRITER,
            awaits=PageStatus.AWAITING_TRIAGE,
            leaves=PageStatus.EXTRACTING,
            action=AuditAction.PAGE_ACCEPTED,
        ),
        Decision.DENY: DecisionRule(
            role=DemoRole.UNDERWRITER,
            awaits=PageStatus.AWAITING_TRIAGE,
            leaves=PageStatus.DENIED,
            action=AuditAction.PAGE_DENIED,
        ),
    }
)

# The page statuses in which a page waits for a person.
STATUSES_AWAITING_A_DECISION: frozenset[PageStatus] = frozenset(
    rule.awaits for rule in DECISION_RULES.values()
)


def decision_rule(decision: Decision) -> DecisionRule:
    """The rule of one decision."""
    return DECISION_RULES[decision]


def decisions_open_to(role: DemoRole) -> frozenset[Decision]:
    """The decisions a demo role may make."""
    return frozenset(
        decision for decision, rule in DECISION_RULES.items() if rule.role is role
    )


def human_role(actor: str) -> DemoRole | None:
    """The demo role an actor is, or None if the actor is not a human role (AD-10)."""
    try:
        return DemoRole(actor)
    except ValueError:
        return None
