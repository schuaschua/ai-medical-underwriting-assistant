"""Story 1.1: enums, ids, `rule_id` patterns, page type mapping, normalisation, query builder."""

import re

import pytest
from pydantic import TypeAdapter, ValidationError

from contracts import enums
from contracts.enums import DemoRole, PageType
from contracts.ids import Uuid7Str, is_uuid7, new_id
from contracts.query import build_fact_query
from contracts.rules import (
    RULE_DEFINITION_PATTERN,
    RuleId,
    is_medical,
    is_rule_id,
    rule_ids_defined_in,
)
from contracts.text import normalise

RULE_ID = TypeAdapter(RuleId)
UUID7 = TypeAdapter(Uuid7Str)


def test_story_1_1_enums_hold_exactly_the_spine_values() -> None:
    expected = {
        enums.Verdict: {"standard", "loaded", "decline", "refer"},
        enums.PageStatus: {
            "uploaded",
            "classified",
            "awaiting_customer",
            "awaiting_triage",
            "extracting",
            "extracted",
            "discarded",
            "denied",
            "failed",
        },
        enums.CaseStatus: {"running", "awaiting_human", "completed", "failed"},
        enums.StageStatus: {"running", "done", "failed"},
        enums.Decision: {"keep", "discard", "accept", "deny"},
        enums.DemoRole: {"customer", "underwriter"},
        enums.PageType: {
            "lab_report",
            "attending_physician_statement",
            "application_form",
            "id_document",
            "invoice",
            "other",
        },
        enums.ActorKind: {"human", "ai"},
        enums.ClassifierContender: {"llm", "doc-intelligence"},
        enums.RetrieverConfig: {"r1", "r2", "r3", "r4", "r5", "r6"},
        enums.ChunkSet: {"fixed", "smart"},
        enums.ToolName: {"list_facts", "search_rules", "read_rule"},
        enums.ReasonEffect: {"none", "debit", "decline"},
        enums.SystemReason: {
            "no_matching_rule",
            "conflicting_rules",
            "unverified_quote",
            "low_confidence",
            "step_limit",
        },
        enums.StopAfter: {"gate"},
        enums.Service: {
            "web",
            "intake",
            "classification",
            "extraction",
            "retrieval",
            "verdict",
            "workflow",
        },
    }
    for enum, values in expected.items():
        assert {member.value for member in enum} == values, enum.__name__


def test_story_1_1_demo_role_admin_is_not_a_member() -> None:
    assert DemoRole("customer") is DemoRole.CUSTOMER
    with pytest.raises(ValueError):
        DemoRole("admin")


def test_story_1_1_new_id_is_a_uuid7_carrying_its_timestamp() -> None:
    unix_ms = 1_791_288_000_000

    value = new_id(unix_ms)

    assert is_uuid7(value)
    assert UUID7.validate_python(value) == value
    assert int(value.replace("-", "")[:12], 16) == unix_ms
    assert new_id() != new_id()
    # Ids sort by the time they were made.
    assert new_id(1) < new_id(2)


def test_story_1_1_new_id_rejects_a_timestamp_that_does_not_fit() -> None:
    with pytest.raises(ValueError):
        new_id(-1)
    with pytest.raises(ValueError):
        new_id(1 << 48)


@pytest.mark.parametrize(
    "value",
    [
        "0199b7a0-0000-4000-8000-000000000001",  # version 4
        "0199B7A0-0000-7000-8000-000000000001",  # upper case
        "0199b7a000007000800000000000001",
        "0199b7a0-0000-7000-c000-000000000001",  # wrong variant
        "",
    ],
)
def test_story_1_1_id_type_rejects_anything_but_a_canonical_uuid7(value: str) -> None:
    assert not is_uuid7(value)
    with pytest.raises(ValidationError):
        UUID7.validate_python(value)


@pytest.mark.parametrize("value", ["UW-DM-003", "UW-HTN-120"])
def test_story_1_1_good_rule_id_is_accepted(value: str) -> None:
    assert RULE_ID.validate_python(value) == value
    assert is_rule_id(value)


