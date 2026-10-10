"""Story 1.10: the one mapping of who may decide what about a page (AD-9, AD-10)."""

import pytest

from contracts.audit import DECISION_ACTIONS, AuditAction
from contracts.decisions import (
    DECISION_RULES,
    STATUSES_AWAITING_A_DECISION,
    decision_rule,
    decisions_open_to,
    human_role,
)
from contracts.enums import Decision, DemoRole, PageStatus, Service
from contracts.models.web import PageDecisionRequest
from contracts.models.workflow import DecisionRequest


def test_story_1_10_each_decision_has_its_role_its_page_status_and_its_action() -> None:
    expected = [
        ("keep", "customer", "awaiting_customer", "awaiting_triage", "page.kept"),
        ("discard", "customer", "awaiting_customer", "discarded", "page.discarded"),
        ("accept", "underwriter", "awaiting_triage", "extracting", "page.accepted"),
        ("deny", "underwriter", "awaiting_triage", "denied", "page.denied"),
    ]
    for decision, role, awaits, leaves, action in expected:
        rule = decision_rule(Decision(decision))
        assert (rule.role, rule.awaits, rule.leaves, rule.action) == (
            DemoRole(role),
            PageStatus(awaits),
            PageStatus(leaves),
            AuditAction(action),
        )
    assert set(DECISION_RULES) == set(Decision)
    # AD-10: the audit actions only a human may take are exactly the four decisions'.
    assert {rule.action for rule in DECISION_RULES.values()} == DECISION_ACTIONS
    assert STATUSES_AWAITING_A_DECISION == {
        PageStatus.AWAITING_CUSTOMER,
        PageStatus.AWAITING_TRIAGE,
    }


def test_story_1_10_each_role_has_its_two_decisions_and_the_mapping_is_read_only() -> (
    None
):
    assert decisions_open_to(DemoRole.CUSTOMER) == {Decision.KEEP, Decision.DISCARD}
    assert decisions_open_to(DemoRole.UNDERWRITER) == {Decision.ACCEPT, Decision.DENY}
    with pytest.raises(TypeError):
        DECISION_RULES[Decision.KEEP] = decision_rule(Decision.DENY)  # type: ignore[index]  # proving the mapping is read-only


def test_story_1_10_only_a_demo_role_is_a_human_actor() -> None:
    for role in DemoRole:
        assert human_role(role.value) is role
    for actor in (
        *(service.value for service in Service),
        "workflow:gate",
        "classification:chat-main",
        "Customer",
        "admin",
        " customer",
        "",
    ):
        assert human_role(actor) is None, actor


def test_story_1_10_the_browsers_decision_names_no_actor_and_the_domain_judges_it() -> (
    None
):
    assert set(PageDecisionRequest.model_fields) == {"decision"}
    # An actor that is no demo role passes validation, so that `workflow`'s
    # domain rule is what refuses it, as `actor_not_human`.
    request = DecisionRequest.model_validate(
        {"decision": "keep", "actor": "classification:chat-main"}
    )
    assert request.actor == "classification:chat-main"
    assert human_role(request.actor) is None
