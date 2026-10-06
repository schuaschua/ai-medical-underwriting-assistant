"""Story 1.1: what the contract models reject, and the error shape."""

import copy
import pickle
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from contracts.audit import AuditAction, AuditRecord, ai_actor
from contracts.base import ContractModel, NonEmptyStr, OneLine, TraceId
from contracts.enums import Service
from contracts.errors import (
    HTTP_STATUS,
    NO_TRACE_ID,
    DomainError,
    ErrorBody,
    ErrorCode,
)
from contracts.models.classification import Classification, ClassificationResult
from contracts.models.extraction import Fact, FactSetResult
from contracts.models.intake import (
    PageBoxes,
    PageBoxesQuery,
    PageText,
    RedactionResult,
    WordBox,
)
from contracts.models.retrieval import MAX_TOP_K, SearchItem, SearchRequest
from contracts.models.verdict import (
    SUGGESTION_LABEL,
    Reason,
    VerdictRun,
    VerdictRunResult,
)
from contracts.models.workflow import CaseStarted, DecisionRequest, StartCaseRequest

CASE = "0199b7a0-0000-7000-8000-000000000001"
OTHER_CASE = "0199b7a0-0000-7000-8000-0000000000ff"
DOCUMENT = "0199b7a0-0000-7000-8000-000000000002"
OTHER_ID = "0199b7a0-0000-7000-8000-0000000000ee"
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


# --- enums


