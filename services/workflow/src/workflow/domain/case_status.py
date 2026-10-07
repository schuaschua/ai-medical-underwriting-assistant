"""The case status follows its pages: the one rule (AD-5, AD-10, AD-15).

Once a case's pages are routed, what the case is waiting for is read off
them. The store applies this in the transaction of every change that can
move the case, so no late retry sets a status the pages do not have. The
rule is pure: the case orchestration may call it too.

A case whose every page is final is not complete yet: its verdict runs come
first, one per retriever configuration it was started with. Until they are
recorded it is `running`, and it is completed by a step of its own.
"""

from collections.abc import Iterable

from contracts.decisions import STATUSES_AWAITING_A_DECISION
from contracts.enums import CaseStatus, PageStatus, StopAfter
from workflow.domain.transitions import PAGE_TRANSITIONS

# A page in one of these statuses will not change again.
FINAL_PAGE_STATUSES: frozenset[PageStatus] = frozenset(
    status for status, following in PAGE_TRANSITIONS.items() if not following
)


def pages_are_final(page_statuses: Iterable[PageStatus]) -> bool:
    """Whether a case has pages and every one of them is final: the condition of a verdict run."""
    statuses = frozenset(page_statuses)
    return bool(statuses) and not statuses - FINAL_PAGE_STATUSES


def case_status_following(
    page_statuses: Iterable[PageStatus], *, verdicts_recorded: bool = False
) -> CaseStatus:
    """The status a case has, given the statuses of its pages.

    It waits for a human if any page does. Otherwise it is running while
    any page is still in work, and also while every page is final and its
    verdict runs are still to be recorded (AD-15). It is completed once
    every page is final and `verdicts_recorded` says the runs are in the
    trail. A case with no page yet is running: its pages are still to come.
    """
    statuses = frozenset(page_statuses)
    if statuses & STATUSES_AWAITING_A_DECISION:
        return CaseStatus.AWAITING_HUMAN
    if not pages_are_final(statuses) or not verdicts_recorded:
        return CaseStatus.RUNNING
    return CaseStatus.COMPLETED


def case_status_after_gate(
    page_statuses: Iterable[PageStatus], stop_after: StopAfter | None
) -> CaseStatus:
    """The case's status once every page is routed.

    A case told to stop after the gate is complete, whatever its pages wait
    for (AD-17: nobody decides the pages of a bake-off run). Every other
    case follows its pages.
    """
    if stop_after is StopAfter.GATE:
        return CaseStatus.COMPLETED
    return case_status_following(page_statuses)
