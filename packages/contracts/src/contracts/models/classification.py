"""Payloads of the operations `classification` owns (AD-13)."""

from typing import ClassVar, Self

from pydantic import model_validator

from contracts.audit import AuditAction
from contracts.base import Confidence, ContractModel, OneLine
from contracts.enums import ClassifierContender, PageType, StageStatus
from contracts.ids import CaseId, ClassificationId, PageId
from contracts.models._stage import StageCommand, StageResult
from contracts.rules import is_medical


class ClassifierOutput(ContractModel):
    """What one LLM classifier run must return; anything else is a failed parse."""

    page_type: PageType
    reason: OneLine


class ClassifyCommand(StageCommand):
    """Request of `POST /classifications`; key `case_id` + `page_id` + `contender`."""

    case_id: CaseId
    page_id: PageId
    contender: ClassifierContender


class Classification(ContractModel):
    """One classifier's reading of one page; it never carries a route (AD-7)."""

    classification_id: ClassificationId
    case_id: CaseId
    page_id: PageId
    contender: ClassifierContender
    page_type: PageType
    is_medical: bool
    confidence: Confidence
    reason: OneLine

    @model_validator(mode="after")
    def _is_medical_follows_page_type(self) -> Self:
        # AD-13: `is_medical` comes from the one mapping, never from the classifier.
        if self.is_medical != is_medical(self.page_type):
            raise ValueError("is_medical must follow the page_type mapping")
        return self


class ClassificationResult(StageResult):
    DONE_ACTION: ClassVar[AuditAction] = AuditAction.PAGE_CLASSIFIED

    classification_id: ClassificationId
    page_id: PageId
    contender: ClassifierContender
    # Null when the stage failed.
    classification: Classification | None

    @model_validator(mode="after")
    def _classification_when_done(self) -> Self:
        if (self.status is StageStatus.DONE) != (self.classification is not None):
            raise ValueError(
                "classification is set when, and only when, status is done"
            )
        return self

    @model_validator(mode="after")
    def _classification_is_this_result(self) -> Self:
        inner = self.classification
        if inner is not None and (
            inner.classification_id != self.classification_id
            or inner.case_id != self.case_id
            or inner.page_id != self.page_id
            or inner.contender is not self.contender
        ):
            raise ValueError("the classification must be the one this result is about")
        return self


class ClassificationList(ContractModel):
    """Response of `GET /cases/{case_id}/classifications`."""

    case_id: CaseId
    classifications: list[Classification]
