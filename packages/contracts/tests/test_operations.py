"""Story 1.1: the registry covers the spine's Operations table, and the package stays pure."""

import ast
import sys
from pathlib import Path

import contracts
from contracts.base import ContractModel
from contracts.models import (
    classification,
    extraction,
    intake,
    retrieval,
    verdict,
    workflow,
)
from contracts.operations import OPERATIONS, Operation, get_operation

# The spine's Operations table, row by row: (method, owner, path, callers, idempotency key).
INTAKE_READERS = {"web", "workflow", "classification", "extraction"}
SPINE_TABLE: list[tuple[str, str, str, set[str], tuple[str, ...]]] = [
    ("POST", "intake", "/cases", {"web"}, ()),
    ("POST", "intake", "/cases/{case_id}/redaction", {"workflow"}, ("case_id",)),
    ("GET", "intake", "/cases/{case_id}/pages", INTAKE_READERS, ()),
    ("GET", "intake", "/pages/{page_id}/text", INTAKE_READERS, ()),
    ("GET", "intake", "/pages/{page_id}/boxes", INTAKE_READERS, ()),
    ("GET", "intake", "/pages/{page_id}/thumbnail", INTAKE_READERS, ()),
    ("GET", "intake", "/documents/{document_id}/file", INTAKE_READERS, ()),
    ("POST", "workflow", "/cases/{case_id}/start", {"web"}, ("case_id",)),
    ("POST", "workflow", "/cases/{case_id}/pages/{page_id}/decisions", {"web"}, ()),
    (
        "POST",
        "workflow",
        "/cases/{case_id}/verdict-runs",
        {"web"},
        ("case_id", "retriever_config"),
    ),
    ("GET", "workflow", "/cases/{case_id}/progress", {"web"}, ()),
    ("GET", "workflow", "/cases/{case_id}/audit", {"web"}, ()),
    ("GET", "workflow", "/pages", {"web"}, ()),
    (
        "POST",
        "classification",
        "/classifications",
        {"workflow"},
        ("case_id", "page_id", "contender"),
    ),
    ("GET", "classification", "/cases/{case_id}/classifications", {"web"}, ()),
    ("POST", "extraction", "/fact-sets", {"workflow"}, ("case_id", "page_id")),
    ("GET", "extraction", "/cases/{case_id}/facts", {"web", "verdict"}, ()),
    ("POST", "verdict", "/verdict-runs", {"workflow"}, ("case_id", "retriever_config")),
    ("GET", "verdict", "/cases/{case_id}/verdict-runs", {"web"}, ()),
    ("GET", "verdict", "/verdict-runs/{verdict_run_id}/steps", {"web"}, ()),
    ("GET", "verdict", "/cases/{case_id}/agent-steps", {"web"}, ()),
    ("POST", "retrieval", "/searches", {"verdict", "web"}, ()),
    ("GET", "retrieval", "/rules/{rule_id}", {"verdict", "web"}, ()),
]
# Operations the owner approved after the spine was written; the spine's
# table does not list them yet (deferred-work.md). Anything beyond these is drift.
APPROVED_ADDITIONS: list[tuple[str, str, str, set[str], tuple[str, ...]]] = [
    # Story 1.13: the underwriter's case list (owner, 2026-10-07).
    ("GET", "workflow", "/cases", {"web"}, ()),
    # Story 4.2: one redacted page as a one-page PDF, for the Document
    # Intelligence classifier (spec 4.2; deferred-work.md).
    (
        "GET",
        "intake",
        "/documents/{document_id}/pages/{page_number}/file",
        {"classification"},
        (),
    ),
]
REGISTERED = SPINE_TABLE + APPROVED_ADDITIONS

