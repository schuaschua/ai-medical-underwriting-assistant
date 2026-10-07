"""PostgreSQL adapter: the chunk table of schema `retrieval` and its repository (AD-4, AD-12)."""

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from pgvector.sqlalchemy import VECTOR
from psycopg import errors as pg_errors
from sqlalchemy import (
    URL,
    Column,
    Computed,
    DateTime,
    Engine,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    delete,
    event,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from contracts.enums import ChunkSet
from retrieval.adapters.credential import azure_credential
from retrieval.adapters.telemetry import adapter_span
from retrieval.domain.entities import (
    EMBEDDING_DIMENSIONS,
    ChunkRecord,
    IngestRun,
    StoredChunk,
)
from retrieval.domain.ports import IndexChanged
from retrieval.settings import APP_ID, SCHEMA, Settings

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

# The scope of an Entra token for Azure Database for PostgreSQL.
POSTGRESQL_TOKEN_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"  # noqa: S105 - a public scope name, not a credential
VERSION_TABLE = "alembic_version"
# A token this close to its end is replaced before it is used.
TOKEN_REFRESH_MARGIN_SECONDS = 300

# The text-search configuration of the stored full-text column. A search
# must use the same one (story 2.3).
TEXT_SEARCH_CONFIG = "english"
# The text-search parser would split a rule id at its dashes and keep
# `UW-DM-001` as `uw-dm`, `uw`, `dm` and `001`, so that every rule shares
# words with every other. Before the text is parsed, each id is written as one
# word (`UWDM001`): a rule id in a query, written the same way, then finds the
# chunks that name that rule and no others. The pattern is the contracts'
# `RULE_ID_PATTERN` with its three parts as groups, in PostgreSQL's syntax.
RULE_ID_AS_WORDS = r"\mUW-([A-Z]{2,4})-([0-9]{3})\M"
RULE_ID_AS_ONE_WORD = r"UW\1\2"
FULL_TEXT_INDEX = "ix_chunk_text_search"


def full_text_of(column: str) -> str:
    """The SQL of the full-text value of a text column, or of a bound parameter.

    It builds the stored column. A search reads its query with the same
    configuration and the same rewriting of rule ids (`adapters/index.py`,
    story 2.3). Its inputs are constants of this module and a column name:
    no value is ever put into it.
    """
    return (
        f"to_tsvector('{TEXT_SEARCH_CONFIG}'::regconfig, "
        f"regexp_replace({column}, '{RULE_ID_AS_WORDS}', "
        f"'{RULE_ID_AS_ONE_WORD}', 'g'))"
    )


logger = logging.getLogger(__name__)

tracer = trace.get_tracer(APP_ID)

# Conventions: snake_case, singular table names.
metadata = MetaData(schema=SCHEMA)

# AD-12: one row per chunk, the one source for every store. `chunk_id` is
# derived from the chunk set and the rule, so a rerun writes the same key.
# `rule_ids` are only the rules the text defines; the rules it refers to are
# kept apart, in `reference_rule_ids`. `embedding` is the vector of the
# context line followed by the text. There is no approximate index on it:
# search is exact (AD-12), and a hundred chunks need none.
chunk_table = Table(
    "chunk",
    metadata,
    Column("chunk_id", String(64), primary_key=True),
    Column("chunk_set", String(16), nullable=False),
    Column("rule_ids", ARRAY(String(16)), nullable=False),
    Column("reference_rule_ids", ARRAY(String(16)), nullable=False),
    # The numbered part of the manual the rule is printed in, and its heading.
    Column("section_id", String(16), nullable=False),
    Column("section_title", Text, nullable=False),
    Column("impairment", Text, nullable=False),
    Column("manual_page", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("context_line", Text, nullable=False),
    Column("embedding", VECTOR(EMBEDDING_DIMENSIONS), nullable=False),
    # What the context line and the vector were made from, and with which
    # deployments: a rerun calls a model only where the hash differs.
    Column("content_hash", String(64), nullable=False),
    Column("chat_deployment", String(128), nullable=False),
    Column("embedding_deployment", String(128), nullable=False),
    Column(
        "ingested_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    # Hybrid search (story 2.3): the chunk text as a stored full-text value,
    # kept by the database itself.
    Column("text_search", TSVECTOR, Computed(full_text_of("text"), persisted=True)),
    Index("ix_chunk_chunk_set", "chunk_set"),
    Index(FULL_TEXT_INDEX, "text_search", postgresql_using="gin"),
)

# What each chunk set was last built from, by a run that succeeded: the
# manual's SHA-256, the prompt's digest and the two deployment names. One row
# per chunk set, written in the transaction that stores the run's chunks, so
# the index can always say which manual it holds. Nothing serves it over HTTP.
ingest_run_table = Table(
    "ingest_run",
    metadata,
    Column("chunk_set", String(16), primary_key=True),
    Column("manual_sha256", String(64), nullable=False),
    Column("prompt_digest", String(64), nullable=False),
    Column("chat_deployment", String(128), nullable=False),
    Column("embedding_deployment", String(128), nullable=False),
    Column("chunk_count", Integer, nullable=False),
    Column(
        "ingested_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
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
    deployments and Document Intelligence are signed in to with one for
    their own scope. Fetching a token is a blocking network call. The service
    fetches it on a worker thread with `refresh` before it opens a
    connection, and does so well ahead of the token's end, so the connection hook (`value`) always
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


class SqlChunkRepository:
    """Every statement uses bound parameters (security rule 21)."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def stored(self, chunk_set: ChunkSet) -> Mapping[str, StoredChunk]:
        with adapter_span(tracer, "retrieval.db.stored_chunks"):
            async with self._database.connect() as connection:
                return await _stored(connection, chunk_set)

    async def last_run(self, chunk_set: ChunkSet) -> IngestRun | None:
        with adapter_span(tracer, "retrieval.db.last_ingest_run"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(ingest_run_table).where(
                        ingest_run_table.c.chunk_set == chunk_set.value
                    )
                )
                row = result.first()
        if row is None:
            return None
        return IngestRun(
            manual_sha256=row.manual_sha256,
            prompt_digest=row.prompt_digest,
            chat_deployment=row.chat_deployment,
            embedding_deployment=row.embedding_deployment,
            chunk_count=row.chunk_count,
        )

    async def apply(
        self,
        chunk_set: ChunkSet,
        *,
        planned_from: Mapping[str, StoredChunk],
        write: Sequence[ChunkRecord],
        move: Mapping[str, int],
        remove: Sequence[str],
        run: IngestRun,
    ) -> None:
        """One transaction per run: all of it is stored, or none of it."""
        with adapter_span(tracer, "retrieval.db.apply_chunks") as span:
            span.set_attribute("retrieval.chunks.written", len(write))
            span.set_attribute("retrieval.chunks.moved", len(move))
            span.set_attribute("retrieval.chunks.removed", len(remove))
            async with self._database.begin() as connection:
                # One run at a time writes a chunk set: the lock is held to
                # the end of the transaction. A run that waited for it then
                # finds what the other one stored, not what it planned from,
                # and writes nothing: its plan would undo the other's work.
                await connection.execute(
                    select(
                        func.pg_advisory_xact_lock(
                            func.hashtext(f"{SCHEMA}.chunk:{chunk_set.value}")
                        )
                    )
                )
                if await _stored(connection, chunk_set) != dict(planned_from):
                    raise IndexChanged
                for record in write:
                    await connection.execute(_upsert(record))
                for chunk_id, manual_page in move.items():
                    await connection.execute(
                        update(chunk_table)
                        .where(
                            chunk_table.c.chunk_id == chunk_id,
                            chunk_table.c.chunk_set == chunk_set.value,
                        )
                        .values(manual_page=manual_page)
                    )
                if remove:
                    await connection.execute(
                        delete(chunk_table).where(
                            chunk_table.c.chunk_id.in_(list(remove)),
                            # A run never reaches into another chunk set.
                            chunk_table.c.chunk_set == chunk_set.value,
                        )
                    )
                await connection.execute(_record_run(chunk_set, run))


async def _stored(
    connection: AsyncConnection, chunk_set: ChunkSet
) -> dict[str, StoredChunk]:
    result = await connection.execute(
        select(
            chunk_table.c.chunk_id,
            chunk_table.c.content_hash,
            chunk_table.c.manual_page,
        ).where(chunk_table.c.chunk_set == chunk_set.value)
    )
    return {
        row.chunk_id: StoredChunk(row.chunk_id, row.content_hash, row.manual_page)
        for row in result
    }


def _record_run(chunk_set: ChunkSet, run: IngestRun) -> Any:
    """Note what the chunk set was built from, in place of the note before it."""
    values: dict[str, Any] = {
        "manual_sha256": run.manual_sha256,
        "prompt_digest": run.prompt_digest,
        "chat_deployment": run.chat_deployment,
        "embedding_deployment": run.embedding_deployment,
        "chunk_count": run.chunk_count,
    }
    return (
        pg_insert(ingest_run_table)
        .values(chunk_set=chunk_set.value, **values)
        .on_conflict_do_update(
            index_elements=[ingest_run_table.c.chunk_set],
            set_={**values, "ingested_at": func.now()},
        )
    )


def _upsert(record: ChunkRecord) -> Any:
    """Insert one record, or replace the one stored under its id."""
    chunk = record.chunk
    values: dict[str, Any] = {
        "chunk_set": chunk.chunk_set.value,
        "rule_ids": list(chunk.rule_ids),
        "reference_rule_ids": list(chunk.reference_rule_ids),
        "section_id": chunk.section_id,
        "section_title": chunk.section_title,
        "impairment": chunk.impairment,
        "manual_page": chunk.manual_page,
        "text": chunk.text,
        "context_line": record.context_line,
        "embedding": list(record.embedding),
        "content_hash": record.content_hash,
        "chat_deployment": record.chat_deployment,
        "embedding_deployment": record.embedding_deployment,
    }
    return (
        pg_insert(chunk_table)
        .values(chunk_id=chunk.chunk_id, **values)
        .on_conflict_do_update(
            index_elements=[chunk_table.c.chunk_id],
            set_={**values, "ingested_at": func.now()},
        )
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
        with adapter_span(tracer, "retrieval.db.read_revision"):
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
