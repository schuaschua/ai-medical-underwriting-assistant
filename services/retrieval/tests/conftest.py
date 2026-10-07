"""Shared fixtures of the `retrieval` tests: the fakes, the gateway stub and the local database."""

import secrets
import socket
from collections.abc import Iterator

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from psycopg import sql
from retrieval_fakes import (
    CHAT,
    EMBEDDING,
    FakeLayout,
    FakeManual,
    MemoryIndex,
    MemoryRepository,
    MemorySchemaRevision,
    StubModel,
)

from retrieval.adapters.http.app import create_app
from retrieval.adapters.http.routes import Dependencies
from retrieval.adapters.migrations import alembic_config, bundled_head
from retrieval.domain.ingest import IngestPorts
from retrieval.domain.search import SearchPorts
from retrieval.settings import Settings

# Where the stand-ins are said to be; loopback, as the settings demand.
# Named, never called over the network: tests hand the adapters a transport.
MODEL_ENDPOINT = "http://127.0.0.1:5101"
LAYOUT_ENDPOINT = "http://127.0.0.1:5102"


@pytest.fixture
def repository() -> MemoryRepository:
    return MemoryRepository()


@pytest.fixture
def model() -> StubModel:
    return StubModel()


@pytest.fixture
def manual_store() -> FakeManual:
    return FakeManual()


@pytest.fixture
def layout_parser() -> FakeLayout:
    return FakeLayout()


@pytest.fixture
def ports(
    manual_store: FakeManual,
    layout_parser: FakeLayout,
    model: StubModel,
    repository: MemoryRepository,
) -> IngestPorts:
    return IngestPorts(
        manual=manual_store, layout=layout_parser, model=model, repository=repository
    )


@pytest.fixture
def schema_revision() -> MemorySchemaRevision:
    return MemorySchemaRevision(bundled_head())


@pytest.fixture
def settings() -> Settings:
    return Settings(
        applicationinsights_connection_string=None,
        model_endpoint=MODEL_ENDPOINT,
        layout_endpoint=LAYOUT_ENDPOINT,
        chat_deployment=CHAT,
        embedding_deployment=EMBEDDING,
        # A throttled call is sent again at once.
        model_retry_seconds=0.01,
        layout_poll_seconds=0.01,
    )


@pytest.fixture
def index() -> MemoryIndex:
    return MemoryIndex()


@pytest.fixture
def dependencies(
    schema_revision: MemorySchemaRevision, model: StubModel, index: MemoryIndex
) -> Dependencies:
    return Dependencies(
        search=SearchPorts(model=model, index=index),
        schema_revision=schema_revision,
        head_revision=bundled_head(),
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
