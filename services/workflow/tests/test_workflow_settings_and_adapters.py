"""Story 1.6: settings, sign-in, the database connection, start-up and telemetry of `workflow`."""

import re

import pytest
from fastapi.testclient import TestClient

from workflow.adapters.db import (
    database_url,
)
from workflow.adapters.http.app import create_app
from workflow.adapters.migrations import MIGRATIONS_DIR
from workflow.settings import Settings, get_settings

# --- Azure sign-in ------------------------------------------------------------


def test_story_1_6_database_url_holds_no_password_and_requires_tls_in_azure() -> None:
    local = database_url(Settings())
    azure = database_url(
        Settings(
            database_host="pgsql-aiuw-demo-wus3.postgres.database.azure.com",
            database_user="id-aiuw-demo-wus3-workflow",
            database_entra_auth=True,
            database_connect_timeout_seconds=7,
        )
    )

    assert local.render_as_string(hide_password=False) == (
        "postgresql+psycopg://aiuw@127.0.0.1:5432/aiuw?connect_timeout=10"
    )
    assert azure.password is None
    assert azure.query == {"connect_timeout": "7", "sslmode": "require"}
    assert azure.username == "id-aiuw-demo-wus3-workflow"


# --- Migrations ---------------------------------------------------------------


def test_story_1_6_no_migration_and_no_code_updates_or_deletes_an_audit_event() -> None:
    package = MIGRATIONS_DIR.parent
    migration = (
        MIGRATIONS_DIR / "versions" / "v0001_case_status_page_status_and_audit_event.py"
    ).read_text()

    # AD-8, security rule 32: the service role is granted SELECT and INSERT only.
    assert 'grant_on_table("audit_event", ["SELECT", "INSERT"])' in migration
    assert migration.count('grant_on_table("audit_event"') == 1
    # DELETE is granted on no table at all.
    assert re.findall(r'grant_on_table\([^)]*"DELETE"', migration) == []
    # The one module that touches the table inserts into it and reads it.
    users = [
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if "audit_event_table" in path.read_text()
    ]
    assert users == ["adapters/db.py"]
    adapter = (package / "adapters" / "db.py").read_text()
    assert "insert(audit_event_table)" in adapter
    assert "update(audit_event_table" not in adapter
    assert "delete(" not in adapter


# --- Start-up and telemetry ---------------------------------------------------


def test_story_1_6_app_built_from_the_environment_uses_the_real_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing listens on these ports: building the app and starting its worker
    # open no connection that the health route needs.
    monkeypatch.setenv("WORKFLOW_SCHEDULER_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("WORKFLOW_DATABASE_PORT", "1")
    get_settings.cache_clear()
    try:
        app = create_app()
    finally:
        get_settings.cache_clear()

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
