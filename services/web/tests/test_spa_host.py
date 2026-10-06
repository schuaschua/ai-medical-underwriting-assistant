"""Story 1.3: the SPA is served from the same origin, with security headers."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts.errors import ErrorBody
from web.adapters.http.app import create_app
from web.settings import Settings


def test_story_1_3_root_serves_the_spa_html(
    client: TestClient, index_html: str
) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == index_html
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/triage", "/cases/123/audit", "/index.html"])
def test_story_1_3_deep_link_serves_the_spa_html(
    client: TestClient, index_html: str, path: str
) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.text == index_html


def test_story_1_3_built_assets_are_served(client: TestClient) -> None:
    response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_story_1_3_missing_asset_is_404_not_the_spa_html(client: TestClient) -> None:
    response = client.get("/assets/missing.js")

    assert response.status_code == 404
    assert ErrorBody.model_validate(response.json()).error.code == "not_found"


@pytest.mark.parametrize(
    "path",
    [
        "/..%2foutside.txt",
        "/assets/..%2f..%2foutside.txt",
        "/%2e%2e/outside.txt",
        "/assets/%2e%2e/%2e%2e/outside.txt",
    ],
)
def test_story_1_3_a_path_outside_the_spa_folder_is_never_served(
    client: TestClient, index_html: str, path: str
) -> None:
    response = client.get(path)

    # Either "no such asset" or the SPA's own page, never the file's content.
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert response.text == index_html
    else:
        assert ErrorBody.model_validate(response.json()).error.code == "not_found"


@pytest.mark.parametrize(
    "path",
    ["/%00", "/assets/%00.js", "/a%00b/c", "/" + "a" * 5000, "/assets/" + "a" * 5000],
)
def test_story_1_3_a_path_the_file_system_cannot_look_up_is_404(
    client: TestClient, path: str
) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert ErrorBody.model_validate(response.json()).error.code == "not_found"


@pytest.mark.parametrize("path", ["/cases/report.v2", "/users/jane.doe", "/v1.0"])
def test_story_1_3_a_client_route_with_a_dot_serves_the_spa_html(
    client: TestClient, index_html: str, path: str
) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.text == index_html


@pytest.mark.parametrize("path", ["/", "/triage", "/assets/app.js"])
def test_story_1_3_spa_routes_answer_head(client: TestClient, path: str) -> None:
    response = client.head(path)

    assert response.status_code == 200
    assert response.content == b""
    assert int(response.headers["content-length"]) > 0


def test_story_1_3_hashed_assets_are_cached_for_a_long_time(
    client: TestClient,
) -> None:
    response = client.get("/assets/app.js")

    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("path", ["/", "/index.html", "/triage"])
def test_story_1_3_the_entry_page_is_revalidated_on_every_visit(
    client: TestClient, path: str
) -> None:
    assert client.get(path).headers["cache-control"] == "no-cache"


@pytest.mark.parametrize(
    "path", ["/", "/triage", "/assets/app.js", "/api/health", "/api/me", "/api/nope"]
)
def test_story_1_3_every_response_carries_the_security_headers(
    client: TestClient, path: str
) -> None:
    headers = client.get(path).headers

    assert headers["strict-transport-security"].startswith("max-age=")
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "same-origin"
    assert headers["x-frame-options"] == "DENY"
    policy = headers["content-security-policy"]
    assert {part.strip() for part in policy.split(";")} == {
        "default-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
    }
    # Self only: no inline scripts, no eval, no other origin.
    assert "unsafe" not in policy
    assert "http" not in policy
    assert "*" not in policy


@pytest.mark.parametrize("path", ["/", "/api/health", "/api/me"])
def test_story_1_3_cross_origin_request_gets_no_cors_headers(
    client: TestClient, path: str
) -> None:
    origin = {"Origin": "https://elsewhere.example", "X-Demo-Role": "customer"}

    responses = [
        client.get(path, headers=origin),
        client.options(
            path, headers={**origin, "Access-Control-Request-Method": "GET"}
        ),
    ]

    for response in responses:
        assert not [
            name for name in response.headers if name.startswith("access-control-")
        ]


def test_story_1_3_unbuilt_spa_is_404_in_the_error_shape(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(spa_dir=tmp_path / "absent")))

    response = client.get("/")

    assert response.status_code == 404
    assert ErrorBody.model_validate(response.json()).error.code == "not_found"
    # The API does not depend on the SPA being there.
    assert client.get("/api/health").status_code == 200