# The query strings the table prints, by path.
SPINE_QUERY_FIELDS = {
    ("workflow", "/pages"): {"status"},
    # Story 2.8 added the cursor to both step reads, and the two filters to
    # the read by run (deferred-work.md).
    ("verdict", "/verdict-runs/{verdict_run_id}/steps"): {
        "tool",
        "rule_id",
        "after_step_no",
    },
    ("verdict", "/cases/{case_id}/agent-steps"): {
        "tool",
        "rule_id",
        "after_verdict_run_id",
        "after_step_no",
    },
    ("retrieval", "/rules/{rule_id}"): {"retriever_config"},
}

# Calls the spine's diagram allows; any other caller and owner pair is forbidden.
ALLOWED_CALLS = {
    ("web", "intake"),
    ("web", "workflow"),
    ("web", "classification"),
    ("web", "extraction"),
    ("web", "verdict"),
    ("web", "retrieval"),
    ("workflow", "classification"),
    ("workflow", "extraction"),
    ("workflow", "verdict"),
    ("workflow", "intake"),
    ("classification", "intake"),
    ("extraction", "intake"),
    ("verdict", "extraction"),
    ("verdict", "retrieval"),
}

Model = type[ContractModel] | None

# Every operation by name: (method, owner, path, request, query, response model).
EXPECTED_MODELS: dict[str, tuple[str, str, str, Model, Model, Model]] = {
    "create_case": ("POST", "intake", "/cases", None, None, intake.CaseCreated),
    "redact_document": (
        "POST",
        "intake",
        "/cases/{case_id}/redaction",
        intake.RedactionCommand,
        None,
        intake.RedactionResult,
    ),
    "list_pages": (
        "GET",
        "intake",
        "/cases/{case_id}/pages",
        None,
        None,
        intake.PageList,
    ),
    "read_page_text": (
        "GET",
        "intake",
        "/pages/{page_id}/text",
        None,
        None,
        intake.PageText,
    ),
    "read_page_boxes": (
        "GET",
        "intake",
        "/pages/{page_id}/boxes",
        None,
        intake.PageBoxesQuery,
        intake.PageBoxes,
    ),
    "read_page_thumbnail": (
        "GET",
        "intake",
        "/pages/{page_id}/thumbnail",
        None,
        None,
        None,
    ),
    "read_document_file": (
        "GET",
        "intake",
        "/documents/{document_id}/file",
        None,
        None,
        None,
    ),
    "read_page_file": (
        "GET",
        "intake",
        "/documents/{document_id}/pages/{page_number}/file",
        None,
        None,
        None,
    ),
    "start_case": (
        "POST",
        "workflow",
        "/cases/{case_id}/start",
        workflow.StartCaseRequest,
        None,
        workflow.CaseStarted,
    ),
    "list_cases": (
        "GET",
        "workflow",
        "/cases",
        None,
        None,
        workflow.CaseList,
    ),
    "record_decision": (
        "POST",
        "workflow",
        "/cases/{case_id}/pages/{page_id}/decisions",
        workflow.DecisionRequest,
        None,
        workflow.DecisionRecorded,
    ),
    "request_verdict_run": (
        "POST",
        "workflow",
        "/cases/{case_id}/verdict-runs",
        workflow.VerdictRunRequest,
        None,
        workflow.VerdictRunRequested,
    ),
    "read_progress": (
        "GET",
        "workflow",
        "/cases/{case_id}/progress",
        None,
        None,
        workflow.CaseProgress,
    ),
    "read_audit_trail": (
        "GET",
        "workflow",
        "/cases/{case_id}/audit",
        None,
        None,
        workflow.AuditTrail,
    ),
    "list_pages_by_status": (
        "GET",
        "workflow",
        "/pages",
        None,
        workflow.PageQueueQuery,
        workflow.PageQueue,
    ),
    "classify_page": (
        "POST",
        "classification",
        "/classifications",
        classification.ClassifyCommand,
        None,
        classification.ClassificationResult,
    ),
    "list_classifications": (
        "GET",
        "classification",
        "/cases/{case_id}/classifications",
        None,
        None,
        classification.ClassificationList,
    ),
    "extract_facts": (
        "POST",
        "extraction",
        "/fact-sets",
        extraction.ExtractFactsCommand,
        None,
        extraction.FactSetResult,
    ),
    "list_facts": (
        "GET",
        "extraction",
        "/cases/{case_id}/facts",
        None,
        None,
        extraction.FactList,
    ),
    "run_verdict": (
        "POST",
        "verdict",
        "/verdict-runs",
        verdict.VerdictRunCommand,
        None,
        verdict.VerdictRunResult,
    ),
    "list_verdict_runs": (
        "GET",
        "verdict",
        "/cases/{case_id}/verdict-runs",
        None,
        None,
        verdict.VerdictRunList,
    ),
    "list_run_steps": (
        "GET",
        "verdict",
        "/verdict-runs/{verdict_run_id}/steps",
        None,
        verdict.RunStepQuery,
        verdict.AgentStepList,
    ),
    "list_case_agent_steps": (
        "GET",
        "verdict",
        "/cases/{case_id}/agent-steps",
        None,
        verdict.AgentStepQuery,
        verdict.AgentStepList,
    ),
    "search_rules": (
        "POST",
        "retrieval",
        "/searches",
        retrieval.SearchRequest,
        None,
        retrieval.SearchResponse,
    ),
    "read_rule": (
        "GET",
        "retrieval",
        "/rules/{rule_id}",
        None,
        retrieval.RuleReadQuery,
        retrieval.RuleText,
    ),
}


