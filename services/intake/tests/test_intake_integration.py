"""Story 1.5, against a real PostgreSQL and the blob emulator (compose.yaml).

Run `docker compose up --detach --wait` first. No test here calls Azure.
"""

import asyncio
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContainerClient
from fastapi.testclient import TestClient
from intake_fakes import MemoryOriginalStore, memory_redaction
from sqlalchemy import create_engine

from contracts.errors import ErrorBody
from contracts.models.intake import CaseCreated
from contracts.upload import MAX_UPLOAD_BYTES
from intake.adapters.blob import (
    BlobOriginalStore,
    build_blob_service,
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
from intake.domain.entities import Case, Document
from intake.settings import Settings

pytestmark = pytest.mark.integration

PDF = {"Content-Type": "application/pdf"}
CASES_DIR = Path(__file__).resolve().parents[3] / "data" / "cases"


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

    async def find_by_idempotency_key(self, idempotency_key: str) -> Document | None:
        return await self.inner.find_by_idempotency_key(idempotency_key)


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


@pytest.mark.parametrize("name", ["case-001.pdf"])
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
    ],
    ids=["too-large", "not-a-pdf"],
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
            **memory_redaction(),
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
            **memory_redaction(),
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
        # Story 1.7.
        ("intake", "page"),
        ("intake", "page_text"),
        ("intake", "redaction"),
        ("intake", "word_box"),
    ]
    assert version == [(bundled_head(),)]


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


# --- The database adapter -----------------------------------------------------
