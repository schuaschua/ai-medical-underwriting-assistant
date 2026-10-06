"""Payloads of the operations `extraction` owns (AD-14)."""

from typing import ClassVar, Self

from pydantic import model_validator

from contracts.audit import AuditAction
from contracts.base import (
    ContractModel,
    Count,
    NonEmptyStr,
    Offset,
    OneLine,
    PageNumber,
)
from contracts.enums import StageStatus
from contracts.ids import CaseId, FactId, FactSetId, PageId
from contracts.models._stage import StageCommand, StageResult


class ExtractedFact(ContractModel):
    statement: OneLine
    quote: NonEmptyStr


class ExtractionOutput(ContractModel):
    """What the extraction model must return for one page; anything else is a failed parse."""

    facts: list[ExtractedFact]


class ExtractFactsCommand(StageCommand):
    """Request of `POST /fact-sets`; key `case_id` + `page_id`."""

    case_id: CaseId
    page_id: PageId


class FactSetResult(StageResult):
    DONE_ACTION: ClassVar[AuditAction] = AuditAction.FACTS_EXTRACTED

    fact_set_id: FactSetId
    page_id: PageId
    fact_ids: list[FactId]
    # How many of `fact_ids` have a quote that was not found in the page text.
    unverified_count: Count

    @model_validator(mode="after")
    def _facts_are_consistent(self) -> Self:
        if self.status is StageStatus.FAILED and self.fact_ids:
            raise ValueError("a failed extraction stores no facts")
        if len(set(self.fact_ids)) != len(self.fact_ids):
            raise ValueError("fact_ids must not repeat a value")
        if self.unverified_count > len(self.fact_ids):
            raise ValueError("unverified_count cannot exceed the number of facts")
        return self


class Fact(ContractModel):
    fact_id: FactId
    case_id: CaseId
    page_id: PageId
    page_number: PageNumber
    # One line saying what the fact is, for example "HbA1c 8.2%".
    statement: OneLine
    quote: NonEmptyStr
    quote_verified: bool
    # Offsets into the page text; set when, and only when, the quote is verified.
    quote_start: Offset | None
    quote_end: Offset | None

    @model_validator(mode="after")
    def _offsets_follow_verification(self) -> Self:
        if (self.quote_start is not None) != self.quote_verified or (
            self.quote_end is not None
        ) != self.quote_verified:
            raise ValueError(
                "quote offsets are set when, and only when, quote_verified is true"
            )
        if (
            self.quote_start is not None
            and self.quote_end is not None
            and self.quote_start >= self.quote_end
        ):
            raise ValueError("quote_start must be before quote_end")
        return self


class FactList(ContractModel):
    """Response of `GET /cases/{case_id}/facts`."""

    case_id: CaseId
    facts: list[Fact]
