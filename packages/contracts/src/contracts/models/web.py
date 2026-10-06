"""Payloads `web` answers the SPA with that no other service owns, and the probe answer."""

from typing import Literal

from contracts.base import ContractModel
from contracts.enums import DemoRole
from contracts.ids import CaseId, DocumentId


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
