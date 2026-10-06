"""Story 1.1: the registry covers the spine's Operations table, and the package stays pure."""

import ast
import re
import sys
from pathlib import Path

import pytest

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
from contracts.operations import JSON, OPERATIONS, HttpMethod, Operation, get_operation

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

# The query strings the table prints, by path.
SPINE_QUERY_FIELDS = {
    ("workflow", "/pages"): {"status"},
    ("verdict", "/cases/{case_id}/agent-steps"): {"tool", "rule_id"},
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

OPERATION_IDS = [operation.name for operation in OPERATIONS]

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
    "start_case": (
        "POST",
        "workflow",
        "/cases/{case_id}/start",
        workflow.StartCaseRequest,
        None,
        workflow.CaseStarted,
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
        None,
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
        (method, owner, path) for method, owner, path, _, _ in SPINE_TABLE
    }
    assert len(OPERATIONS) == len(SPINE_TABLE) == 23


def test_story_1_1_registry_names_are_unique_and_resolvable() -> None:
    assert len(set(OPERATION_IDS)) == len(OPERATIONS)
    for operation in OPERATIONS:
        assert get_operation(operation.name) is operation
    with pytest.raises(KeyError):
        get_operation("approve_case")


def test_story_1_1_registry_callers_and_keys_match_the_spine() -> None:
    expected = {
        (method, owner, path): (callers, idempotency_key)
        for method, owner, path, callers, idempotency_key in SPINE_TABLE
    }
    for operation in OPERATIONS:
        callers, idempotency_key = expected[key(operation)]
        assert {caller.value for caller in operation.callers} == callers, operation.name
        assert operation.idempotency_key == idempotency_key, operation.name


@pytest.mark.parametrize("operation", OPERATIONS, ids=OPERATION_IDS)
def test_story_1_1_registry_never_lists_a_forbidden_call(operation: Operation) -> None:
    for caller in operation.callers:
        assert (caller.value, operation.owner.value) in ALLOWED_CALLS


@pytest.mark.parametrize("operation", OPERATIONS, ids=OPERATION_IDS)
def test_story_1_1_every_operation_has_its_models(operation: Operation) -> None:
    if operation.response_model is None:
        # Only the two file reads have no JSON response.
        assert operation.response_media_type in {"application/pdf", "image/png"}
        assert operation.method is HttpMethod.GET
    else:
        assert issubclass(operation.response_model, ContractModel)
        assert operation.response_media_type == JSON

    if operation.method is HttpMethod.GET:
        assert operation.request_model is None
        assert operation.request_media_type is None
    elif operation.request_model is None:
        # Only the upload has a body that is not JSON.
        assert operation.name == "create_case"
        assert operation.request_media_type == "application/pdf"
    else:
        assert issubclass(operation.request_model, ContractModel)
        assert operation.request_media_type == JSON
        assert operation.query_model is None


def test_story_1_1_only_the_two_file_reads_have_no_response_model() -> None:
    without = {
        operation.name for operation in OPERATIONS if operation.response_model is None
    }

    assert without == {"read_page_thumbnail", "read_document_file"}


@pytest.mark.parametrize("operation", OPERATIONS, ids=OPERATION_IDS)
def test_story_1_1_idempotency_key_fields_are_in_the_path_or_the_request(
    operation: Operation,
) -> None:
    available = set(re.findall(r"{(\w+)}", operation.path))
    if operation.request_model is not None:
        available |= set(operation.request_model.model_fields)

    assert set(operation.idempotency_key) <= available


def test_story_1_1_query_models_carry_the_spine_query_fields() -> None:
    for operation in OPERATIONS:
        expected = SPINE_QUERY_FIELDS.get((operation.owner.value, operation.path))
        if expected is not None:
            assert operation.query_model is not None, operation.name
            assert set(operation.query_model.model_fields) == expected, operation.name


@pytest.mark.parametrize("operation", OPERATIONS, ids=OPERATION_IDS)
def test_story_1_1_paths_are_well_formed(operation: Operation) -> None:
    assert re.fullmatch(r"(/([a-z]+(-[a-z]+)*|{[a-z_]+}))+", operation.path)


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