@pytest.mark.parametrize(
    "value",
    ["uw-dm-3", "UW-D-003", "UW-DIABE-003", "UW-DM-003 ", "UW-DM-003\n", "XUW-DM-003"],
)
def test_story_1_1_bad_rule_id_is_rejected(value: str) -> None:
    assert not is_rule_id(value)
    with pytest.raises(ValidationError):
        RULE_ID.validate_python(value)


def test_story_1_1_definition_marker_finds_defined_rules_only() -> None:
    text = (
        "Rule UW-DM-003: HbA1c from 8.0% to 8.9% carries a debit of 50%. "
        "See also UW-HTN-120 and Rule UW-DM-004 for related impairments.\n"
        "Rule UW-DM-005: HbA1c of 9.0% or more is declined. Rule UW-DM-003: repeated."
    )

    assert rule_ids_defined_in(text) == ["UW-DM-003", "UW-DM-005"]
    assert rule_ids_defined_in("Refer to UW-DM-003 where it applies.") == []
    assert (
        rule_ids_defined_in("Rule uw-dm-3: malformed. Rule UW-DIABE-003: too long.")
        == []
    )
    assert re.search(RULE_DEFINITION_PATTERN, "Rule UW-DM-003: text") is not None


@pytest.mark.parametrize(
    ("page_type", "expected"),
    [
        ("lab_report", True),
        ("attending_physician_statement", True),
        ("application_form", True),
        ("id_document", False),
        ("invoice", False),
        ("other", False),
    ],
)
def test_story_1_1_page_type_mapping(page_type: str, expected: bool) -> None:
    assert is_medical(page_type) is expected
    assert is_medical(PageType(page_type)) is expected


def test_story_1_1_page_type_mapping_rejects_an_unknown_type() -> None:
    with pytest.raises(ValueError):
        is_medical("utility_bill")


def test_story_1_1_normalise_ignores_case_and_whitespace() -> None:
    assert normalise("  HbA1c\n 8.2 % ") == normalise("hba1c 8.2 %") == "hba1c 8.2 %"
    assert normalise("A\t\tB C\r\nD") == "a b c d"
    assert normalise("   ") == ""


def test_story_1_1_normalise_keeps_a_mask_token_as_one_token() -> None:
    normalised = normalise("Patient [Person] seen")

    assert normalised == "patient [person] seen"
    assert "[person]" in normalised.split(" ")


def test_story_1_1_normalise_folds_a_pdf_ligature() -> None:
    assert (
        normalise("de\ufb01ned \ufb02uid")
        == normalise("defined fluid")
        == "defined fluid"
    )


def test_story_1_1_normalise_drops_a_soft_hyphen() -> None:
    assert (
        normalise("hyper\u00adtension") == normalise("Hypertension") == "hypertension"
    )


@pytest.mark.parametrize("invisible", ["\u200b", "\u200c", "\u200d", "\ufeff"])
def test_story_1_1_normalise_drops_zero_width_characters(invisible: str) -> None:
    assert normalise(f"HbA1c{invisible} 8.2{invisible}%") == "hba1c 8.2%"
    assert normalise(f"{invisible}HbA1c") == "hba1c"


def test_story_1_1_normalise_folds_case_beyond_ascii() -> None:
    assert normalise("STRASSE") == normalise("stra\u00dfe") == "strasse"


def test_story_1_1_normalise_keeps_a_mask_token_through_pdf_artefacts() -> None:
    normalised = normalise("Patient\u00a0[Per\u00adson]\u200b seen by [Person]")

    assert normalised == "patient [person] seen by [person]"
    assert normalised.split(" ").count("[person]") == 2


def test_story_1_1_query_builder_gives_the_same_query_for_the_same_statement() -> None:
    statement = "HbA1c 8.2%"

    assert build_fact_query(statement) == build_fact_query(statement) == "HbA1c 8.2%"
    assert build_fact_query("  HbA1c \n 8.2% ") == build_fact_query(statement)


def test_story_1_1_query_builder_rejects_a_blank_statement() -> None:
    with pytest.raises(ValueError):
        build_fact_query(" \n ")
