"""Story 1.1: what the contract models reject, and the error shape."""

from typing import Any

import pytest
from pydantic import ValidationError

from contracts.audit import AuditAction, AuditRecord, RouteDetail
from contracts.errors import (
    DomainError,
    ErrorCode,
)
from contracts.models.classification import Classification, ClassificationResult
from contracts.models.extraction import Fact
from contracts.models.retrieval import (
    RuleText,
)
from contracts.models.verdict import (
    Reason,
    VerdictRun,
    VerdictRunResult,
)
from contracts.models.workflow import (
    CaseProgress,
)

CASE = "0199b7a0-0000-7000-8000-000000000001"
OTHER_CASE = "0199b7a0-0000-7000-8000-0000000000ff"
DOCUMENT = "0199b7a0-0000-7000-8000-000000000002"
PAGE = "0199b7a0-0000-7000-8000-000000000003"
FACT = "0199b7a0-0000-7000-8000-000000000004"
RUN = "0199b7a0-0000-7000-8000-000000000005"
REF = "0199b7a0-0000-7000-8000-000000000007"
TRACE = "0af7651916cd43dd8448eb211c80319c"


def audit(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "actor_kind": "ai",
        "actor": "classification:chat-main",
        "action": "page.classified",
        "occurred_at": "2026-10-06T12:00:00Z",
        "case_id": CASE,
        "page_id": PAGE,
        "ref": REF,
        "detail": None,
        "trace_id": TRACE,
        "eval_run_id": None,
    }
    record.update(changes)
    return record


def classification(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "classification_id": REF,
        "case_id": CASE,
        "page_id": PAGE,
        "contender": "llm",
        "page_type": "lab_report",
        "is_medical": True,
        "confidence": 0.9,
        "reason": "Lists laboratory values.",
    }
    record.update(changes)
    return record


def classification_result(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "case_id": CASE,
        "status": "done",
        "error_code": None,
        "audit": audit(),
        "classification_id": REF,
        "page_id": PAGE,
        "contender": "llm",
        "classification": classification(),
    }
    record.update(changes)
    return record


def verdict_run(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "verdict_run_id": RUN,
        "case_id": CASE,
        "retriever_config": "r3",
        "status": "done",
        "verdict": "standard",
        "loading_pct": None,
        "confidence": 0.9,
        "reasons": [],
        "system_reasons": [],
        "error_code": None,
    }
    record.update(changes)
    return record


def fact(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "fact_id": FACT,
        "case_id": CASE,
        "page_id": PAGE,
        "page_number": 1,
        "statement": "HbA1c 8.2%",
        "quote": "HbA1c 8.2 %",
        "quote_verified": True,
        "quote_start": 0,
        "quote_end": 11,
    }
    record.update(changes)
    return record


# --- numbers


def test_story_1_1_confidence_outside_zero_to_one_is_rejected() -> None:
    for confidence in (1.2, -0.1):
        with pytest.raises(ValidationError):
            Classification.model_validate(classification(confidence=confidence))
        with pytest.raises(ValidationError):
            VerdictRun.model_validate(verdict_run(confidence=confidence))
    for confidence in (0.0, 1.0):
        assert Classification.model_validate(classification(confidence=confidence))


# --- audit record


# The audit catalogue as the spine lists it (AD-8).
SPINE_AUDIT_ACTIONS = {
    "document.redacted",
    "page.classified",
    "page.kept",
    "page.discarded",
    "page.accepted",
    "page.denied",
    "facts.extracted",
    "verdict.suggested",
    "stage.failed",
}
# Actions the owner approved after the spine was written; the spine does not
# list them yet (deferred-work.md). Anything beyond these is drift.
APPROVED_AUDIT_ACTIONS = {
    # Story 1.9: the gate's own action.
    "page.routed",
    # Story 1.13: who started the case, and that it was completed.
    "case.started",
    "case.completed",
}


