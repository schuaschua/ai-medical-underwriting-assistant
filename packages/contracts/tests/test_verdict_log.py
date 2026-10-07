"""Stories 2.5 and 2.6: what the contracts say of a verdict run and of the agent's log."""

from typing import Any

import pytest
from pydantic import ValidationError

from contracts.audit import AuditRecord
from contracts.enums import StepOutcome
from contracts.errors import ErrorCode
from contracts.ids import new_id
from contracts.models.verdict import (
    AgentStep,
    VerdictRun,
)
from contracts.models.workflow import VerdictRunRequested

CASE = new_id()
RUN = new_id()
FACT = new_id()


def step(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "verdict_run_id": RUN,
        "case_id": CASE,
        "step_no": 2,
        "tool": "read_rule",
        "arguments": {"rule_id": "UW-DM-002"},
        "fact_id": None,
        "rule_ids": ["UW-DM-002"],
        "outcome": "done",
        "error_code": None,
        "latency_ms": 12,
        "occurred_at": "2026-10-07T12:00:00Z",
    }
    record.update(changes)
    return record


def run(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "verdict_run_id": RUN,
        "case_id": CASE,
        "retriever_config": "r3",
        "status": "done",
        "verdict": "refer",
        "loading_pct": None,
        "confidence": None,
        "reasons": [],
        "system_reasons": ["no_matching_rule"],
        "error_code": None,
    }
    record.update(changes)
    return record


def test_story_2_5_a_refused_step_says_so_with_a_code() -> None:
    refused = step(outcome="refused", error_code="rule_not_seen", rule_ids=[])

    assert AgentStep.model_validate(step()).outcome is StepOutcome.DONE
    assert AgentStep.model_validate(refused).outcome is StepOutcome.REFUSED
    assert AgentStep.model_validate(
        step(outcome="failed", error_code="upstream_unavailable", rule_ids=[])
    )
    for broken in (
        # A done call has no code; one that was refused or failed has one.
        step(error_code="rule_not_seen"),
        step(outcome="refused", rule_ids=[]),
        # A refused read returned no rule.
        step(outcome="refused", error_code="rule_not_seen"),
        step(outcome="skipped"),
    ):
        with pytest.raises(ValidationError):
            AgentStep.model_validate(broken)


def test_story_2_5_a_requested_run_names_its_run_once_it_has_ended() -> None:
    asked = {"case_id": CASE, "retriever_config": "r3"}

    running = VerdictRunRequested.model_validate(
        {**asked, "status": "running", "verdict_run_id": None}
    )
    done = VerdictRunRequested.model_validate(
        {**asked, "status": "done", "verdict_run_id": RUN}
    )

    assert running.verdict_run_id is None
    assert done.verdict_run_id == RUN
    # A run refused before it began failed without an id, and says why.
    refused = VerdictRunRequested.model_validate(
        {
            **asked,
            "status": "failed",
            "verdict_run_id": None,
            "error_code": "retriever_not_available",
        }
    )
    assert refused.error_code is ErrorCode.RETRIEVER_NOT_AVAILABLE
    for broken in (
        {**asked, "status": "running", "verdict_run_id": RUN},
        {**asked, "status": "done", "verdict_run_id": None},
        {**asked, "status": "done"},
        # A failed run names its code, and no other run has one.
        {**asked, "status": "failed", "verdict_run_id": None},
        {
            **asked,
            "status": "done",
            "verdict_run_id": RUN,
            "error_code": "stage_failed",
        },
    ):
        with pytest.raises(ValidationError):
            VerdictRunRequested.model_validate(broken)


def test_story_2_6_a_done_run_may_have_no_confidence_and_refers_for_a_reason() -> None:
    # No facts, or the step limit: the agent gave no answer, so no figure.
    assert VerdictRun.model_validate(run()).confidence is None
    for broken in (
        # `refer` always says why, and nothing else carries a system reason.
        run(system_reasons=[]),
        run(verdict="standard"),
        # A run that is not done has neither figure nor reasons.
        run(
            status="failed",
            verdict=None,
            error_code="model_unavailable",
            system_reasons=["step_limit"],
        ),
        run(
            status="failed",
            verdict=None,
            error_code="model_unavailable",
            confidence=0.5,
        ),
    ):
        with pytest.raises(ValidationError):
            VerdictRun.model_validate(broken)


def test_story_2_5_a_run_that_is_not_done_carries_no_reasons() -> None:
    reason = {
        "rule_id": "UW-DM-002",
        "fact_ids": [FACT],
        "effect": "debit",
        "debit_pct": 50,
    }
    for status, error_code in (("failed", "model_unavailable"), ("running", None)):
        with pytest.raises(ValidationError):
            VerdictRun.model_validate(
                run(
                    status=status,
                    verdict=None,
                    system_reasons=[],
                    error_code=error_code,
                    reasons=[reason],
                )
            )


def test_story_2_5_a_suggested_verdicts_audit_record_names_its_retriever_configuration() -> (
    None
):
    record = {
        "actor_kind": "ai",
        "actor": "verdict:chat-main",
        "action": "verdict.suggested",
        "occurred_at": "2026-10-07T12:00:00Z",
        "case_id": CASE,
        "page_id": None,
        "ref": RUN,
        "detail": {"retriever_config": "r3"},
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "eval_run_id": None,
    }

    parsed = AuditRecord.model_validate(record)

    assert parsed.model_dump(mode="json")["detail"] == {"retriever_config": "r3"}
    for detail in (None, {"retriever_config": "r9"}, {"Person": 1}):
        with pytest.raises(ValidationError):
            AuditRecord.model_validate({**record, "detail": detail})
    # No other action carries it.
    with pytest.raises(ValidationError):
        AuditRecord.model_validate({**record, "action": "facts.extracted"})
