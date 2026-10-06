"""The `/api` routes that exist so far: health and the role echo.

A route is added to `role_checked`, never to `router` itself, so that it
cannot be reached without a valid `X-Demo-Role` (spine AD-9). A route for one
role only adds `Depends(role_for(RouteGroup.<role>))` of its own.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends

from contracts.base import ContractModel
from contracts.enums import DemoRole
from web.adapters.http.errors import API_PREFIX
from web.adapters.http.roles import role_for
from web.domain.roles import RouteGroup
from web.settings import HEALTH_PATH


class Health(ContractModel):
    status: Literal["ok"] = "ok"


class Me(ContractModel):
    """The demo role `web` read from the request, as the server understood it."""

    role: DemoRole


any_role = role_for(RouteGroup.ANY_ROLE)

# Every route here is refused with 400 without a demo role.
role_checked = APIRouter(dependencies=[Depends(any_role)])


@role_checked.get("/me")
async def me(role: Annotated[DemoRole, Depends(any_role)]) -> Me:
    return Me(role=role)


router = APIRouter(prefix=API_PREFIX)


# The one route without a role check: the platform's probes send no header.
@router.api_route(HEALTH_PATH.removeprefix(API_PREFIX), methods=["GET", "HEAD"])
async def health() -> Health:
    return Health()


router.include_router(role_checked)