def test_story_1_1_audit_actions_are_the_spine_catalogue_and_the_approved_additions() -> (
    None
):
    assert len(SPINE_AUDIT_ACTIONS) == 9
    assert not SPINE_AUDIT_ACTIONS & APPROVED_AUDIT_ACTIONS
    assert {action.value for action in AuditAction} == (
        SPINE_AUDIT_ACTIONS | APPROVED_AUDIT_ACTIONS
    )


def test_story_1_1_audit_human_actor_that_is_no_demo_role_is_rejected() -> None:
    for actor in ("admin", "classification:chat-main", "Customer"):
        with pytest.raises(ValidationError):
            AuditRecord.model_validate(
                audit(actor_kind="human", actor=actor, action="page.kept")
            )
    # An AI actor names a service of this system and its deployment.
    for actor in ("customer", "billing:chat-main", "intake:", "intake: "):
        with pytest.raises(ValidationError):
            AuditRecord.model_validate(audit(actor=actor))


def test_story_1_1_audit_detail_is_redaction_counts_and_nothing_else() -> None:
    redacted = audit(action="document.redacted", page_id=None, detail={"Person": 2})

    assert AuditRecord.model_validate(redacted).detail == {"Person": 2}
    with pytest.raises(ValidationError):
        AuditRecord.model_validate({**redacted, "detail": None})
    with pytest.raises(ValidationError):
        AuditRecord.model_validate({**redacted, "detail": {"Person": "Jane Doe"}})
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(audit(detail={"Person": 2}))


def test_story_1_9_a_routed_page_carries_the_route_and_the_threshold() -> None:
    detail = {"route": "awaiting_customer", "threshold": 0.9}
    routed = audit(action="page.routed", actor="workflow:gate", detail=detail)

    record = AuditRecord.model_validate(routed)

    assert record.detail == RouteDetail.model_validate(detail)
    assert record.model_dump(mode="json")["detail"] == detail
    for route in ("extracting", "awaiting_customer", "awaiting_triage"):
        assert RouteDetail.model_validate({"route": route, "threshold": 0.9})
    for wrong in (
        # A route is never without its detail, and never carries counts.
        {**routed, "detail": None},
        {**routed, "detail": {"Person": 2}},
        {**routed, "detail": {"route": "somewhere", "threshold": 0.9}},
        # A page status, but not one the gate gives.
        *(
            {**routed, "detail": {"route": status, "threshold": 0.9}}
            for status in (
                "uploaded",
                "classified",
                "extracted",
                "discarded",
                "denied",
                "failed",
            )
        ),
        {**routed, "detail": {"route": "extracting", "threshold": 1.5}},
        {**routed, "detail": {"route": "extracting"}},
        # A page action names its page; the gate is not a human.
        {**routed, "page_id": None},
        {**routed, "actor_kind": "human", "actor": "underwriter"},
        # No other action carries a route.
        audit(detail=detail),
        audit(action="document.redacted", page_id=None, detail=detail),
    ):
        with pytest.raises(ValidationError):
            AuditRecord.model_validate(wrong)


def test_story_1_9_progress_carries_a_failure_reason_from_the_catalogue() -> None:
    page = {"page_id": PAGE, "page_number": 1, "page_status": "failed"}
    case = {
        "case_id": CASE,
        "case_status": "failed",
        "redaction_status": "done",
        "pages": [{**page, "error_code": "invalid_model_output"}],
        "error_code": "invalid_model_output",
    }

    progress = CaseProgress.model_validate(case)

    assert progress.error_code is ErrorCode.INVALID_MODEL_OUTPUT
    assert progress.pages[0].error_code is ErrorCode.INVALID_MODEL_OUTPUT
    # Nothing failed: both are null, also when the field is left out.
    untouched = CaseProgress.model_validate(
        {**case, "case_status": "running", "pages": [page], "error_code": None}
    )
    assert untouched.error_code is None
    assert untouched.pages[0].error_code is None
    assert untouched.model_dump(mode="json")["pages"][0]["error_code"] is None
    with pytest.raises(ValidationError):
        CaseProgress.model_validate({**case, "error_code": "it broke"})


# --- errors


