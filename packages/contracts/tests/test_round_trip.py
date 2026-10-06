"""Story 1.1: every contract model parses a sample payload and dumps the same JSON."""

import importlib
import json
import pkgutil
from typing import Any

import pytest

import contracts
from contracts.base import ContractModel
from contracts.models._stage import StageCommand, StageResult

CASE = "0199b7a0-0000-7000-8000-000000000001"
DOCUMENT = "0199b7a0-0000-7000-8000-000000000002"
PAGE = "0199b7a0-0000-7000-8000-000000000003"
FACT = "0199b7a0-0000-7000-8000-000000000004"
RUN = "0199b7a0-0000-7000-8000-000000000005"
EVAL = "0199b7a0-0000-7000-8000-000000000006"
REF = "0199b7a0-0000-7000-8000-000000000007"
TRACE = "0af7651916cd43dd8448eb211c80319c"
WHEN = "2026-10-06T12:00:00Z"


def audit(action: str, **changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "actor_kind": "ai",
        "actor": "classification:chat-main",
        "action": action,
        "occurred_at": WHEN,
        "case_id": CASE,
        "page_id": PAGE,
        "ref": REF,
        "detail": None,
        "trace_id": TRACE,
        "eval_run_id": None,
    }
    record.update(changes)
    return record


REDACTION_AUDIT = audit(
    "document.redacted",
    actor="intake:azure-ai-language",
    page_id=None,
    detail={"Person": 3, "Email": 1},
    eval_run_id=EVAL,
)
CLASSIFICATION = {
    "classification_id": REF,
    "case_id": CASE,
    "page_id": PAGE,
    "contender": "llm",
    "page_type": "lab_report",
    "is_medical": True,
    "confidence": 0.9,
    "reason": "Lists laboratory values with reference ranges.",
}
FACT_RECORD = {
    "fact_id": FACT,
    "case_id": CASE,
    "page_id": PAGE,
    "page_number": 2,
    "statement": "HbA1c 8.2%",
    "quote": "HbA1c 8.2 %",
    "quote_verified": True,
    "quote_start": 14,
    "quote_end": 25,
}
REASON = {
    "rule_id": "UW-DM-003",
    "fact_ids": [FACT],
    "effect": "debit",
    "debit_pct": 50,
}
STEP = {
    "verdict_run_id": RUN,
    "case_id": CASE,
    "step_no": 1,
    "tool": "search_rules",
    "arguments": {"query": "HbA1c 8.2%", "fact_id": FACT},
    "fact_id": FACT,
    "rule_ids": ["UW-DM-003", "UW-HTN-120"],
    "latency_ms": 412,
    "occurred_at": "2026-10-06T12:00:01.250000Z",
}
SEARCH_ITEM = {
    "chunk_id": "smart-0042",
    "rule_ids": ["UW-DM-003"],
    "rank": 1,
    "score": 0.83,
    "text": "Rule UW-DM-003: HbA1c from 8.0% to 8.9% carries a debit of 50%.",
    "manual_page": 12,
    "impairment": "Diabetes mellitus",
}
VERDICT_RUN = {
    "verdict_run_id": RUN,
    "case_id": CASE,
    "retriever_config": "r3",
    "status": "done",
    "label": "AI suggestion, not a decision",
    "verdict": "loaded",
    "loading_pct": 50,
    "confidence": 0.86,
    "reasons": [REASON],
    "system_reasons": [],
    "error_code": None,
}

