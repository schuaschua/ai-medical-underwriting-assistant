"""Shared fixtures of the `workflow` tests: fakes for the store and engine, and the local stack."""

import secrets
import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
from alembic import command
from fastapi.testclient import TestClient
from psycopg import sql
from workflow_fakes import (
    FakeEngine,
    MemoryCaseStore,
    MemorySchemaRevision,
    MemoryTrailGuard,
)
from workflow_local import as_service, connect

from contracts.ids import new_id
from workflow.adapters.http.app import create_app, default_parameters
from workflow.adapters.http.routes import Dependencies
from workflow.adapters.local_role import ensure_local_service_role
from workflow.adapters.migrations import alembic_config, bundled_head
from workflow.settings import Settings

FIXED_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
# The task hub of compose.yaml that only these tests use.
TEST_TASK_HUB = "aiuw-test"


@pytest.fixture
def fixed_now() -> datetime:
    return FIXED_NOW


@pytest.fixture
def case_id() -> str:
    return new_id()


@pytest.fixture
def store() -> MemoryCaseStore:
    return MemoryCaseStore()


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def schema_revision() -> MemorySchemaRevision:
    return MemorySchemaRevision(bundled_head())


@pytest.fixture
def trail_guard() -> MemoryTrailGuard:
    return MemoryTrailGuard()


@pytest.fixture
def settings() -> Settings:
    return Settings(applicationinsights_connection_string=None)


@pytest.fixture
def dependencies(
    settings: Settings,
    store: MemoryCaseStore,
    engine: FakeEngine,
    schema_revision: MemorySchemaRevision,
    trail_guard: MemoryTrailGuard,
) -> Dependencies:
    return Dependencies(
        store=store,
        engine=engine,
        schema_revision=schema_revision,
        trail_guard=trail_guard,
        head_revision=bundled_head(),
        defaults=default_parameters(settings),
        now=lambda: FIXED_NOW,
    )


@pytest.fixture
def client(settings: Settings, dependencies: Dependencies) -> Iterator[TestClient]:
    app = create_app(settings, dependencies=dependencies)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --- The local stack, for integration tests ----------------------------------


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def local_database() -> Settings:
    """The PostgreSQL of compose.yaml; an integration test cannot run without it."""
    settings = Settings(
        applicationinsights_connection_string=None, scheduler_task_hub=TEST_TASK_HUB
    )
    if not _listening(settings.database_host, settings.database_port):
        pytest.fail(
            "Integration tests need PostgreSQL: run "
            "`docker compose up --detach --wait` first.",
            pytrace=False,
        )
    return settings


@pytest.fixture
def local_scheduler(local_database: Settings) -> Settings:
    """The Durable Task Scheduler emulator of compose.yaml, and its test task hub."""
    endpoint = urlsplit(local_database.scheduler_endpoint)
    if not _listening(endpoint.hostname or "", endpoint.port or 0):
        pytest.fail(
            "Integration tests need the Durable Task Scheduler emulator: run "
            "`docker compose up --detach --wait` first.",
            pytrace=False,
        )
    return local_database


@pytest.fixture
def empty_database(local_database: Settings) -> Iterator[Settings]:
    """Settings of the migration role for a new, empty database; dropped afterwards.

    The database comes with a service role of its own, which the migrations
    grant their rights to; `as_service` gives the settings the service runs with.
    """
    suffix = secrets.token_hex(6)
    name = f"aiuw_test_{suffix}"
    role = f"workflow_test_{suffix}"
    with connect(local_database, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    settings = local_database.model_copy(
        update={"database_name": name, "database_service_role": role}
    )
    ensure_local_service_role(settings)
    try:
        yield settings
    finally:
        with connect(local_database, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)
                )
            )
            # Its rights went with the database, so nothing holds the role.
            connection.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
            )


@pytest.fixture
def migrated_database(empty_database: Settings) -> Settings:
    """Settings of the migration role for a database with every migration applied."""
    command.upgrade(alembic_config(empty_database), "head")
    return empty_database


@pytest.fixture
def service_settings(migrated_database: Settings) -> Settings:
    """What the running service uses against the migrated database: its own role."""
    return as_service(migrated_database)
