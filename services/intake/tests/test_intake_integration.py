"""Story 1.5, against a real PostgreSQL and the blob emulator (compose.yaml).

Run `docker compose up --detach --wait` first. No test here calls Azure.
"""

import asyncio
import hashlib
import re
import secrets
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import main as alembic_command_line
from alembic.migration import MigrationContext
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContainerClient
from fastapi.testclient import TestClient
from intake_fakes import MemoryOriginalStore
from psycopg import sql
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError

from contracts.errors import ErrorBody
from contracts.models.intake import CaseCreated
from contracts.upload import MAX_UPLOAD_BYTES
from intake.adapters.blob import (
    BlobOriginalStore,
    build_blob_service,
    ensure_local_containers,
)
from intake.adapters.db import (
    SqlCaseRepository,
    SqlSchemaRevision,
    build_database,
    database_url,
    metadata,
)
from intake.adapters.http.app import create_app
from intake.adapters.http.routes import Dependencies
from intake.adapters.migrations import alembic_config, bundled_head, include_name
from intake.domain.entities import Case, Document, new_case_with_document
from intake.settings import Settings, get_settings

pytestmark = pytest.mark.integration

PDF = {"Content-Type": "application/pdf"}
CASES_DIR = Path(__file__).resolve().parents[3] / "data" / "cases"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@dataclass
class SameBlobName:
    """Records every document under one blob name, so the second insert fails."""

    inner: SqlCaseRepository

    async def add(self, case: Case, document: Document) -> None:
        await self.inner.add(
            case, replace(document, original_blob_name="fixed/name.pdf")
        )

    async def document_exists(self, document_id: str) -> bool:
        return await self.inner.document_exists(document_id)


def rows(settings: Settings, table: str) -> list[tuple[object, ...]]:
    """Every row of one of the two tables, read with a connection of the test's own."""
    queries = {
        "case": 'SELECT case_id::text, created_at FROM intake."case"',
        "document": (
            "SELECT document_id::text, case_id::text, original_blob_name, "
            "size_bytes, sha256 FROM intake.document"
        ),
    }
    with psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
    ) as connection:
        return connection.execute(queries[table]).fetchall()


def blob_names(originals: ContainerClient) -> set[str]:
    return set(originals.list_blob_names())


@pytest.fixture
def client(migrated_database: Settings) -> Iterator[TestClient]:
    """The service as it really runs: its own PostgreSQL and blob adapters."""
    with TestClient(
        create_app(migrated_database), raise_server_exceptions=False
    ) as test_client:
        yield test_client


@pytest.mark.parametrize("name", ["case-001.pdf", "case-002.pdf", "case-003.pdf"])
def test_story_1_5_an_uploaded_pdf_is_in_originals_byte_for_byte_with_its_rows(
    client: TestClient,
    migrated_database: Settings,
    originals: ContainerClient,
    name: str,
) -> None:
    pdf = (CASES_DIR / name).read_bytes()

    response = client.post("/cases", content=pdf, headers=PDF)

    assert response.status_code == 201
    created = CaseCreated.model_validate(response.json())
    blob_name = f"{created.case_id}/{created.document_id}.pdf"
    try:
        blob = originals.get_blob_client(blob_name)
        assert blob.download_blob().readall() == pdf
        assert blob.get_blob_properties().content_settings.content_type == (
            "application/pdf"
        )
        ((case_id, created_at),) = rows(migrated_database, "case")
        assert case_id == created.case_id
        assert created_at is not None
        assert rows(migrated_database, "document") == [
            (
                created.document_id,
                created.case_id,
                blob_name,
                len(pdf),
                hashlib.sha256(pdf).hexdigest(),
            )
        ]
    finally:
        originals.delete_blob(blob_name)


