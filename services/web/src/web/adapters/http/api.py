"""The `/api` routes: health, the role echo, the upload and the case's lifecycle.

`web` holds no rule of its own about a case (spine AD-2): it hands the upload
to `intake`, asks `workflow` to start the case, and reads progress and the
audit trail from `workflow`.

A route is added to `role_checked`, never to `router` itself, so that it
cannot be reached without a valid `X-Demo-Role` (spine AD-9). A route for one
role only adds `Depends(role_for(RouteGroup.<role>))` of its own.

No route here returns a document file; the uploaded original is never served
by any service (AD-21).
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Request

from contracts.enums import DemoRole
from contracts.errors import DomainError, ErrorCode
from contracts.ids import UUID7_PATTERN
from contracts.models.web import Health, Me, UploadedCase
from contracts.models.workflow import (
    AuditTrail,
    CaseProgress,
    CaseStarted,
    StartCaseRequest,
)
from contracts.operations import get_operation
from contracts.upload import IDEMPOTENCY_KEY_HEADER, parse_idempotency_key
from web.adapters.dapr import ServiceClient
from web.adapters.http.errors import API_PREFIX
from web.adapters.http.roles import role_for
from web.adapters.http.upload import checked_pdf, declared_length
from web.domain.roles import RouteGroup
from web.settings import HEALTH_PATH

# Where the app factory keeps the client for calls to other services.
SERVICES_STATE = "services"

any_role = role_for(RouteGroup.ANY_ROLE)
customer_only = role_for(RouteGroup.CUSTOMER)
underwriter_only = role_for(RouteGroup.UNDERWRITER)

START_OPTIONS_MESSAGE = "Start options are not open to your role."

# An id that is not a UUIDv7 is refused with 422 before any service is called.
CaseIdPath = Annotated[str, Path(pattern=UUID7_PATTERN)]


def _services(request: Request) -> ServiceClient:
    services: ServiceClient = getattr(request.app.state, SERVICES_STATE)
    return services


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


# AD-2: the second half of an upload. `workflow` makes it idempotent on the
# case id, so a caller that got no answer simply asks again. Either role may
# start a case; how it runs (classifier, retriever configurations, where it
# stops, the bake-off run it belongs to) is the underwriter's to say.
@role_checked.post(get_operation("start_case").path)
async def start_case(
    case_id: CaseIdPath,
    request: Request,
    role: Annotated[DemoRole, Depends(any_role)],
    # Every option is optional, and so is the body itself.
    options: Annotated[StartCaseRequest | None, Body()] = None,
) -> CaseStarted:
    if (
        role is not DemoRole.UNDERWRITER
        and options is not None
        and options.model_dump(exclude_none=True)
    ):
        raise DomainError(ErrorCode.ROLE_NOT_ALLOWED, START_OPTIONS_MESSAGE)
    return await _services(request).start_case(
        case_id, options, traceparent=request.headers.get("traceparent")
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


router = APIRouter(prefix=API_PREFIX)


# The one route without a role check: the platform's probes send no header.
@router.api_route(HEALTH_PATH.removeprefix(API_PREFIX), methods=["GET", "HEAD"])
async def health() -> Health:
    return Health()


router.include_router(role_checked)
