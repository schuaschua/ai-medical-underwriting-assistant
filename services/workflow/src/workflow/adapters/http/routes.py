"""The routes of `workflow`: the probes, the start of a case, its progress and its audit trail.

Only `web` calls these, through Dapr (spine, Operations). Stage results are
not posted here: the orchestration's activities record them (AD-2, AD-8).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Body, Path

from contracts.errors import DomainError, ErrorCode
from contracts.ids import UUID7_PATTERN
from contracts.models.web import Health
from contracts.models.workflow import (
    AuditTrail,
    CaseProgress,
    CaseStarted,
    StartCaseRequest,
)
from contracts.operations import get_operation
from workflow.domain.cases import (
    read_audit_trail,
    read_progress,
    start_case,
    utc_now,
)
from workflow.domain.entities import StartParameters
from workflow.domain.ports import CaseStore, LifecycleEngine
from workflow.settings import HEALTH_PATH, READY_PATH

logger = logging.getLogger(__name__)

NOT_READY_MESSAGE = "The service is not ready."

# An id that is not a UUIDv7 is refused with 422 before anything is looked up.
CaseIdPath = Annotated[str, Path(pattern=UUID7_PATTERN)]


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

    @router.post(get_operation("start_case").path)
    async def start_case_route(
        case_id: CaseIdPath,
        # Every field is optional, and so is the body itself.
        request: Annotated[StartCaseRequest | None, Body()] = None,
    ) -> CaseStarted:
        return await start_case(
            case_id,
            request,
            store=dependencies.store,
            engine=dependencies.engine,
            defaults=dependencies.defaults,
            now=dependencies.now,
        )

    @router.get(get_operation("read_progress").path)
    async def progress_route(case_id: CaseIdPath) -> CaseProgress:
        return await read_progress(case_id, store=dependencies.store)

    @router.get(get_operation("read_audit_trail").path)
    async def audit_trail_route(case_id: CaseIdPath) -> AuditTrail:
        return await read_audit_trail(case_id, store=dependencies.store)

    return router