def test_story_1_1_error_body_has_the_one_shape() -> None:
    error = DomainError(ErrorCode.IN_PROGRESS, "The stage is still running.")

    body = error.to_body(TRACE)

    assert body.model_dump(mode="json") == {
        "error": {
            "code": "in_progress",
            "message": "The stage is still running.",
            "trace_id": TRACE,
        }
    }
    assert error.http_status == 409
    assert str(error) == "The stage is still running."


# --- shape rules the spine states


def test_story_1_1_is_medical_must_follow_the_page_type() -> None:
    with pytest.raises(ValidationError):
        Classification.model_validate(
            classification(page_type="invoice", is_medical=True)
        )
    with pytest.raises(ValidationError):
        Classification.model_validate(
            classification(page_type="lab_report", is_medical=False)
        )


def test_story_1_1_failed_stage_result_carries_a_code_and_a_stage_failed_record() -> (
    None
):
    failed = classification_result(
        status="failed",
        error_code="stage_timeout",
        audit=audit(action="stage.failed"),
        classification=None,
    )

    assert (
        ClassificationResult.model_validate(failed).error_code
        is ErrorCode.STAGE_TIMEOUT
    )
    for broken in (
        {**failed, "error_code": None},
        {**failed, "audit": audit()},
        {**failed, "classification": classification()},
        classification_result(error_code="stage_failed"),
        classification_result(classification=None),
        classification_result(audit=audit(case_id=OTHER_CASE)),
    ):
        with pytest.raises(ValidationError):
            ClassificationResult.model_validate(broken)


def test_story_1_1_fact_offsets_are_set_only_for_a_verified_quote() -> None:
    unverified = fact(quote_verified=False, quote_start=None, quote_end=None)

    assert Fact.model_validate(unverified).quote_verified is False
    for broken in (
        fact(quote_start=None, quote_end=None),
        fact(quote_end=None),
        fact(quote_verified=False),
        fact(quote_start=11, quote_end=11),
        fact(page_number=0),
    ):
        with pytest.raises(ValidationError):
            Fact.model_validate(broken)


def test_story_1_1_verdict_run_shape_follows_its_status() -> None:
    loaded = verdict_run(verdict="loaded", loading_pct=50)
    failed = verdict_run(
        status="failed", verdict=None, confidence=None, error_code="model_unavailable"
    )

    assert VerdictRun.model_validate(loaded).loading_pct == 50
    assert VerdictRun.model_validate(failed).verdict is None
    for broken in (
        verdict_run(loading_pct=50),
        verdict_run(verdict="loaded", loading_pct=None),
        verdict_run(verdict=None),
        verdict_run(error_code="stage_failed"),
        {**failed, "error_code": None},
        {**failed, "verdict": "refer", "confidence": 0.5},
    ):
        with pytest.raises(ValidationError):
            VerdictRun.model_validate(broken)


# --- review fixes: audit record


def test_story_1_1_audit_actions_of_a_human_and_of_a_stage_are_kept_apart() -> None:
    for action in ("page.kept", "page.discarded", "page.accepted", "page.denied"):
        assert AuditRecord.model_validate(
            audit(actor_kind="human", actor="underwriter", action=action)
        )
        with pytest.raises(ValidationError):
            AuditRecord.model_validate(audit(action=action))
    # And the other way round: what a stage records is never a person's.
    cases: list[tuple[str, dict[str, Any]]] = [
        ("page.classified", {}),
        ("facts.extracted", {}),
        ("verdict.suggested", {"page_id": None, "detail": {"retriever_config": "r3"}}),
        ("stage.failed", {}),
        ("document.redacted", {"page_id": None, "detail": {"Person": 1}}),
    ]
    for action, changes in cases:
        assert AuditRecord.model_validate(audit(action=action, **changes))
        with pytest.raises(ValidationError):
            AuditRecord.model_validate(
                audit(actor_kind="human", actor="customer", action=action, **changes)
            )


# --- review fixes: reasons and verdict runs


def reason(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "rule_id": "UW-DM-003",
        "fact_ids": [FACT],
        "effect": "debit",
        "debit_pct": 50,
    }
    record.update(changes)
    return record


