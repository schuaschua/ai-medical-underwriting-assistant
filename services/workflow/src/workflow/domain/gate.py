"""The gate: where a classified page goes next (AD-7).

The routing table and its threshold live here and nowhere else: no other
service, screen or prompt works out a route. The rule is pure, so the case
orchestration may call it on values an activity returned (AD-5).
"""

import math
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import TypeGuard

from contracts.audit import AuditAction, AuditRecord, RouteDetail
from contracts.enums import ActorKind, CaseStatus, PageStatus, StopAfter
from contracts.errors import NO_TRACE_ID, DomainError, ErrorCode
from workflow.domain.recording import PageChange, Recording

# What the gate is called in an audit record, as `<app id>:<part>`.
GATE_ACTOR = "workflow:gate"
# The threshold when the setting `WORKFLOW_GATE_THRESHOLD` is left out.
DEFAULT_GATE_THRESHOLD = 0.90
NOT_A_UNIT_NUMBER_MESSAGE = "A confidence and a threshold are numbers from 0 to 1."


def is_unit_number(value: object) -> TypeGuard[float]:
    """Whether a value is a finite number from 0 to 1, as a confidence and a threshold are."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


class Route(StrEnum):
    """The three ways out of the gate; each value is the page status it gives."""

    # Medical and sure: on to extraction.
    EXTRACTION = PageStatus.EXTRACTING.value
    # Not medical and sure: the customer says keep or discard.
    CUSTOMER = PageStatus.AWAITING_CUSTOMER.value
    # Not sure, whatever the label: the underwriter says accept or deny.
    TRIAGE = PageStatus.AWAITING_TRIAGE.value

    @property
    def page_status(self) -> PageStatus:
        """The status a page routed this way is left in."""
        return PageStatus(self.value)


# The statuses the gate may leave a case in, besides `running`, which it has.
STATUSES_THE_GATE_SETS = frozenset({CaseStatus.AWAITING_HUMAN, CaseStatus.COMPLETED})
# The routes that leave a page waiting for a person.
_AWAITS_A_HUMAN = frozenset({Route.CUSTOMER, Route.TRIAGE})


def route_page(*, is_medical: bool, confidence: float, threshold: float) -> Route:
    """Route one classified page. A confidence equal to the threshold counts as sure.

    A confidence or a threshold that is not a finite number from 0 to 1 is
    `validation_failed`: no page is routed on a value that means nothing.
    """
    if not is_unit_number(confidence) or not is_unit_number(threshold):
        raise DomainError(ErrorCode.VALIDATION_FAILED, NOT_A_UNIT_NUMBER_MESSAGE)
    if confidence < threshold:
        return Route.TRIAGE
    return Route.EXTRACTION if is_medical else Route.CUSTOMER


def case_status_after_gate(
    routes: Iterable[Route], stop_after: StopAfter | None
) -> CaseStatus:
    """The case's status once every page is routed.

    A case told to stop after the gate is complete, whatever its pages wait
    for (AD-17: nobody decides the pages of a bake-off run). Otherwise it
    waits for a human if any page does, and goes on running if none does.
    """
    if stop_after is StopAfter.GATE:
        return CaseStatus.COMPLETED
    if any(route in _AWAITS_A_HUMAN for route in routes):
        return CaseStatus.AWAITING_HUMAN
    return CaseStatus.RUNNING


def route_recording(
    case_id: str,
    page_id: str,
    classification_id: str,
    route: Route,
    threshold: float,
    *,
    occurred_at: datetime,
    eval_run_id: str | None = None,
    trace_id: str | None = None,
) -> Recording:
    """What is recorded when the gate routes a page: its new status and `page.routed` (AD-8).

    The event's reference is the classification the route was worked out
    from, so however often one route is recorded, the trail holds it once.
    Only a page that is `classified` is routed. A threshold that is not a
    finite number from 0 to 1 is `validation_failed`.
    """
    if not is_unit_number(threshold):
        raise DomainError(ErrorCode.VALIDATION_FAILED, NOT_A_UNIT_NUMBER_MESSAGE)
    return Recording(
        audit=AuditRecord(
            actor_kind=ActorKind.AI,
            actor=GATE_ACTOR,
            action=AuditAction.PAGE_ROUTED,
            occurred_at=occurred_at,
            case_id=case_id,
            page_id=page_id,
            ref=classification_id,
            detail=RouteDetail.model_validate(
                {"route": route.value, "threshold": threshold}
            ),
            trace_id=trace_id or NO_TRACE_ID,
            eval_run_id=eval_run_id,
        ),
        page_change=PageChange(
            page_id, route.page_status, only_from=PageStatus.CLASSIFIED
        ),
    )
