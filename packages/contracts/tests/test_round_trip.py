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
        "error_code": None,
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
ROUTE_DETAIL = {"route": "awaiting_triage", "threshold": 0.9}
ROUTE_AUDIT = audit("page.routed", actor="workflow:gate", detail=ROUTE_DETAIL)
CASE_SUMMARY = {
    "case_id": CASE,
    "case_status": "awaiting_human",
    "started_at": "2026-10-07T09:00:00Z",
    "page_count": 3,
    "waiting_page_count": 1,
}
QUEUED_PAGE = {
    "case_id": CASE,
    "page_id": PAGE,
    "page_number": 3,
    "page_status": "awaiting_triage",
    "classifier_contender": "llm",
    "queued_by": "gate",
}
TRIAGE_PAGE = {
    "case_id": CASE,
    "page_id": PAGE,
    "page_number": 3,
    "thumbnail_path": f"/api/pages/{PAGE}/thumbnail",
    "page_type": "lab_report",
    "is_medical": True,
    "confidence": 0.6,
    "reason": "Lists laboratory values with reference ranges.",
    "queued_by": "gate",
}
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
    "outcome": "done",
    "error_code": None,
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
SCOREBOARD_RUN = {
    "eval_run_id": EVAL,
    "started_at": WHEN,
    "finished_at": "2026-10-06T12:20:00Z",
    "web_address": "http://localhost:8000",
    "stand_ins": True,
}
STATED_FIGURE = {
    "amount": "2.50",
    "unit": "stories built",
    "source": "epics.md, stories 2.2 and 2.3",
}
ROW_SCORE = {
    "retriever_config": "r1",
    "store": "pgvector",
    "chunk_set": "fixed",
    "method": "Vector only",
    "measured": True,
    "rule_recall": 0.6667,
    "recall_hits": 2,
    "recall_searches": 3,
    "verdict_accuracy": 0.5,
    "right_runs": 1,
    "cases": 2,
    "latency_ms_median": 21,
    "latency_ms_p95": 40,
    "latency_searches": 3,
    "cost": None,
    "effort": STATED_FIGURE,
}
UNMEASURED_ROW = {
    **dict.fromkeys(ROW_SCORE),
    "store": "pgvector",
    "chunk_set": "smart",
    "method": "Hybrid",
    "measured": False,
}
FAILED_SEARCH = {
    "retriever_config": "r1",
    "case_key": "case-002",
    "fact_number": 4,
    "error_code": "upstream_unavailable",
}
UNSCORED_CASE = {
    "case_key": "case-002",
    "case_id": CASE,
    "case_status": "failed",
    "reason": "case_failed",
    "error_code": "stage_failed",
}
LEAK = {"case_key": "case-002", "page_number": 3, "category": "person_name"}

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
    "RouteDetail": ROUTE_DETAIL,
    "VerdictDetail": {"retriever_config": "r3"},
    "CaseCreated": {"case_id": CASE, "document_id": DOCUMENT},
    "Health": {"status": "ok"},
    "Me": {"role": "customer"},
    "UploadedCase": {"case_id": CASE, "document_id": DOCUMENT},
    "PageDecisionRequest": {"decision": "keep"},
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
    "StartCaseOptions": {
        "classifier_contender": "llm",
        "retriever_configs": ["r3"],
        "stop_after": "gate",
        "eval_run_id": EVAL,
    },
    "StartCaseRequest": {
        "actor": "underwriter",
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
        "verdict_run_id": None,
        "error_code": None,
    },
    "PageProgress": {
        "page_id": PAGE,
        "page_number": 1,
        "page_status": "failed",
        "error_code": "model_unavailable",
    },
    "CaseProgress": {
        "case_id": CASE,
        "case_status": "awaiting_human",
        "redaction_status": "done",
        "pages": [
            {
                "page_id": PAGE,
                "page_number": 1,
                "page_status": "awaiting_customer",
                "error_code": None,
            }
        ],
        "error_code": None,
    },
    "AuditTrail": {
        "case_id": CASE,
        "events": [
            REDACTION_AUDIT,
            audit("page.classified"),
            ROUTE_AUDIT,
            audit("stage.failed", error_code="model_unavailable"),
        ],
        "has_more": False,
    },
    "PageQueueQuery": {"status": "awaiting_triage"},
    "QueuedPage": QUEUED_PAGE,
    "PageQueue": {
        "pages": [QUEUED_PAGE, {**QUEUED_PAGE, "queued_by": "customer"}],
        "has_more": False,
    },
    "CaseSummary": CASE_SUMMARY,
    "CaseList": {"cases": [CASE_SUMMARY], "has_more": True},
    "TriagePage": TRIAGE_PAGE,
    "ScoreboardRun": SCOREBOARD_RUN,
    "StatedFigure": STATED_FIGURE,
    "RetrievalRowScore": ROW_SCORE,
    "FailedSearch": FAILED_SEARCH,
    "UnscoredCase": UNSCORED_CASE,
    "RetrievalScoreboard": {
        "run": SCOREBOARD_RUN,
        "top_k": 5,
        "rows": [
            ROW_SCORE,
            *(
                {**UNMEASURED_ROW, "retriever_config": row}
                for row in ("r2", "r3", "r4", "r5", "r6")
            ),
        ],
        "winner": "r1",
        "failed_searches": [FAILED_SEARCH],
        "unscored_cases": [UNSCORED_CASE],
    },
    "RedactionLeak": LEAK,
    "RedactionScoreboard": {
        "run": SCOREBOARD_RUN,
        "clean": False,
        "cases_checked": 2,
        "pages_checked": 9,
        "identifiers_checked": 18,
        "leaks": [LEAK],
        "may_also_be_redacted": 30,
        "may_also_be_redacted_masked": 4,
        "cases_not_checked": ["case-003"],
    },
    "TriageQueue": {
        "pages": [
            TRIAGE_PAGE,
            # A page whose classification could not be read.
            {
                **TRIAGE_PAGE,
                "page_type": None,
                "is_medical": None,
                "confidence": None,
                "reason": None,
                "queued_by": None,
            },
        ],
        "has_more": True,
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
        "reference_rule_ids": ["UW-HT-002"],
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
            "verdict.suggested",
            actor="verdict:chat-main",
            page_id=None,
            ref=RUN,
            detail={"retriever_config": "r3"},
        ),
        "verdict_run_id": RUN,
        "retriever_config": "r3",
        "verdict": "loaded",
    },
    "VerdictRun": VERDICT_RUN,
    "VerdictRunList": {
        "case_id": CASE,
        "verdict_runs": [VERDICT_RUN],
        "has_more": False,
    },
    "AgentStep": STEP,
    "RunStepQuery": {"tool": "read_rule", "rule_id": "UW-DM-003", "after_step_no": 4},
    "AgentStepQuery": {
        "tool": "read_rule",
        "rule_id": "UW-DM-003",
        "after_verdict_run_id": RUN,
        "after_step_no": 4,
    },
    "AgentStepList": {"steps": [STEP], "has_more": False},
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
    # Other packages extend the base too (synthdata does); only the models of
    # this package are contracts, whatever else has been imported by now.
    own = {model for model in found if model.__module__.startswith("contracts.")}
    return sorted(own - ABSTRACT, key=lambda model: model.__name__)


def test_story_1_1_every_model_round_trips_its_sample_and_rejects_unknown_fields() -> (
    None
):
    models = all_models()

    assert {model.__name__ for model in models} == set(SAMPLES)
    for model in models:
        name = model.__name__
        payload = SAMPLES[name]

        parsed = model.model_validate_json(json.dumps(payload))

        assert json.loads(parsed.model_dump_json()) == payload, name
        assert parsed.model_dump(mode="json") == payload, name
        for field in model.model_fields:
            assert field == field.lower() and field.isidentifier(), name
            assert not field.startswith("_"), name
        with pytest.raises(ValueError, match="extra"):
            model.model_validate({**payload, "surprise": 1})