def test_story_1_1_unknown_verdict_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VerdictRun.model_validate(verdict_run(verdict="approve"))


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (DecisionRequest, {"decision": "approve", "actor": "customer"}),
        (DecisionRequest, {"decision": "keep", "actor": "admin"}),
        (StartCaseRequest, {"classifier_contender": "regex"}),
        (StartCaseRequest, {"retriever_configs": ["r7"]}),
        (StartCaseRequest, {"stop_after": "extraction"}),
        (SearchRequest, {"query": "HbA1c", "retriever_config": "R3"}),
    ],
)
def test_story_1_1_unknown_enum_value_is_rejected(
    model: type[Any], payload: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


# --- numbers


@pytest.mark.parametrize("confidence", [1.2, -0.1])
def test_story_1_1_confidence_outside_zero_to_one_is_rejected(
    confidence: float,
) -> None:
    with pytest.raises(ValidationError):
        Classification.model_validate(classification(confidence=confidence))
    with pytest.raises(ValidationError):
        VerdictRun.model_validate(verdict_run(confidence=confidence))


@pytest.mark.parametrize("confidence", [0.0, 0.9, 1.0])
def test_story_1_1_confidence_within_range_is_accepted(confidence: float) -> None:
    assert Classification.model_validate(classification(confidence=confidence))


def test_story_1_1_score_outside_zero_to_one_is_rejected() -> None:
    item = {
        "chunk_id": "smart-0042",
        "rule_ids": ["UW-DM-003"],
        "rank": 1,
        "score": 1.5,
        "text": "Rule UW-DM-003: text",
        "manual_page": 12,
        "impairment": "Diabetes mellitus",
    }
    with pytest.raises(ValidationError):
        SearchItem.model_validate(item)


@pytest.mark.parametrize("debit_pct", [12.5, -5, "50"])
def test_story_1_1_debit_is_a_whole_non_negative_percentage(debit_pct: object) -> None:
    with pytest.raises(ValidationError):
        Reason.model_validate(
            {
                "rule_id": "UW-DM-003",
                "fact_ids": [FACT],
                "effect": "debit",
                "debit_pct": debit_pct,
            }
        )


# --- rule ids and ids inside models


@pytest.mark.parametrize("rule_id", ["uw-dm-3", "UW-D-003", "UW-DIABE-003"])
def test_story_1_1_malformed_rule_id_in_a_payload_is_rejected(rule_id: str) -> None:
    with pytest.raises(ValidationError):
        Reason.model_validate(
            {
                "rule_id": rule_id,
                "fact_ids": [FACT],
                "effect": "none",
                "debit_pct": None,
            }
        )


def test_story_1_1_id_that_is_not_a_uuid7_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Fact.model_validate(fact(fact_id="not-an-id"))


# --- timestamps


@pytest.mark.parametrize(
    "occurred_at", ["2026-10-06T12:00:00", "2026-10-06T12:00:00+08:00", "yesterday"]
)
def test_story_1_1_timestamp_that_is_not_utc_is_rejected(occurred_at: str) -> None:
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(audit(occurred_at=occurred_at))


# --- audit record


def test_story_1_1_audit_actions_are_the_spine_catalogue() -> None:
    assert {action.value for action in AuditAction} == {
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


@pytest.mark.parametrize("actor", ["customer", "underwriter"])
def test_story_1_1_audit_human_actor_is_a_demo_role(actor: str) -> None:
    record = AuditRecord.model_validate(
        audit(actor_kind="human", actor=actor, action="page.kept")
    )

    assert record.actor == actor


@pytest.mark.parametrize("actor", ["admin", "classification:chat-main", "Customer"])
def test_story_1_1_audit_human_actor_that_is_no_demo_role_is_rejected(
    actor: str,
) -> None:
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(
            audit(actor_kind="human", actor=actor, action="page.kept")
        )


@pytest.mark.parametrize(
    "actor", ["customer", "billing:chat-main", "intake:", "intake"]
)
def test_story_1_1_audit_ai_actor_must_name_service_and_deployment(actor: str) -> None:
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(audit(actor=actor))


def test_story_1_1_ai_actor_builder_matches_what_the_record_accepts() -> None:
    actor = ai_actor(Service.INTAKE, "azure-ai-language")

    assert actor == "intake:azure-ai-language"
    assert AuditRecord.model_validate(audit(actor=actor)).actor == actor
    with pytest.raises(ValueError):
        ai_actor(Service.INTAKE, " ")


def test_story_1_1_audit_detail_is_redaction_counts_and_nothing_else() -> None:
    redacted = audit(action="document.redacted", page_id=None, detail={"Person": 2})

    assert AuditRecord.model_validate(redacted).detail == {"Person": 2}
    with pytest.raises(ValidationError):
        AuditRecord.model_validate({**redacted, "detail": None})
    with pytest.raises(ValidationError):
        AuditRecord.model_validate({**redacted, "detail": {"Person": "Jane Doe"}})
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(audit(detail={"Person": 2}))


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


def test_story_1_1_error_catalogue_is_exactly_these_codes_and_statuses() -> None:
    assert {code.value: status for code, status in HTTP_STATUS.items()} == {
        "validation_failed": 422,
        "invalid_role": 400,
        "role_not_allowed": 403,
        "actor_not_human": 403,
        "not_found": 404,
        "method_not_allowed": 405,
        "file_too_large": 413,
        "unsupported_file_type": 415,
        "payload_too_large": 413,
        "unsupported_media_type": 415,
        "too_many_requests": 429,
        "in_progress": 409,
        "not_redacted": 409,
        "not_awaiting_decision": 409,
        "pages_not_terminal": 409,
        "rule_not_seen": 409,
        "stage_timeout": 504,
        "stage_failed": 500,
        "redaction_failed": 502,
        "invalid_model_output": 502,
        "model_unavailable": 503,
        "upstream_unavailable": 502,
        "internal_error": 500,
    }
    assert set(HTTP_STATUS) == set(ErrorCode)


@pytest.mark.parametrize("message", ["", "   ", "\n"])
def test_story_1_1_domain_error_needs_a_message(message: str) -> None:
    with pytest.raises(ValueError):
        DomainError(ErrorCode.INTERNAL_ERROR, message)


@pytest.mark.parametrize(
    "trace_id", [None, "", "not-a-trace", TRACE.upper(), TRACE + "0", TRACE + "\n"]
)
def test_story_1_1_error_body_falls_back_to_the_zero_trace_id(
    trace_id: str | None,
) -> None:
    body = DomainError(ErrorCode.NOT_FOUND, "No such case.").to_body(trace_id)

    assert body.error.trace_id == NO_TRACE_ID == "0" * 32
    assert body.error.code is ErrorCode.NOT_FOUND


def test_story_1_1_domain_error_survives_pickle_and_copy() -> None:
    error = DomainError(ErrorCode.NOT_REDACTED, "Not redacted yet.")

    for clone in (pickle.loads(pickle.dumps(error)), copy.deepcopy(error)):  # noqa: S301  # round trip of our own object, no untrusted input
        assert isinstance(clone, DomainError)
        assert clone.code is ErrorCode.NOT_REDACTED
        assert clone.message == str(clone) == "Not redacted yet."
        assert clone.to_body(TRACE) == error.to_body(TRACE)


def test_story_1_1_unknown_error_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ErrorBody.model_validate(
            {"error": {"code": "oops", "message": "x", "trace_id": TRACE}}
        )


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


def test_story_1_1_classification_reason_is_one_line() -> None:
    with pytest.raises(ValidationError):
        Classification.model_validate(classification(reason="first\nsecond"))


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


def test_story_1_1_verdict_run_carries_the_suggestion_label() -> None:
    run = VerdictRun.model_validate(verdict_run())

    assert run.label == SUGGESTION_LABEL == "AI suggestion, not a decision"
    assert run.model_dump(mode="json")["label"] == "AI suggestion, not a decision"
    with pytest.raises(ValidationError):
        VerdictRun.model_validate(verdict_run(label="Final decision"))


def test_story_1_1_verdict_run_shape_follows_its_status() -> None:
    loaded = verdict_run(verdict="loaded", loading_pct=50)
    failed = verdict_run(
        status="failed", verdict=None, confidence=None, error_code="model_unavailable"
    )

    assert VerdictRun.model_validate(loaded).loading_pct == 50
    assert VerdictRun.model_validate(failed).verdict is None
    for broken in (
        verdict_run(loading_pct=50),
        verdict_run(verdict=None),
        verdict_run(confidence=None),
        verdict_run(error_code="stage_failed"),
        {**failed, "error_code": None},
        {**failed, "verdict": "refer", "confidence": 0.5},
    ):
        with pytest.raises(ValidationError):
            VerdictRun.model_validate(broken)


def test_story_1_1_start_request_fields_are_all_optional() -> None:
    request = StartCaseRequest.model_validate({})

    assert request.model_dump() == {
        "classifier_contender": None,
        "retriever_configs": None,
        "stop_after": None,
        "eval_run_id": None,
    }
    with pytest.raises(ValidationError):
        StartCaseRequest.model_validate({"retriever_configs": []})
    with pytest.raises(ValidationError):
        StartCaseRequest.model_validate({"retriever_configs": ["r3", "r3"]})


def test_story_1_1_search_request_defaults_to_top_five() -> None:
    request = SearchRequest.model_validate(
        {"query": "HbA1c 8.2%", "retriever_config": "r3"}
    )

    assert request.top_k == 5
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"query": "", "retriever_config": "r3"})
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(
            {"query": "x", "retriever_config": "r3", "top_k": 0}
        )


