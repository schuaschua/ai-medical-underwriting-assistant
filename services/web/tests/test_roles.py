"""Story 1.3: the role table in the domain package."""

import pytest

from contracts.enums import DemoRole
from contracts.errors import DomainError, ErrorCode
from web.domain.roles import ALLOWED_ROLES, RouteGroup, parse_role, require_role


def test_story_1_3_a_role_is_parsed_strictly_and_checked_against_the_role_table() -> (
    None
):
    for role in DemoRole:
        assert parse_role(role.value) is role
    for value in (None, "", "admin", " customer", "CUSTOMER"):
        with pytest.raises(DomainError) as raised:
            parse_role(value)
        assert raised.value.code is ErrorCode.INVALID_ROLE
        assert raised.value.http_status == 400

    table = [
        (DemoRole.CUSTOMER, RouteGroup.ANY_ROLE, True),
        (DemoRole.UNDERWRITER, RouteGroup.ANY_ROLE, True),
        (DemoRole.CUSTOMER, RouteGroup.CUSTOMER, True),
        (DemoRole.UNDERWRITER, RouteGroup.CUSTOMER, False),
        (DemoRole.CUSTOMER, RouteGroup.UNDERWRITER, False),
        (DemoRole.UNDERWRITER, RouteGroup.UNDERWRITER, True),
    ]
    assert set(ALLOWED_ROLES) == set(RouteGroup)
    for role, group, allowed in table:
        if allowed:
            assert require_role(role, group) is role
            continue
        with pytest.raises(DomainError) as raised:
            require_role(role, group)
        assert raised.value.code is ErrorCode.ROLE_NOT_ALLOWED
        assert raised.value.http_status == 403