@pytest.mark.parametrize(
    ("content", "status", "code"),
    [
        (b"%PDF-1.7\n" + b"x" * MAX_UPLOAD_BYTES, 413, "file_too_large"),
        (b"plain text, renamed to report.pdf", 415, "unsupported_file_type"),
        (b"", 422, "validation_failed"),
    ],
    ids=["too-large", "not-a-pdf", "empty"],
)
def test_story_1_5_a_refused_upload_stores_nothing(
    client: TestClient,
    migrated_database: Settings,
    originals: ContainerClient,
    content: bytes,
    status: int,
    code: str,
) -> None:
    before = blob_names(originals)

    response = client.post("/cases", content=content, headers=PDF)

    assert response.status_code == status
    assert ErrorBody.model_validate(response.json()).error.code.value == code
    assert blob_names(originals) == before
    assert rows(migrated_database, "case") == []
    assert rows(migrated_database, "document") == []


def test_story_1_5_when_storage_fails_no_row_remains(
    migrated_database: Settings, case_pdf: bytes
) -> None:
    database = build_database(migrated_database)
    app = create_app(
        migrated_database,
        dependencies=Dependencies(
            store=MemoryOriginalStore(fail_put=True),
            repository=SqlCaseRepository(database),
            schema_revision=SqlSchemaRevision(database),
            head_revision=bundled_head(),
        ),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/cases", content=case_pdf, headers=PDF)

    assert response.status_code == 502
    assert ErrorBody.model_validate(response.json()).error.code.value == (
        "upstream_unavailable"
    )
    assert rows(migrated_database, "case") == []
    assert rows(migrated_database, "document") == []


def test_story_1_5_when_the_database_fails_the_stored_original_is_removed(
    empty_database: Settings, originals: ContainerClient, case_pdf: bytes
) -> None:
    # A database with no tables: the blob is written, then the insert fails.
    before = blob_names(originals)

    with TestClient(
        create_app(empty_database), raise_server_exceptions=False
    ) as client:
        response = client.post("/cases", content=case_pdf, headers=PDF)

    assert response.status_code == 502
    assert ErrorBody.model_validate(response.json()).error.code.value == (
        "upstream_unavailable"
    )
    assert "intake" not in response.text  # no SQL, no schema name
    assert blob_names(originals) == before


def test_story_1_5_a_failed_insert_leaves_neither_row(
    migrated_database: Settings, case_pdf: bytes
) -> None:
    # The second upload reuses the first one's blob name, which the document
    # table holds unique: its case row must be rolled back with it.
    store = MemoryOriginalStore()
    database = build_database(migrated_database)
    app = create_app(
        migrated_database,
        dependencies=Dependencies(
            store=store,
            repository=SameBlobName(SqlCaseRepository(database)),
            schema_revision=SqlSchemaRevision(database),
            head_revision=bundled_head(),
        ),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/cases", content=case_pdf, headers=PDF).status_code == 201
        assert client.post("/cases", content=case_pdf, headers=PDF).status_code == 502

    assert len(rows(migrated_database, "case")) == 1
    assert len(rows(migrated_database, "document")) == 1


def test_story_1_5_readiness_fails_until_the_schema_is_at_the_bundled_head(
    empty_database: Settings,
) -> None:
    config = alembic_config(empty_database)

    def ready() -> int:
        # A new app each time: what a probe sees after the pipeline has migrated.
        with TestClient(
            create_app(empty_database), raise_server_exceptions=False
        ) as client:
            assert client.get("/health").status_code == 200
            return int(client.get("/ready").status_code)

    # No migration has run: the schema does not even exist.
    assert ready() == 502

    command.upgrade(config, "head")
    assert ready() == 200

    # One revision behind the head (here: back before the first one).
    command.downgrade(config, "base")
    assert ready() == 502
    assert rows_exist(empty_database) is False

    command.upgrade(config, "head")
    assert ready() == 200


def rows_exist(settings: Settings) -> bool:
    with psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
    ) as connection:
        found = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'intake' AND table_name IN ('case', 'document')"
        ).fetchone()
    return found is not None and found[0] > 0


