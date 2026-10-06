"""Shared fixtures of the `intake` tests: fakes for the stores, and the local stack."""

import contextlib
import secrets
import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from alembic import command
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContainerClient
from fastapi.testclient import TestClient
from intake_fakes import (
    MemoryCaseRepository,
    MemoryOriginalStore,
    MemorySchemaRevision,
)
from psycopg import sql
from pydantic import SecretStr

from intake.adapters.blob import build_blob_service, ensure_local_containers
from intake.adapters.http.app import create_app
from intake.adapters.http.routes import Dependencies
from intake.adapters.migrations import alembic_config, bundled_head
from intake.settings import Settings

CASES_DIR = Path(__file__).resolve().parents[3] / "data" / "cases"
FIXED_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
# The emulator's built-in account; the SDK knows its address and key.
EMULATOR = "UseDevelopmentStorage=true"


@pytest.fixture
def case_pdf() -> bytes:
    """A synthetic case PDF from story 1.4."""
    return (CASES_DIR / "case-001.pdf").read_bytes()


@pytest.fixture
def fixed_now() -> datetime:
    return FIXED_NOW


@pytest.fixture
def store() -> MemoryOriginalStore:
    return MemoryOriginalStore()


@pytest.fixture
def repository() -> MemoryCaseRepository:
    return MemoryCaseRepository()


@pytest.fixture
def schema_revision() -> MemorySchemaRevision:
    return MemorySchemaRevision(bundled_head())


@pytest.fixture
def settings() -> Settings:
    return Settings(applicationinsights_connection_string=None)


@pytest.fixture
def dependencies(
    store: MemoryOriginalStore,
    repository: MemoryCaseRepository,
    schema_revision: MemorySchemaRevision,
) -> Dependencies:
    return Dependencies(
        store=store,
        repository=repository,
        schema_revision=schema_revision,
        head_revision=bundled_head(),
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
def local_stack() -> Iterator[Settings]:
    """The containers of compose.yaml; an integration test cannot run without them.

    Each test gets blob containers of its own, removed afterwards, so the
    developer's `originals` and `cases` are never touched.
    """
    suffix = secrets.token_hex(6)
    settings = Settings(
        applicationinsights_connection_string=None,
        blob_connection_string=SecretStr(EMULATOR),
        originals_container=f"originals-test-{suffix}",
        cases_container=f"cases-test-{suffix}",
    )
    if not _listening(settings.database_host, settings.database_port) or not _listening(
        "127.0.0.1", 10000
    ):
        pytest.fail(
            "Integration tests need PostgreSQL and the blob emulator: run "
            "`docker compose up --detach --wait` first.",
            pytrace=False,
        )
    names = ensure_local_containers(settings)
    try:
        yield settings
    finally:
        service = build_blob_service(settings)
        for name in names:
            with contextlib.suppress(ResourceNotFoundError):
                service.delete_container(name)


@pytest.fixture
def empty_database(local_stack: Settings) -> Iterator[Settings]:
    """Settings for a new, empty database on the local server; dropped afterwards."""
    name = f"aiuw_test_{secrets.token_hex(6)}"
    admin = (
        f"host={local_stack.database_host} port={local_stack.database_port} "
        f"dbname={local_stack.database_name} user={local_stack.database_user}"
    )
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield local_stack.model_copy(update={"database_name": name})
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


@pytest.fixture
def originals(local_stack: Settings) -> ContainerClient:
    """This test's originals container, read directly: the service cannot read it."""
    service: BlobServiceClient = build_blob_service(local_stack)
    return service.get_container_client(local_stack.originals_container)
