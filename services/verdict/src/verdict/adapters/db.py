"""PostgreSQL adapter: the tables of schema `verdict` and their repository (AD-4, AD-6, AD-15)."""

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from psycopg import errors as pg_errors
from sqlalchemy import (
    URL,
    BigInteger,
    CheckConstraint,
    Column,
    ColumnElement,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Row,
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from contracts.enums import (
    ReasonEffect,
    RetrieverConfig,
    StageStatus,
    StepOutcome,
    SystemReason,
    ToolName,
    Verdict,
)
from contracts.errors import ErrorCode
from contracts.models.verdict import (
    MAX_ASKED_TOOL_CHARS,
    AgentStep,
    Reason,
    VerdictRun,
)
from verdict.adapters.credential import azure_credential
from verdict.adapters.telemetry import adapter_span
from verdict.domain.entities import KeyRow, RunKey, Suggestion
from verdict.domain.ports import KeyRowGone
from verdict.domain.run import verdict_run
from verdict.settings import APP_ID, SCHEMA, Settings

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

# The scope of an Entra token for Azure Database for PostgreSQL.
POSTGRESQL_TOKEN_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"  # noqa: S105 - a public scope name, not a credential
VERSION_TABLE = "alembic_version"
# A token this close to its end is replaced before it is used.
TOKEN_REFRESH_MARGIN_SECONDS = 300
VERDICT_RUN_TABLE = "verdict_run"
REASON_TABLE = "reason"
AGENT_STEP_TABLE = "agent_step"
# AD-6: one run per case and retriever configuration.
KEY_UNIQUE = "uq_verdict_verdict_run_case_retriever_config"

logger = logging.getLogger(__name__)

tracer = trace.get_tracer(APP_ID)

# Conventions: snake_case, singular table names; ids are UUIDv7, kept as
# canonical lower-case strings in code. A column has the name its field has
# in the API.
metadata = MetaData(schema=SCHEMA)

# AD-6: the key row of one run. Inserted as `running` before any work.
# `result` is the stored stage result as JSON. The other columns are what a
# done run suggests, every one set by domain code (AD-15): no column holds
# the agent's own verdict word, and none holds a decision (AD-10). `case_id`
# is `intake`'s id: an id column only, no foreign key into another service's
# schema (AD-4).
verdict_run_table = Table(
    VERDICT_RUN_TABLE,
    metadata,
    Column("verdict_run_id", Uuid(as_uuid=False), primary_key=True),
    Column("case_id", Uuid(as_uuid=False), nullable=False),
    Column("retriever_config", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("result", Text, nullable=True),
    Column("verdict", Text, nullable=True),
    Column("loading_pct", Integer, nullable=True),
    Column("confidence", Float, nullable=True),
    Column("system_reasons", ARRAY(Text), nullable=False, server_default="{}"),
    # Set when, and only when, the run failed.
    Column("error_code", Text, nullable=True),
    UniqueConstraint("case_id", "retriever_config", name=KEY_UNIQUE),
    CheckConstraint(
        "(verdict = 'loaded') = (loading_pct IS NOT NULL)",
        name="ck_verdict_verdict_run_loading_follows_verdict",
    ),
)

# AD-15: one row per stored reason, written with its run's result and never
# changed: a rule, the facts it was applied to, and its effect. `fact_ids`
# are `extraction`'s ids. Only reasons the run could bear out are ever here.
reason_table = Table(
    REASON_TABLE,
    metadata,
    Column(
        "verdict_run_id",
        Uuid(as_uuid=False),
        ForeignKey(verdict_run_table.c.verdict_run_id),
        nullable=False,
    ),
    # The reason's place among its run's reasons.
    Column("position", Integer, nullable=False),
    Column("rule_id", Text, nullable=False),
    Column("fact_ids", ARRAY(Uuid(as_uuid=False)), nullable=False),
    Column("effect", Text, nullable=False),
    Column("debit_pct", Integer, nullable=True),
    PrimaryKeyConstraint("verdict_run_id", "position", name="pk_verdict_reason"),
    CheckConstraint(
        "(effect = 'debit') = (debit_pct IS NOT NULL)",
        name="ck_verdict_reason_debit_follows_effect",
    ),
)

# AD-15: the agent's step log, one row per tool call. `tool` is one of the
# three tools, or null for a call to a tool that does not exist: then
# `asked_tool` holds the name the model asked for (migration 0002). Append-only: this
# module has an INSERT for it and no UPDATE or DELETE, the service's
# database role is granted neither, and a trigger refuses both for every
# role (migration 0001). No foreign key to the run: a step is written while
# its run is under way, and the steps of a run whose key row was given up
# (a request cancelled half way) stay in the log.
agent_step_table = Table(
    AGENT_STEP_TABLE,
    metadata,
    Column("verdict_run_id", Uuid(as_uuid=False), nullable=False),
    Column("step_no", Integer, nullable=False),
    Column("case_id", Uuid(as_uuid=False), nullable=False),
    Column("tool", Text, nullable=True),
    Column("asked_tool", Text, nullable=True),
    Column("arguments", JSONB, nullable=False),
    Column("fact_id", Uuid(as_uuid=False), nullable=True),
    # The rules the call returned or read.
    Column("rule_ids", ARRAY(Text), nullable=False),
    Column("outcome", Text, nullable=False),
    # Set when, and only when, the call was refused or failed.
    Column("error_code", Text, nullable=True),
    Column("latency_ms", Integer, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    # The order the steps were logged in, numbered by the database at the
    # insert: the order of a case's steps across its runs.
    Column("agent_step_seq", BigInteger, Identity(always=True), nullable=False),
    PrimaryKeyConstraint("verdict_run_id", "step_no", name="pk_verdict_agent_step"),
    Index("ix_verdict_agent_step_case_id_agent_step_seq", "case_id", "agent_step_seq"),
    CheckConstraint(
        "(tool IS NULL) = (asked_tool IS NOT NULL)",
        name="ck_verdict_agent_step_asked_tool_stands_in_for_no_tool",
    ),
    CheckConstraint(
        f"char_length(asked_tool) <= {MAX_ASKED_TOOL_CHARS}",
        name="ck_verdict_agent_step_asked_tool_is_bounded",
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


def _utc(value: datetime) -> datetime:
    # The server hands a timestamp back in the session's time zone; the
    # contracts take UTC only.
    return value.astimezone(UTC)


def _key_row(row: Row[Any]) -> KeyRow:
    return KeyRow(
        verdict_run_id=row.verdict_run_id,
        key=RunKey(row.case_id, RetrieverConfig(row.retriever_config)),
        started_at=row.started_at,
        result_json=row.result,
    )


def _step(row: Row[Any]) -> AgentStep:
    return AgentStep(
        verdict_run_id=row.verdict_run_id,
        case_id=row.case_id,
        step_no=row.step_no,
        tool=ToolName(row.tool) if row.tool is not None else None,
        asked_tool=row.asked_tool,
        arguments=row.arguments,
        fact_id=row.fact_id,
        rule_ids=list(row.rule_ids),
        outcome=StepOutcome(row.outcome),
        error_code=ErrorCode(row.error_code) if row.error_code is not None else None,
        latency_ms=row.latency_ms,
        occurred_at=_utc(row.occurred_at),
    )


def _run(row: Row[Any], reasons: list[Reason]) -> VerdictRun:
    """One stored run as it is read; what a done run suggests is rebuilt from its columns."""
    suggestion = None
    if row.verdict is not None:
        suggestion = Suggestion(
            verdict=Verdict(row.verdict),
            loading_pct=row.loading_pct,
            confidence=row.confidence,
            reasons=tuple(reasons),
            system_reasons=tuple(SystemReason(reason) for reason in row.system_reasons),
        )
    return verdict_run(
        row.verdict_run_id,
        RunKey(row.case_id, RetrieverConfig(row.retriever_config)),
        StageStatus(row.status),
        suggestion,
        ErrorCode(row.error_code) if row.error_code is not None else None,
    )


class SqlRunRepository:
    """Every statement uses bound parameters (security rule 21).

    The step log is only ever inserted into and read (AD-15, security rule 32).
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def find(self, key: RunKey) -> KeyRow | None:
        with adapter_span(tracer, "verdict.db.find_verdict_run"):
            async with self._database.connect() as connection:
                return await _find(connection, key)

    async def begin(
        self, verdict_run_id: str, key: RunKey, started_at: datetime
    ) -> KeyRow | None:
        """Insert the key row as running; the unique key settles calls that race."""
        with adapter_span(tracer, "verdict.db.begin_verdict_run"):
            # The row in the way can be released between the insert that
            # met it and the read of it: the insert is then tried again, so
            # that None is only ever answered for a row this call inserted.
            for _ in range(BEGIN_ATTEMPTS):
                async with self._database.begin() as connection:
                    inserted = await connection.execute(
                        pg_insert(verdict_run_table)
                        .values(
                            verdict_run_id=verdict_run_id,
                            case_id=key.case_id,
                            retriever_config=key.retriever_config.value,
                            status=StageStatus.RUNNING.value,
                            started_at=started_at,
                        )
                        .on_conflict_do_nothing(constraint=KEY_UNIQUE)
                        .returning(verdict_run_table.c.verdict_run_id)
                    )
                    if inserted.first() is not None:
                        return None
                    existing = await _find(connection, key)
                    if existing is not None:
                        return existing
        raise KeyRowContended

    async def finish(
        self,
        verdict_run_id: str,
        result_json: str,
        suggestion: Suggestion | None,
        error_code: ErrorCode | None,
    ) -> str:
        """Store the result, and a done run's suggestion with its reasons, only if the key row is still running."""
        status = StageStatus.DONE if suggestion is not None else StageStatus.FAILED
        values: dict[str, object] = {
            "status": status.value,
            "result": result_json,
            "finished_at": func.now(),
            "error_code": error_code.value if error_code is not None else None,
        }
        if suggestion is not None:
            values |= {
                "verdict": suggestion.verdict.value,
                "loading_pct": suggestion.loading_pct,
                "confidence": suggestion.confidence,
                "system_reasons": [
                    reason.value for reason in suggestion.system_reasons
                ],
            }
        with adapter_span(tracer, "verdict.db.finish_verdict_run"):
            # One transaction: the reasons are stored with the result that
            # they belong to, or neither is.
            async with self._database.begin() as connection:
                settled = await connection.execute(
                    update(verdict_run_table)
                    .where(
                        verdict_run_table.c.verdict_run_id == verdict_run_id,
                        verdict_run_table.c.status == StageStatus.RUNNING.value,
                    )
                    .values(**values)
                    .returning(verdict_run_table.c.verdict_run_id)
                )
                if settled.first() is None:
                    # Settled by another call: that result stands.
                    stored = await connection.execute(
                        select(verdict_run_table.c.result).where(
                            verdict_run_table.c.verdict_run_id == verdict_run_id
                        )
                    )
                    held = stored.scalar_one_or_none()
                    if held is None:
                        # Released by the call that owned it, or by a
                        # cancellation: there is nothing to settle.
                        raise KeyRowGone
                    return str(held)
                if suggestion is not None and suggestion.reasons:
                    await connection.execute(
                        insert(reason_table),
                        [
                            {
                                "verdict_run_id": verdict_run_id,
                                "position": position,
                                "rule_id": reason.rule_id,
                                "fact_ids": list(reason.fact_ids),
                                "effect": reason.effect.value,
                                "debit_pct": reason.debit_pct,
                            }
                            for position, reason in enumerate(suggestion.reasons)
                        ],
                    )
                return result_json

    async def release(self, verdict_run_id: str) -> None:
        """Remove the key row, only while it is still running. Its steps stay in the log."""
        with adapter_span(tracer, "verdict.db.release_verdict_run"):
            async with self._database.begin() as connection:
                await connection.execute(
                    delete(verdict_run_table).where(
                        verdict_run_table.c.verdict_run_id == verdict_run_id,
                        verdict_run_table.c.status == StageStatus.RUNNING.value,
                    )
                )

    async def append_step(self, step: AgentStep) -> None:
        """Add one tool call to the log."""
        with adapter_span(tracer, "verdict.db.append_agent_step"):
            async with self._database.begin() as connection:
                await connection.execute(
                    insert(agent_step_table).values(
                        verdict_run_id=step.verdict_run_id,
                        step_no=step.step_no,
                        case_id=step.case_id,
                        tool=step.tool.value if step.tool is not None else None,
                        asked_tool=step.asked_tool,
                        arguments=step.arguments,
                        fact_id=step.fact_id,
                        rule_ids=list(step.rule_ids),
                        outcome=step.outcome.value,
                        error_code=step.error_code.value
                        if step.error_code is not None
                        else None,
                        latency_ms=step.latency_ms,
                        occurred_at=step.occurred_at,
                    )
                )

    async def runs_of_case(self, case_id: str, limit: int) -> list[VerdictRun]:
        with adapter_span(tracer, "verdict.db.runs_of_case"):
            async with self._database.connect() as connection:
                rows = (
                    await connection.execute(
                        select(verdict_run_table)
                        .where(verdict_run_table.c.case_id == case_id)
                        # Oldest first; UUIDv7 ids keep one order for runs
                        # begun in the same instant.
                        .order_by(
                            verdict_run_table.c.started_at,
                            verdict_run_table.c.verdict_run_id,
                        )
                        .limit(limit)
                    )
                ).all()
                if not rows:
                    return []
                stored = await connection.execute(
                    select(reason_table)
                    .where(
                        reason_table.c.verdict_run_id.in_(
                            [row.verdict_run_id for row in rows]
                        )
                    )
                    .order_by(reason_table.c.verdict_run_id, reason_table.c.position)
                )
                reasons: dict[str, list[Reason]] = {}
                for reason in stored:
                    reasons.setdefault(reason.verdict_run_id, []).append(
                        Reason(
                            rule_id=reason.rule_id,
                            fact_ids=list(reason.fact_ids),
                            effect=ReasonEffect(reason.effect),
                            debit_pct=reason.debit_pct,
                        )
                    )
                return [_run(row, reasons.get(row.verdict_run_id, [])) for row in rows]

    async def run_exists(self, verdict_run_id: str) -> bool:
        with adapter_span(tracer, "verdict.db.find_verdict_run_by_id"):
            async with self._database.connect() as connection:
                found = await connection.execute(
                    select(verdict_run_table.c.verdict_run_id).where(
                        verdict_run_table.c.verdict_run_id == verdict_run_id
                    )
                )
                return found.first() is not None

    async def steps_of_run(
        self,
        verdict_run_id: str,
        tool: ToolName | None,
        rule_id: str | None,
        after_step_no: int | None,
        limit: int,
    ) -> list[AgentStep]:
        only = [
            agent_step_table.c.verdict_run_id == verdict_run_id,
            *_matching(tool, rule_id),
        ]
        if after_step_no is not None:
            only.append(agent_step_table.c.step_no > after_step_no)
        with adapter_span(tracer, "verdict.db.steps_of_run"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(agent_step_table)
                    .where(*only)
                    .order_by(agent_step_table.c.step_no)
                    .limit(limit)
                )
                return [_step(row) for row in result]

    async def steps_of_case(
        self,
        case_id: str,
        tool: ToolName | None,
        rule_id: str | None,
        after: tuple[str, int] | None,
        limit: int,
    ) -> list[AgentStep]:
        only = [agent_step_table.c.case_id == case_id, *_matching(tool, rule_id)]
        if after is not None:
            # The place in the log of the step the cursor names. A step the
            # case does not have gives no place, and then nothing is listed.
            after_run, after_step_no = after
            only.append(
                agent_step_table.c.agent_step_seq
                > select(agent_step_table.c.agent_step_seq)
                .where(
                    agent_step_table.c.case_id == case_id,
                    agent_step_table.c.verdict_run_id == after_run,
                    agent_step_table.c.step_no == after_step_no,
                )
                .scalar_subquery()
            )
        with adapter_span(tracer, "verdict.db.steps_of_case"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(agent_step_table)
                    .where(*only)
                    .order_by(agent_step_table.c.agent_step_seq)
                    .limit(limit)
                )
                return [_step(row) for row in result]


def _matching(tool: ToolName | None, rule_id: str | None) -> list[ColumnElement[bool]]:
    """The two filters of the step reads, as conditions on the step log."""
    only: list[ColumnElement[bool]] = []
    if tool is not None:
        # One of the three tools: a step that asked for a tool that does
        # not exist has none, and is listed only when no tool is named.
        only.append(agent_step_table.c.tool == tool.value)
    if rule_id is not None:
        # The rule is among those the call returned or read.
        only.append(agent_step_table.c.rule_ids.contains([rule_id]))
    return only


async def _find(connection: AsyncConnection, key: RunKey) -> KeyRow | None:
    result = await connection.execute(
        select(verdict_run_table).where(
            verdict_run_table.c.case_id == key.case_id,
            verdict_run_table.c.retriever_config == key.retriever_config.value,
        )
    )
    row = result.first()
    return _key_row(row) if row is not None else None


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
        with adapter_span(tracer, "verdict.db.read_revision"):
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
