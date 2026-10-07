"""The one decision operation: a person keeps, discards, accepts or denies a page (AD-10).

This is the only code that records a decision, and it refuses every actor
that is not a demo role: the AI never decides. Who may decide what is the
contracts' one mapping (`contracts.decisions`); the checks are made here, in
domain code, whatever the caller or a prompt says.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from contracts.audit import AuditRecord
from contracts.decisions import DecisionRule, decision_rule, human_role
from contracts.enums import (
    ActorKind,
    CaseStatus,
    Decision,
    DemoRole,
    PageStatus,
    StopAfter,
)
from contracts.errors import NO_TRACE_ID, DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.workflow import DecisionRecorded, DecisionRequest
from workflow.domain.cases import fail_case, utc_now
from workflow.domain.entities import PageDecision
from workflow.domain.ports import CaseStore, LifecycleEngine, Told
from workflow.domain.recording import DecisionOutcome, PageChange, Recording

logger = logging.getLogger(__name__)

ACTOR_NOT_HUMAN_MESSAGE = "Only a person may decide about a page."
ROLE_NOT_ALLOWED_MESSAGE = "This decision is not open to your role."
NOT_AWAITING_MESSAGE = "That page is not waiting for this decision."
UNKNOWN_CASE_MESSAGE = "That case could not be found."
UNKNOWN_PAGE_MESSAGE = "That page could not be found."
NOT_TOLD_MESSAGE = (
    "Your decision was saved, but the case could not be told. Please try again."
)

# A failed or completed case takes no decision.
CASE_STATUSES_TAKING_DECISIONS: frozenset[CaseStatus] = frozenset(
    {CaseStatus.RUNNING, CaseStatus.AWAITING_HUMAN}
)


def authorise(actor: str, decision: Decision) -> tuple[DemoRole, DecisionRule]:
    """Check who decides what; answer with the actor's role and the decision's rule.

    An actor that is not a demo role is `actor_not_human`, whatever it asks
    for. A demo role asking for a decision that is not its own is
    `role_not_allowed`.
    """
    role = human_role(actor)
    if role is None:
        raise DomainError(ErrorCode.ACTOR_NOT_HUMAN, ACTOR_NOT_HUMAN_MESSAGE)
    rule = decision_rule(decision)
    if rule.role is not role:
        raise DomainError(ErrorCode.ROLE_NOT_ALLOWED, ROLE_NOT_ALLOWED_MESSAGE)
    return role, rule


def case_takes_decisions(case_status: CaseStatus, stop_after: StopAfter | None) -> bool:
    """Whether a page of a case in that state may be decided at all.

    Not when the case has failed or is complete, and never for a case told
    to stop after the gate (AD-17: nobody decides the pages of a bake-off run).
    """
    return (
        case_status in CASE_STATUSES_TAKING_DECISIONS
        and stop_after is not StopAfter.GATE
    )


def status_a_decision_leaves(
    awaited: PageStatus, decision: object
) -> PageStatus | None:
    """The status a decision leaves a page in that awaits it; None if it is no decision for that status.

    Pure: the case orchestration calls it on the payload of a decision event.
    """
    try:
        rule = decision_rule(Decision(str(decision)))
    except ValueError:
        return None
    return rule.leaves if rule.awaits is awaited else None


def decision_recording(
    decision: PageDecision, rule: DecisionRule, trace_id: str | None = None
) -> Recording:
    """What a decision changes: its own row, the page's status and one audit event (AD-8).

    The event's actor kind is `human`, its actor the demo role and its
    reference the decision's id. The page moves only from the status the
    decision needs. The store sets the case status the pages then give
    (`domain/case_status.py`), in the same transaction.
    """
    return Recording(
        audit=AuditRecord(
            actor_kind=ActorKind.HUMAN,
            actor=decision.actor.value,
            action=rule.action,
            occurred_at=decision.occurred_at,
            case_id=decision.case_id,
            page_id=decision.page_id,
            ref=decision.decision_id,
            detail=None,
            trace_id=trace_id or NO_TRACE_ID,
            # The store fills in the eval run the case belongs to, if any.
            eval_run_id=None,
        ),
        page_change=PageChange(decision.page_id, rule.leaves, only_from=rule.awaits),
        decision=decision,
    )


def decision_recorded(decision: PageDecision) -> DecisionRecorded:
    """The answer to a decision: the stored one, with the status it left the page in."""
    return DecisionRecorded(
        decision_id=decision.decision_id,
        case_id=decision.case_id,
        page_id=decision.page_id,
        decision=decision.decision,
        actor=decision.actor,
        page_status=decision_rule(decision.decision).leaves,
        occurred_at=decision.occurred_at,
    )


def _log_refusal(
    case_id: str, page_id: str, decision: Decision, code: ErrorCode
) -> None:
    logger.warning(
        "decision refused: case_id=%s page_id=%s decision=%s code=%s",
        case_id,
        page_id,
        decision.value,
        code.value,
    )


async def record_decision(
    case_id: str,
    page_id: str,
    request: DecisionRequest,
    *,
    store: CaseStore,
    engine: LifecycleEngine,
    trace_id: str | None = None,
    now: Callable[[], datetime] = utc_now,
    new_decision_id: Callable[[], str] = new_id,
) -> DecisionRecorded:
    """Record one human decision about one page, then tell the case's orchestration (AD-10).

    The decision's row, the page's new status, the case status that follows
    and the audit event are written in one transaction, or not at all. Only
    then is the orchestration told, by an event: the person has an answer at
    once, and the event only wakes the lifecycle.

    The same decision again writes nothing: it is answered with the stored
    one, and the event is raised again. A decision whose event could not be
    raised (`upstream_unavailable`) is not lost either way: the caller may
    repeat it, and if nobody does, the service tells the orchestration by
    itself (`tell_untold_decisions`). A decision that was told is marked so.

    Refused, with nothing changed: an actor that is not a demo role
    (`actor_not_human`), a role deciding what is not its own
    (`role_not_allowed`), an unknown case or page (`not_found`), and a page
    that awaits no such decision or whose case is failed, completed or told
    to stop after the gate (`not_awaiting_decision`).
    """
    try:
        role, rule = authorise(request.actor, request.decision)
    except DomainError as refusal:
        # security rule 31: ids, the decision and the code. The actor is the
        # caller's own text and is never logged.
        _log_refusal(case_id, page_id, request.decision, refusal.code)
        raise
    wanted = PageDecision(
        decision_id=new_decision_id(),
        case_id=case_id,
        page_id=page_id,
        decision=request.decision,
        actor=role,
        occurred_at=now(),
    )
    decided = await store.decide(
        decision_recording(wanted, rule, trace_id), wanted.occurred_at
    )
    # security rule 31: ids, the decision and a code; nothing of the page.
    logger.info(
        "decision %s: case_id=%s page_id=%s decision=%s actor=%s",
        decided.outcome.value,
        case_id,
        page_id,
        request.decision.value,
        role.value,
    )
    if decided.outcome is DecisionOutcome.UNKNOWN_CASE:
        _log_refusal(case_id, page_id, request.decision, ErrorCode.NOT_FOUND)
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_CASE_MESSAGE)
    if decided.outcome is DecisionOutcome.UNKNOWN_PAGE:
        _log_refusal(case_id, page_id, request.decision, ErrorCode.NOT_FOUND)
        raise DomainError(ErrorCode.NOT_FOUND, UNKNOWN_PAGE_MESSAGE)
    if decided.decision is None:
        _log_refusal(
            case_id, page_id, request.decision, ErrorCode.NOT_AWAITING_DECISION
        )
        raise DomainError(ErrorCode.NOT_AWAITING_DECISION, NOT_AWAITING_MESSAGE)
    try:
        told = await engine.decision_made(
            case_id, page_id, rule.awaits, request.decision
        )
    except Exception as error:
        # security rule 31: the error's type; its message can hold an address.
        # The decision is stored and carries no mark of having been told:
        # the service tells it again by itself (`tell_untold_decisions`),
        # whether or not the caller repeats it.
        logger.error(
            "decision event not raised: case_id=%s page_id=%s type=%s",
            case_id,
            page_id,
            type(error).__qualname__,
        )
        raise DomainError(ErrorCode.UPSTREAM_UNAVAILABLE, NOT_TOLD_MESSAGE) from error
    await _settle_telling(decided.decision, told, store, now, trace_id)
    return decision_recorded(decided.decision)


async def _mark_told(
    decision: PageDecision, store: CaseStore, now: Callable[[], datetime]
) -> None:
    """Note that the orchestration was told of a decision; a failure here is not the caller's.

    Without the mark the decision is told once more later, which does no
    harm: the orchestration takes each decision once.
    """
    try:
        await store.mark_decision_told(decision.decision_id, now())
    except Exception as error:  # noqa: BLE001 - the decision is stored and told; only the mark is missing
        # security rule 31: ids and the error's type.
        logger.warning(
            "decision told but not marked: case_id=%s page_id=%s decision_id=%s type=%s",
            decision.case_id,
            decision.page_id,
            decision.decision_id,
            type(error).__qualname__,
        )


async def _settle_telling(
    decision: PageDecision,
    told: Told,
    store: CaseStore,
    now: Callable[[], datetime],
    trace_id: str | None = None,
) -> None:
    """Act on what became of telling a decision: mark it, or see to a case nothing runs.

    Told, or not needed because the lifecycle ended as it should: the
    decision is marked. But when the case has no orchestration, or a dead
    one, nothing will ever go on with the decision. It is not marked as
    told. The case is failed through the lifecycle's own failure path
    (status and one case-level `stage.failed` event, AD-8), so that it does
    not stay `running` or waiting for ever, and the log says why. A failed
    case's decisions are looked for no more. If the case cannot fail any
    more because it is complete already, there is nothing left to wake and
    the decision is marked after all.
    """
    if not told.nothing_runs_the_case:
        await _mark_told(decision, store, now)
        return
    outcome = await fail_case(decision.case_id, store=store, trace_id=trace_id, now=now)
    status = await store.status(decision.case_id)
    # security rule 31: ids and codes.
    logger.error(
        "decision has no orchestration to tell: case_id=%s page_id=%s "
        "decision_id=%s orchestration=%s case_failed=%s case_status=%s",
        decision.case_id,
        decision.page_id,
        decision.decision_id,
        told.value,
        outcome.value,
        status.value if status is not None else None,
    )
    if status is CaseStatus.COMPLETED:
        await _mark_told(decision, store, now)


@dataclass(frozen=True, slots=True)
class DecisionTeller:
    """What the service's own telling of decisions runs with (AD-5): the settings `WORKFLOW_DECISION_TELL_*`."""

    # How long the service waits between two looks.
    interval_seconds: float = 15.0
    # After a look that failed (the scheduler or the database could not be
    # reached) the wait doubles, up to this; a look that worked resets it.
    max_interval_seconds: float = 300.0
    # A decision younger than this is still being told by the request that stored it.
    grace_seconds: float = 30.0
    # How many decisions one look takes at most.
    batch_size: int = 50

    def wait_after(self, failed_looks: int) -> float:
        """How long to wait before the next look, after that many failed looks in a row."""
        if failed_looks <= 0:
            return self.interval_seconds
        # Capped before it is multiplied out, so a long outage cannot overflow.
        doublings = min(failed_looks, 32)
        return min(self.interval_seconds * 2.0**doublings, self.max_interval_seconds)


