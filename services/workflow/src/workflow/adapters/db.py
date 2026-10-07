"""PostgreSQL adapter: the tables of schema `workflow` and the case store (AD-4, AD-8)."""

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
from pydantic import BaseModel
from sqlalchemy import (
    URL,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Row,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    exists,
    func,
    insert,
    select,
    update,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from contracts.audit import AuditAction, AuditRecord, RouteDetail
from contracts.decisions import STATUSES_AWAITING_A_DECISION
from contracts.enums import (
    ActorKind,
    CaseStatus,
    ClassifierContender,
    Decision,
    DemoRole,
    PageStatus,
    RetrieverConfig,
    StageStatus,
    StopAfter,
)
from contracts.errors import ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import (
    AuditTrail,
    CaseList,
    CaseProgress,
    CaseSummary,
    PageProgress,
    PageQueue,
    QueuedPage,
)
from workflow.adapters.credential import azure_credential
from workflow.adapters.telemetry import adapter_span
from workflow.domain.case_list import waiting_page_count
from workflow.domain.case_status import case_status_after_gate, case_status_following
from workflow.domain.decisions import (
    CASE_STATUSES_TAKING_DECISIONS,
    case_takes_decisions,
)
from workflow.domain.entities import (
    CaseRecord,
    PageDecision,
    SettledCase,
    StartParameters,
)
from workflow.domain.queue import queued_by
from workflow.domain.recording import (
    Decided,
    DecisionOutcome,
    Recording,
    RecordOutcome,
    case_completed_event,
)
from workflow.domain.transitions import (
    CASE_STATUSES_TAKING_RESULTS,
    CASE_TRANSITIONS,
    case_statuses_before,
    page_statuses_before,
    redaction_statuses_before,
)
from workflow.settings import APP_ID, SCHEMA, Settings

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

# The scope of an Entra token for Azure Database for PostgreSQL.
POSTGRESQL_TOKEN_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"  # noqa: S105 - a public scope name, not a credential
VERSION_TABLE = "alembic_version"
# A token this close to its end is replaced before it is used.
TOKEN_REFRESH_MARGIN_SECONDS = 300

CASE_STATUS_TABLE = "case_status"
PAGE_STATUS_TABLE = "page_status"
AUDIT_EVENT_TABLE = "audit_event"
HUMAN_DECISION_TABLE = "human_decision"
DECISION_TOLD_TABLE = "decision_told"
# AD-8: one event per case, page, action and reference; a null page counts as
# one value, so a case-level event cannot be written twice either.
AUDIT_EVENT_UNIQUE = "uq_workflow_audit_event_subject"
# A case is started once and completed once, whatever reference a row names
# (migration 0005): the trail is append-only, so a second such event could
# never be taken back.
ONE_CASE_STARTED = "uq_workflow_audit_event_case_started"
ONE_CASE_COMPLETED = "uq_workflow_audit_event_case_completed"

logger = logging.getLogger(__name__)

tracer = trace.get_tracer(APP_ID)

# Conventions: snake_case, singular table names; ids are UUIDv7, kept as
# canonical lower-case strings in code. A status column has the name its
# field has in the API. Cross-service references (the case, its pages) are
# plain id columns: the rows they name belong to `intake` (AD-4).
metadata = MetaData(schema=SCHEMA)

case_status_table = Table(
    CASE_STATUS_TABLE,
    metadata,
    Column("case_id", Uuid(as_uuid=False), primary_key=True),
    Column("case_status", Text, nullable=False),
    Column("redaction_status", Text, nullable=False),
    # What the case was started with (spine, Operations: `start`).
    Column("classifier_contender", Text, nullable=False),
    Column("retriever_configs", ARRAY(Text), nullable=False),
    Column("stop_after", Text, nullable=True),
    Column("eval_run_id", Uuid(as_uuid=False), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

page_status_table = Table(
    PAGE_STATUS_TABLE,
    metadata,
    Column("page_id", Uuid(as_uuid=False), primary_key=True),
    Column(
        "case_id",
        Uuid(as_uuid=False),
        ForeignKey(case_status_table.c.case_id),
        nullable=False,
    ),
    Column("page_number", Integer, nullable=False),
    Column("page_status", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "case_id", "page_number", name="uq_workflow_page_status_case_id_page_number"
    ),
)

# AD-8: the only audit table. Append-only: this module has an INSERT for it
# and no UPDATE or DELETE, and the service's database role is granted neither.
audit_event_table = Table(
    AUDIT_EVENT_TABLE,
    metadata,
    Column("audit_event_id", Uuid(as_uuid=False), primary_key=True),
    Column("actor_kind", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    # Set by the service that did the work.
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column(
        "case_id",
        Uuid(as_uuid=False),
        ForeignKey(case_status_table.c.case_id),
        nullable=False,
    ),
    Column("page_id", Uuid(as_uuid=False), nullable=True),
    Column("ref", Uuid(as_uuid=False), nullable=False),
    # A missing detail is SQL NULL, not the JSON value `null`.
    Column("detail", JSONB(none_as_null=True), nullable=True),
    # For `stage.failed`: the code of the failure, from the error catalogue.
    Column("error_code", Text, nullable=True),
    Column("trace_id", String(32), nullable=False),
    Column("eval_run_id", Uuid(as_uuid=False), nullable=True),
    # When `workflow` wrote the event.
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    # The order the events were written in: numbered by the database at the
    # insert, never by this module. A case's events are inserted one after
    # the other under the lock on its row, so within a case the number is
    # the order of writing, whatever any clock says.
    Column("audit_event_seq", BigInteger, Identity(always=True), nullable=False),
    UniqueConstraint(
        "case_id",
        "page_id",
        "action",
        "ref",
        name=AUDIT_EVENT_UNIQUE,
        postgresql_nulls_not_distinct=True,
    ),
    Index(
        "ix_workflow_audit_event_case_id_audit_event_seq", "case_id", "audit_event_seq"
    ),
    Index(
        ONE_CASE_STARTED,
        "case_id",
        unique=True,
        postgresql_where=sa_text(f"action = '{AuditAction.CASE_STARTED.value}'"),
    ),
    Index(
        ONE_CASE_COMPLETED,
        "case_id",
        unique=True,
        postgresql_where=sa_text(f"action = '{AuditAction.CASE_COMPLETED.value}'"),
    ),
)

# AD-10: the decisions people made, one row each. Like the trail it is only
# ever added to: this module has an INSERT for it and no UPDATE or DELETE, and
# the service's database role is granted neither. A page takes each decision
# once, so the same decision sent again finds its row.
human_decision_table = Table(
    HUMAN_DECISION_TABLE,
    metadata,
    Column("decision_id", Uuid(as_uuid=False), primary_key=True),
    Column(
        "case_id",
        Uuid(as_uuid=False),
        ForeignKey(case_status_table.c.case_id),
        nullable=False,
    ),
    Column(
        "page_id",
        Uuid(as_uuid=False),
        ForeignKey(page_status_table.c.page_id),
        nullable=False,
    ),
    Column("decision", Text, nullable=False),
    # The demo role that decided.
    Column("actor", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "page_id", "decision", name="uq_workflow_human_decision_page_id_decision"
    ),
    # AD-10: the database too takes a decision from a demo role only.
    CheckConstraint(
        "actor IN ('customer', 'underwriter')", name="ck_workflow_human_decision_actor"
    ),
    CheckConstraint(
        "decision IN ('keep', 'discard', 'accept', 'deny')",
        name="ck_workflow_human_decision_decision",
    ),
    Index("ix_workflow_human_decision_case_id", "case_id"),
)

# AD-5: the mark that a case's orchestration was told of a stored decision,
# one row per decision told. A table of its own, so that the decision's row
# is never changed: like it, this one is only ever added to. A decision
# without a row here is told again by the service (`domain/decisions.py`).
decision_told_table = Table(
    DECISION_TOLD_TABLE,
    metadata,
    Column(
        "decision_id",
        Uuid(as_uuid=False),
        ForeignKey(human_decision_table.c.decision_id),
        primary_key=True,
    ),
    Column("told_at", DateTime(timezone=True), nullable=False),
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
    """The Entra token that is the database password in Azure (security rule 9).

    Fetching a token is a blocking network call. The service fetches it on a
    worker thread with `refresh` before it opens a connection, and does so
    well ahead of the token's end, so the connection hook (`value`) always
    finds a valid one in memory and the event loop is never held up.
    """

    def __init__(
        self, credential: "TokenCredential", clock: Callable[[], float] = time.time
    ) -> None:
        self._credential = credential
        self._clock = clock
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
                token = self._credential.get_token(POSTGRESQL_TOKEN_SCOPE)
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
        # A fixed number of connections: the worker's concurrency is sized
        # against it (settings).
        pool_size=settings.database_pool_size,
        max_overflow=0,
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


class _Refused(Exception):
    """Ends a recording's transaction without writing anything."""

    def __init__(self, outcome: RecordOutcome) -> None:
        super().__init__(outcome.value)
        self.outcome = outcome


def _values(statuses: frozenset[Any]) -> list[str]:
    return sorted(status.value for status in statuses)


def _utc(value: datetime) -> datetime:
    # The server hands a timestamp back in the session's time zone; the
    # contracts take UTC only.
    return value.astimezone(UTC)


_CASE_COLUMNS = (
    case_status_table.c.case_id,
    case_status_table.c.case_status,
    case_status_table.c.redaction_status,
    case_status_table.c.classifier_contender,
    case_status_table.c.retriever_configs,
    case_status_table.c.stop_after,
    case_status_table.c.eval_run_id,
    case_status_table.c.created_at,
)


def _case(row: Row[*tuple[Any, ...]]) -> CaseRecord:
    return CaseRecord(
        case_id=row.case_id,
        case_status=CaseStatus(row.case_status),
        redaction_status=StageStatus(row.redaction_status),
        parameters=StartParameters(
            classifier_contender=ClassifierContender(row.classifier_contender),
            retriever_configs=tuple(
                RetrieverConfig(config) for config in row.retriever_configs
            ),
            stop_after=StopAfter(row.stop_after)
            if row.stop_after is not None
            else None,
            eval_run_id=row.eval_run_id,
        ),
        created_at=_utc(row.created_at),
    )


def _detail_json(detail: object) -> object:
    """An audit record's detail as the JSON the column stores."""
    return detail.model_dump(mode="json") if isinstance(detail, BaseModel) else detail


def _stored_error_code(row: Row[*tuple[Any, ...]]) -> ErrorCode | None:
    """The error code to answer for a stored event; a row that breaks the rule is not a fault.

    The code belongs to `stage.failed` and comes from the catalogue. A row
    that holds one on another action, or one the catalogue does not know (a
    newer build's, or a row written by hand), is answered all the same: one
    such row must not make the whole trail unreadable.
    """
    if row.error_code is None:
        return None
    if row.action != AuditAction.STAGE_FAILED.value:
        # security rule 31: ids only.
        logger.warning(
            "audit event error code ignored: case_id=%s audit_event_id=%s",
            row.case_id,
            row.audit_event_id,
        )
        return None
    try:
        return ErrorCode(row.error_code)
    except ValueError:
        logger.warning(
            "audit event error code unknown: case_id=%s audit_event_id=%s",
            row.case_id,
            row.audit_event_id,
        )
        return ErrorCode.STAGE_FAILED


def _audit_record(row: Row[*tuple[Any, ...]]) -> AuditRecord:
    return AuditRecord(
        actor_kind=ActorKind(row.actor_kind),
        actor=row.actor,
        action=AuditAction(row.action),
        occurred_at=_utc(row.occurred_at),
        case_id=row.case_id,
        page_id=row.page_id,
        ref=row.ref,
        detail=row.detail,
        trace_id=row.trace_id,
        eval_run_id=row.eval_run_id,
        error_code=_stored_error_code(row),
    )


def _decision(row: Row[*tuple[Any, ...]]) -> PageDecision:
    return PageDecision(
        decision_id=row.decision_id,
        case_id=row.case_id,
        page_id=row.page_id,
        decision=Decision(row.decision),
        actor=DemoRole(row.actor),
        occurred_at=_utc(row.occurred_at),
    )


def _audit_values(
    recording: Recording, recorded_at: datetime, eval_run_id: str | None
) -> dict[str, object]:
    """The row of a recording's audit event."""
    audit = recording.audit
    return {
        "audit_event_id": new_id(),
        "actor_kind": audit.actor_kind.value,
        "actor": audit.actor,
        "action": audit.action.value,
        "occurred_at": audit.occurred_at,
        "case_id": audit.case_id,
        "page_id": audit.page_id,
        "ref": audit.ref,
        "detail": _detail_json(audit.detail),
        "error_code": recording.error_code.value
        if recording.error_code is not None
        else None,
        "trace_id": audit.trace_id,
        "eval_run_id": eval_run_id,
        "recorded_at": recorded_at,
    }


async def _follow_pages(
    connection: AsyncConnection,
    case_id: str,
    current: CaseStatus,
    stop_after: StopAfter | None,
    moved_at: datetime,
    *,
    at_the_gate: bool,
    eval_run_id: str | None,
    trace_id: str | None,
    verdicts_recorded: bool = False,
) -> SettledCase:
    """Give a case the status its stored pages give it; return the case as it is afterwards.

    The caller holds the lock on the case's row, so the pages read here and
    the status set here belong together. A status that may not follow the
    current one (domain/transitions.py) is not set. A case moved to
    `completed` gets its `case.completed` event here, with the status
    (AD-8); nothing follows `completed`, so the case gets it once. A case
    whose pages are all final is completed only when `verdicts_recorded`
    says its verdict runs are in the trail (AD-15), or at the gate when it
    was told to stop there.
    """
    pages = await connection.execute(
        select(page_status_table.c.page_id, page_status_table.c.page_status).where(
            page_status_table.c.case_id == case_id
        )
    )
    page_statuses = {page.page_id: PageStatus(page.page_status) for page in pages}
    wanted = (
        case_status_after_gate(page_statuses.values(), stop_after)
        if at_the_gate
        else case_status_following(
            page_statuses.values(), verdicts_recorded=verdicts_recorded
        )
    )
    if wanted is current:
        return SettledCase(current, page_statuses)
    if wanted not in CASE_TRANSITIONS[current]:
        # The case keeps its status, and the log says its pages ask for another.
        logger.warning(
            "case status not followed: case_id=%s case_status=%s wanted=%s",
            case_id,
            current.value,
            wanted.value,
        )
        return SettledCase(current, page_statuses)
    await connection.execute(
        update(case_status_table)
        .where(case_status_table.c.case_id == case_id)
        .values(case_status=wanted.value, updated_at=moved_at)
    )
    if wanted is CaseStatus.COMPLETED:
        completed = case_completed_event(
            case_id, occurred_at=moved_at, eval_run_id=eval_run_id, trace_id=trace_id
        )
        await connection.execute(
            insert(audit_event_table).values(
                **_audit_values(Recording(audit=completed), moved_at, eval_run_id)
            )
        )
    return SettledCase(wanted, page_statuses)


class SqlCaseStore:
    """Case and page status, the decisions people made, and the audit trail.

    Every statement uses bound parameters (security rule 21). The audit table
    is only ever inserted into and read (AD-8, security rule 32).
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def start(self, case: CaseRecord, started: AuditRecord) -> CaseRecord:
        """Insert the case, with its `case.started` event, unless its id is taken.

        Returns the stored case either way.
        """
        parameters = case.parameters
        with adapter_span(tracer, "workflow.db.start_case"):
            async with self._database.begin() as connection:
                inserted = await connection.execute(
                    upsert(case_status_table)
                    .values(
                        case_id=case.case_id,
                        case_status=case.case_status.value,
                        redaction_status=case.redaction_status.value,
                        classifier_contender=parameters.classifier_contender.value,
                        retriever_configs=[
                            config.value for config in parameters.retriever_configs
                        ],
                        stop_after=parameters.stop_after.value
                        if parameters.stop_after is not None
                        else None,
                        eval_run_id=parameters.eval_run_id,
                        created_at=case.created_at,
                        updated_at=case.created_at,
                    )
                    # AD-6: the key of a start is the `case_id`; a repeat changes nothing.
                    .on_conflict_do_nothing(
                        index_elements=[case_status_table.c.case_id]
                    )
                    .returning(case_status_table.c.case_id)
                )
                if inserted.first() is not None:
                    # AD-8: the event is written where the row is, and only
                    # when it is: a repeat of the start, by whichever role,
                    # finds the case and adds nothing. A start that stores
                    # the case and then cannot reach the scheduler has its
                    # event all the same.
                    await connection.execute(
                        insert(audit_event_table).values(
                            **_audit_values(
                                Recording(audit=started),
                                case.created_at,
                                parameters.eval_run_id,
                            )
                        )
                    )
                result = await connection.execute(
                    select(*_CASE_COLUMNS).where(
                        case_status_table.c.case_id == case.case_id
                    )
                )
                return _case(result.one())

    async def status(self, case_id: str) -> CaseStatus | None:
        """The case's status, or None if the case is unknown."""
        with adapter_span(tracer, "workflow.db.read_case_status"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(case_status_table.c.case_status).where(
                        case_status_table.c.case_id == case_id
                    )
                )
                value = result.scalar_one_or_none()
        return CaseStatus(value) if value is not None else None

    async def case(self, case_id: str) -> CaseRecord | None:
        """The case as it is stored, with what it was started with; None if it is unknown."""
        with adapter_span(tracer, "workflow.db.read_case"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(*_CASE_COLUMNS).where(case_status_table.c.case_id == case_id)
                )
                row = result.first()
        return _case(row) if row is not None else None

    async def complete_case(
        self, case_id: str, completed_at: datetime, trace_id: str | None
    ) -> SettledCase | None:
        """Complete a case whose pages are final and whose verdict runs are recorded (AD-15)."""
        with adapter_span(tracer, "workflow.db.complete_case"):
            async with self._database.begin() as connection:
                # Locked, as for every recording: a result recorded at the
                # same moment is either seen here whole or written after.
                locked = await connection.execute(
                    select(
                        case_status_table.c.case_status,
                        case_status_table.c.stop_after,
                        case_status_table.c.eval_run_id,
                        case_status_table.c.retriever_configs,
                    )
                    .where(case_status_table.c.case_id == case_id)
                    .with_for_update()
                )
                case = locked.first()
                if case is None:
                    return None
                # AD-15: each `verdict.suggested` event names its retriever
                # configuration in its detail. The case needs one for each
                # configuration it was started with: another row's run, asked
                # for or recorded twice, stands in for none of them.
                recorded = await connection.execute(
                    select(audit_event_table.c.detail["retriever_config"].astext)
                    .where(
                        audit_event_table.c.case_id == case_id,
                        audit_event_table.c.action
                        == AuditAction.VERDICT_SUGGESTED.value,
                    )
                    .distinct()
                )
                verdicts_recorded = set(case.retriever_configs) <= set(
                    recorded.scalars()
                )
                if not verdicts_recorded:
                    logger.warning(
                        "case not completed: case_id=%s reason=verdict_runs_not_recorded",
                        case_id,
                    )
                return await _follow_pages(
                    connection,
                    case_id,
                    CaseStatus(case.case_status),
                    StopAfter(case.stop_after) if case.stop_after is not None else None,
                    completed_at,
                    at_the_gate=False,
                    eval_run_id=case.eval_run_id,
                    trace_id=trace_id,
                    verdicts_recorded=verdicts_recorded,
                )

    async def record(
        self, recording: Recording, recorded_at: datetime
    ) -> RecordOutcome:
        """Write the status changes and the audit event in one transaction, or neither."""
        with adapter_span(tracer, "workflow.db.record"):
            try:
                async with self._database.begin() as connection:
                    await self._record(connection, recording, recorded_at)
            except _Refused as refused:
                return refused.outcome
        return RecordOutcome.RECORDED

    async def _record(
        self, connection: AsyncConnection, recording: Recording, recorded_at: datetime
    ) -> None:
        audit = recording.audit
        if recording.case_status is CaseStatus.COMPLETED:
            # A case is completed where its `case.completed` event is
            # written with the status (`_follow_pages`), and nowhere else:
            # by `complete_case`, once its verdict runs are recorded.
            raise ValueError(
                "a recording does not complete a case: the case status follows its pages"
            )
        # The case's row is locked first, so two recordings for one case run
        # one after the other and the check below cannot be raced.
        locked = await connection.execute(
            select(
                case_status_table.c.case_status,
                case_status_table.c.stop_after,
                case_status_table.c.eval_run_id,
            )
            .where(case_status_table.c.case_id == audit.case_id)
            .with_for_update()
        )
        case = locked.first()
        if case is None:
            raise _Refused(RecordOutcome.UNKNOWN_CASE)
        case_status = case.case_status
        # AD-8: an activity that ran again finds its event and writes nothing.
        existing = await connection.execute(
            select(audit_event_table.c.audit_event_id).where(
                audit_event_table.c.case_id == audit.case_id,
                audit_event_table.c.page_id.is_not_distinct_from(audit.page_id),
                audit_event_table.c.action == audit.action.value,
                audit_event_table.c.ref == audit.ref,
            )
        )
        if existing.first() is not None:
            raise _Refused(RecordOutcome.DUPLICATE)
        if CaseStatus(case_status) not in CASE_STATUSES_TAKING_RESULTS:
            raise _Refused(RecordOutcome.CASE_FAILED)

        # Each change below is made only from a status it may follow
        # (domain/transitions.py), in the statement that makes it: a result
        # that came late or out of order matches no row and changes nothing.
        if recording.page_change is not None:
            change = recording.page_change
            this_page = (
                page_status_table.c.page_id == change.page_id,
                page_status_table.c.case_id == audit.case_id,
            )
            changed = await connection.execute(
                update(page_status_table)
                .where(
                    *this_page,
                    page_status_table.c.page_status.in_(
                        _values(
                            page_statuses_before(change.page_status, change.only_from)
                        )
                    ),
                )
                .values(page_status=change.page_status.value, updated_at=recorded_at)
                .returning(page_status_table.c.page_id)
            )
            if changed.first() is None:
                known = await connection.execute(
                    select(page_status_table.c.page_id).where(*this_page)
                )
                raise _Refused(
                    RecordOutcome.OUT_OF_ORDER
                    if known.first() is not None
                    else RecordOutcome.UNKNOWN_PAGE
                )
        if recording.new_pages:
            tracked = await connection.execute(
                select(page_status_table.c.page_id)
                .where(
                    (page_status_table.c.case_id == audit.case_id)
                    | page_status_table.c.page_id.in_(
                        [page.page_id for page in recording.new_pages]
                    )
                )
                .limit(1)
            )
            if tracked.first() is not None:
                # A second redaction result, under another ref: the pages of
                # a case are tracked once.
                raise _Refused(RecordOutcome.PAGES_ALREADY_TRACKED)
            await connection.execute(
                insert(page_status_table),
                [
                    {
                        "page_id": page.page_id,
                        "case_id": audit.case_id,
                        "page_number": page.page_number,
                        "page_status": PageStatus.UPLOADED.value,
                        "updated_at": recorded_at,
                    }
                    for page in recording.new_pages
                ],
            )
        case_changes: dict[str, object] = {}
        may_change = [case_status_table.c.case_id == audit.case_id]
        if recording.case_status is not None:
            case_changes["case_status"] = recording.case_status.value
            may_change.append(
                case_status_table.c.case_status.in_(
                    _values(case_statuses_before(recording.case_status))
                )
            )
        if recording.redaction_status is not None:
            case_changes["redaction_status"] = recording.redaction_status.value
            may_change.append(
                case_status_table.c.redaction_status.in_(
                    _values(redaction_statuses_before(recording.redaction_status))
                )
            )
        if case_changes:
            changed = await connection.execute(
                update(case_status_table)
                .where(*may_change)
                .values(**case_changes, updated_at=recorded_at)
                .returning(case_status_table.c.case_id)
            )
            if changed.first() is None:
                raise _Refused(RecordOutcome.OUT_OF_ORDER)
        # In the same transaction: if this insert fails, the status changes
        # above are rolled back with it.
        await connection.execute(
            insert(audit_event_table).values(
                **_audit_values(recording, recorded_at, audit.eval_run_id)
            )
        )
        if recording.follows_pages:
            # A page stage result moved its page: the case status follows
            # the pages, here and not later (AD-5), by the rule every
            # decision uses. It never completes the case: with its last
            # page final the case is `running`, for its verdict runs.
            await _follow_pages(
                connection,
                audit.case_id,
                CaseStatus(case_status),
                StopAfter(case.stop_after) if case.stop_after is not None else None,
                recorded_at,
                at_the_gate=False,
                eval_run_id=case.eval_run_id,
                trace_id=audit.trace_id,
            )

    async def decide(self, recording: Recording, recorded_at: datetime) -> Decided:
        """Write a human decision in one transaction, or nothing (AD-8, AD-10)."""
        with adapter_span(tracer, "workflow.db.decide"):
            # Every check comes before the first write, so a refusal leaves
            # a transaction that changed nothing.
            async with self._database.begin() as connection:
                return await self._decide(connection, recording, recorded_at)

    async def _decide(
        self, connection: AsyncConnection, recording: Recording, recorded_at: datetime
    ) -> Decided:
        audit = recording.audit
        wanted = recording.decision
        change = recording.page_change
        if wanted is None or change is None:
            raise ValueError("a decision's recording names the decision and its page")
        # The case's row is locked first, as for every recording: two
        # decisions about one case run one after the other.
        locked = await connection.execute(
            select(
                case_status_table.c.case_status,
                case_status_table.c.stop_after,
                case_status_table.c.eval_run_id,
            )
            .where(case_status_table.c.case_id == audit.case_id)
            .with_for_update()
        )
        case = locked.first()
        if case is None:
            return Decided(DecisionOutcome.UNKNOWN_CASE)
        this_page = (
            page_status_table.c.page_id == change.page_id,
            page_status_table.c.case_id == audit.case_id,
        )
        known = await connection.execute(
            select(page_status_table.c.page_id).where(*this_page)
        )
        if known.first() is None:
            return Decided(DecisionOutcome.UNKNOWN_PAGE)
        # The same decision again: answered with the stored one, whatever
        # has become of the page and the case since. The role was checked
        # against the decision before, so the stored row is that role's.
        stored = await connection.execute(
            select(human_decision_table).where(
                human_decision_table.c.page_id == wanted.page_id,
                human_decision_table.c.decision == wanted.decision.value,
            )
        )
        repeated = stored.first()
        if repeated is not None:
            return Decided(DecisionOutcome.REPEATED, _decision(repeated))
        case_status = CaseStatus(case.case_status)
        stop_after = StopAfter(case.stop_after) if case.stop_after is not None else None
        if not case_takes_decisions(case_status, stop_after):
            return Decided(DecisionOutcome.NOT_AWAITING)
        # Made only from the status the decision needs, in the statement
        # that makes it: a page that awaits something else matches no row.
        changed = await connection.execute(
            update(page_status_table)
            .where(
                *this_page,
                page_status_table.c.page_status.in_(
                    _values(page_statuses_before(change.page_status, change.only_from))
                ),
            )
            .values(page_status=change.page_status.value, updated_at=recorded_at)
            .returning(page_status_table.c.page_id)
        )
        if changed.first() is None:
            return Decided(DecisionOutcome.NOT_AWAITING)
        await connection.execute(
            insert(human_decision_table).values(
                decision_id=wanted.decision_id,
                case_id=wanted.case_id,
                page_id=wanted.page_id,
                decision=wanted.decision.value,
                actor=wanted.actor.value,
                occurred_at=wanted.occurred_at,
            )
        )
        # In the same transaction, as for every recording: if this insert
        # fails, all of the above is rolled back with it.
        await connection.execute(
            insert(audit_event_table).values(
                **_audit_values(recording, recorded_at, case.eval_run_id)
            )
        )
        # The case status follows the pages, here and not later (AD-5). A
        # decision that makes the last page final leaves the case
        # `running`, for its verdict runs: it does not complete it.
        await _follow_pages(
            connection,
            audit.case_id,
            case_status,
            stop_after,
            recorded_at,
            at_the_gate=False,
            eval_run_id=case.eval_run_id,
            trace_id=audit.trace_id,
        )
        return Decided(DecisionOutcome.RECORDED, wanted)

    async def route_of(
        self, case_id: str, page_id: str, ref: str
    ) -> RouteDetail | None:
        """The detail of the page's `page.routed` event under that reference, if it is in the trail."""
        with adapter_span(tracer, "workflow.db.read_route"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(audit_event_table.c.detail).where(
                        audit_event_table.c.case_id == case_id,
                        audit_event_table.c.page_id == page_id,
                        audit_event_table.c.action == AuditAction.PAGE_ROUTED.value,
                        audit_event_table.c.ref == ref,
                    )
                )
                detail = result.scalar_one_or_none()
        return RouteDetail.model_validate(detail) if detail is not None else None

    async def settle_case(
        self, case_id: str, settled_at: datetime, trace_id: str | None
    ) -> SettledCase | None:
        """Give the case the status its pages give it after the gate; return the case as it is now."""
        with adapter_span(tracer, "workflow.db.settle_case"):
            async with self._database.begin() as connection:
                # Locked, so a decision made at the same moment is either
                # seen here whole or made after this.
                locked = await connection.execute(
                    select(
                        case_status_table.c.case_status,
                        case_status_table.c.stop_after,
                        case_status_table.c.eval_run_id,
                    )
                    .where(case_status_table.c.case_id == case_id)
                    .with_for_update()
                )
                case = locked.first()
                if case is None:
                    return None
                return await _follow_pages(
                    connection,
                    case_id,
                    CaseStatus(case.case_status),
                    StopAfter(case.stop_after) if case.stop_after is not None else None,
                    settled_at,
                    at_the_gate=True,
                    eval_run_id=case.eval_run_id,
                    trace_id=trace_id,
                )

    async def mark_decision_told(self, decision_id: str, told_at: datetime) -> None:
        """Add the mark that the orchestration was told of a decision; once, whatever is repeated."""
        with adapter_span(tracer, "workflow.db.mark_decision_told"):
            async with self._database.begin() as connection:
                await connection.execute(
                    upsert(decision_told_table)
                    .values(decision_id=decision_id, told_at=told_at)
                    .on_conflict_do_nothing(
                        index_elements=[decision_told_table.c.decision_id]
                    )
                )

    async def decisions_not_told(
        self, decided_before: datetime, limit: int
    ) -> list[PageDecision]:
        """The decisions without that mark, made before the given time, oldest first."""
        told = exists().where(
            decision_told_table.c.decision_id == human_decision_table.c.decision_id
        )
        # A failed case has no lifecycle to tell: its orchestration ended
        # itself, or is dead and was the reason the case was failed.
        of_a_failed_case = exists().where(
            case_status_table.c.case_id == human_decision_table.c.case_id,
            case_status_table.c.case_status == CaseStatus.FAILED.value,
        )
        statement = (
            select(human_decision_table)
            .where(
                ~told,
                ~of_a_failed_case,
                human_decision_table.c.occurred_at < decided_before,
            )
            .order_by(
                human_decision_table.c.occurred_at, human_decision_table.c.decision_id
            )
            .limit(limit)
        )
        with adapter_span(tracer, "workflow.db.read_decisions_not_told"):
            async with self._database.connect() as connection:
                rows = (await connection.execute(statement)).all()
        return [_decision(row) for row in rows]

    async def progress(self, case_id: str) -> CaseProgress | None:
        """The case's status and its tracked pages, by page number, with their failure codes."""
        with adapter_span(tracer, "workflow.db.read_progress"):
            async with self._database.connect() as connection:
                found = await connection.execute(
                    select(
                        case_status_table.c.case_status,
                        case_status_table.c.redaction_status,
                    ).where(case_status_table.c.case_id == case_id)
                )
                case = found.first()
                if case is None:
                    return None
                pages = await connection.execute(
                    select(
                        page_status_table.c.page_id,
                        page_status_table.c.page_number,
                        page_status_table.c.page_status,
                    )
                    .where(page_status_table.c.case_id == case_id)
                    .order_by(page_status_table.c.page_number)
                )
                # The failure reason is the code stored with a `stage.failed`
                # event: the case's is that of its first one, a page's that
                # of the event about that page. Only a failed case has one:
                # a verdict run asked for on a finished case that failed
                # leaves its `stage.failed` event and the case as it was
                # (AD-15), and that case has not failed.
                failures = (
                    await connection.execute(
                        select(
                            audit_event_table.c.page_id,
                            audit_event_table.c.error_code,
                        )
                        .where(
                            audit_event_table.c.case_id == case_id,
                            audit_event_table.c.action
                            == AuditAction.STAGE_FAILED.value,
                            audit_event_table.c.error_code.is_not(None),
                        )
                        .order_by(audit_event_table.c.audit_event_seq)
                    )
                ).all()
                page_codes: dict[str, ErrorCode] = {}
                for failure in failures:
                    if failure.page_id is not None:
                        page_codes.setdefault(
                            failure.page_id, ErrorCode(failure.error_code)
                        )
                return CaseProgress(
                    case_id=case_id,
                    case_status=CaseStatus(case.case_status),
                    redaction_status=StageStatus(case.redaction_status),
                    pages=[
                        PageProgress(
                            page_id=page.page_id,
                            page_number=page.page_number,
                            page_status=PageStatus(page.page_status),
                            error_code=page_codes.get(page.page_id),
                        )
                        for page in pages
                    ],
                    error_code=ErrorCode(failures[0].error_code)
                    if failures and case.case_status == CaseStatus.FAILED.value
                    else None,
                )

    async def audit_trail(self, case_id: str, limit: int) -> AuditTrail | None:
        """The case's first audit events, in the order they were written, at most `limit`."""
        with adapter_span(tracer, "workflow.db.read_audit_trail"):
            async with self._database.connect() as connection:
                found = await connection.execute(
                    select(case_status_table.c.case_id).where(
                        case_status_table.c.case_id == case_id
                    )
                )
                if found.first() is None:
                    return None
                events = await connection.execute(
                    select(audit_event_table)
                    .where(audit_event_table.c.case_id == case_id)
                    # The order `workflow` wrote them in, by the number the
                    # database gave each at its insert, and by nothing
                    # else. Not `occurred_at`, which the clock of the
                    # service that did the work sets, and not the record
                    # time, which is read before the case's row is locked:
                    # a route must never show before the classification it
                    # follows.
                    .order_by(audit_event_table.c.audit_event_seq)
                    # One more than is listed: that one says more exist.
                    .limit(limit + 1)
                )
                rows = events.all()
                return AuditTrail(
                    case_id=case_id,
                    events=[_audit_record(row) for row in rows[:limit]],
                    has_more=len(rows) > limit,
                )

    async def queue(self, status: PageStatus, limit: int) -> PageQueue:
        """The pages across cases in `status` that a person can still decide, oldest waiting first."""
        # AD-10: a page the customer kept has a `keep` decision of its own.
        kept_by_customer = (
            exists()
            .where(
                human_decision_table.c.page_id == page_status_table.c.page_id,
                human_decision_table.c.decision == Decision.KEEP.value,
            )
            .label("kept_by_customer")
        )
        statement = (
            select(
                page_status_table.c.case_id,
                page_status_table.c.page_id,
                page_status_table.c.page_number,
                case_status_table.c.classifier_contender,
                kept_by_customer,
            )
            .join(
                case_status_table,
                case_status_table.c.case_id == page_status_table.c.case_id,
            )
            .where(
                page_status_table.c.page_status == status.value,
                # AD-17: nobody decides the pages of an eval run.
                case_status_table.c.eval_run_id.is_(None),
                # The rule of `case_takes_decisions`, in the statement: a
                # page of a failed or completed case, or of a case told to
                # stop after the gate, would be listed and then refused.
                case_status_table.c.case_status.in_(
                    _values(CASE_STATUSES_TAKING_DECISIONS)
                ),
                case_status_table.c.stop_after.is_distinct_from(StopAfter.GATE.value),
            )
            # A page's `updated_at` is when it got the status it waits in.
            .order_by(
                page_status_table.c.updated_at,
                page_status_table.c.case_id,
                page_status_table.c.page_number,
            )
            # One more than is listed: that one says more pages wait.
            .limit(limit + 1)
        )
        with adapter_span(tracer, "workflow.db.read_page_queue"):
            async with self._database.connect() as connection:
                rows = (await connection.execute(statement)).all()
        return PageQueue(
            pages=[
                QueuedPage(
                    case_id=row.case_id,
                    page_id=row.page_id,
                    page_number=row.page_number,
                    page_status=status,
                    classifier_contender=ClassifierContender(row.classifier_contender),
                    queued_by=queued_by(status, bool(row.kept_by_customer)),
                )
                for row in rows[:limit]
            ],
            has_more=len(rows) > limit,
        )

    async def case_list(self, limit: int) -> CaseList:
        """The cases outside any eval run, newest started first, at most `limit`."""
        pages_of_the_case = page_status_table.c.case_id == case_status_table.c.case_id
        page_count = (
            select(func.count())
            .select_from(page_status_table)
            .where(pages_of_the_case)
            .scalar_subquery()
        )
        awaiting_count = (
            select(func.count())
            .select_from(page_status_table)
            .where(
                pages_of_the_case,
                page_status_table.c.page_status.in_(
                    _values(STATUSES_AWAITING_A_DECISION)
                ),
            )
            .scalar_subquery()
        )
        statement = (
            select(
                case_status_table.c.case_id,
                case_status_table.c.case_status,
                case_status_table.c.stop_after,
                case_status_table.c.created_at,
                page_count.label("page_count"),
                awaiting_count.label("awaiting_count"),
            )
            # AD-17: the cases of an eval run are not the underwriter's to open.
            .where(case_status_table.c.eval_run_id.is_(None))
            # Newest first by when the case was started; the id settles a tie.
            .order_by(
                case_status_table.c.created_at.desc(),
                case_status_table.c.case_id.desc(),
            )
            # One more than is listed: that one says more cases exist.
            .limit(limit + 1)
        )
        with adapter_span(tracer, "workflow.db.read_case_list"):
            async with self._database.connect() as connection:
                rows = (await connection.execute(statement)).all()
        return CaseList(
            cases=[
                CaseSummary(
                    case_id=row.case_id,
                    case_status=CaseStatus(row.case_status),
                    started_at=_utc(row.created_at),
                    page_count=row.page_count,
                    waiting_page_count=waiting_page_count(
                        CaseStatus(row.case_status),
                        StopAfter(row.stop_after)
                        if row.stop_after is not None
                        else None,
                        row.awaiting_count,
                    ),
                )
                for row in rows[:limit]
            ],
            has_more=len(rows) > limit,
        )


class SqlTrailGuard:
    """Says whether the role the service is connected as could change the audit trail."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def role_can_change_trail(self) -> bool:
        """Whether the connected role holds UPDATE or DELETE on the audit table, or owns it.

        AD-8: the service must run as a role that can only add to the trail.
        A superuser holds every right, so it is caught here too.
        """
        table = sa_text(
            "SELECT has_table_privilege(c.oid, 'UPDATE') "
            "OR has_table_privilege(c.oid, 'DELETE') "
            "OR pg_has_role(c.relowner, 'MEMBER') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relname = :table"
        )
        with adapter_span(tracer, "workflow.db.check_trail_rights"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    table, {"schema": SCHEMA, "table": AUDIT_EVENT_TABLE}
                )
                # No such table: nothing to change; the revision check says so.
                return bool(result.scalar_one_or_none())


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
        with adapter_span(tracer, "workflow.db.read_revision"):
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
