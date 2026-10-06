"""Story 1.3: the role table in the domain package."""

import pytest

from contracts.enums import DemoRole
from contracts.errors import DomainError, ErrorCode
from web.domain.roles import ALLOWED_ROLES, RouteGroup, parse_role, require_role


def test_story_1_3_every_route_group_has_an_entry_in_the_role_table() -> None:
    assert set(ALLOWED_ROLES) == set(RouteGroup)


@pytest.mark.parametrize("role", list(DemoRole))
def test_story_1_3_parse_role_accepts_each_demo_role(role: DemoRole) -> None:
    assert parse_role(role.value) is role


@pytest.mark.parametrize("value", [None, "", "admin", " customer", "CUSTOMER"])
def test_story_1_3_parse_role_rejects_anything_else(value: str | None) -> None:
    with pytest.raises(DomainError) as raised:
        parse_role(value)

    assert raised.value.code is ErrorCode.INVALID_ROLE
    assert raised.value.http_status == 400


@pytest.mark.parametrize(
    ("role", "group", "allowed"),
    [
        (DemoRole.CUSTOMER, RouteGroup.ANY_ROLE, True),
        (DemoRole.UNDERWRITER, RouteGroup.ANY_ROLE, True),
        (DemoRole.CUSTOMER, RouteGroup.CUSTOMER, True),
        (DemoRole.UNDERWRITER, RouteGroup.CUSTOMER, False),
        (DemoRole.CUSTOMER, RouteGroup.UNDERWRITER, False),
        (DemoRole.UNDERWRITER, RouteGroup.UNDERWRITER, True),
    ],
)
def test_story_1_3_require_role_follows_the_role_table(
    role: DemoRole, group: RouteGroup, allowed: bool
) -> None:
    if allowed:
        assert require_role(role, group) is role
        return
    with pytest.raises(DomainError) as raised:
        require_role(role, group)
    assert raised.value.code is ErrorCode.ROLE_NOT_ALLOWED
    assert raised.value.http_status == 403
