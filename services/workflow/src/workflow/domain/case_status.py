"""The case status follows its pages: the one rule (AD-5, AD-10).

Once a case's pages are routed, what the case is waiting for is read off
them. The store applies this in the transaction of every change that can
move the case, so no late retry sets a status the pages do not have. The
rule is pure: the case orchestration may call it too.
"""

from collections.abc import Iterable

from contracts.decisions import STATUSES_AWAITING_A_DECISION
from contracts.enums import CaseStatus, PageStatus, StopAfter
from workflow.domain.transitions import PAGE_TRANSITIONS

# A page in one of these statuses will not change again.
FINAL_PAGE_STATUSES: frozenset[PageStatus] = frozenset(
    status for status, following in PAGE_TRANSITIONS.items() if not following
)


def case_status_following(page_statuses: Iterable[PageStatus]) -> CaseStatus:
    """The status a case has, given the statuses of its pages.

    It waits for a human if any page does. Otherwise it is running if any
    page is still in work, and completed once every page is final. A case
    with no page yet is running: its pages are still to come.
    """
    statuses = frozenset(page_statuses)
    if statuses & STATUSES_AWAITING_A_DECISION:
        return CaseStatus.AWAITING_HUMAN
    if not statuses or statuses - FINAL_PAGE_STATUSES:
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
