"""Shared fixtures of the `verdict` tests: the fakes, the agent stub and the local database."""

import secrets
import socket
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from alembic import command
from fastapi.testclient import TestClient
from psycopg import sql
from verdict_fakes import (
    ACTOR,
    DEPLOYMENT,
    FakeFacts,
    FakeRules,
    MemoryRepository,
    MemorySchemaRevision,
    StubAgent,
    as_service,
    connect,
)

from contracts.ids import new_id
from verdict.adapters.http.app import create_app
from verdict.adapters.http.routes import Dependencies
from verdict.adapters.local_role import ensure_local_service_role
from verdict.adapters.migrations import alembic_config, bundled_head
from verdict.domain.run import RunOptions, RunPorts
from verdict.settings import Settings

FIXED_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
# Where the model stand-in is said to be; loopback, as the settings demand.
# Named, never called over the network: tests hand the app a transport.
MODEL_ENDPOINT = "http://127.0.0.1:5101"


@pytest.fixture
def fixed_now() -> datetime:
    return FIXED_NOW


@pytest.fixture
def case_id() -> str:
    return new_id()


@pytest.fixture
def repository() -> MemoryRepository:
    return MemoryRepository()


@pytest.fixture
def facts() -> FakeFacts:
    return FakeFacts()


@pytest.fixture
def rules() -> FakeRules:
    return FakeRules()


@pytest.fixture
def agent() -> StubAgent:
    return StubAgent()


@pytest.fixture
def ports(
    repository: MemoryRepository, facts: FakeFacts, rules: FakeRules, agent: StubAgent
) -> RunPorts:
    return RunPorts(repository=repository, facts=facts, rules=rules, agent=agent)


@pytest.fixture
def options() -> RunOptions:
    return RunOptions(actor=ACTOR)


@pytest.fixture
def schema_revision() -> MemorySchemaRevision:
    return MemorySchemaRevision(bundled_head())


@pytest.fixture
def settings() -> Settings:
    return Settings(
        applicationinsights_connection_string=None,
        model_endpoint=MODEL_ENDPOINT,
        chat_deployment=DEPLOYMENT,
    )


@pytest.fixture
def dependencies(
    ports: RunPorts,
    options: RunOptions,
    schema_revision: MemorySchemaRevision,
) -> Dependencies:
    return Dependencies(
        run=ports,
        options=options,
        schema_revision=schema_revision,
        head_revision=bundled_head(),
        now=lambda: FIXED_NOW,
    )


@pytest.fixture
def client(settings: Settings, dependencies: Dependencies) -> Iterator[TestClient]:
    app = create_app(settings, dependencies=dependencies)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# --- The local database, for integration tests ----------------------------------


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def local_database(settings: Settings) -> Settings:
    """The PostgreSQL of compose.yaml; an integration test cannot run without it."""
    if not _listening(settings.database_host, settings.database_port):
        pytest.fail(
            "Integration tests need PostgreSQL: run "
            "`docker compose up --detach --wait` first.",
            pytrace=False,
        )
    return settings


@pytest.fixture
def empty_database(local_database: Settings) -> Iterator[Settings]:
    """Settings of the migration role for a new, empty database; dropped afterwards.

    The database comes with a service role of its own, which the migrations
    grant their rights to; `service_settings` are what the service runs with.
    """
    suffix = secrets.token_hex(6)
    name = f"aiuw_test_{suffix}"
    role = f"verdict_test_{suffix}"
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
