"""The cross-case queue: the pages that wait for a person, oldest first (spine, Operations).

A queue is asked for by the status its pages wait in. It lists only pages a
person can still decide: none of a case that belongs to an eval run (AD-17:
nobody decides the pages of a bake-off run), and none of a case that takes no
decision (`domain/decisions.py`). The store applies these rules in the
statement that reads the queue.
"""

from contracts.decisions import STATUSES_AWAITING_A_DECISION
from contracts.enums import PageStatus, QueuedBy
from contracts.errors import DomainError, ErrorCode
from contracts.models.workflow import PageQueue
from workflow.domain.ports import CaseStore

# How many pages one read of a queue lists at most (WORKFLOW_PAGE_QUEUE_LIMIT).
DEFAULT_PAGE_QUEUE_LIMIT = 100

NOT_A_QUEUE_MESSAGE = "That status is not a queue."


def is_queue(status: PageStatus) -> bool:
    """Whether pages in that status wait for a person, and so form a queue."""
    return status in STATUSES_AWAITING_A_DECISION


def queued_by(status: PageStatus, kept_by_customer: bool) -> QueuedBy | None:
    """How a page came to wait in that status, as far as the stored decisions say.

    A page in triage that the customer kept came from the customer; any other
    page in triage was sent there by the gate. Nothing is said of other queues.
    """
    if status is not PageStatus.AWAITING_TRIAGE:
        return None
    return QueuedBy.CUSTOMER if kept_by_customer else QueuedBy.GATE


async def read_page_queue(
    status: PageStatus, *, store: CaseStore, limit: int = DEFAULT_PAGE_QUEUE_LIMIT
) -> PageQueue:
    """The pages across cases that wait in `status`, oldest waiting first, at most `limit`.

    `validation_failed` for a status in which no page waits for a person.
    """
    if not is_queue(status):
        raise DomainError(ErrorCode.VALIDATION_FAILED, NOT_A_QUEUE_MESSAGE)
    return await store.queue(status, limit)