async def tell_untold_decisions(
    teller: DecisionTeller,
    *,
    store: CaseStore,
    engine: LifecycleEngine,
    now: Callable[[], datetime] = utc_now,
) -> int:
    """Tell the orchestrations of every stored decision they were not told of; answer how many were told.

    A decision is stored by the request that makes it and its event is
    raised afterwards. If that raise fails, or the process dies between the
    two, the page and the case status are right and the orchestration waits.
    This finds such decisions by the missing mark, once they are older than
    the grace time, and raises their events: the same event the request
    would have raised, which the orchestration takes once however often it
    is told. What follows is as for the request (`_settle_telling`): a
    decision that was told is marked; a case that turns out to have no
    orchestration is failed, not left waiting.

    No decision is ever given up. If the engine cannot be reached the look
    ends there and raises, with a log line; the decision is still unmarked,
    and the caller looks again after a wait that grows up to a cap
    (`DecisionTeller.wait_after`).

    It is the service that looks, on a timer of its own. The orchestration
    neither polls nor has a timer (AD-5).
    """
    untold = await store.decisions_not_told(
        now() - timedelta(seconds=teller.grace_seconds), teller.batch_size
    )
    told_now = 0
    for decision in untold:
        try:
            told = await engine.decision_made(
                decision.case_id,
                decision.page_id,
                decision_rule(decision.decision).awaits,
                decision.decision,
            )
        except Exception as error:
            # security rule 31: ids, counts and the error's type. The rest
            # of this look would meet the same engine: it ends here.
            logger.warning(
                "decision not told again: case_id=%s page_id=%s decision_id=%s "
                "told_in_this_look=%d left=%d type=%s",
                decision.case_id,
                decision.page_id,
                decision.decision_id,
                told_now,
                len(untold) - told_now,
                type(error).__qualname__,
            )
            raise
        logger.info(
            "decision told again: case_id=%s page_id=%s decision_id=%s orchestration=%s",
            decision.case_id,
            decision.page_id,
            decision.decision_id,
            told.value,
        )
        await _settle_telling(decision, told, store, now)
        told_now += told is Told.TOLD
    return told_now
