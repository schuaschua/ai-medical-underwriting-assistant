"""Story 1.3: the SPA is served from the same origin, with security headers."""

from fastapi.testclient import TestClient

from contracts.errors import ErrorBody


def test_story_1_3_deep_link_serves_the_spa_html(
    client: TestClient, index_html: str
) -> None:
    for path in ["/triage", "/cases/123/audit", "/index.html"]:
        response = client.get(path)

        assert response.status_code == 200
        assert response.text == index_html


def test_story_1_3_a_path_outside_the_spa_folder_is_never_served(
    client: TestClient, index_html: str
) -> None:
    for path in [
        "/..%2foutside.txt",
        "/assets/..%2f..%2foutside.txt",
        "/%2e%2e/outside.txt",
        "/assets/%2e%2e/%2e%2e/outside.txt",
    ]:
        response = client.get(path)

        # Either "no such asset" or the SPA's own page, never the file's content.
        assert response.status_code in (200, 404)
        if response.status_code == 200:
            assert response.text == index_html
        else:
            assert ErrorBody.model_validate(response.json()).error.code == "not_found"


def test_story_1_3_every_response_carries_the_security_headers(
    client: TestClient,
) -> None:
    for path in [
        "/",
        "/triage",
        "/assets/app.js",
        "/assets/pdf.worker.min.mjs",
        "/api/health",
        "/api/me",
        "/api/nope",
    ]:
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

    # Story 2.7: the PDF renderer's worker is a script of this origin. With
    # `nosniff` a browser runs it only when it is served as JavaScript.
    worker = client.get("/assets/pdf.worker.min.mjs")
    assert worker.status_code == 200
    assert worker.headers["content-type"].split(";")[0] == "text/javascript"
