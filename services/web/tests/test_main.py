"""Story 1.3: `python -m web` starts the server with the values from the settings."""

from typing import Any

import pytest
import uvicorn

import web.__main__ as entry
from web.settings import get_settings


def test_story_1_3_server_starts_on_the_configured_host_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, Any] = {}

    def fake_run(app: str, **options: Any) -> None:
        started.update(options, app=app)

    monkeypatch.setenv("WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_PORT", "8123")
    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(
        entry, "configure_logging", lambda: started.update(logging=True)
    )
    get_settings.cache_clear()
    try:
        entry.main()
    finally:
        get_settings.cache_clear()

    assert started["app"] == "web.adapters.http.app:create_app"
    assert started["factory"] is True
    assert (started["host"], started["port"]) == ("127.0.0.1", 8123)
    assert started["server_header"] is False
    assert started["logging"] is True
