"""The `/api` routes: health, the role echo, the upload, the case's lifecycle, the case list, decisions, triage and the result view's reads.

`web` holds no rule of its own about a case (spine AD-2): it hands the upload
to `intake`, asks `workflow` to start the case, reads progress, the audit
trail and the case list from `workflow` and the classifications from
`classification`, and passes a start and a person's decision about a page on
to `workflow` with the request's demo role as the actor (AD-9, AD-10). The
underwriter's triage queue is `workflow`'s queue joined with
`classification`'s readings (`adapters/http/triage.py`).

A route is added to `role_checked`, never to `router` itself, so that it
cannot be reached without a valid `X-Demo-Role` (spine AD-9). A route for one
role only adds `Depends(role_for(RouteGroup.<role>))` of its own.

The underwriter's result view (story 2.7) is read through six pass-through
routes, each one operation of the service that owns the data: the facts
(`extraction`), the verdict runs (`verdict`), a rule's text (`retrieval`),
and the page list, a page's word boxes and the document file (`intake`).

The agent's log (story 2.8) is read through two more, both `verdict`'s: the
steps of a run, and the steps of a case. `web` never writes to that log.

The uploaded original is never served by any service (AD-21). The one
document file served is the redacted PDF, and the one image a page's
thumbnail, which `intake` makes from that PDF.
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Request, Response
from pydantic import ValidationError

from contracts.enums import DemoRole
from contracts.errors import DomainError, ErrorCode
from contracts.ids import UUID7_PATTERN
from contracts.models.classification import ClassificationList
from contracts.models.extraction import FactList
from contracts.models.intake import PageBoxes, PageBoxesQuery, PageList
from contracts.models.retrieval import RuleText
from contracts.models.verdict import (
    AgentStepList,
    AgentStepQuery,
    RunStepQuery,
    VerdictRunList,
)
from contracts.models.web import (
    Health,
    Me,
    PageDecisionRequest,
    TriageQueue,
    UploadedCase,
)
from contracts.models.workflow import (
    AuditTrail,
    CaseList,
    CaseProgress,
    CaseStarted,
    DecisionRecorded,
    DecisionRequest,
    StartCaseOptions,
    StartCaseRequest,
)
from contracts.operations import get_operation
from contracts.rules import RULE_ID_PATTERN
from contracts.upload import IDEMPOTENCY_KEY_HEADER, parse_idempotency_key
from web.adapters.dapr import ServiceClient
from web.adapters.http.errors import API_PREFIX, INVALID_REQUEST_MESSAGE
from web.adapters.http.roles import role_for
from web.adapters.http.triage import TriageReader
from web.adapters.http.upload import checked_pdf, declared_length
from web.domain.roles import RouteGroup
from web.settings import HEALTH_PATH

# Where the app factory keeps the client for calls to other services.
SERVICES_STATE = "services"
# Where it keeps the reader of the triage queue.
TRIAGE_STATE = "triage"
# The underwriter's queue. It is `web`'s own resource: no one service owns it.
TRIAGE_PATH = "/triage"

any_role = role_for(RouteGroup.ANY_ROLE)
customer_only = role_for(RouteGroup.CUSTOMER)
underwriter_only = role_for(RouteGroup.UNDERWRITER)

START_OPTIONS_MESSAGE = "Start options are not open to your role."

# An id that is not a UUIDv7 is refused with 422 before any service is called.
CaseIdPath = Annotated[str, Path(pattern=UUID7_PATTERN)]
PageIdPath = Annotated[str, Path(pattern=UUID7_PATTERN)]
DocumentIdPath = Annotated[str, Path(pattern=UUID7_PATTERN)]
RunIdPath = Annotated[str, Path(pattern=UUID7_PATTERN)]
# A rule is named as the manual prints it (AD-12), or the call is not made.
RuleIdPath = Annotated[str, Path(pattern=rf"^{RULE_ID_PATTERN}$")]
OffsetQuery = Annotated[int | None, Query(ge=0)]


def _services(request: Request) -> ServiceClient:
    services: ServiceClient = getattr(request.app.state, SERVICES_STATE)
    return services


def _triage(request: Request) -> TriageReader:
    triage: TriageReader = getattr(request.app.state, TRIAGE_STATE)
    return triage


# Every route here is refused with 400 without a demo role.
role_checked = APIRouter(dependencies=[Depends(any_role)])


@role_checked.get("/me")
async def me(role: Annotated[DemoRole, Depends(any_role)]) -> Me:
    return Me(role=role)


# The same resource path as on the owning service (spine, Operations).
@role_checked.post(get_operation("create_case").path, status_code=201)
async def upload_case(
    request: Request, _role: Annotated[DemoRole, Depends(customer_only)]
) -> UploadedCase:
    # The role is checked before a byte of the body is read, and so is the
    # key that makes the upload safe to retry.
    idempotency_key = parse_idempotency_key(request.headers.get(IDEMPOTENCY_KEY_HEADER))
    content_length = declared_length(request)
    pdf = await checked_pdf(request, content_length)
    created = await _services(request).create_case(
        pdf,
        content_length=content_length,
        idempotency_key=idempotency_key,
        traceparent=request.headers.get("traceparent"),
    )
    # The case is not started here: the caller asks for that next, with the
    # options it wants, and may ask again if the first try fails.
    return UploadedCase(case_id=created.case_id, document_id=created.document_id)


# The underwriter's list of cases, newest first: how a case, also a finished
# one, is found and its audit trail opened. For the underwriter only (AD-9).
# Which cases are listed, and how many, is `workflow`'s to say. The same
# resource path as on the owning service; the upload above is its POST.
@role_checked.get(get_operation("list_cases").path)
async def list_cases(
    request: Request, _role: Annotated[DemoRole, Depends(underwriter_only)]
) -> CaseList:
    return await _services(request).list_cases(
        traceparent=request.headers.get("traceparent")
    )


# AD-2: the second half of an upload. `workflow` makes it idempotent on the
# case id, so a caller that got no answer simply asks again. Either role may
# start a case; how it runs (classifier, retriever configurations, where it
# stops, the bake-off run it belongs to) is the underwriter's to say.
@role_checked.post(get_operation("start_case").path)
async def start_case(
    case_id: CaseIdPath,
    request: Request,
    role: Annotated[DemoRole, Depends(any_role)],
    # Every option is optional, and so is the body itself. The body names
    # no actor: a browser cannot say who started the case.
    options: Annotated[StartCaseOptions | None, Body()] = None,
) -> CaseStarted:
    wanted = options.model_dump() if options is not None else {}
    if role is not DemoRole.UNDERWRITER and any(
        value is not None for value in wanted.values()
    ):
        raise DomainError(ErrorCode.ROLE_NOT_ALLOWED, START_OPTIONS_MESSAGE)
    return await _services(request).start_case(
        case_id,
        # AD-9: the actor is the demo role the request was checked for, as
        # for a decision.
        StartCaseRequest(**wanted, actor=role.value),
        traceparent=request.headers.get("traceparent"),
    )


# AD-19: the SPA reads progress by polling this route.
@role_checked.get(get_operation("read_progress").path)
async def read_progress(case_id: CaseIdPath, request: Request) -> CaseProgress:
    return await _services(request).read_progress(
        case_id, traceparent=request.headers.get("traceparent")
    )


@role_checked.get(get_operation("read_audit_trail").path)
async def read_audit_trail(
    case_id: CaseIdPath,
    request: Request,
    _role: Annotated[DemoRole, Depends(underwriter_only)],
) -> AuditTrail:
    return await _services(request).read_audit_trail(
        case_id, traceparent=request.headers.get("traceparent")
    )


# What the classifier said of each page of a case: the page type and the
# confidence the customer's prompt names (story 1.10), and later the reason
# the triage queue shows. Read from `classification`, which owns it.
@role_checked.get(get_operation("list_classifications").path)
async def list_classifications(
    case_id: CaseIdPath, request: Request
) -> ClassificationList:
    return await _services(request).list_classifications(
        case_id, traceparent=request.headers.get("traceparent")
    )


# AD-10: keep, discard, accept or deny one page. Open to both roles, and
# `web` adds no rule: the body names the decision only, the actor is the
# demo role the request was checked for (AD-9), and `workflow` decides
# whether that role may make that decision about that page.
@role_checked.post(get_operation("record_decision").path)
async def record_decision(
    case_id: CaseIdPath,
    page_id: PageIdPath,
    decision: PageDecisionRequest,
    request: Request,
    role: Annotated[DemoRole, Depends(any_role)],
) -> DecisionRecorded:
    return await _services(request).record_decision(
        case_id,
        page_id,
        DecisionRequest(decision=decision.decision, actor=role.value),
        traceparent=request.headers.get("traceparent"),
    )


# The pages that wait for the underwriter, across cases, each with what the
# classifier read on it. For the underwriter only (AD-9). Which pages are
# listed, and in what order, is `workflow`'s to say; the decision about one
# goes through the decision route above.
@role_checked.get(TRIAGE_PATH)
async def read_triage_queue(
    request: Request, _role: Annotated[DemoRole, Depends(underwriter_only)]
) -> TriageQueue:
    return await _triage(request).read(traceparent=request.headers.get("traceparent"))


# A page's thumbnail, for either role: the picture `intake` made of the
# redacted page (AD-21). The same resource path as on the owning service.
@role_checked.get(get_operation("read_page_thumbnail").path)
async def read_page_thumbnail(page_id: PageIdPath, request: Request) -> Response:
    content = await _services(request).read_page_thumbnail(
        page_id, traceparent=request.headers.get("traceparent")
    )
    return Response(
        content, media_type=get_operation("read_page_thumbnail").response_media_type
    )


# --- The underwriter's result view (story 2.7) ---------------------------------------
#
# Six reads, for the underwriter only (AD-9), each the same resource path as
# on the service that owns it. `web` passes the answer on as it is and works
# nothing out of it (AD-2): no verdict, loading or verification is its own.


@role_checked.get(get_operation("list_facts").path)
async def list_facts(
    case_id: CaseIdPath,
    request: Request,
    _role: Annotated[DemoRole, Depends(underwriter_only)],
) -> FactList:
    return await _services(request).list_facts(
        case_id, traceparent=request.headers.get("traceparent")
    )


# Every run carries the label "AI suggestion, not a decision" (AD-10); the
# screen shows it from this payload.
@role_checked.get(get_operation("list_verdict_runs").path)
async def list_verdict_runs(
    case_id: CaseIdPath,
    request: Request,
    _role: Annotated[DemoRole, Depends(underwriter_only)],
) -> VerdictRunList:
    return await _services(request).list_verdict_runs(
        case_id, traceparent=request.headers.get("traceparent")
    )


# The manual's text of one rule, as the one-rule chunk holds it.
@role_checked.get(get_operation("read_rule").path)
async def read_rule(
    rule_id: RuleIdPath,
    request: Request,
    _role: Annotated[DemoRole, Depends(underwriter_only)],
) -> RuleText:
    return await _services(request).read_rule(
        rule_id, traceparent=request.headers.get("traceparent")
    )


# Which document a case has and the number of each page; empty until
# redaction is done.
@role_checked.get(get_operation("list_pages").path)
async def list_pages(
    case_id: CaseIdPath,
    request: Request,
    _role: Annotated[DemoRole, Depends(underwriter_only)],
) -> PageList:
    return await _services(request).list_pages(
        case_id, traceparent=request.headers.get("traceparent")
    )


# AD-14: where the words of an offset range sit on a page, for the highlight
# of a fact's quote. The offsets are the fact's own; `intake` finds the words.
@role_checked.get(get_operation("read_page_boxes").path)
async def read_page_boxes(
    page_id: PageIdPath,
    request: Request,
    _role: Annotated[DemoRole, Depends(underwriter_only)],
    quote_start: OffsetQuery = None,
    quote_end: OffsetQuery = None,
) -> PageBoxes:
    try:
        query = PageBoxesQuery(quote_start=quote_start, quote_end=quote_end)
    except ValidationError:
        # Half a range, or one that runs backwards: `intake` is not asked.
        raise DomainError(
            ErrorCode.VALIDATION_FAILED, INVALID_REQUEST_MESSAGE
        ) from None
    return await _services(request).read_page_boxes(
        page_id, query, traceparent=request.headers.get("traceparent")
    )


# AD-21: the redacted PDF, the only document file any route returns. `intake`
# answers `not_redacted` until there is one, and holds no route for an original.
@role_checked.get(get_operation("read_document_file").path)
async def read_document_file(
    document_id: DocumentIdPath,
    request: Request,
    _role: Annotated[DemoRole, Depends(underwriter_only)],
) -> Response:
    content = await _services(request).read_document_file(
        document_id, traceparent=request.headers.get("traceparent")
    )
    return Response(
        content, media_type=get_operation("read_document_file").response_media_type
    )


# --- The agent's log (story 2.8) ------------------------------------------------------
#
# AD-15: the searches and rule reads behind a suggestion, for the underwriter
# only (AD-9). Two reads of `verdict`'s log, passed on as they are: the
# narrowing by tool and rule and the cursor are `verdict`'s work. The query
# is checked here against the contract first, so a value that is not a tool,
# not a well-formed rule id or not a cursor is 422 and `verdict` is not asked.


@role_checked.get(get_operation("list_run_steps").path)
async def list_run_steps(
    verdict_run_id: RunIdPath,
    query: Annotated[RunStepQuery, Query()],
    request: Request,
    _role: Annotated[DemoRole, Depends(underwriter_only)],
) -> AgentStepList:
    return await _services(request).list_run_steps(
        verdict_run_id, query, traceparent=request.headers.get("traceparent")
    )


@role_checked.get(get_operation("list_case_agent_steps").path)
async def list_case_agent_steps(
    case_id: CaseIdPath,
    query: Annotated[AgentStepQuery, Query()],
    request: Request,
    _role: Annotated[DemoRole, Depends(underwriter_only)],
) -> AgentStepList:
    return await _services(request).list_case_agent_steps(
        case_id, query, traceparent=request.headers.get("traceparent")
    )


router = APIRouter(prefix=API_PREFIX)


# The one route without a role check: the platform's probes send no header.
@router.api_route(HEALTH_PATH.removeprefix(API_PREFIX), methods=["GET", "HEAD"])
async def health() -> Health:
    return Health()


router.include_router(role_checked)
