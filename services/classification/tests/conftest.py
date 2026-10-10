"""Shared fixtures of the `classification` tests: the fakes, the gateway stub and the local database."""

import secrets
import socket
from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest
from alembic import command
from classification_fakes import (
    ACTOR,
    DEPLOYMENT,
    FakePages,
    MemoryRepository,
    MemorySchemaRevision,
    StubModel,
)
from fastapi.testclient import TestClient
from psycopg import sql

from classification.adapters.http.app import create_app
from classification.adapters.http.routes import Dependencies
from classification.adapters.migrations import alembic_config, bundled_head
from classification.domain.classify import ClassifyOptions, ClassifyPorts
from classification.settings import Settings
from contracts.ids import new_id

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
def pages() -> FakePages:
    return FakePages()


@pytest.fixture
def model() -> StubModel:
    return StubModel()


@pytest.fixture
def ports(
    repository: MemoryRepository, pages: FakePages, model: StubModel
) -> ClassifyPorts:
    return ClassifyPorts(repository=repository, pages=pages, model=model)


@pytest.fixture
def options() -> ClassifyOptions:
    return ClassifyOptions(actor=ACTOR)


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
    ports: ClassifyPorts,
    options: ClassifyOptions,
    schema_revision: MemorySchemaRevision,
) -> Dependencies:
    return Dependencies(
        classify=ports,
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
    """Settings for a new, empty database on the local server; dropped afterwards."""
    name = f"aiuw_test_{secrets.token_hex(6)}"
    admin = (
        f"host={local_database.database_host} port={local_database.database_port} "
        f"dbname={local_database.database_name} user={local_database.database_user}"
    )
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield local_database.model_copy(update={"database_name": name})
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)
                )
            )


@pytest.fixture
def migrated_database(empty_database: Settings) -> Settings:
    """Settings for a database with every bundled migration applied."""
    command.upgrade(alembic_config(empty_database), "head")
    return empty_database