def test_story_1_1_search_request_caps_top_k() -> None:
    at_cap = {"query": "x", "retriever_config": "r3", "top_k": MAX_TOP_K}

    assert MAX_TOP_K == 50
    assert SearchRequest.model_validate(at_cap).top_k == 50
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({**at_cap, "top_k": MAX_TOP_K + 1})


def test_story_1_1_box_query_takes_a_whole_range_or_none() -> None:
    assert PageBoxesQuery.model_validate({}).quote_start is None
    assert (
        PageBoxesQuery.model_validate({"quote_start": 3, "quote_end": 9}).quote_end == 9
    )
    with pytest.raises(ValidationError):
        PageBoxesQuery.model_validate({"quote_start": 3})
    with pytest.raises(ValidationError):
        PageBoxesQuery.model_validate({"quote_start": 9, "quote_end": 3})
    with pytest.raises(ValidationError):
        PageBoxesQuery.model_validate({"quote_start": 3, "quote_end": 3})


# --- review fixes: audit record


@pytest.mark.parametrize(
    "action", ["page.kept", "page.discarded", "page.accepted", "page.denied"]
)
def test_story_1_1_audit_human_action_from_an_ai_actor_is_rejected(action: str) -> None:
    assert AuditRecord.model_validate(
        audit(actor_kind="human", actor="underwriter", action=action)
    )
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(audit(action=action))