# One valid payload per model, keyed by class name.
SAMPLES: dict[str, dict[str, Any]] = {
    "ErrorDetail": {
        "code": "in_progress",
        "message": "Still running.",
        "trace_id": TRACE,
    },
    "ErrorBody": {
        "error": {
            "code": "not_redacted",
            "message": "Not redacted yet.",
            "trace_id": TRACE,
        }
    },
    "AuditRecord": audit("page.kept", actor_kind="human", actor="customer"),
    "CaseCreated": {"case_id": CASE, "document_id": DOCUMENT},
    "Health": {"status": "ok"},
    "Me": {"role": "customer"},
    "UploadedCase": {"case_id": CASE, "document_id": DOCUMENT},
    "RedactionCommand": {"eval_run_id": EVAL},
    "RedactionResult": {
        "case_id": CASE,
        "status": "done",
        "error_code": None,
        "audit": REDACTION_AUDIT,
        "document_id": DOCUMENT,
        "page_ids": [PAGE],
        "redaction_counts": {"Person": 3, "Email": 1},
    },
    "Page": {
        "page_id": PAGE,
        "case_id": CASE,
        "document_id": DOCUMENT,
        "page_number": 1,
    },
    "PageList": {
        "case_id": CASE,
        "pages": [
            {
                "page_id": PAGE,
                "case_id": CASE,
                "document_id": DOCUMENT,
                "page_number": 1,
            }
        ],
    },
    "PageText": {
        "page_id": PAGE,
        "page_number": 1,
        "text": "Patient [Person] seen.\nHbA1c 8.2 %",
    },
    "PageBoxesQuery": {"quote_start": 14, "quote_end": 25},
    "WordBox": {
        "char_start": 0,
        "char_end": 7,
        "x0": 72.0,
        "y0": 90.5,
        "x1": 110.25,
        "y1": 102.5,
    },
    "PageBoxes": {
        "page_id": PAGE,
        "page_number": 1,
        "page_width": 595.0,
        "page_height": 842.0,
        "boxes": [
            {
                "char_start": 0,
                "char_end": 7,
                "x0": 72.0,
                "y0": 90.5,
                "x1": 110.25,
                "y1": 102.5,
            }
        ],
    },
    "StartCaseRequest": {
        "classifier_contender": "doc-intelligence",
        "retriever_configs": ["r1", "r2", "r3", "r4", "r5", "r6"],
        "stop_after": "gate",
        "eval_run_id": EVAL,
    },
    "CaseStarted": {
        "case_id": CASE,
        "case_status": "running",
        "classifier_contender": "llm",
        "retriever_configs": ["r3"],
        "stop_after": None,
        "eval_run_id": None,
    },
    "DecisionRequest": {"decision": "accept", "actor": "underwriter"},
    "DecisionRecorded": {
        "decision_id": REF,
        "case_id": CASE,
        "page_id": PAGE,
        "decision": "accept",
        "actor": "underwriter",
        "page_status": "extracting",
        "occurred_at": WHEN,
    },
    "VerdictRunRequest": {"retriever_config": "r5"},
    "VerdictRunRequested": {
        "case_id": CASE,
        "retriever_config": "r5",
        "status": "running",
    },
    "PageProgress": {
        "page_id": PAGE,
        "page_number": 1,
        "page_status": "awaiting_triage",
    },
    "CaseProgress": {
        "case_id": CASE,
        "case_status": "awaiting_human",
        "redaction_status": "done",
        "pages": [
            {"page_id": PAGE, "page_number": 1, "page_status": "awaiting_customer"}
        ],
    },
    "AuditTrail": {
        "case_id": CASE,
        "events": [REDACTION_AUDIT, audit("page.classified")],
    },
    "PageQueueQuery": {"status": "awaiting_triage"},
    "QueuedPage": {
        "case_id": CASE,
        "page_id": PAGE,
        "page_number": 3,
        "page_status": "awaiting_triage",
    },
    "PageQueue": {
        "pages": [
            {
                "case_id": CASE,
                "page_id": PAGE,
                "page_number": 3,
                "page_status": "awaiting_triage",
            }
        ]
    },
    "ClassifierOutput": {"page_type": "invoice", "reason": "Shows an amount due."},
    "ClassifyCommand": {
        "eval_run_id": None,
        "case_id": CASE,
        "page_id": PAGE,
        "contender": "llm",
    },
    "Classification": CLASSIFICATION,
    "ClassificationResult": {
        "case_id": CASE,
        "status": "done",
        "error_code": None,
        "audit": audit("page.classified"),
        "classification_id": REF,
        "page_id": PAGE,
        "contender": "llm",
        "classification": CLASSIFICATION,
    },
    "ClassificationList": {"case_id": CASE, "classifications": [CLASSIFICATION]},
    "ExtractedFact": {"statement": "HbA1c 8.2%", "quote": "HbA1c 8.2 %"},
    "ExtractionOutput": {
        "facts": [{"statement": "HbA1c 8.2%", "quote": "HbA1c 8.2 %"}]
    },
    "ExtractFactsCommand": {"eval_run_id": None, "case_id": CASE, "page_id": PAGE},
    "FactSetResult": {
        "case_id": CASE,
        "status": "failed",
        "error_code": "model_unavailable",
        "audit": audit("stage.failed", actor="extraction:chat-main"),
        "fact_set_id": REF,
        "page_id": PAGE,
        "fact_ids": [],
        "unverified_count": 0,
    },
    "Fact": FACT_RECORD,
    "FactList": {
        "case_id": CASE,
        "facts": [
            FACT_RECORD,
            {
                **FACT_RECORD,
                "quote_verified": False,
                "quote_start": None,
                "quote_end": None,
            },
        ],
    },
    "SearchRequest": {"query": "HbA1c 8.2%", "retriever_config": "r3", "top_k": 5},
    "SearchItem": SEARCH_ITEM,
    "SearchResponse": {
        "retriever_config": "r3",
        "latency_ms": 180,
        "items": [SEARCH_ITEM],
    },
    "RuleReadQuery": {"retriever_config": "r1"},
    "RuleText": {
        "rule_id": "UW-DM-003",
        "chunk_id": "smart-0042",
        "chunk_set": "smart",
        "text": "Rule UW-DM-003: HbA1c from 8.0% to 8.9% carries a debit of 50%.",
        "manual_page": 12,
        "impairment": "Diabetes mellitus",
    },
    "Reason": REASON,
    "VerdictOutput": {
        "verdict": "refer",
        "confidence": 0.4,
        "reasons": [{**REASON, "effect": "none", "debit_pct": None}],
        "system_reasons": ["conflicting_rules", "low_confidence"],
    },
    "SearchRulesArguments": {"query": "HbA1c 8.2%", "fact_id": FACT},
    "ReadRuleArguments": {"rule_id": "UW-HTN-120"},
    "VerdictRunCommand": {
        "eval_run_id": EVAL,
        "case_id": CASE,
        "retriever_config": "r6",
    },
    "VerdictRunResult": {
        "case_id": CASE,
        "status": "done",
        "error_code": None,
        "audit": audit(
            "verdict.suggested", actor="verdict:chat-main", page_id=None, ref=RUN
        ),
        "verdict_run_id": RUN,
        "retriever_config": "r3",
        "verdict": "loaded",
    },
    "VerdictRun": VERDICT_RUN,
    "VerdictRunList": {"case_id": CASE, "verdict_runs": [VERDICT_RUN]},
    "AgentStep": STEP,
    "AgentStepQuery": {"tool": "read_rule", "rule_id": "UW-DM-003"},
    "AgentStepList": {"steps": [STEP]},
}

