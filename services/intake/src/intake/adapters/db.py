"""PostgreSQL adapter: the tables of schema `intake` and the case repository (AD-4)."""

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from psycopg import errors as pg_errors
from sqlalchemy import (
    URL,
    BigInteger,
    Column,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    insert,
    select,
)
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from intake.adapters.credential import azure_credential
from intake.adapters.telemetry import adapter_span
from intake.domain.entities import Case, Document
from intake.domain.ports import DuplicateUpload
from intake.settings import APP_ID, SCHEMA, Settings

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

# The scope of an Entra token for Azure Database for PostgreSQL.
POSTGRESQL_TOKEN_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"  # noqa: S105 - a public scope name, not a credential
VERSION_TABLE = "alembic_version"
# A token this close to its end is replaced before it is used.
TOKEN_REFRESH_MARGIN_SECONDS = 300
# One upload per idempotency key; a document uploaded without a key has none.
IDEMPOTENCY_KEY_UNIQUE = "uq_intake_document_idempotency_key"

logger = logging.getLogger(__name__)

tracer = trace.get_tracer(APP_ID)

# Conventions: snake_case, singular table names; ids are UUIDv7, kept as
# canonical lower-case strings in code.
metadata = MetaData(schema=SCHEMA)

case_table = Table(
    "case",
    metadata,
    Column("case_id", Uuid(as_uuid=False), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

document_table = Table(
    "document",
    metadata,
    Column("document_id", Uuid(as_uuid=False), primary_key=True),
    Column(
        "case_id",
        Uuid(as_uuid=False),
        ForeignKey(case_table.c.case_id),
        nullable=False,
        index=True,
    ),
    Column("original_blob_name", Text, nullable=False, unique=True),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("idempotency_key", Text, nullable=True),
    UniqueConstraint("idempotency_key", name=IDEMPOTENCY_KEY_UNIQUE),
)

# AD-6: the key row of a case's redaction. Inserted as `running` before any
# work; `result` is the stored stage result (ids and counts only), as JSON.
redaction_table = Table(
    "redaction",
    metadata,
    Column(
        "case_id",
        Uuid(as_uuid=False),
        ForeignKey(case_table.c.case_id),
        primary_key=True,
    ),
    Column(
        "document_id",
        Uuid(as_uuid=False),
        ForeignKey(document_table.c.document_id),
        nullable=False,
    ),
    Column("status", String(16), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    # The Language job, so a redaction left behind can still be cancelled.
    Column("job_id", Text, nullable=True),
    Column("result", Text, nullable=True),
    # Where the redacted PDF sits in the `cases` container, once there is one (AD-21).
    Column("redacted_blob_name", Text, nullable=True),
)

# AD-14: one row per page of the redacted PDF, with the one reading of it
# beside it in `page_text` and `word_box`.
page_table = Table(
    "page",
    metadata,
    Column("page_id", Uuid(as_uuid=False), primary_key=True),
    Column(
        "case_id",
        Uuid(as_uuid=False),
        ForeignKey(case_table.c.case_id),
        nullable=False,
        index=True,
    ),
    Column(
        "document_id",
        Uuid(as_uuid=False),
        ForeignKey(document_table.c.document_id),
        nullable=False,
    ),
    # 1-based, in document order.
    Column("page_number", Integer, nullable=False),
    # Page size in PDF points, as the page is shown.
    Column("page_width", Float, nullable=False),
    Column("page_height", Float, nullable=False),
    Column("thumbnail_blob_name", Text, nullable=False),
    UniqueConstraint(
        "document_id", "page_number", name="uq_intake_page_document_page_number"
    ),
)

page_text_table = Table(
    "page_text",
    metadata,
    Column(
        "page_id",
        Uuid(as_uuid=False),
        ForeignKey(page_table.c.page_id),
        primary_key=True,
    ),
    Column("text", Text, nullable=False),
)

word_box_table = Table(
    "word_box",
    metadata,
    Column(
        "page_id",
        Uuid(as_uuid=False),
        ForeignKey(page_table.c.page_id),
        primary_key=True,
    ),
    # The word's place in reading order.
    Column("word_number", Integer, primary_key=True),
    # Offsets into the page's text.
    Column("char_start", Integer, nullable=False),
    Column("char_end", Integer, nullable=False),
    Column("x0", Float, nullable=False),
    Column("y0", Float, nullable=False),
    Column("x1", Float, nullable=False),
    Column("y1", Float, nullable=False),
)

# Alembic's own table, in the service's schema (Conventions, Database).
_version_table = Table(
    VERSION_TABLE,
    MetaData(schema=SCHEMA),
    Column("version_num", String(32), primary_key=True),
)


def database_url(settings: Settings) -> URL:
    """The connection URL. It never holds a password: see `use_entra_token`."""
    query = {"connect_timeout": str(settings.database_connect_timeout_seconds)}
    if settings.database_entra_auth:
        # Azure requires TLS; the local container speaks plain TCP on loopback.
        query["sslmode"] = "require"
    return URL.create(
        "postgresql+psycopg",
        username=settings.database_user,
        host=settings.database_host,
        port=settings.database_port,
        database=settings.database_name,
        query=query,
    )


class EntraToken:
    """An Entra token for one scope, kept fresh in memory (security rule 9).

    By default the token that is the database password in Azure; Azure AI
    Language is signed in to with one for its own scope. Fetching a token is a blocking network call. The service fetches it on a
    worker thread with `refresh` before it opens a connection, and does so
    well ahead of the token's end, so the connection hook (`value`) always
    finds a valid one in memory and the event loop is never held up.
    """

    def __init__(
        self,
        credential: "TokenCredential",
        clock: Callable[[], float] = time.time,
        scope: str = POSTGRESQL_TOKEN_SCOPE,
    ) -> None:
        self._credential = credential
        self._clock = clock
        self._scope = scope
        self._token: str | None = None
        self._expires_on = 0.0
        # One fetch at a time: callers that arrive together share its result.
        self._fetching = threading.Lock()

    def _is_fresh(self) -> bool:
        """Whether the token is far enough from its end to need no refresh yet."""
        return (
            self._token is not None
            and self._expires_on - self._clock() > TOKEN_REFRESH_MARGIN_SECONDS
        )

    def _is_valid(self) -> bool:
        return self._token is not None and self._expires_on > self._clock()

    def _refresh_if_stale(self) -> str:
        with self._fetching:
            if not self._is_fresh() or self._token is None:
                token = self._credential.get_token(self._scope)
                self._token, self._expires_on = token.token, float(token.expires_on)
            return self._token

    async def refresh(self) -> None:
        """Make sure a token with time to spare is in memory, fetching off the event loop."""
        if not self._is_fresh():
            await asyncio.to_thread(self._refresh_if_stale)

    def value(self) -> str:
        """The token held, as long as it is valid.

        `refresh` replaces a token while it still has minutes to live, so a
        connection opened right after it finds one here. Only a caller that
        never refreshed (migrations, which have no event loop) fetches on the spot.
        """
        if self._token is None or not self._is_valid():
            return self._refresh_if_stale()
        return self._token


def entra_token_for(settings: Settings) -> EntraToken:
    """The token source for the identity the settings name."""
    return EntraToken(azure_credential(settings))


def use_entra_token(engine: Engine, token: EntraToken) -> None:
    """Sign every new connection of the engine in with the Entra token."""

    @event.listens_for(engine, "do_connect")
    def _set_token(
        dialect: Any, record: Any, arguments: Any, parameters: dict[str, Any]
    ) -> None:
        parameters["password"] = token.value()


class Database:
    """The service's engine, with the token kept fresh before each use."""

    def __init__(self, engine: AsyncEngine, token: EntraToken | None = None) -> None:
        self.engine = engine
        self._token = token

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[AsyncConnection]:
        if self._token is not None:
            await self._token.refresh()
        async with self.engine.connect() as connection:
            yield connection

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[AsyncConnection]:
        if self._token is not None:
            await self._token.refresh()
        async with self.engine.begin() as connection:
            yield connection

    async def dispose(self) -> None:
        await self.engine.dispose()


def build_database(settings: Settings) -> Database:
    """The service's database access. Building it opens no connection."""
    statement_timeout_ms = settings.database_statement_timeout_seconds * 1000
    engine = create_async_engine(
        database_url(settings),
        pool_pre_ping=True,
        # security rule 31: an error of the driver never carries the values
        # a statement was run with, into a log or onto a span.
        hide_parameters=True,
        # How long a request waits for a free connection.
        pool_timeout=settings.database_pool_timeout_seconds,
        # The server ends any statement that runs longer than this. Migrations
        # use their own engine and are not bound by it.
        connect_args={"options": f"-c statement_timeout={statement_timeout_ms}"},
    )
    token = None
    if settings.database_entra_auth:
        token = entra_token_for(settings)
        use_entra_token(engine.sync_engine, token)
    return Database(engine, token)


def _is_missing_relation(error: ProgrammingError) -> bool:
    """Whether a statement failed only because its table or schema does not exist."""
    return isinstance(
        error.orig, pg_errors.UndefinedTable | pg_errors.InvalidSchemaName
    )


def _violates(error: IntegrityError, constraint: str) -> bool:
    """Whether an insert failed on one named unique constraint."""
    original = error.orig
    return (
        isinstance(original, pg_errors.UniqueViolation)
        and original.diag.constraint_name == constraint
    )


class SqlCaseRepository:
    """Writes cases and documents. Every statement uses bound parameters (security rule 21)."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def add(self, case: Case, document: Document) -> None:
        """Insert both rows in one transaction, so a failure leaves neither."""
        with adapter_span(tracer, "intake.db.add_case"):
            try:
                async with self._database.begin() as connection:
                    await connection.execute(
                        insert(case_table).values(
                            case_id=case.case_id, created_at=case.created_at
                        )
                    )
                    await connection.execute(
                        insert(document_table).values(
                            document_id=document.document_id,
                            case_id=document.case_id,
                            original_blob_name=document.original_blob_name,
                            size_bytes=document.size_bytes,
                            sha256=document.sha256,
                            created_at=document.created_at,
                            idempotency_key=document.idempotency_key,
                        )
                    )
            except IntegrityError as error:
                if _violates(error, IDEMPOTENCY_KEY_UNIQUE):
                    # The same upload, recorded by a call that got there first.
                    raise DuplicateUpload from error
                raise

    async def find_by_idempotency_key(self, idempotency_key: str) -> Document | None:
        """The document an earlier upload with this key recorded, if there is one."""
        with adapter_span(tracer, "intake.db.find_by_idempotency_key"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(document_table).where(
                        document_table.c.idempotency_key == idempotency_key
                    )
                )
                row = result.first()
        if row is None:
            return None
        return Document(
            document_id=row.document_id,
            case_id=row.case_id,
            original_blob_name=row.original_blob_name,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            created_at=row.created_at,
            idempotency_key=row.idempotency_key,
        )

    async def document_exists(self, document_id: str) -> bool:
        """Whether the document's row is in the database."""
        with adapter_span(tracer, "intake.db.document_exists"):
            try:
                async with self._database.connect() as connection:
                    result = await connection.execute(
                        select(document_table.c.document_id).where(
                            document_table.c.document_id == document_id
                        )
                    )
                    return result.first() is not None
            except ProgrammingError as error:
                if _is_missing_relation(error):
                    # No table, so no row. Any other error is not an answer.
                    return False
                raise


class SqlSchemaRevision:
    """Reads the migration revision the database is at."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def current(self) -> str | None:
        """The applied revision, or None if no migration has ever run.

        Only a missing version table or schema means "never migrated". Any
        other error, such as permission denied, is raised: the caller reports
        "not ready", and the SQLSTATE is logged here.
        """
        with adapter_span(tracer, "intake.db.read_revision"):
            try:
                async with self._database.connect() as connection:
                    result = await connection.execute(
                        select(_version_table.c.version_num)
                    )
                    revisions = result.scalars().all()
            except ProgrammingError as error:
                if _is_missing_relation(error):
                    return None
                # security rule 31: the code, never the message.
                logger.warning(
                    "schema revision unreadable: sqlstate=%s",
                    getattr(error.orig, "sqlstate", None),
                )
                raise
        # More than one row means branched migrations: that is not the one head.
        return revisions[0] if len(revisions) == 1 else None