@pytest.mark.parametrize(
    ("action", "changes"),
    [
        ("page.classified", {}),
        ("facts.extracted", {}),
        ("verdict.suggested", {"page_id": None}),
        ("stage.failed", {}),
        ("document.redacted", {"page_id": None, "detail": {"Person": 1}}),
    ],
)
def test_story_1_1_audit_ai_action_from_a_human_actor_is_rejected(
    action: str, changes: dict[str, Any]
) -> None:
    assert AuditRecord.model_validate(audit(action=action, **changes))
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(
            audit(actor_kind="human", actor="customer", action=action, **changes)
        )


@pytest.mark.parametrize(
    "actor", ["intake: ", "intake: chat-main", "intake:chat-main ", "intake:\t"]
)
def test_story_1_1_audit_ai_actor_with_a_blank_or_padded_deployment_is_rejected(
    actor: str,
) -> None:
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(audit(actor=actor))
    with pytest.raises(ValueError):
        ai_actor(Service.INTAKE, actor.partition(":")[2])


@pytest.mark.parametrize(
    ("action", "human"),
    [
        ("page.classified", False),
        ("page.kept", True),
        ("page.discarded", True),
        ("page.accepted", True),
        ("page.denied", True),
    ],
)
def test_story_1_1_audit_page_action_without_a_page_is_rejected(
    action: str, human: bool
) -> None:
    actor = {"actor_kind": "human", "actor": "customer"} if human else {}

    assert AuditRecord.model_validate(audit(action=action, **actor))
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(audit(action=action, page_id=None, **actor))


def test_story_1_1_audit_case_level_stage_failure_needs_no_page() -> None:
    record = AuditRecord.model_validate(audit(action="stage.failed", page_id=None))

    assert record.page_id is None


@pytest.mark.parametrize(
    "trace_id", ["", "abc", TRACE.upper(), TRACE[:-1], TRACE + "0"]
)
def test_story_1_1_malformed_trace_id_is_rejected(trace_id: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(TraceId).validate_python(trace_id)
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(audit(trace_id=trace_id))


# --- review fixes: text types


@pytest.mark.parametrize("text", ["", " ", "\t\n", "\u00a0\u2003"])
def test_story_1_1_blank_text_is_no_non_empty_string(text: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(NonEmptyStr).validate_python(text)
    with pytest.raises(ValidationError):
        TypeAdapter(OneLine).validate_python(text)
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"query": text, "retriever_config": "r3"})


def test_story_1_1_non_empty_text_is_kept_as_given() -> None:
    assert TypeAdapter(NonEmptyStr).validate_python(" HbA1c\n8.2 ") == " HbA1c\n8.2 "
    assert TypeAdapter(OneLine).validate_python(" HbA1c 8.2% ") == " HbA1c 8.2% "


@pytest.mark.parametrize(
    "separator", ["\n", "\r", "\x0b", "\x0c", "\x85", "\u2028", "\u2029"]
)
def test_story_1_1_one_line_text_holds_no_line_separator(separator: str) -> None:
    for text in (f"first{separator}second", f"first{separator}", f"{separator}second"):
        with pytest.raises(ValidationError):
            TypeAdapter(OneLine).validate_python(text)
        with pytest.raises(ValidationError):
            Classification.model_validate(classification(reason=text))


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_story_1_1_page_text_may_be_empty_or_blank(text: str) -> None:
    page = PageText.model_validate({"page_id": PAGE, "page_number": 1, "text": text})

    assert page.text == text


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


