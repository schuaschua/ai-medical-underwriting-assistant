"""The routes of redaction and of what it stores (spine, Operations; AD-21, AD-14).

`POST /cases/{case_id}/redaction` is `workflow`'s command. The six reads
answer from the redacted PDF and what was made from it: until redaction is
done the page list is empty and the file is `not_redacted`. No route here
returns an original.
"""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response
from pydantic import ValidationError

from contracts.errors import NO_TRACE_ID, DomainError, ErrorCode
from contracts.ids import UUID7_PATTERN
from contracts.models.intake import (
    PageBoxes,
    PageBoxesQuery,
    PageList,
    PageText,
    RedactionCommand,
    RedactionResult,
)
from contracts.operations import get_operation
from intake.adapters.http.errors import INVALID_REQUEST_MESSAGE
from intake.adapters.http.routes import Dependencies
from intake.adapters.telemetry import current_trace_id
from intake.domain.pages import (
    list_pages,
    read_document_file,
    read_page_boxes,
    read_page_file,
    read_page_text,
    read_page_thumbnail,
)
from intake.domain.redaction import redact_document

# An id that is not a UUIDv7 is refused before anything is looked up.
IdPath = Annotated[str, Path(pattern=UUID7_PATTERN)]
OffsetQuery = Annotated[int | None, Query(ge=0)]
# 1-based; a number that cannot be a page's is refused before anything is read.
PageNumberPath = Annotated[int, Path(ge=1, le=100_000)]


def build_page_router(dependencies: Dependencies) -> APIRouter:
    """Build the redaction command and the page reads around one set of dependencies."""
    router = APIRouter()
    redact = get_operation("redact_document")
    pages = get_operation("list_pages")
    text = get_operation("read_page_text")
    boxes = get_operation("read_page_boxes")
    thumbnail = get_operation("read_page_thumbnail")
    file = get_operation("read_document_file")
    page_file = get_operation("read_page_file")
    repository = dependencies.pages
    files = dependencies.redaction.files

    @router.post(redact.path)
    async def redact_document_route(
        case_id: IdPath, command: RedactionCommand, request: Request
    ) -> RedactionResult:
        return await redact_document(
            case_id,
            command,
            ports=dependencies.redaction,
            categories=dependencies.redaction_categories,
            deadline_seconds=dependencies.redaction_deadline_seconds,
            stale_margin_seconds=dependencies.redaction_stale_margin_seconds,
            cancel_seconds=dependencies.redaction_cancel_seconds,
            trace_id=current_trace_id(request.headers.get("traceparent"))
            or NO_TRACE_ID,
            now=dependencies.now,
        )

    @router.get(pages.path)
    async def list_pages_route(case_id: IdPath) -> PageList:
        return await list_pages(case_id, repository=repository)

    @router.get(text.path)
    async def read_page_text_route(page_id: IdPath) -> PageText:
        return await read_page_text(page_id, repository=repository)

    @router.get(boxes.path)
    async def read_page_boxes_route(
        page_id: IdPath,
        quote_start: OffsetQuery = None,
        quote_end: OffsetQuery = None,
    ) -> PageBoxes:
        try:
            query = PageBoxesQuery(quote_start=quote_start, quote_end=quote_end)
        except ValidationError:
            # Half a range, or one that runs backwards.
            raise DomainError(
                ErrorCode.VALIDATION_FAILED, INVALID_REQUEST_MESSAGE
            ) from None
        return await read_page_boxes(page_id, query, repository=repository)

    @router.get(thumbnail.path)
    async def read_page_thumbnail_route(page_id: IdPath) -> Response:
        content = await read_page_thumbnail(page_id, repository=repository, files=files)
        return Response(content, media_type=thumbnail.response_media_type)

    @router.get(file.path)
    async def read_document_file_route(document_id: IdPath) -> Response:
        content = await read_document_file(
            document_id, repository=repository, files=files
        )
        return Response(content, media_type=file.response_media_type)

    @router.get(page_file.path)
    async def read_page_file_route(
        document_id: IdPath, page_number: PageNumberPath
    ) -> Response:
        content = await read_page_file(
            document_id,
            page_number,
            repository=repository,
            files=files,
            splitter=dependencies.redaction.splitter,
        )
        return Response(content, media_type=page_file.response_media_type)

    return router
