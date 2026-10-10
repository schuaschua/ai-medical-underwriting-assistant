"""Which demo role may use which group of routes (spine AD-9)."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from contracts.enums import DemoRole
from contracts.errors import DomainError, ErrorCode


class RouteGroup(StrEnum):
    """A set of routes that share one role requirement."""

    ANY_ROLE = "any_role"
    CUSTOMER = "customer"
    UNDERWRITER = "underwriter"


# The one table of role rights. A route names its group; nothing else decides access.
ALLOWED_ROLES: Mapping[RouteGroup, frozenset[DemoRole]] = MappingProxyType(
    {
        RouteGroup.ANY_ROLE: frozenset(DemoRole),
        RouteGroup.CUSTOMER: frozenset({DemoRole.CUSTOMER}),
        RouteGroup.UNDERWRITER: frozenset({DemoRole.UNDERWRITER}),
    }
)


def parse_role(value: str | None) -> DemoRole:
    """Turn the `X-Demo-Role` header value into a demo role, or raise `invalid_role`."""
    if value is None:
        raise DomainError(ErrorCode.INVALID_ROLE, "The X-Demo-Role header is missing.")
    try:
        return DemoRole(value)
    except ValueError:
        # The value itself is left out of the message and the logs (security rule 31).
        raise DomainError(
            ErrorCode.INVALID_ROLE, "The X-Demo-Role header is not a demo role."
        ) from None


def require_role(role: DemoRole, group: RouteGroup) -> DemoRole:
    """Return the role if the route group is open to it, or raise `role_not_allowed`."""
    if role not in ALLOWED_ROLES[group]:
        raise DomainError(
            ErrorCode.ROLE_NOT_ALLOWED, "This action is not open to your role."
        )
    return role
