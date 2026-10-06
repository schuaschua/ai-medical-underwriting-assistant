"""The routes of `intake`: the probes and `POST /cases` (spine, Operations).

No route here, or anywhere in the service, returns an uploaded original (AD-21).
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from fastapi import APIRouter, Request
from starlette.requests import ClientDisconnect

from contracts.errors import DomainError, ErrorCode
from contracts.models.intake import CaseCreated
from contracts.models.web import Health
from contracts.operations import PDF, get_operation
from contracts.upload import (
    MAX_UPLOAD_BYTES,
    TOO_LARGE_MESSAGE,
    check_received_length,
    check_upload_size,
    parse_declared_length,
)
from intake.adapters.http.errors import UNSUPPORTED_MEDIA_TYPE_MESSAGE
from intake.domain.ports import CaseRepository, OriginalStore
from intake.domain.upload import NOT_STORED_MESSAGE, create_case, utc_now
from intake.settings import HEALTH_PATH, READY_PATH

logger = logging.getLogger(__name__)

NOT_READY_MESSAGE = "The service is not ready."
INTERRUPTED_MESSAGE = "The upload was interrupted."


class SchemaRevision(Protocol):
    async def current(self) -> str | None:
        """The migration revision the database is at, or None if there is none."""
        ...


@dataclass(frozen=True, slots=True)
class Dependencies:
    """What the routes work with; the app factory or a test provides it."""

    store: OriginalStore
    repository: CaseRepository
    schema_revision: SchemaRevision
    # The newest migration bundled with this build.
    head_revision: str
    now: Callable[[], datetime] = field(default=utc_now)
    # INTAKE_UPLOAD_DEADLINE_SECONDS: see the settings for the three upload
    # deadlines and their order.
    upload_deadline_seconds: float = 90.0


async def read_upload(request: Request) -> bytes:
    """Read the request body, refusing it as soon as it cannot be a valid upload."""
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != PDF:
        raise DomainError(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE, UNSUPPORTED_MEDIA_TYPE_MESSAGE
        )
    declared = parse_declared_length(request.headers.get("content-length"))
    if declared is not None:
        # Refused before a byte of the body is read.
        check_upload_size(declared)
    content = bytearray()
    try:
        async for chunk in request.stream():
            content += chunk
            if len(content) > MAX_UPLOAD_BYTES:
                # A body with no declared length, or one longer than it declared.
                raise DomainError(ErrorCode.FILE_TOO_LARGE, TOO_LARGE_MESSAGE)
    except ClientDisconnect:
        # The caller gave up mid-upload (`web` does when the file runs past
        # the limit). Nothing was stored; nobody is left to read the answer.
        raise DomainError(ErrorCode.VALIDATION_FAILED, INTERRUPTED_MESSAGE) from None
    # A body cut short or padded is not the file that was sent.
    check_received_length(declared, len(content))
    return bytes(content)


def build_router(dependencies: Dependencies) -> APIRouter:
    """Build the service's routes around one set of dependencies."""
    router = APIRouter()
    create_case_operation = get_operation("create_case")

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

    @router.post(create_case_operation.path, status_code=201)
    async def create_case_route(request: Request) -> CaseCreated:
        # One deadline for the whole call: reading the body, then storing it.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + dependencies.upload_deadline_seconds
        try:
            async with asyncio.timeout_at(deadline):
                content = await read_upload(request)
        except TimeoutError:
            # Nothing has been stored yet.
            logger.error("upload failed: stage=read_body type=TimeoutError")
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE, NOT_STORED_MESSAGE
            ) from None
        return await create_case(
            content,
            store=dependencies.store,
            repository=dependencies.repository,
            now=dependencies.now,
            deadline_seconds=max(deadline - loop.time(), 0.0),
        )

    return router
