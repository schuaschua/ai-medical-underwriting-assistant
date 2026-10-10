"""The role check every `/api` route except health depends on (spine AD-9)."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Header

from contracts.enums import DemoRole
from web.domain.roles import RouteGroup, parse_role, require_role

ROLE_HEADER = "X-Demo-Role"


def role_for(group: RouteGroup) -> Callable[..., DemoRole]:
    """Build the dependency for one route group: it returns the caller's demo role.

    A missing or unknown header raises `invalid_role` (400); a role the group
    is not open to raises `role_not_allowed` (403).
    """

    def dependency(
        x_demo_role: Annotated[str | None, Header(alias=ROLE_HEADER)] = None,
    ) -> DemoRole:
        return require_role(parse_role(x_demo_role), group)

    return dependency
