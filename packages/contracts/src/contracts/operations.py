"""The operation registry: every row of the spine's Operations table, once.

Paths are on the owning service; `web` exposes the same resources under `/api`.
"""

from dataclasses import dataclass
from enum import StrEnum

from contracts.base import ContractModel
from contracts.enums import Service
from contracts.models import (
    classification,
    extraction,
    intake,
    retrieval,
    verdict,
    workflow,
)

JSON = "application/json"
PDF = "application/pdf"
PNG = "image/png"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"


@dataclass(frozen=True, slots=True)
class Operation:
    name: str
    callers: tuple[Service, ...]
    owner: Service
    method: HttpMethod
    # Path parameters are written `{case_id}`.
    path: str
    # None when the call has no JSON body: a plain read, or the PDF upload.
    request_model: type[ContractModel] | None
    # None when the response is a file (see `response_media_type`).
    response_model: type[ContractModel] | None
    # The fields that make up the idempotency key of AD-6; empty when there is none.
    idempotency_key: tuple[str, ...] = ()
    query_model: type[ContractModel] | None = None
    request_media_type: str | None = None
    response_media_type: str = JSON


# "any reader" in the Operations table: the services the spine's call diagram lets read `intake`.
_INTAKE_READERS = (
    Service.WEB,
    Service.WORKFLOW,
    Service.CLASSIFICATION,
    Service.EXTRACTION,
)
_WEB = (Service.WEB,)
_WORKFLOW = (Service.WORKFLOW,)