# Bases that only exist to be extended; they are no payload of their own.
ABSTRACT = {ContractModel, StageCommand, StageResult}


def all_models() -> list[type[ContractModel]]:
    for module in pkgutil.walk_packages(contracts.__path__, "contracts."):
        importlib.import_module(module.name)
    found: set[type[ContractModel]] = set()
    pending = [ContractModel]
    while pending:
        for child in pending.pop().__subclasses__():
            if child not in found:
                found.add(child)
                pending.append(child)
    return sorted(found - ABSTRACT, key=lambda model: model.__name__)


MODELS = all_models()


def test_story_1_1_every_model_has_a_sample_payload() -> None:
    assert {model.__name__ for model in MODELS} == set(SAMPLES)


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_story_1_1_round_trip_is_lossless(model: type[ContractModel]) -> None:
    payload = SAMPLES[model.__name__]

    parsed = model.model_validate_json(json.dumps(payload))

    assert json.loads(parsed.model_dump_json()) == payload
    assert parsed.model_dump(mode="json") == payload


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_story_1_1_field_names_are_snake_case(model: type[ContractModel]) -> None:
    for name in model.model_fields:
        assert name == name.lower() and name.isidentifier() and not name.startswith("_")


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_story_1_1_unknown_fields_are_rejected(model: type[ContractModel]) -> None:
    with pytest.raises(ValueError, match="extra"):
        model.model_validate({**SAMPLES[model.__name__], "surprise": 1})