def test_story_1_1_debit_pct_is_set_only_for_a_debit() -> None:
    assert Reason.model_validate(reason()).debit_pct == 50
    assert Reason.model_validate(reason(effect="none", debit_pct=None))
    assert Reason.model_validate(reason(effect="decline", debit_pct=None))
    for broken in (
        reason(debit_pct=None),
        reason(effect="none"),
        reason(effect="decline"),
        reason(effect="none", debit_pct=0),
    ):
        with pytest.raises(ValidationError):
            Reason.model_validate(broken)


def verdict_run_result(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "case_id": CASE,
        "status": "done",
        "error_code": None,
        "audit": audit(
            action="verdict.suggested",
            actor="verdict:chat-main",
            page_id=None,
            ref=RUN,
            detail={"retriever_config": "r3"},
        ),
        "verdict_run_id": RUN,
        "retriever_config": "r3",
        "verdict": "standard",
    }
    record.update(changes)
    return record


def test_story_1_1_verdict_run_result_has_a_verdict_only_when_done() -> None:
    failed = verdict_run_result(
        status="failed",
        error_code="model_unavailable",
        audit=audit(action="stage.failed", page_id=None),
        verdict=None,
    )

    assert VerdictRunResult.model_validate(verdict_run_result()).verdict is not None
    assert VerdictRunResult.model_validate(failed).verdict is None
    with pytest.raises(ValidationError):
        VerdictRunResult.model_validate(verdict_run_result(verdict=None))
    with pytest.raises(ValidationError):
        VerdictRunResult.model_validate({**failed, "verdict": "refer"})


# --- story 1.12: the error code of a failed stage, and the bounded trail


def test_story_1_12_only_a_stage_failed_record_has_an_error_code() -> None:
    failed = AuditRecord.model_validate(
        audit(action="stage.failed", error_code="model_unavailable")
    )
    assert failed.error_code is ErrorCode.MODEL_UNAVAILABLE
    others: list[dict[str, Any]] = [
        {"action": "page.classified"},
        {"action": "page.kept", "actor_kind": "human", "actor": "customer"},
        {"action": "document.redacted", "page_id": None, "detail": {"Person": 1}},
    ]
    for changes in others:
        assert AuditRecord.model_validate(audit(**changes)).error_code is None
        with pytest.raises(ValidationError, match="stage.failed only"):
            AuditRecord.model_validate(audit(**changes, error_code="stage_failed"))
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(
            audit(action="stage.failed", error_code="something_else")
        )


def test_story_1_12_a_failed_result_and_its_audit_record_name_the_same_code() -> None:
    failed = classification_result(
        status="failed",
        error_code="stage_timeout",
        audit=audit(action="stage.failed", error_code="stage_timeout"),
        classification=None,
    )

    assert (
        ClassificationResult.model_validate(failed).audit.error_code
        is ErrorCode.STAGE_TIMEOUT
    )
    with pytest.raises(ValidationError, match="must be the result's"):
        ClassificationResult.model_validate(
            {
                **failed,
                "audit": audit(action="stage.failed", error_code="model_unavailable"),
            }
        )


def test_story_2_3_a_rule_read_names_the_rules_its_text_refers_to() -> None:
    rule = {
        "rule_id": "UW-DM-003",
        "chunk_id": "smart-UW-DM-003",
        "chunk_set": "smart",
        "text": "Rule UW-DM-003: text. See rule UW-HT-002.",
        "manual_page": 12,
        "impairment": "Type 2 diabetes mellitus",
        "reference_rule_ids": ["UW-HT-002"],
    }

    assert RuleText.model_validate(rule).reference_rule_ids == ["UW-HT-002"]
    # A rule that refers to none says so with an empty list, never by leaving it out.
    assert RuleText.model_validate({**rule, "reference_rule_ids": []})
    without = {key: value for key, value in rule.items() if key != "reference_rule_ids"}
    for refused in (without, {**rule, "reference_rule_ids": ["not-a-rule"]}):
        with pytest.raises(ValidationError):
            RuleText.model_validate(refused)