def test_story_1_1_reason_with_no_facts_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Reason.model_validate(reason(fact_ids=[]))


def test_story_1_1_loaded_verdict_needs_a_loading_pct() -> None:
    assert VerdictRun.model_validate(verdict_run(verdict="loaded", loading_pct=0))
    with pytest.raises(ValidationError):
        VerdictRun.model_validate(verdict_run(verdict="loaded", loading_pct=None))


def verdict_run_result(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "case_id": CASE,
        "status": "done",
        "error_code": None,
        "audit": audit(
            action="verdict.suggested", actor="verdict:chat-main", page_id=None, ref=RUN
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


# --- review fixes: stage results


def redaction_result(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "case_id": CASE,
        "status": "done",
        "error_code": None,
        "audit": audit(
            action="document.redacted",
            actor="intake:azure-ai-language",
            page_id=None,
            detail={"Person": 2},
        ),
        "document_id": DOCUMENT,
        "page_ids": [PAGE],
        "redaction_counts": {"Person": 2},
    }
    record.update(changes)
    return record


def fact_set_result(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "case_id": CASE,
        "status": "done",
        "error_code": None,
        "audit": audit(action="facts.extracted", actor="extraction:chat-main"),
        "fact_set_id": REF,
        "page_id": PAGE,
        "fact_ids": [FACT, OTHER_ID],
        "unverified_count": 1,
    }
    record.update(changes)
    return record


STAGE_RESULTS: list[tuple[type[ContractModel], dict[str, Any], str]] = [
    (RedactionResult, redaction_result(), "document.redacted"),
    (ClassificationResult, classification_result(), "page.classified"),
    (FactSetResult, fact_set_result(), "facts.extracted"),
    (VerdictRunResult, verdict_run_result(), "verdict.suggested"),
]
STAGE_RESULT_IDS = [model.__name__ for model, _, _ in STAGE_RESULTS]


@pytest.mark.parametrize(
    ("model", "payload", "action"), STAGE_RESULTS, ids=STAGE_RESULT_IDS
)
def test_story_1_1_stage_result_is_never_running(
    model: type[ContractModel], payload: dict[str, Any], action: str
) -> None:
    assert model.model_validate(payload)
    with pytest.raises(ValidationError, match="never running"):
        model.model_validate({**payload, "status": "running"})


@pytest.mark.parametrize(
    ("model", "payload", "action"), STAGE_RESULTS, ids=STAGE_RESULT_IDS
)
def test_story_1_1_done_stage_result_carries_its_own_audit_action(
    model: type[ContractModel], payload: dict[str, Any], action: str
) -> None:
    assert (
        model.model_validate(payload).model_dump(mode="json")["audit"]["action"]
        == action
    )
    others = {
        "document.redacted": audit(
            action="document.redacted", page_id=None, detail={"Person": 2}
        ),
        "page.classified": audit(action="page.classified"),
        "facts.extracted": audit(action="facts.extracted"),
        "verdict.suggested": audit(action="verdict.suggested"),
        "page.kept": audit(action="page.kept", actor_kind="human", actor="customer"),
    }
    del others[action]
    for other in others.values():
        with pytest.raises(ValidationError, match="audit record"):
            model.model_validate({**payload, "audit": other})


@pytest.mark.parametrize(
    "changes",
    [
        {"classification_id": OTHER_ID},
        {"case_id": OTHER_CASE},
        {"page_id": OTHER_ID},
        {"contender": "doc-intelligence"},
    ],
)
def test_story_1_1_nested_classification_must_match_its_result(
    changes: dict[str, Any],
) -> None:
    broken = classification_result(classification=classification(**changes))

    with pytest.raises(ValidationError, match="this result is about"):
        ClassificationResult.model_validate(broken)


def test_story_1_1_failed_redaction_has_no_pages() -> None:
    failed = redaction_result(
        status="failed",
        error_code="redaction_failed",
        audit=audit(
            action="stage.failed", actor="intake:azure-ai-language", page_id=None
        ),
        page_ids=[],
        redaction_counts={},
    )

    assert RedactionResult.model_validate(failed).page_ids == []
    with pytest.raises(ValidationError, match="no pages"):
        RedactionResult.model_validate({**failed, "page_ids": [PAGE]})


@pytest.mark.parametrize("counts", [{}, {"Person": 3}, {"Person": 2, "Email": 1}])
def test_story_1_1_redaction_counts_must_equal_the_audit_detail(
    counts: dict[str, int],
) -> None:
    with pytest.raises(ValidationError, match="audit record's detail"):
        RedactionResult.model_validate(redaction_result(redaction_counts=counts))


def test_story_1_1_fact_set_result_counts_are_consistent() -> None:
    failed = fact_set_result(
        status="failed",
        error_code="model_unavailable",
        audit=audit(action="stage.failed", actor="extraction:chat-main"),
        fact_ids=[],
        unverified_count=0,
    )

    assert FactSetResult.model_validate(fact_set_result(unverified_count=2))
    assert FactSetResult.model_validate(failed).fact_ids == []
    for broken in (
        fact_set_result(unverified_count=3),
        fact_set_result(fact_ids=[FACT, FACT]),
        {**failed, "fact_ids": [FACT]},
    ):
        with pytest.raises(ValidationError):
            FactSetResult.model_validate(broken)


# --- review fixes: case start, boxes


def case_started(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "case_id": CASE,
        "case_status": "running",
        "classifier_contender": "llm",
        "retriever_configs": ["r3", "r4"],
        "stop_after": None,
        "eval_run_id": None,
    }
    record.update(changes)
    return record


def test_story_1_1_case_started_needs_distinct_retriever_configs() -> None:
    assert CaseStarted.model_validate(case_started())
    with pytest.raises(ValidationError):
        CaseStarted.model_validate(case_started(retriever_configs=[]))
    with pytest.raises(ValidationError):
        CaseStarted.model_validate(case_started(retriever_configs=["r3", "r3"]))


def word_box(**changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "char_start": 0,
        "char_end": 7,
        "x0": 72.0,
        "y0": 90.5,
        "x1": 110.25,
        "y1": 102.5,
    }
    record.update(changes)
    return record


@pytest.mark.parametrize(
    "changes",
    [
        {"char_start": 7},
        {"char_start": 8},
        {"x0": 111.0},
        {"y0": 103.0},
        {"x1": float("inf")},
        {"y1": float("inf")},
        {"x0": float("nan")},
        {"x0": -1.0},
    ],
)
def test_story_1_1_inverted_or_unbounded_word_box_is_rejected(
    changes: dict[str, Any],
) -> None:
    assert WordBox.model_validate(word_box())
    with pytest.raises(ValidationError):
        WordBox.model_validate(word_box(**changes))


@pytest.mark.parametrize("size", [float("inf"), float("nan"), 0.0, -1.0])
def test_story_1_1_page_size_is_a_finite_positive_length(size: float) -> None:
    boxes = {
        "page_id": PAGE,
        "page_number": 1,
        "page_width": 595.0,
        "page_height": 842.0,
        "boxes": [word_box()],
    }

    assert PageBoxes.model_validate(boxes)
    with pytest.raises(ValidationError):
        PageBoxes.model_validate({**boxes, "page_width": size})
    with pytest.raises(ValidationError):
        PageBoxes.model_validate({**boxes, "page_height": size})


def test_story_1_1_models_are_immutable() -> None:
    request = DecisionRequest.model_validate({"decision": "keep", "actor": "customer"})

    with pytest.raises(ValidationError):
        request.decision = "discard"  # type: ignore[assignment]  # proving the model is frozen
