"""Story 1.3: the `/api` routes, the role header and the error shape."""

import logging
import re
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.testclient import TestClient

from contracts.enums import DemoRole
from contracts.errors import ErrorBody
from web.adapters.http import spa
from web.adapters.http.app import create_app
from web.adapters.http.errors import install_error_handlers, is_api_path
from web.adapters.http.middleware import (
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from web.adapters.http.roles import role_for
from web.domain.roles import RouteGroup
from web.settings import Settings

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


def error_of(response_json: object) -> tuple[str, str, str]:
    """Parse a body with the contracts model, so any other shape fails the test."""
    detail = ErrorBody.model_validate(response_json).error
    return detail.code.value, detail.message, detail.trace_id


def test_story_1_3_missing_role_is_400_invalid_role_with_a_trace_id(
    client: TestClient,
) -> None:
    response = client.get("/api/me", headers={"traceparent": TRACEPARENT})

    assert response.status_code == 400
    code, _, trace_id = error_of(response.json())
    assert code == "invalid_role"
    assert trace_id == TRACE_ID


def role_probe_app() -> FastAPI:
    """An app with one underwriter-only route, built from the service's own parts.

    No underwriter-only screen exists yet (triage is story 1.11), so the check
    is exercised through the dependency every such route will use.
    """
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/api/underwriter-only")
    async def underwriter_only(
        role: Annotated[DemoRole, Depends(role_for(RouteGroup.UNDERWRITER))],
    ) -> dict[str, str]:
        return {"role": role.value}

    @app.get("/api/customer-only")
    async def customer_only(
        role: Annotated[DemoRole, Depends(role_for(RouteGroup.CUSTOMER))],
    ) -> dict[str, str]:
        return {"role": role.value}

    @app.get("/api/broken")
    async def broken() -> None:
        raise RuntimeError("secret detail /srv/app/file.py SELECT 1")

    @app.get("/api/number/{value}")
    async def number(value: int) -> dict[str, int]:
        return {"value": value}

    return app


def test_story_1_3_wrong_role_for_a_route_is_403_role_not_allowed() -> None:
    client = TestClient(role_probe_app())

    response = client.get("/api/underwriter-only", headers={"X-Demo-Role": "customer"})

    assert response.status_code == 403
    assert error_of(response.json())[0] == "role_not_allowed"
    assert (
        client.get(
            "/api/customer-only", headers={"X-Demo-Role": "underwriter"}
        ).status_code
        == 403
    )


def test_story_1_3_invalid_input_is_422_without_echoing_the_input() -> None:
    client = TestClient(role_probe_app())

    response = client.get("/api/number/not-a-number")

    assert response.status_code == 422
    assert error_of(response.json())[0] == "validation_failed"
    assert "not-a-number" not in response.text


# GET and HEAD of health share one function, which only the schema builder minds.
@pytest.mark.filterwarnings("ignore:Duplicate Operation ID")
def test_story_1_3_every_api_route_but_health_checks_the_role(
    settings: Settings,
) -> None:
    app = create_app(settings)
    client = TestClient(app)
    # Every route the app really serves, with the methods it takes.
    paths = get_openapi(title="web", version="0", routes=app.routes)["paths"]
    operations = [
        (method.upper(), path)
        for path, methods in paths.items()
        for method in methods
        if is_api_path(path)
    ]

    without_check = [
        (method, path)
        for method, path in operations
        # Any value does for a path parameter: the role is checked first.
        if client.request(method, re.sub(r"\{[^}]+\}", "x", path)).status_code != 400
    ]

    # The walk really sees the API: a role-checked route and the exemption.
    assert ("GET", "/api/me") in operations
    assert sorted(without_check) == [("GET", "/api/health"), ("HEAD", "/api/health")]


def test_story_1_3_unhandled_error_in_the_real_app_is_a_plain_500(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_response(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret detail SELECT 1")

    monkeypatch.setattr(spa, "FileResponse", broken_response)
    client = TestClient(create_app(settings), raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR):
        response = client.get("/", headers={"traceparent": TRACEPARENT})

    assert response.status_code == 500
    code, _, trace_id = error_of(response.json())
    assert (code, trace_id) == ("internal_error", TRACE_ID)
    assert "secret" not in response.text
    assert response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    # The log line: type, trace id and code location, without the message.
    (record,) = [r for r in caplog.records if r.name.startswith("web.")]
    line = record.getMessage()
    assert "type=RuntimeError" in line
    assert f"trace_id={TRACE_ID}" in line
    assert re.search(r"test_api\.py:\d+ in broken_response", line)
    assert "spa.py" in line
    assert "secret detail" not in line
    assert "SELECT" not in line
