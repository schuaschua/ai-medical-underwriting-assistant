"""Story 1.1: enums, ids, `rule_id` patterns, page type mapping, normalisation, query builder."""

import re

import pytest
from pydantic import TypeAdapter, ValidationError

from contracts.enums import PageType
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


def test_story_1_1_ids_are_canonical_uuid7_carrying_their_timestamp() -> None:
    unix_ms = 1_791_288_000_000

    value = new_id(unix_ms)

    assert is_uuid7(value)
    assert UUID7.validate_python(value) == value
    assert int(value.replace("-", "")[:12], 16) == unix_ms
    assert new_id() != new_id()
    # Ids sort by the time they were made.
    assert new_id(1) < new_id(2)
    for unfit in (-1, 1 << 48):
        with pytest.raises(ValueError):
            new_id(unfit)
    for wrong in (
        "0199b7a0-0000-4000-8000-000000000001",  # version 4
        "0199B7A0-0000-7000-8000-000000000001",  # upper case
        "0199b7a0-0000-7000-c000-000000000001",  # wrong variant
        "",
    ):
        assert not is_uuid7(wrong)
        with pytest.raises(ValidationError):
            UUID7.validate_python(wrong)


def test_story_1_1_rule_ids_follow_the_pattern_and_only_defined_rules_are_found() -> (
    None
):
    for good in ("UW-DM-003", "UW-HTN-120"):
        assert RULE_ID.validate_python(good) == good
        assert is_rule_id(good)
    for bad in ("uw-dm-3", "UW-D-003", "UW-DIABE-003", "UW-DM-003\n", "XUW-DM-003"):
        assert not is_rule_id(bad)
        with pytest.raises(ValidationError):
            RULE_ID.validate_python(bad)

    text = (
        "Rule UW-DM-003: HbA1c from 8.0% to 8.9% carries a debit of 50%. "
        "See also UW-HTN-120 and Rule UW-DM-004 for related impairments.\n"
        "Rule UW-DM-005: HbA1c of 9.0% or more is declined. Rule UW-DM-003: repeated."
    )
    assert rule_ids_defined_in(text) == ["UW-DM-003", "UW-DM-005"]
    assert rule_ids_defined_in("Refer to UW-DM-003 where it applies.") == []
    assert re.search(RULE_DEFINITION_PATTERN, "Rule UW-DM-003: text") is not None


def test_story_1_1_page_type_mapping_says_which_types_are_medical() -> None:
    expected = {
        "lab_report": True,
        "attending_physician_statement": True,
        "application_form": True,
        "id_document": False,
        "invoice": False,
        "other": False,
    }
    for page_type, medical in expected.items():
        assert is_medical(page_type) is medical
        assert is_medical(PageType(page_type)) is medical
    with pytest.raises(ValueError):
        is_medical("utility_bill")


def test_story_1_1_normalise_ignores_case_whitespace_and_pdf_artefacts() -> None:
    assert normalise("  HbA1c\n 8.2 % ") == normalise("hba1c 8.2 %") == "hba1c 8.2 %"
    assert normalise("A\t\tB C\r\nD") == "a b c d"
    assert normalise("   ") == ""
    # A ligature, a soft hyphen, zero-width characters, case beyond ASCII.
    assert normalise("de\ufb01ned \ufb02uid") == "defined fluid"
    assert normalise("hyper\u00adtension") == normalise("Hypertension")
    assert normalise("\ufeffHbA1c\u200b 8.2\u200d%") == "hba1c 8.2%"
    assert normalise("STRASSE") == normalise("stra\u00dfe") == "strasse"
    # A mask token stays one token through all of it.
    normalised = normalise("Patient\u00a0[Per\u00adson]\u200b seen by [Person]")
    assert normalised == "patient [person] seen by [person]"
    assert normalised.split(" ").count("[person]") == 2


def test_story_1_1_query_builder_gives_the_same_query_for_the_same_statement() -> None:
    statement = "HbA1c 8.2%"

    assert build_fact_query(statement) == build_fact_query(statement) == "HbA1c 8.2%"
    assert build_fact_query("  HbA1c \n 8.2% ") == build_fact_query(statement)
    with pytest.raises(ValueError):
        build_fact_query(" \n ")