def test_story_1_1_every_operation_has_exactly_its_own_models() -> None:
    registered = {
        operation.name: (
            operation.method.value,
            operation.owner.value,
            operation.path,
            operation.request_model,
            operation.query_model,
            operation.response_model,
        )
        for operation in OPERATIONS
    }

    assert registered == EXPECTED_MODELS


def key(operation: Operation) -> tuple[str, str, str]:
    return (operation.method.value, operation.owner.value, operation.path)


def test_story_1_1_registry_lists_every_spine_operation_exactly_once() -> None:
    listed = [key(operation) for operation in OPERATIONS]

    assert len(listed) == len(set(listed))
    assert set(listed) == {
        (method, owner, path) for method, owner, path, _, _ in REGISTERED
    }
    assert len(SPINE_TABLE) == 23
    assert len(OPERATIONS) == len(REGISTERED) == 25


def test_story_1_1_registry_callers_and_keys_match_the_spine() -> None:
    expected = {
        (method, owner, path): (callers, idempotency_key)
        for method, owner, path, callers, idempotency_key in REGISTERED
    }
    for operation in OPERATIONS:
        callers, idempotency_key = expected[key(operation)]
        assert {caller.value for caller in operation.callers} == callers, operation.name
        assert operation.idempotency_key == idempotency_key, operation.name
        # No caller and owner pair outside the spine's diagram.
        for caller in operation.callers:
            assert (caller.value, operation.owner.value) in ALLOWED_CALLS
        assert get_operation(operation.name) is operation


def test_story_1_1_query_models_carry_the_spine_query_fields() -> None:
    for operation in OPERATIONS:
        expected = SPINE_QUERY_FIELDS.get((operation.owner.value, operation.path))
        if expected is not None:
            assert operation.query_model is not None, operation.name
            assert set(operation.query_model.model_fields) == expected, operation.name


def test_story_1_1_package_imports_only_the_standard_library_and_pydantic() -> None:
    source_root = Path(contracts.__file__).parent
    allowed = set(sys.stdlib_module_names) | {"pydantic", "contracts"}
    files = sorted(source_root.rglob("*.py"))
    imported: set[str] = set()

    for file in files:
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{file.name}: relative import"
                assert node.module is not None
                imported.add(node.module.split(".")[0])

    assert len(files) >= 15
    assert "pydantic" in imported
    assert imported <= allowed, sorted(imported - allowed)
