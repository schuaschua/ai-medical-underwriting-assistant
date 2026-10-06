"""Payloads `web` answers the SPA with that no other service owns, and the probe answer."""

from typing import Literal

from contracts.base import ContractModel
from contracts.enums import CaseStatus, DemoRole
from contracts.ids import CaseId, DocumentId


class Health(ContractModel):
    """Response of every service's health and readiness routes."""

    status: Literal["ok"] = "ok"


class Me(ContractModel):
    """Response of `GET /api/me`: the demo role `web` read from the request."""

    role: DemoRole


class UploadedCase(ContractModel):
    """Response of `POST /api/cases`: the case `intake` created, with its status."""

    case_id: CaseId
    document_id: DocumentId
    status: CaseStatus