def test_story_1_5_migration_keeps_everything_in_schema_intake(
    migrated_database: Settings,
) -> None:
    with psycopg.connect(
        host=migrated_database.database_host,
        port=migrated_database.database_port,
        dbname=migrated_database.database_name,
        user=migrated_database.database_user,
    ) as connection:
        tables = connection.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY table_name"
        ).fetchall()
        version = connection.execute(
            "SELECT version_num FROM intake.alembic_version"
        ).fetchall()

    # The version table too is in the service's own schema (AD-4).
    assert tables == [
        ("intake", "alembic_version"),
        ("intake", "case"),
        ("intake", "document"),
    ]
    assert version == [(bundled_head(),)]


def test_story_1_5_ready_is_not_fooled_by_an_unknown_revision(
    migrated_database: Settings,
) -> None:
    with psycopg.connect(
        host=migrated_database.database_host,
        port=migrated_database.database_port,
        dbname=migrated_database.database_name,
        user=migrated_database.database_user,
        autocommit=True,
    ) as connection:
        connection.execute("UPDATE intake.alembic_version SET version_num = '0000'")

    with TestClient(
        create_app(migrated_database), raise_server_exceptions=False
    ) as client:
        assert client.get("/ready").status_code == 502


def test_story_1_5_ready_fails_when_the_database_is_unreachable(
    local_stack: Settings,
) -> None:
    # A port nothing listens on.
    settings = local_stack.model_copy(update={"database_port": 1})

    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/ready")

    assert response.status_code == 502
    assert "127.0.0.1" not in response.text


def test_story_1_5_the_store_never_overwrites_and_tolerates_a_missing_blob(
    local_stack: Settings, originals: ContainerClient
) -> None:
    store = BlobOriginalStore(
        build_blob_service(local_stack), local_stack.originals_container
    )
    name = "test-overwrite/original.pdf"
    asyncio.run(store.delete(name))

    asyncio.run(store.put(name, b"%PDF-first"))
    with pytest.raises(ResourceExistsError):
        asyncio.run(store.put(name, b"%PDF-second"))
    assert originals.download_blob(name).readall() == b"%PDF-first"

    asyncio.run(store.delete(name))
    asyncio.run(store.delete(name))
    assert name not in blob_names(originals)


def test_story_1_5_local_containers_are_created_once(local_stack: Settings) -> None:
    names = [local_stack.originals_container, local_stack.cases_container]

    assert ensure_local_containers(local_stack) == names
    assert ensure_local_containers(local_stack) == names
    service = build_blob_service(local_stack)
    assert set(names) <= {c.name for c in service.list_containers()}


def test_story_1_5_each_test_has_blob_containers_of_its_own(
    local_stack: Settings, originals: ContainerClient
) -> None:
    # Never the developer's own containers.
    assert originals.container_name == local_stack.originals_container
    assert re.fullmatch(r"originals-test-[0-9a-f]{12}", originals.container_name)
    assert re.fullmatch(r"cases-test-[0-9a-f]{12}", local_stack.cases_container)
    assert blob_names(originals) == set()


# --- The schema and its migrations --------------------------------------------


def connect(settings: Settings, **options: Any) -> psycopg.Connection[Any]:
    return psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
        **options,
    )


def test_story_1_5_the_tables_in_code_match_the_migrated_database(
    migrated_database: Settings,
) -> None:
    engine = create_engine(database_url(migrated_database))
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_schemas": True,
                    "include_name": include_name,
                    "version_table": "alembic_version",
                    "version_table_schema": "intake",
                    "compare_type": True,
                },
            )
            differences = compare_metadata(context, metadata)
    finally:
        engine.dispose()

    # Nothing to add, drop or alter: what the service writes is what exists.
    assert differences == []


def test_story_1_5_schema_comparison_looks_at_schema_intake_only() -> None:
    assert include_name("intake", "schema", {}) is True
    for other in ("workflow", "public", None):
        assert include_name(other, "schema", {}) is False
    assert include_name("document", "table", {"schema_name": "intake"}) is True


