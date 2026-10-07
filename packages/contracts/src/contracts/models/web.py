"""Payloads `web` answers the SPA with that no other service owns, and the probe answer."""

from typing import Literal, Self

from pydantic import model_validator

from contracts.base import Confidence, ContractModel, NonEmptyStr, OneLine, PageNumber
from contracts.enums import Decision, DemoRole, PageType, QueuedBy
from contracts.ids import CaseId, DocumentId, PageId
from contracts.rules import is_medical


class Health(ContractModel):
    """Response of every service's health and readiness routes."""

    status: Literal["ok"] = "ok"


class Me(ContractModel):
    """Response of `GET /api/me`: the demo role `web` read from the request."""

    role: DemoRole


class UploadedCase(ContractModel):
    """Response of `POST /api/cases`: the case `intake` created.

    It carries no status: the case is started by a call of its own
    (`POST /api/cases/{case_id}/start`), and `workflow` reports its status
    from then on (`GET /api/cases/{case_id}/progress`).
    """

    case_id: CaseId
    document_id: DocumentId


class PageDecisionRequest(ContractModel):
    """Request of `POST /api/cases/{case_id}/pages/{page_id}/decisions`.

    It names no actor: `web` passes the request's demo role on as the actor
    (AD-9), so a browser cannot say who decided.
    """

    decision: Decision


class TriagePage(ContractModel):
    """One page that waits for the underwriter, with what the classifier said of it.

    `web` composes it: the page from `workflow`'s queue, the reading from
    `classification`, for the classifier the case was started with. The four
    fields of the reading are null together when it could not be read; the
    page can be decided all the same.
    """

    case_id: CaseId
    page_id: PageId
    page_number: PageNumber
    # Where `web` serves the page's thumbnail (the redacted page, as PNG).
    thumbnail_path: NonEmptyStr
    page_type: PageType | None
    is_medical: bool | None
    confidence: Confidence | None
    reason: OneLine | None
    # How the page came to wait: the gate was unsure, or the customer kept it.
    queued_by: QueuedBy | None = None

    @model_validator(mode="after")
    def _reading_is_whole_or_absent(self) -> Self:
        reading = (self.page_type, self.is_medical, self.confidence, self.reason)
        if len({value is None for value in reading}) != 1:
            raise ValueError(
                "page_type, is_medical, confidence and reason are set together"
            )
        # AD-13: `is_medical` comes from the one mapping, here as everywhere.
        if self.page_type is not None and self.is_medical != is_medical(self.page_type):
            raise ValueError("is_medical must follow the page_type mapping")
        return self


class TriageQueue(ContractModel):
    """Response of `GET /api/triage`: the pages that wait for the underwriter, oldest first."""

    pages: list[TriagePage]
    # More pages wait than are listed.
    has_more: bool
