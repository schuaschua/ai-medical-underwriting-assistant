"""PostgreSQL adapter: the tables of schema `extraction` and their repository (AD-4, AD-6, AD-14)."""

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from psycopg import errors as pg_errors
from sqlalchemy import (
    URL,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    delete,
    event,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from contracts.enums import StageStatus
from contracts.models.extraction import Fact
from extraction.adapters.credential import azure_credential
from extraction.adapters.telemetry import adapter_span
from extraction.domain.entities import FactSetKey, KeyRow
from extraction.settings import APP_ID, SCHEMA, Settings

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

# The scope of an Entra token for Azure Database for PostgreSQL.
POSTGRESQL_TOKEN_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"  # noqa: S105 - a public scope name, not a credential
VERSION_TABLE = "alembic_version"
# A token this close to its end is replaced before it is used.
TOKEN_REFRESH_MARGIN_SECONDS = 300
# AD-6: one fact set per case and page.
KEY_UNIQUE = "uq_extraction_fact_set_case_page"

logger = logging.getLogger(__name__)

tracer = trace.get_tracer(APP_ID)

# Conventions: snake_case, singular table names; ids are UUIDv7, kept as
# canonical lower-case strings in code.
metadata = MetaData(schema=SCHEMA)

# AD-6: the key row of one page's extraction. Inserted as `running` before
# any work. `result` is the stored stage result as JSON. `case_id` and
# `page_id` are `intake`'s ids: id columns only, no foreign key into another
# service's schema (AD-4).
fact_set_table = Table(
    "fact_set",
    metadata,
    Column("fact_set_id", Uuid(as_uuid=False), primary_key=True),
    Column("case_id", Uuid(as_uuid=False), nullable=False),
    Column("page_id", Uuid(as_uuid=False), nullable=False),
    Column("status", String(16), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("result", Text, nullable=True),
    UniqueConstraint("case_id", "page_id", name=KEY_UNIQUE),
)

# AD-14: one row per fact, written with its fact set's result and never
# changed. `position` is the fact's place among its page's facts, as the
# model listed them. `quote_start` and `quote_end` index the page text as
# `intake` stores it, in the contracts' `QUOTE_OFFSET_UNIT`; both are set
# when, and only when, the quote was found (the check constraint holds that).
fact_table = Table(
    "fact",
    metadata,
    Column("fact_id", Uuid(as_uuid=False), primary_key=True),
    Column(
        "fact_set_id",
        Uuid(as_uuid=False),
        ForeignKey(fact_set_table.c.fact_set_id),
        nullable=False,
    ),
    Column("case_id", Uuid(as_uuid=False), nullable=False),
    Column("page_id", Uuid(as_uuid=False), nullable=False),
    Column("page_number", Integer, nullable=False),
    Column("position", Integer, nullable=False),
    Column("statement", Text, nullable=False),
    Column("quote", Text, nullable=False),
    Column("quote_verified", Boolean, nullable=False),
    Column("quote_start", Integer, nullable=True),
    Column("quote_end", Integer, nullable=True),
    UniqueConstraint(
        "fact_set_id", "position", name="uq_extraction_fact_fact_set_position"
    ),
    CheckConstraint(
        "(quote_verified AND quote_start IS NOT NULL AND quote_end IS NOT NULL "
        "AND quote_start < quote_end) OR (NOT quote_verified AND quote_start IS NULL "
        "AND quote_end IS NULL)",
        name="ck_extraction_fact_offsets_follow_verification",
    ),
    # The read of one case's facts, in page order.
    Index("ix_extraction_fact_case_id_page_number", "case_id", "page_number"),
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


# How often `begin` tries its insert when the row in its way keeps vanishing.
BEGIN_ATTEMPTS = 5


class KeyRowContended(Exception):
    """A key row could neither be inserted nor read, try after try."""


class SqlFactRepository:
    """Every statement uses bound parameters (security rule 21)."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def find(self, key: FactSetKey) -> KeyRow | None:
        with adapter_span(tracer, "extraction.db.find_fact_set"):
            async with self._database.connect() as connection:
                return await _key_row(connection, key)

    async def begin(
        self, fact_set_id: str, key: FactSetKey, started_at: datetime
    ) -> KeyRow | None:
        """Insert the key row as running; the unique key settles calls that race."""
        with adapter_span(tracer, "extraction.db.begin_fact_set"):
            # The row in the way can be released between the insert that
            # met it and the read of it: the insert is then tried again, so
            # that None is only ever answered for a row this call inserted.
            for _ in range(BEGIN_ATTEMPTS):
                async with self._database.begin() as connection:
                    inserted = await connection.execute(
                        pg_insert(fact_set_table)
                        .values(
                            fact_set_id=fact_set_id,
                            case_id=key.case_id,
                            page_id=key.page_id,
                            status=StageStatus.RUNNING.value,
                            started_at=started_at,
                        )
                        .on_conflict_do_nothing(constraint=KEY_UNIQUE)
                        .returning(fact_set_table.c.fact_set_id)
                    )
                    if inserted.first() is not None:
                        return None
                    existing = await _key_row(connection, key)
                    if existing is not None:
                        return existing
        raise KeyRowContended

    async def take_over(self, row: KeyRow, started_at: datetime) -> bool:
        """Make a row left running this call's own; of calls that race, one wins."""
        with adapter_span(tracer, "extraction.db.take_over_fact_set"):
            async with self._database.begin() as connection:
                taken = await connection.execute(
                    update(fact_set_table)
                    .where(
                        fact_set_table.c.fact_set_id == row.fact_set_id,
                        fact_set_table.c.status == StageStatus.RUNNING.value,
                        # As it was when it was found stale: a call that
                        # took it over first has moved this on.
                        fact_set_table.c.started_at == row.started_at,
                    )
                    .values(started_at=started_at)
                    .returning(fact_set_table.c.fact_set_id)
                )
                return taken.first() is not None

    async def finish(
        self, fact_set_id: str, result_json: str, facts: Sequence[Fact] | None
    ) -> str:
        """Store the result and its facts together, only if the key row is still running."""
        status = StageStatus.DONE if facts is not None else StageStatus.FAILED
        with adapter_span(tracer, "extraction.db.finish_fact_set"):
            # One transaction: the facts are stored with the result that
            # names them, or neither is.
            async with self._database.begin() as connection:
                settled = await connection.execute(
                    update(fact_set_table)
                    .where(
                        fact_set_table.c.fact_set_id == fact_set_id,
                        fact_set_table.c.status == StageStatus.RUNNING.value,
                    )
                    .values(
                        status=status.value, result=result_json, finished_at=func.now()
                    )
                    .returning(fact_set_table.c.fact_set_id)
                )
                if settled.first() is None:
                    # Settled by another call: that result stands.
                    stored = await connection.execute(
                        select(fact_set_table.c.result).where(
                            fact_set_table.c.fact_set_id == fact_set_id
                        )
                    )
                    return str(stored.scalar_one())
                if facts:
                    await connection.execute(
                        insert(fact_table),
                        [
                            {
                                "fact_id": fact.fact_id,
                                "fact_set_id": fact_set_id,
                                "case_id": fact.case_id,
                                "page_id": fact.page_id,
                                "page_number": fact.page_number,
                                "position": position,
                                "statement": fact.statement,
                                "quote": fact.quote,
                                "quote_verified": fact.quote_verified,
                                "quote_start": fact.quote_start,
                                "quote_end": fact.quote_end,
                            }
                            for position, fact in enumerate(facts)
                        ],
                    )
                return result_json

    async def release(self, fact_set_id: str) -> None:
        """Remove the key row, only while it is still running."""
        with adapter_span(tracer, "extraction.db.release_fact_set"):
            async with self._database.begin() as connection:
                await connection.execute(
                    delete(fact_set_table).where(
                        fact_set_table.c.fact_set_id == fact_set_id,
                        fact_set_table.c.status == StageStatus.RUNNING.value,
                    )
                )

    async def of_case(self, case_id: str) -> list[Fact]:
        with adapter_span(tracer, "extraction.db.facts_of_case"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(fact_table)
                    .where(fact_table.c.case_id == case_id)
                    # Page order, then the order the facts were stored in;
                    # the fact set's id keeps one order should two pages
                    # ever share a number.
                    .order_by(
                        fact_table.c.page_number,
                        fact_table.c.fact_set_id,
                        fact_table.c.position,
                    )
                )
                return [
                    Fact(
                        fact_id=row.fact_id,
                        case_id=row.case_id,
                        page_id=row.page_id,
                        page_number=row.page_number,
                        statement=row.statement,
                        quote=row.quote,
                        quote_verified=row.quote_verified,
                        quote_start=row.quote_start,
                        quote_end=row.quote_end,
                    )
                    for row in result
                ]


async def _key_row(connection: AsyncConnection, key: FactSetKey) -> KeyRow | None:
    result = await connection.execute(
        select(fact_set_table).where(
            fact_set_table.c.case_id == key.case_id,
            fact_set_table.c.page_id == key.page_id,
        )
    )
    row = result.first()
    if row is None:
        return None
    return KeyRow(
        fact_set_id=row.fact_set_id,
        key=FactSetKey(row.case_id, row.page_id),
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
        with adapter_span(tracer, "extraction.db.read_revision"):
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