def test_story_1_5_the_documented_alembic_command_migrates_the_database_named_in_the_environment(
    empty_database: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # README, 'Run locally': alembic -c services/intake/alembic.ini upgrade head,
    # with the database chosen by INTAKE_DATABASE_* variables alone.
    monkeypatch.setenv("INTAKE_DATABASE_HOST", empty_database.database_host)
    monkeypatch.setenv("INTAKE_DATABASE_PORT", str(empty_database.database_port))
    monkeypatch.setenv("INTAKE_DATABASE_NAME", empty_database.database_name)
    monkeypatch.setenv("INTAKE_DATABASE_USER", empty_database.database_user)
    monkeypatch.setenv("INTAKE_DATABASE_ENTRA_AUTH", "false")
    get_settings.cache_clear()
    try:
        alembic_command_line(argv=["-c", str(ALEMBIC_INI), "upgrade", "head"])
    finally:
        get_settings.cache_clear()

    with connect(empty_database) as connection:
        version = connection.execute(
            "SELECT version_num FROM intake.alembic_version"
        ).fetchall()
    assert version == [(bundled_head(),)]
    assert rows_exist(empty_database) is True


# --- The database adapter -----------------------------------------------------


def test_story_1_5_readiness_reports_a_permission_error_as_such_not_as_unmigrated(
    migrated_database: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # A role that may sign in but has no rights on schema `intake`.
    role = f"no_rights_{secrets.token_hex(4)}"
    with connect(migrated_database, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
    settings = migrated_database.model_copy(update={"database_user": role})
    try:
        with TestClient(create_app(settings), raise_server_exceptions=False) as client:
            response = client.get("/ready")
    finally:
        with connect(migrated_database, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    assert response.status_code == 502
    # Its own log code (insufficient privilege), and no claim that nothing was migrated.
    assert "schema revision unreadable: sqlstate=42501" in caplog.text
    assert "not ready: database type=ProgrammingError" in caplog.text
    assert "schema_revision=None" not in caplog.text
    assert role not in caplog.text


def test_story_1_5_the_repository_can_say_whether_a_document_was_recorded(
    migrated_database: Settings,
) -> None:
    async def scenario() -> tuple[bool, bool]:
        database = build_database(migrated_database)
        repository = SqlCaseRepository(database)
        case, document = new_case_with_document(b"%PDF-1.7", datetime.now(UTC))
        try:
            before = await repository.document_exists(document.document_id)
            await repository.add(case, document)
            return before, await repository.document_exists(document.document_id)
        finally:
            await database.dispose()

    assert asyncio.run(scenario()) == (False, True)


def test_story_1_5_a_statement_that_runs_too_long_is_ended_by_the_server(
    migrated_database: Settings,
) -> None:
    settings = migrated_database.model_copy(
        update={"database_statement_timeout_seconds": 1}
    )

    async def scenario() -> None:
        database = build_database(settings)
        try:
            async with database.connect() as connection:
                await connection.execute(select(func.pg_sleep(5)))
        finally:
            await database.dispose()

    started = time.monotonic()
    with pytest.raises(OperationalError) as raised:
        asyncio.run(scenario())

    assert isinstance(raised.value.orig, psycopg.errors.QueryCanceled)
    assert time.monotonic() - started < 4


def test_story_1_5_a_database_that_does_not_answer_is_given_up_on(
    local_stack: Settings,
) -> None:
    # A listening socket that never speaks the protocol.
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        settings = local_stack.model_copy(
            update={
                "database_port": silent.getsockname()[1],
                "database_connect_timeout_seconds": 2,
            }
        )
        started = time.monotonic()
        with TestClient(create_app(settings), raise_server_exceptions=False) as client:
            response = client.get("/ready")

    assert response.status_code == 502
    assert time.monotonic() - started < 8
