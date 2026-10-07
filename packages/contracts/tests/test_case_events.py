"""Story 1.13: the two case-level audit actions, the start's actor and the case list."""

from typing import Any

import pytest
from pydantic import ValidationError

from contracts.audit import (
    ACTIONS_BY_A_HUMAN,
    CASE_ACTIONS,
    DECISION_ACTIONS,
    AuditAction,
    AuditRecord,
)
from contracts.models.workflow import (
    CaseList,
    CaseSummary,
    StartCaseOptions,
    StartCaseRequest,
)
from contracts.operations import get_operation

CASE = "0199b7a0-0000-7000-8000-000000000001"
PAGE = "0199b7a0-0000-7000-8000-000000000003"
OTHER = "0199b7a0-0000-7000-8000-000000000009"


def started(**changes: Any) -> dict[str, Any]:
    return {
        "actor_kind": "human",
        "actor": "customer",
        "action": "case.started",
        "occurred_at": "2026-10-07T09:00:00Z",
        "case_id": CASE,
        "page_id": None,
        "ref": CASE,
        "detail": None,
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "eval_run_id": None,
        **changes,
    }


def completed(**changes: Any) -> dict[str, Any]:
    return started(
        **{
            "actor_kind": "ai",
            "actor": "workflow:case-lifecycle",
            "action": "case.completed",
            **changes,
        }
    )


def summary(**changes: Any) -> dict[str, Any]:
    return {
        "case_id": CASE,
        "case_status": "awaiting_human",
        "started_at": "2026-10-07T09:00:00Z",
        "page_count": 3,
        "waiting_page_count": 1,
        **changes,
    }


def test_story_1_13_the_catalogue_holds_the_two_case_actions() -> None:
    assert AuditAction.CASE_STARTED.value == "case.started"
    assert AuditAction.CASE_COMPLETED.value == "case.completed"
    assert CASE_ACTIONS == {AuditAction.CASE_STARTED, AuditAction.CASE_COMPLETED}
    # The start comes from a person; it is no decision about a page (AD-10).
    assert ACTIONS_BY_A_HUMAN == DECISION_ACTIONS | {AuditAction.CASE_STARTED}
    assert AuditAction.CASE_STARTED not in DECISION_ACTIONS


@pytest.mark.parametrize("role", ["customer", "underwriter"])
def test_story_1_13_a_case_is_started_by_a_demo_role(role: str) -> None:
    record = AuditRecord.model_validate(started(actor=role))

    assert (record.actor_kind.value, record.actor) == ("human", role)
    assert (record.page_id, record.ref, record.detail) == (None, CASE, None)


def test_story_1_13_a_case_is_completed_by_the_lifecycle() -> None:
    record = AuditRecord.model_validate(completed())

    assert (record.actor_kind.value, record.actor) == ("ai", "workflow:case-lifecycle")
    assert (record.page_id, record.ref, record.detail) == (None, CASE, None)


@pytest.mark.parametrize(
    "record",
    [
        # The start is a person's; the completion is not.
        started(actor_kind="ai", actor="workflow:case-lifecycle"),
        started(actor="workflow:case-lifecycle"),
        completed(actor_kind="human", actor="underwriter"),
        # Case-level: no page, and the case is the reference.
        started(page_id=PAGE),
        completed(page_id=PAGE),
        started(ref=OTHER),
        completed(ref=OTHER),
        # No detail and no error code.
        started(detail={"Person": 1}),
        completed(detail={"route": "extracting", "threshold": 0.9}),
        completed(error_code="stage_failed"),
    ],
)
def test_story_1_13_a_case_event_that_breaks_its_rules_is_rejected(
    record: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(record)


def test_story_1_13_a_start_names_its_actor_and_any_text_reaches_the_domain() -> None:
    assert StartCaseRequest.model_validate({"actor": "customer"}).actor == "customer"
    # Refused by `workflow`'s domain rule, not as a request that is not valid.
    assert StartCaseRequest.model_validate({"actor": "verdict"}).actor == "verdict"
    assert StartCaseRequest.model_validate({}).actor is None
    # Blank too: `actor_not_human` there, not a request that is not valid.
    assert StartCaseRequest.model_validate({"actor": " "}).actor == " "
    assert StartCaseRequest.model_validate({"actor": ""}).actor == ""
    with pytest.raises(ValidationError):
        StartCaseRequest.model_validate({"actor": 1})


def test_story_1_13_the_browsers_start_options_name_no_actor() -> None:
    options = StartCaseOptions.model_validate({"stop_after": "gate"})

    assert "actor" not in StartCaseOptions.model_fields
    assert set(StartCaseRequest.model_fields) == {
        *StartCaseOptions.model_fields,
        "actor",
    }
    # What `web` sends on: the options as given, and who asked.
    sent = StartCaseRequest(**options.model_dump(), actor="customer")
    assert (sent.stop_after, sent.actor) == (options.stop_after, "customer")
    with pytest.raises(ValidationError):
        StartCaseOptions.model_validate({"actor": "underwriter"})
    with pytest.raises(ValidationError):
        StartCaseOptions.model_validate({"retriever_configs": ["r3", "r3"]})


def test_story_1_13_the_case_list_operation_answers_with_the_list() -> None:
    operation = get_operation("list_cases")

    assert (operation.method.value, operation.path) == ("GET", "/cases")
    assert operation.owner.value == "workflow"
    assert [caller.value for caller in operation.callers] == ["web"]
    assert operation.request_model is None and operation.query_model is None
    assert operation.response_model is CaseList


def test_story_1_13_a_case_list_says_whether_more_cases_exist_than_it_lists() -> None:
    listed = CaseList.model_validate({"cases": [summary()], "has_more": True})

    assert listed.has_more is True
    assert listed.cases[0] == CaseSummary.model_validate(summary())
    assert CaseList.model_validate({"cases": [], "has_more": False}).cases == []
    with pytest.raises(ValidationError):
        CaseList.model_validate({"cases": [summary()]})


@pytest.mark.parametrize(
    "changes",
    [
        {"case_status": "started"},
        {"started_at": "2026-10-07T09:00:00"},
        {"page_count": -1},
        {"waiting_page_count": -1},
        # No more pages can wait than the case has.
        {"page_count": 1, "waiting_page_count": 2},
        {"case_id": "not-an-id"},
        {"eval_run_id": OTHER},
    ],
)
def test_story_1_13_a_case_summary_with_a_wrong_field_is_rejected(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        CaseSummary.model_validate(summary(**changes))
