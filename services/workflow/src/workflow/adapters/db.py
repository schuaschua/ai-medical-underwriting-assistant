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
from sqlalchemy import (
    URL,
    Column,
    DateTime,
    Engine,
    ForeignKey,
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
    insert,
    select,
    update,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from contracts.audit import AuditAction, AuditRecord
from contracts.enums import (
    ActorKind,
    CaseStatus,
    ClassifierContender,
    PageStatus,
    RetrieverConfig,
    StageStatus,
    StopAfter,
)
from contracts.ids import new_id
from contracts.models.workflow import AuditTrail, CaseProgress, PageProgress
from workflow.adapters.credential import azure_credential
from workflow.domain.entities import CaseRecord, StartParameters
from workflow.domain.recording import Recording, RecordOutcome
from workflow.domain.transitions import (
    CASE_STATUSES_TAKING_RESULTS,
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
# AD-8: one event per case, page, action and reference; a null page counts as
# one value, so a case-level event cannot be written twice either.
AUDIT_EVENT_UNIQUE = "uq_workflow_audit_event_subject"

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
    UniqueConstraint(
        "case_id",
        "page_id",
        "action",
        "ref",
        name=AUDIT_EVENT_UNIQUE,
        postgresql_nulls_not_distinct=True,
    ),
    Index("ix_workflow_audit_event_case_id_occurred_at", "case_id", "occurred_at"),
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
    )


class SqlCaseStore:
    """Case and page status and the audit trail.

    Every statement uses bound parameters (security rule 21). The audit table
    is only ever inserted into and read (AD-8, security rule 32).
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def start(self, case: CaseRecord) -> CaseRecord:
        """Insert the case unless its id is taken; return the stored case either way."""
        parameters = case.parameters
        with tracer.start_as_current_span("workflow.db.start_case"):
            async with self._database.begin() as connection:
                await connection.execute(
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
                )
                result = await connection.execute(
                    select(*_CASE_COLUMNS).where(
                        case_status_table.c.case_id == case.case_id
                    )
                )
                return _case(result.one())

    async def status(self, case_id: str) -> CaseStatus | None:
        """The case's status, or None if the case is unknown."""
        with tracer.start_as_current_span("workflow.db.read_case_status"):
            async with self._database.connect() as connection:
                result = await connection.execute(
                    select(case_status_table.c.case_status).where(
                        case_status_table.c.case_id == case_id
                    )
                )
                value = result.scalar_one_or_none()
        return CaseStatus(value) if value is not None else None

    async def record(
        self, recording: Recording, recorded_at: datetime
    ) -> RecordOutcome:
        """Write the status changes and the audit event in one transaction, or neither."""
        with tracer.start_as_current_span("workflow.db.record"):
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
        # The case's row is locked first, so two recordings for one case run
        # one after the other and the check below cannot be raced.
        locked = await connection.execute(
            select(case_status_table.c.case_status)
            .where(case_status_table.c.case_id == audit.case_id)
            .with_for_update()
        )
        case_status = locked.scalar_one_or_none()
        if case_status is None:
            raise _Refused(RecordOutcome.UNKNOWN_CASE)
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
                        _values(page_statuses_before(change.page_status))
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
        # Last, in the same transaction: if this insert fails, the status
        # changes above are rolled back with it.
        await connection.execute(
            insert(audit_event_table).values(
                audit_event_id=new_id(),
                actor_kind=audit.actor_kind.value,
                actor=audit.actor,
                action=audit.action.value,
                occurred_at=audit.occurred_at,
                case_id=audit.case_id,
                page_id=audit.page_id,
                ref=audit.ref,
                detail=audit.detail,
                error_code=recording.error_code.value
                if recording.error_code is not None
                else None,
                trace_id=audit.trace_id,
                eval_run_id=audit.eval_run_id,
                recorded_at=recorded_at,
            )
        )

    async def progress(self, case_id: str) -> CaseProgress | None:
        """The case's status and its tracked pages, by page number."""
        with tracer.start_as_current_span("workflow.db.read_progress"):
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
                return CaseProgress(
                    case_id=case_id,
                    case_status=CaseStatus(case.case_status),
                    redaction_status=StageStatus(case.redaction_status),
                    pages=[
                        PageProgress(
                            page_id=page.page_id,
                            page_number=page.page_number,
                            page_status=PageStatus(page.page_status),
                        )
                        for page in pages
                    ],
                )

    async def audit_trail(self, case_id: str) -> AuditTrail | None:
        """The case's audit events, oldest first."""
        with tracer.start_as_current_span("workflow.db.read_audit_trail"):
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
                    # Time order; events of one instant keep the order they
                    # were written in (a UUIDv7 id sorts by its time).
                    .order_by(
                        audit_event_table.c.occurred_at,
                        audit_event_table.c.recorded_at,
                        audit_event_table.c.audit_event_id,
                    )
                )
                return AuditTrail(
                    case_id=case_id, events=[_audit_record(row) for row in events]
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
        with tracer.start_as_current_span("workflow.db.check_trail_rights"):
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
        with tracer.start_as_current_span("workflow.db.read_revision"):
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
