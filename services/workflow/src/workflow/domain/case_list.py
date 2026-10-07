"""The underwriter's list of cases: every case, newest first, with what it waits for.

The list is how a finished case is found again, so it leaves out no case for
its status. It leaves out the cases of an eval run (AD-17), as the triage
queue does. The store applies these rules in the statement that reads the list.
"""

from contracts.enums import CaseStatus, StopAfter
from contracts.models.workflow import CaseList
from workflow.domain.decisions import case_takes_decisions
from workflow.domain.ports import CaseStore

# How many cases one read of the list holds at most (WORKFLOW_CASE_LIST_LIMIT).
DEFAULT_CASE_LIST_LIMIT = 100


def waiting_page_count(
    case_status: CaseStatus, stop_after: StopAfter | None, awaiting: int
) -> int:
    """How many pages of a case wait for a person, given how many are in a waiting status.

    None, when the case takes no decision (`domain/decisions.py`): a page
    of a failed or completed case stays in the status it had, but nobody is
    asked about it any more.
    """
    return awaiting if case_takes_decisions(case_status, stop_after) else 0


async def read_case_list(
    *, store: CaseStore, limit: int = DEFAULT_CASE_LIST_LIMIT
) -> CaseList:
    """The cases, newest started first, at most `limit`.

    A limit under 1 is the caller's mistake and is refused before anything is read.
    """
    if limit < 1:
        raise ValueError("the case list's limit must be at least 1")
    return await store.case_list(limit)