OPERATIONS: tuple[Operation, ...] = (
    # intake
    Operation(
        name="create_case",
        callers=_WEB,
        owner=Service.INTAKE,
        method=HttpMethod.POST,
        path="/cases",
        request_model=None,
        response_model=intake.CaseCreated,
        request_media_type=PDF,
    ),
    Operation(
        name="redact_document",
        callers=_WORKFLOW,
        owner=Service.INTAKE,
        method=HttpMethod.POST,
        path="/cases/{case_id}/redaction",
        request_model=intake.RedactionCommand,
        response_model=intake.RedactionResult,
        idempotency_key=("case_id",),
        request_media_type=JSON,
    ),
    Operation(
        name="list_pages",
        callers=_INTAKE_READERS,
        owner=Service.INTAKE,
        method=HttpMethod.GET,
        path="/cases/{case_id}/pages",
        request_model=None,
        response_model=intake.PageList,
    ),
    Operation(
        name="read_page_text",
        callers=_INTAKE_READERS,
        owner=Service.INTAKE,
        method=HttpMethod.GET,
        path="/pages/{page_id}/text",
        request_model=None,
        response_model=intake.PageText,
    ),
    Operation(
        name="read_page_boxes",
        callers=_INTAKE_READERS,
        owner=Service.INTAKE,
        method=HttpMethod.GET,
        path="/pages/{page_id}/boxes",
        request_model=None,
        response_model=intake.PageBoxes,
        query_model=intake.PageBoxesQuery,
    ),
    Operation(
        name="read_page_thumbnail",
        callers=_INTAKE_READERS,
        owner=Service.INTAKE,
        method=HttpMethod.GET,
        path="/pages/{page_id}/thumbnail",
        request_model=None,
        response_model=None,
        response_media_type=PNG,
    ),
    Operation(
        name="read_document_file",
        callers=_INTAKE_READERS,
        owner=Service.INTAKE,
        method=HttpMethod.GET,
        path="/documents/{document_id}/file",
        request_model=None,
        response_model=None,
        response_media_type=PDF,
    ),
    # workflow
    Operation(
        name="start_case",
        callers=_WEB,
        owner=Service.WORKFLOW,
        method=HttpMethod.POST,
        path="/cases/{case_id}/start",
        request_model=workflow.StartCaseRequest,
        response_model=workflow.CaseStarted,
        idempotency_key=("case_id",),
        request_media_type=JSON,
    ),
    Operation(
        name="record_decision",
        callers=_WEB,
        owner=Service.WORKFLOW,
        method=HttpMethod.POST,
        path="/cases/{case_id}/pages/{page_id}/decisions",
        request_model=workflow.DecisionRequest,
        response_model=workflow.DecisionRecorded,
        request_media_type=JSON,
    ),
    Operation(
        name="request_verdict_run",
        callers=_WEB,
        owner=Service.WORKFLOW,
        method=HttpMethod.POST,
        path="/cases/{case_id}/verdict-runs",
        request_model=workflow.VerdictRunRequest,
        response_model=workflow.VerdictRunRequested,
        idempotency_key=("case_id", "retriever_config"),
        request_media_type=JSON,
    ),
    Operation(
        name="read_progress",
        callers=_WEB,
        owner=Service.WORKFLOW,
        method=HttpMethod.GET,
        path="/cases/{case_id}/progress",
        request_model=None,
        response_model=workflow.CaseProgress,
    ),
    Operation(
        name="read_audit_trail",
        callers=_WEB,
        owner=Service.WORKFLOW,
        method=HttpMethod.GET,
        path="/cases/{case_id}/audit",
        request_model=None,
        response_model=workflow.AuditTrail,
    ),
    Operation(
        name="list_pages_by_status",
        callers=_WEB,
        owner=Service.WORKFLOW,
        method=HttpMethod.GET,
        path="/pages",
        request_model=None,
        response_model=workflow.PageQueue,
        query_model=workflow.PageQueueQuery,
    ),
    # classification
    Operation(
        name="classify_page",
        callers=_WORKFLOW,
        owner=Service.CLASSIFICATION,
        method=HttpMethod.POST,
        path="/classifications",
        request_model=classification.ClassifyCommand,
        response_model=classification.ClassificationResult,
        idempotency_key=("case_id", "page_id", "contender"),
        request_media_type=JSON,
    ),
    Operation(
        name="list_classifications",
        callers=_WEB,
        owner=Service.CLASSIFICATION,
        method=HttpMethod.GET,
        path="/cases/{case_id}/classifications",
        request_model=None,
        response_model=classification.ClassificationList,
    ),
    # extraction
    Operation(
        name="extract_facts",
        callers=_WORKFLOW,
        owner=Service.EXTRACTION,
        method=HttpMethod.POST,
        path="/fact-sets",
        request_model=extraction.ExtractFactsCommand,
        response_model=extraction.FactSetResult,
        idempotency_key=("case_id", "page_id"),
        request_media_type=JSON,
    ),
    Operation(
        name="list_facts",
        callers=(Service.WEB, Service.VERDICT),
        owner=Service.EXTRACTION,
        method=HttpMethod.GET,
        path="/cases/{case_id}/facts",
        request_model=None,
        response_model=extraction.FactList,
    ),
    # verdict
    Operation(
        name="run_verdict",
        callers=_WORKFLOW,
        owner=Service.VERDICT,
        method=HttpMethod.POST,
        path="/verdict-runs",
        request_model=verdict.VerdictRunCommand,
        response_model=verdict.VerdictRunResult,
        idempotency_key=("case_id", "retriever_config"),
        request_media_type=JSON,
    ),
    Operation(
        name="list_verdict_runs",
        callers=_WEB,
        owner=Service.VERDICT,
        method=HttpMethod.GET,
        path="/cases/{case_id}/verdict-runs",
        request_model=None,
        response_model=verdict.VerdictRunList,
    ),
    Operation(
        name="list_run_steps",
        callers=_WEB,
        owner=Service.VERDICT,
        method=HttpMethod.GET,
        path="/verdict-runs/{verdict_run_id}/steps",
        request_model=None,
        response_model=verdict.AgentStepList,
    ),
    Operation(
        name="list_case_agent_steps",
        callers=_WEB,
        owner=Service.VERDICT,
        method=HttpMethod.GET,
        path="/cases/{case_id}/agent-steps",
        request_model=None,
        response_model=verdict.AgentStepList,
        query_model=verdict.AgentStepQuery,
    ),
    # retrieval
    Operation(
        name="search_rules",
        callers=(Service.VERDICT, Service.WEB),
        owner=Service.RETRIEVAL,
        method=HttpMethod.POST,
        path="/searches",
        request_model=retrieval.SearchRequest,
        response_model=retrieval.SearchResponse,
        request_media_type=JSON,
    ),
    Operation(
        name="read_rule",
        callers=(Service.VERDICT, Service.WEB),
        owner=Service.RETRIEVAL,
        method=HttpMethod.GET,
        path="/rules/{rule_id}",
        request_model=None,
        response_model=retrieval.RuleText,
        query_model=retrieval.RuleReadQuery,
    ),
)

_BY_NAME = {operation.name: operation for operation in OPERATIONS}


def get_operation(name: str) -> Operation:
    """Return the operation registered under `name`; an unknown name raises `KeyError`."""
    return _BY_NAME[name]
