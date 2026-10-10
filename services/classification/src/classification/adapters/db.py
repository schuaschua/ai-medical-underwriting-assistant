"""PostgreSQL adapter: the one table of schema `classification` and its repository (AD-4, AD-6)."""

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from psycopg import errors as pg_errors
from sqlalchemy import (
    URL,
    Boolean,
    Column,
    DateTime,
    Engine,
    Float,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    delete,
    event,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from classification.adapters.credential import azure_credential
from classification.adapters.telemetry import adapter_span
from classification.domain.entities import ClassificationKey, KeyRow
from classification.settings import APP_ID, SCHEMA, Settings
from contracts.enums import ClassifierContender, StageStatus
from contracts.models.classification import Classification, ClassificationResult

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

# The scope of an Entra token for Azure Database for PostgreSQL.
POSTGRESQL_TOKEN_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"  # noqa: S105 - a public scope name, not a credential
VERSION_TABLE = "alembic_version"
# A token this close to its end is replaced before it is used.
TOKEN_REFRESH_MARGIN_SECONDS = 300
# AD-6: one classification per case, page and contender.
KEY_UNIQUE = "uq_classification_case_page_contender"

logger = logging.getLogger(__name__)

tracer = trace.get_tracer(APP_ID)

# Conventions: snake_case, singular table names; ids are UUIDv7, kept as
# canonical lower-case strings in code.
metadata = MetaData(schema=SCHEMA)

# AD-6, AD-13: the key row of a classification and, once it is done, what the
# page was classified as. Inserted as `running` before any work. `result` is
# the stored stage result as JSON; it is where the one-line reason is kept.
# `case_id` and `page_id` are `intake`'s ids: id columns only, no foreign key
# into another service's schema (AD-4).
classification_table = Table(
    "classification",
    metadata,
    Column("classification_id", Uuid(as_uuid=False), primary_key=True),
    Column("case_id", Uuid(as_uuid=False), nullable=False),
    Column("page_id", Uuid(as_uuid=False), nullable=False),
    Column("contender", String(32), nullable=False),
    Column("status", String(16), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    # Set when, and only when, the classification is done.
    Column("page_type", String(64), nullable=True),
    Column("is_medical", Boolean, nullable=True),
    Column("confidence", Float, nullable=True),
    Column("result", Text, nullable=True),
    # Its leading column also serves the read of one case's classifications.
    UniqueConstraint("case_id", "page_id", "contender", name=KEY_UNIQUE),
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

    By default the token that is the database password in Azure; the model
    deployment is signed in to with one for its own scope. Fetching a token
    is a blocking network call. The service fetches it on a worker thread with `refresh` before it opens a connection, and does so
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


class SqlClassificationRepository:
    """Every statement uses bound parameters (security rule 21)."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def find(self, key: ClassificationKey) -> KeyRow | None:
        with adapter_span(tracer, "classification.db.find_classification"):
            async with self._database.connect() as connection:
                return await _key_row(connection, key)

    async def begin(
        self, classification_id: str, key: ClassificationKey, started_at: datetime
    ) -> KeyRow | None:
        """Insert the key row as running; the unique key settles calls that race."""
        with adapter_span(tracer, "classification.db.begin_classification"):
            async with self._database.begin() as connection:
                inserted = await connection.execute(
                    pg_insert(classification_table)
                    .values(
                        classification_id=classification_id,
                        case_id=key.case_id,
                        page_id=key.page_id,
                        contender=key.contender.value,
                        status=StageStatus.RUNNING.value,
                        started_at=started_at,
                    )
                    .on_conflict_do_nothing(constraint=KEY_UNIQUE)
                    .returning(classification_table.c.classification_id)
                )
                if inserted.first() is not None:
                    return None
                return await _key_row(connection, key)

    async def finish(
        self,
        classification_id: str,
        result_json: str,
        classification: Classification | None,
    ) -> str:
        """Store the result, only if the key row is still running."""
        status = StageStatus.DONE if classification is not None else StageStatus.FAILED
        values: dict[str, Any] = {
            "status": status.value,
            "result": result_json,
            "finished_at": func.now(),
        }
        if classification is not None:
            values.update(
                page_type=classification.page_type.value,
                is_medical=classification.is_medical,
                confidence=classification.confidence,
            )
        with adapter_span(tracer, "classification.db.finish_classification"):
            async with self._database.begin() as connection:
                settled = await connection.execute(
                    update(classification_table)
                    .where(
                        classification_table.c.classification_id == classification_id,
                        classification_table.c.status == StageStatus.RUNNING.value,
                    )
                    .values(**values)
                    .returning(classification_table.c.classification_id)
                )
                if settled.first() is not None:
                    return result_json
                # Settled by another call: that result stands.
                stored = await connection.execute(
                    select(classification_table.c.result).where(
                        classification_table.c.classification_id == classification_id
                    )
                )
                return str(stored.scalar_one())

    async def release(self, classification_id: str) -> None:
        """Remove the key row, only while it is still running."""
        with adapter_span(tracer, "classification.db.release_classification"):
            async with self._database.begin() as connection:
                await connection.execute(
                    delete(classification_table).where(
                        classification_table.c.classification_id == classification_id,
                        classification_table.c.status == StageStatus.RUNNING.value,
                    )
                )

    async def of_case(self, case_id: str) -> list[Classification]:
        with adapter_span(tracer, "classification.db.classifications_of_case"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(classification_table.c.result)
                    .where(
                        classification_table.c.case_id == case_id,
                        classification_table.c.status == StageStatus.DONE.value,
                    )
                    # Oldest first: by when each was begun, then by id, so
                    # that two begun at the same instant keep one order.
                    .order_by(
                        classification_table.c.started_at,
                        classification_table.c.classification_id,
                    )
                )
                stored = [
                    ClassificationResult.model_validate_json(row.result)
                    for row in result
                ]
        return [
            item.classification for item in stored if item.classification is not None
        ]


async def _key_row(
    connection: AsyncConnection, key: ClassificationKey
) -> KeyRow | None:
    result = await connection.execute(
        select(classification_table).where(
            classification_table.c.case_id == key.case_id,
            classification_table.c.page_id == key.page_id,
            classification_table.c.contender == key.contender.value,
        )
    )
    row = result.first()
    if row is None:
        return None
    return KeyRow(
        classification_id=row.classification_id,
        key=ClassificationKey(
            row.case_id, row.page_id, ClassifierContender(row.contender)
        ),
        started_at=row.started_at,
        result_json=row.result,
    )


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
        with adapter_span(tracer, "classification.db.read_revision"):
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
