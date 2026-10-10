"""The routes of `workflow`: the probes, the start of a case, the case list, progress, audit trail, decisions, the queues and the request for a verdict run.

Only `web` calls these, through Dapr (spine, Operations). Stage results are
not posted here: the orchestration's activities record them (AD-2, AD-8).
The decision route is the only way a page is kept, discarded, accepted or
denied (AD-10). The verdict-run route asks for one more suggestion on a
finished case; it decides nothing.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Body, Path, Query, Request

from contracts.enums import RetrieverConfig
from contracts.errors import DomainError, ErrorCode
from contracts.ids import UUID7_PATTERN
from contracts.models.web import Health
from contracts.models.workflow import (
    AuditTrail,
    CaseList,
    CaseProgress,
    CaseStarted,
    DecisionRecorded,
    DecisionRequest,
    PageQueue,
    PageQueueQuery,
    StartCaseRequest,
    VerdictRunRequest,
    VerdictRunRequested,
)
from contracts.operations import get_operation
from workflow.adapters.telemetry import current_trace_id
from workflow.domain.case_list import DEFAULT_CASE_LIST_LIMIT, read_case_list
from workflow.domain.cases import (
    DEFAULT_AUDIT_TRAIL_LIMIT,
    DEFAULT_AVAILABLE_RETRIEVER_CONFIGS,
    read_audit_trail,
    read_progress,
    start_case,
    utc_now,
)
from workflow.domain.decisions import record_decision
from workflow.domain.entities import StartParameters
from workflow.domain.ports import CaseStore, LifecycleEngine
from workflow.domain.queue import DEFAULT_PAGE_QUEUE_LIMIT, read_page_queue
from workflow.domain.verdicts import request_verdict_run
from workflow.settings import HEALTH_PATH, READY_PATH

logger = logging.getLogger(__name__)

NOT_READY_MESSAGE = "The service is not ready."

# An id that is not a UUIDv7 is refused with 422 before anything is looked up.
CaseIdPath = Annotated[str, Path(pattern=UUID7_PATTERN)]
PageIdPath = Annotated[str, Path(pattern=UUID7_PATTERN)]


class SchemaRevision(Protocol):
    async def current(self) -> str | None:
        """The migration revision the database is at, or None if there is none."""
        ...


class TrailGuard(Protocol):
    async def role_can_change_trail(self) -> bool:
        """Whether the role the service runs as could update or delete an audit event."""
        ...


@dataclass(frozen=True, slots=True)
class Dependencies:
    """What the routes work with; the app factory or a test provides it."""

    store: CaseStore
    engine: LifecycleEngine
    schema_revision: SchemaRevision
    trail_guard: TrailGuard
    # The newest migration bundled with this build.
    head_revision: str
    # What a case is started with when the request leaves a field out.
    defaults: StartParameters
    # AD-11: the ladder rows a case may run with in this build.
    available_retriever_configs: frozenset[RetrieverConfig] = (
        DEFAULT_AVAILABLE_RETRIEVER_CONFIGS
    )
    # How many pages one read of a queue lists at most.
    page_queue_limit: int = DEFAULT_PAGE_QUEUE_LIMIT
    # How many events one read of a case's audit trail lists at most.
    audit_trail_limit: int = DEFAULT_AUDIT_TRAIL_LIMIT
    # How many cases one read of the case list holds at most.
    case_list_limit: int = DEFAULT_CASE_LIST_LIMIT
    now: Callable[[], datetime] = field(default=utc_now)


def build_router(dependencies: Dependencies) -> APIRouter:
    """Build the service's routes around one set of dependencies."""
    router = APIRouter()

    @router.api_route(HEALTH_PATH, methods=["GET", "HEAD"])
    async def health() -> Health:
        return Health()

    @router.api_route(READY_PATH, methods=["GET", "HEAD"])
    async def ready() -> Health:
        # azure.md rule 22: ready only when the schema is at the bundled head.
        try:
            current = await dependencies.schema_revision.current()
        except Exception as error:
            # The database cannot be reached. security rule 31: the error's
            # type only; its message can hold the connection's address.
            logger.warning("not ready: database type=%s", type(error).__qualname__)
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, NOT_READY_MESSAGE
            ) from error
        if current != dependencies.head_revision:
            logger.warning(
                "not ready: schema_revision=%s head_revision=%s",
                current,
                dependencies.head_revision,
            )
            raise DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, NOT_READY_MESSAGE)
        # AD-8: never serve as a role that could change the audit trail.
        try:
            writable = await dependencies.trail_guard.role_can_change_trail()
        except Exception as error:
            logger.warning("not ready: database type=%s", type(error).__qualname__)
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, NOT_READY_MESSAGE
            ) from error
        if writable:
            logger.error("not ready: code=audit_trail_writable")
            raise DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, NOT_READY_MESSAGE)
        return Health()

    # The actor is in the body, as `web` passed it on (AD-9); the domain
    # refuses a start that names no demo role, so the body may be left out
    # here and is refused there.
    @router.post(get_operation("start_case").path)
    async def start_case_route(
        case_id: CaseIdPath,
        http_request: Request,
        request: Annotated[StartCaseRequest | None, Body()] = None,
    ) -> CaseStarted:
        return await start_case(
            case_id,
            request,
            store=dependencies.store,
            engine=dependencies.engine,
            defaults=dependencies.defaults,
            available=dependencies.available_retriever_configs,
            trace_id=current_trace_id(http_request.headers.get("traceparent")),
            now=dependencies.now,
        )

    # The underwriter's case list: every case outside an eval run, newest
    # first, bounded by a setting.
    @router.get(get_operation("list_cases").path)
    async def case_list_route() -> CaseList:
        return await read_case_list(
            store=dependencies.store, limit=dependencies.case_list_limit
        )

    @router.get(get_operation("read_progress").path)
    async def progress_route(case_id: CaseIdPath) -> CaseProgress:
        return await read_progress(case_id, store=dependencies.store)

    @router.get(get_operation("read_audit_trail").path)
    async def audit_trail_route(case_id: CaseIdPath) -> AuditTrail:
        return await read_audit_trail(
            case_id,
            store=dependencies.store,
            limit=dependencies.audit_trail_limit,
        )

    # AD-10: the one decision operation. The actor is in the body, as `web`
    # passed it on (AD-9); the domain refuses any that is not a demo role.
    @router.post(get_operation("record_decision").path)
    async def record_decision_route(
        case_id: CaseIdPath,
        page_id: PageIdPath,
        decision: DecisionRequest,
        request: Request,
    ) -> DecisionRecorded:
        return await record_decision(
            case_id,
            page_id,
            decision,
            store=dependencies.store,
            engine=dependencies.engine,
            trace_id=current_trace_id(request.headers.get("traceparent")),
            now=dependencies.now,
        )

    # AD-15, AD-11: one more verdict run on a case whose pages are all
    # final, as an orchestration of its own. 409 `pages_not_terminal` while
    # a page is not final, 409 `retriever_not_available` for a row this
    # build cannot run. The same request again starts nothing new.
    @router.post(get_operation("request_verdict_run").path)
    async def request_verdict_run_route(
        case_id: CaseIdPath, request: VerdictRunRequest
    ) -> VerdictRunRequested:
        return await request_verdict_run(
            case_id,
            request,
            store=dependencies.store,
            engine=dependencies.engine,
            available=dependencies.available_retriever_configs,
        )

    # The cross-case queue (spine, Operations): the pages that wait in the
    # asked status. A missing or unknown status, or any other query
    # parameter, is refused with 422 before anything is read.
    @router.get(get_operation("list_pages_by_status").path)
    async def page_queue_route(query: Annotated[PageQueueQuery, Query()]) -> PageQueue:
        return await read_page_queue(
            query.status,
            store=dependencies.store,
            limit=dependencies.page_queue_limit,
        )

    return router
