"""The routes of `classification`: the probes, the classify command and the read (spine, Operations).

`POST /classifications` is `workflow`'s command, one page at a time.
`GET /cases/{case_id}/classifications` answers with what was stored. No route
here routes a page or decides anything about it (AD-7).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Path, Request

from classification.adapters.telemetry import current_trace_id
from classification.domain.classify import (
    ClassifyOptions,
    ClassifyPorts,
    classify_page,
    list_classifications,
    utc_now,
)
from classification.settings import HEALTH_PATH, READY_PATH
from contracts.errors import NO_TRACE_ID, DomainError, ErrorCode
from contracts.ids import UUID7_PATTERN
from contracts.models.classification import (
    ClassificationList,
    ClassificationResult,
    ClassifyCommand,
)
from contracts.models.web import Health
from contracts.operations import get_operation

logger = logging.getLogger(__name__)

NOT_READY_MESSAGE = "The service is not ready."
# The W3C trace context headers passed on to `intake` with a read.
_TRACE_HEADERS = ("traceparent", "tracestate")

# An id that is not a UUIDv7 is refused before anything is looked up.
IdPath = Annotated[str, Path(pattern=UUID7_PATTERN)]


class SchemaRevision(Protocol):
    async def current(self) -> str | None:
        """The migration revision the database is at, or None if there is none."""
        ...


@dataclass(frozen=True, slots=True)
class Dependencies:
    """What the routes work with; the app factory or a test provides it."""

    classify: ClassifyPorts
    options: ClassifyOptions
    schema_revision: SchemaRevision
    # The newest migration bundled with this build.
    head_revision: str
    now: Callable[[], datetime] = field(default=utc_now)


def build_router(dependencies: Dependencies) -> APIRouter:
    """Build the service's routes around one set of dependencies."""
    router = APIRouter()
    classify = get_operation("classify_page")
    listing = get_operation("list_classifications")

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
        return Health()

    @router.post(classify.path)
    async def classify_page_route(
        command: ClassifyCommand, request: Request
    ) -> ClassificationResult:
        return await classify_page(
            command,
            ports=dependencies.classify,
            options=dependencies.options,
            trace_id=current_trace_id(request.headers.get("traceparent"))
            or NO_TRACE_ID,
            trace_context={
                name: request.headers[name]
                for name in _TRACE_HEADERS
                if name in request.headers
            },
            now=dependencies.now,
        )

    @router.get(listing.path)
    async def list_classifications_route(case_id: IdPath) -> ClassificationList:
        return await list_classifications(
            case_id, repository=dependencies.classify.repository
        )

    return router
