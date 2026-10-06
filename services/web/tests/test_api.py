"""Story 1.3: the `/api` routes, the role header and the error shape."""

import logging
import re
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.testclient import TestClient

from contracts.enums import DemoRole
from contracts.errors import NO_TRACE_ID, ErrorBody
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


def test_story_1_3_health_answers_without_a_role_header(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("role", ["customer", "underwriter"])
def test_story_1_3_me_echoes_the_role(client: TestClient, role: str) -> None:
    response = client.get("/api/me", headers={"X-Demo-Role": role})

    assert response.status_code == 200
    assert response.json() == {"role": role}


def test_story_1_3_missing_role_is_400_invalid_role_with_a_trace_id(
    client: TestClient,
) -> None:
    response = client.get("/api/me", headers={"traceparent": TRACEPARENT})

    assert response.status_code == 400
    code, _, trace_id = error_of(response.json())
    assert code == "invalid_role"
    assert trace_id == TRACE_ID


def test_story_1_3_error_without_any_trace_carries_the_no_trace_id(
    client: TestClient,
) -> None:
    response = client.get("/api/me", headers={"traceparent": "not-a-traceparent"})

    assert error_of(response.json())[2] == NO_TRACE_ID


@pytest.mark.parametrize("value", ["admin", "", "Customer", "customer,underwriter"])
def test_story_1_3_unknown_role_is_400_invalid_role(
    client: TestClient, value: str
) -> None:
    response = client.get("/api/me", headers={"X-Demo-Role": value})

    assert response.status_code == 400
    code, message, _ = error_of(response.json())
    assert code == "invalid_role"
    # security rule 31: the rejected value itself is not echoed back.
    if value:
        assert value not in message
        assert value not in response.text


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


def test_story_1_3_right_role_for_a_route_is_let_through() -> None:
    client = TestClient(role_probe_app())

    response = client.get(
        "/api/underwriter-only", headers={"X-Demo-Role": "underwriter"}
    )

    assert response.status_code == 200
    assert response.json() == {"role": "underwriter"}


def test_story_1_3_role_route_without_a_header_is_400_before_the_role_check() -> None:
    client = TestClient(role_probe_app())

    response = client.get("/api/underwriter-only")

    assert response.status_code == 400
    assert error_of(response.json())[0] == "invalid_role"


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_story_1_3_unknown_api_path_is_404_in_the_error_shape(
    client: TestClient, method: str
) -> None:
    response = client.request(method, "/api/nope")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert error_of(response.json())[0] == "not_found"


def test_story_1_3_unhandled_error_is_a_plain_500_with_security_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(role_probe_app(), raise_server_exceptions=False)

    response = client.get("/api/broken", headers={"traceparent": TRACEPARENT})

    assert response.status_code == 500
    code, _, trace_id = error_of(response.json())
    assert code == "internal_error"
    assert trace_id == TRACE_ID
    # security rule 26: no stack trace, SQL or path in the body...
    assert not re.search(r"secret|SELECT|/srv|Traceback", response.text)
    # ...rule 31: nor the error's own message in the logs...
    assert "secret detail" not in caplog.text
    assert "RuntimeError" in caplog.text
    # ...and rule 25: the headers are on this response too.
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in response.headers


def test_story_1_3_invalid_input_is_422_without_echoing_the_input() -> None:
    client = TestClient(role_probe_app())

    response = client.get("/api/number/not-a-number")

    assert response.status_code == 422
    assert error_of(response.json())[0] == "validation_failed"
    assert "not-a-number" not in response.text


def test_story_1_3_wrong_method_is_answered_in_the_error_shape(
    client: TestClient,
) -> None:
    response = client.post("/")

    assert response.status_code == 422
    assert error_of(response.json())[0] == "validation_failed"


@pytest.mark.parametrize("path", ["/api", "/api/"])
def test_story_1_3_bare_api_path_is_404_in_the_error_shape(
    client: TestClient, path: str
) -> None:
    response = client.get(path, follow_redirects=False)

    assert response.status_code == 404
    assert error_of(response.json())[0] == "not_found"


def test_story_1_3_wrong_method_on_an_api_route_is_404(client: TestClient) -> None:
    response = client.post("/api/me", headers={"X-Demo-Role": "customer"})

    assert response.status_code == 404
    assert error_of(response.json())[0] == "not_found"


def test_story_1_3_health_answers_head(client: TestClient) -> None:
    response = client.head("/api/health")

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/api/health", "/api/me", "/api/nope", "/api"])
def test_story_1_3_api_responses_are_never_cached(
    client: TestClient, path: str
) -> None:
    response = client.get(path, headers={"X-Demo-Role": "customer"})

    assert response.headers["cache-control"] == "no-store"


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


def test_story_1_3_no_interactive_api_pages_are_served(client: TestClient) -> None:
    # Both are client routes like any other: they get the SPA, not FastAPI's pages.
    for path in ("/openapi.json", "/docs", "/redoc"):
        response = client.get(path)
        assert response.headers["content-type"].startswith("text/html")
        assert "swagger" not in response.text.lower()
        assert "openapi" not in response.text.lower()
