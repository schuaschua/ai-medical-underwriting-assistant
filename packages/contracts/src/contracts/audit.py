"""The audit action catalogue and the audit record (AD-8)."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import model_validator

from contracts.base import (
    Confidence,
    ContractModel,
    Count,
    NonEmptyStr,
    TraceId,
    UtcDatetime,
)
from contracts.enums import ActorKind, DemoRole, PageStatus, Service
from contracts.ids import CaseId, EvalRunId, PageId, Uuid7Str


class AuditAction(StrEnum):
    DOCUMENT_REDACTED = "document.redacted"
    PAGE_CLASSIFIED = "page.classified"
    # AD-7: the gate sent a classified page on. Added in story 1.9, so that the
    # status change out of `classified` has its event (AD-8).
    PAGE_ROUTED = "page.routed"
    PAGE_KEPT = "page.kept"
    PAGE_DISCARDED = "page.discarded"
    PAGE_ACCEPTED = "page.accepted"
    PAGE_DENIED = "page.denied"
    FACTS_EXTRACTED = "facts.extracted"
    VERDICT_SUGGESTED = "verdict.suggested"
    STAGE_FAILED = "stage.failed"


# AD-10: the actions only a human role may take.
HUMAN_ACTIONS: frozenset[AuditAction] = frozenset(
    {
        AuditAction.PAGE_KEPT,
        AuditAction.PAGE_DISCARDED,
        AuditAction.PAGE_ACCEPTED,
        AuditAction.PAGE_DENIED,
    }
)

_PAGE_ACTION_PREFIX = "page."
_AI_ACTOR_SEPARATOR = ":"
_DEMO_ROLES = frozenset(role.value for role in DemoRole)
_SERVICES = frozenset(service.value for service in Service)


def _is_deployment_name(deployment: str) -> bool:
    return bool(deployment) and deployment == deployment.strip()


def ai_actor(service: Service, deployment: str) -> str:
    """Build the `actor` of an AI action: service app id plus model deployment name."""
    if not _is_deployment_name(deployment):
        raise ValueError("a deployment name must not be blank or padded")
    return f"{service.value}{_AI_ACTOR_SEPARATOR}{deployment}"


class RouteDetail(ContractModel):
    """Detail of `page.routed`: the status the gate gave the page, and the threshold it used."""

    # The three statuses the gate may give a page (AD-7), and no other.
    route: Literal[
        PageStatus.EXTRACTING,
        PageStatus.AWAITING_CUSTOMER,
        PageStatus.AWAITING_TRIAGE,
    ]
    threshold: Confidence


class AuditRecord(ContractModel):
    """One row of the audit trail; `workflow` is the only writer."""

    actor_kind: ActorKind
    # A demo role for a human; `<app id>:<model deployment>` for AI
    # (for redaction, `intake:azure-ai-language`).
    actor: NonEmptyStr
    action: AuditAction
    occurred_at: UtcDatetime
    case_id: CaseId
    page_id: PageId | None
    # Id of the owning record: classification, fact set, verdict run, human decision.
    ref: Uuid7Str
    # For `document.redacted`, category to count, never the values; for
    # `page.routed`, the route and the threshold; otherwise null.
    detail: dict[NonEmptyStr, Count] | RouteDetail | None
    trace_id: TraceId
    eval_run_id: EvalRunId | None

    @model_validator(mode="after")
    def _actor_matches_kind(self) -> Self:
        if self.actor_kind is ActorKind.HUMAN:
            if self.actor not in _DEMO_ROLES:
                raise ValueError("a human actor must be a demo role")
            return self
        service, separator, deployment = self.actor.partition(_AI_ACTOR_SEPARATOR)
        if (
            service not in _SERVICES
            or not separator
            or not _is_deployment_name(deployment)
        ):
            raise ValueError("an AI actor must be '<app id>:<model deployment>'")
        return self

    @model_validator(mode="after")
    def _detail_fits_the_action(self) -> Self:
        if self.action is AuditAction.DOCUMENT_REDACTED:
            fits = isinstance(self.detail, dict)
        elif self.action is AuditAction.PAGE_ROUTED:
            fits = isinstance(self.detail, RouteDetail)
        else:
            fits = self.detail is None
        if not fits:
            raise ValueError(
                "detail is the redaction counts for document.redacted, "
                "the route for page.routed, else null"
            )
        return self

    @model_validator(mode="after")
    def _human_actions_come_from_humans(self) -> Self:
        if (self.action in HUMAN_ACTIONS) != (self.actor_kind is ActorKind.HUMAN):
            raise ValueError(
                "keep, discard, accept and deny come from a human, and nothing else does"
            )
        return self

    @model_validator(mode="after")
    def _page_actions_name_a_page(self) -> Self:
        if self.action.value.startswith(_PAGE_ACTION_PREFIX) and self.page_id is None:
            raise ValueError("a page action needs a page_id")
        return self
