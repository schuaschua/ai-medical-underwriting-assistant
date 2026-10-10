"""Payloads of the operations `intake` owns (AD-14, AD-21)."""

from typing import Annotated, ClassVar, Self

from pydantic import Field, model_validator

from contracts.audit import AuditAction
from contracts.base import ContractModel, Count, NonEmptyStr, Offset, PageNumber
from contracts.enums import StageStatus
from contracts.ids import CaseId, DocumentId, PageId
from contracts.models._stage import StageCommand, StageResult

Length = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
PositiveLength = Annotated[float, Field(gt=0.0, allow_inf_nan=False)]


class CaseCreated(ContractModel):
    """Response of `POST /cases`; the request body is the PDF itself."""

    case_id: CaseId
    document_id: DocumentId


class RedactionCommand(StageCommand):
    """Request of `POST /cases/{case_id}/redaction`; the key is the `case_id` in the path."""


class RedactionResult(StageResult):
    DONE_ACTION: ClassVar[AuditAction] = AuditAction.DOCUMENT_REDACTED

    document_id: DocumentId
    # Empty when redaction failed: no pages are created (AD-21).
    page_ids: list[PageId]
    # Category name to number of items redacted, never the values.
    redaction_counts: dict[NonEmptyStr, Count]

    @model_validator(mode="after")
    def _pages_and_counts_follow_status(self) -> Self:
        if self.status is StageStatus.FAILED and self.page_ids:
            raise ValueError("a failed redaction creates no pages")
        if (
            self.status is StageStatus.DONE
            and self.redaction_counts != self.audit.detail
        ):
            raise ValueError("redaction_counts must equal the audit record's detail")
        return self


class Page(ContractModel):
    page_id: PageId
    case_id: CaseId
    document_id: DocumentId
    page_number: PageNumber


class PageList(ContractModel):
    """Response of `GET /cases/{case_id}/pages`; empty until redaction is done."""

    case_id: CaseId
    pages: list[Page]


class PageText(ContractModel):
    """Response of `GET /pages/{page_id}/text`: the one stored reading of the page."""

    page_id: PageId
    page_number: PageNumber
    text: str


class PageBoxesQuery(ContractModel):
    """Query of `GET /pages/{page_id}/boxes`: an optional offset range into the page text."""

    quote_start: Offset | None = None
    quote_end: Offset | None = None

    @model_validator(mode="after")
    def _range_is_whole(self) -> Self:
        if (self.quote_start is None) != (self.quote_end is None):
            raise ValueError(
                "quote_start and quote_end are given together or not at all"
            )
        if (
            self.quote_start is not None
            and self.quote_end is not None
            and self.quote_start >= self.quote_end
        ):
            raise ValueError("quote_start must be before quote_end")
        return self


class WordBox(ContractModel):
    """One word of the page text and where it sits on the page."""

    # Offsets into the page text: `text[char_start:char_end]` is the word.
    char_start: Offset
    char_end: Offset
    # PDF points, origin at the top-left corner of the page.
    x0: Length
    y0: Length
    x1: Length
    y1: Length

    @model_validator(mode="after")
    def _box_is_not_inverted(self) -> Self:
        if self.char_start >= self.char_end:
            raise ValueError("char_start must be before char_end")
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("a box runs from its top-left to its bottom-right corner")
        return self


class PageBoxes(ContractModel):
    """Response of `GET /pages/{page_id}/boxes`."""

    page_id: PageId
    page_number: PageNumber
    # Page size in PDF points, so a viewer can scale the boxes.
    page_width: PositiveLength
    page_height: PositiveLength
    boxes: list[WordBox]
