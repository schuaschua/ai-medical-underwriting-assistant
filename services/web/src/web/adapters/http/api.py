"""The `/api` routes: health, the role echo and the upload.

A route is added to `role_checked`, never to `router` itself, so that it
cannot be reached without a valid `X-Demo-Role` (spine AD-9). A route for one
role only adds `Depends(role_for(RouteGroup.<role>))` of its own.

No route here returns a document file; the uploaded original is never served
by any service (AD-21).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from contracts.enums import CaseStatus, DemoRole
from contracts.models.web import Health, Me, UploadedCase
from contracts.operations import get_operation
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
    # The role is checked before a byte of the body is read.
    content_length = declared_length(request)
    pdf = await checked_pdf(request, content_length)
    services: ServiceClient = getattr(request.app.state, SERVICES_STATE)
    created = await services.create_case(
        pdf,
        content_length=content_length,
        traceparent=request.headers.get("traceparent"),
    )
    # `workflow` owns the case status and does not exist until story 1.6. Its
    # lifecycle reports a started case as `running`, so that is what is shown.
    return UploadedCase(
        case_id=created.case_id,
        document_id=created.document_id,
        status=CaseStatus.RUNNING,
    )


router = APIRouter(prefix=API_PREFIX)


# The one route without a role check: the platform's probes send no header.
@router.api_route(HEALTH_PATH.removeprefix(API_PREFIX), methods=["GET", "HEAD"])
async def health() -> Health:
    return Health()


router.include_router(role_checked)
