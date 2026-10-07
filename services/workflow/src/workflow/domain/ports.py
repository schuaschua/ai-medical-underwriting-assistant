"""What the lifecycle needs from the outside world; adapters provide it."""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from contracts.audit import RouteDetail
from contracts.enums import CaseStatus, ClassifierContender, Decision, PageStatus
from contracts.models.classification import ClassificationResult
from contracts.models.intake import RedactionResult
from contracts.models.workflow import AuditTrail, CaseProgress, PageQueue
from workflow.domain.entities import CaseRecord, SettledCase
from workflow.domain.recording import Decided, Recording, RecordOutcome


class CaseStore(Protocol):
    """Case and page status and the audit trail (schema `workflow`, AD-4)."""

    async def start(self, case: CaseRecord) -> CaseRecord:
        """Store the case unless one with its id exists; return the stored one."""
        ...

    async def status(self, case_id: str) -> CaseStatus | None:
        """The case's status, or None if the case is unknown."""
        ...

    async def record(
        self, recording: Recording, recorded_at: datetime
    ) -> RecordOutcome:
        """Write the status changes and the audit event in one transaction, or neither.

        A recording whose audit event is already in the trail writes nothing,
        and neither does one whose status change may not follow the current
        status (`domain/transitions.py`); the outcome says which.
        The trail is append-only: no implementation updates or deletes an event.
        """
        ...

    async def route_of(
        self, case_id: str, page_id: str, ref: str
    ) -> RouteDetail | None:
        """The detail of the `page.routed` event in the trail for that page and reference, if any."""
        ...

    async def decide(self, recording: Recording, recorded_at: datetime) -> Decided:
        """Write a human decision in one transaction, or nothing (AD-8, AD-10).

        Together: the decision's row, the page's new status (only from the
        status the decision needs), the case status its pages then give
        (`domain/case_status.py`) and the audit event, which takes the case's
        eval run id. A page that holds the same decision already is answered
        with that one and nothing is written. A page that is in another
        status, or whose case takes no decision
        (`domain/decisions.py`), is left as it is.
        """
        ...

    async def settle_case(
        self, case_id: str, settled_at: datetime
    ) -> SettledCase | None:
        """Give the case the status its pages give it after the gate; return the case as it is then.

        The status is worked out from the stored pages, in the transaction
        that sets it (`domain/case_status.py`), so asking again, however
        late, never undoes what a decision has changed since. A status that
        may not follow the one the case has (`domain/transitions.py`) is not
        set. The answer holds the status the case has and the statuses of
        its pages as that transaction read them. None if the case is unknown.
        """
        ...

    async def progress(self, case_id: str) -> CaseProgress | None:
        """The case's status and its pages, each with the code of its failure if a stage failed.

        None if the case is unknown.
        """
        ...

    async def audit_trail(self, case_id: str) -> AuditTrail | None:
        """The case's audit events in time order, or None if the case is unknown."""
        ...

    async def queue(self, status: PageStatus, limit: int) -> PageQueue:
        """The pages across cases in `status`, oldest waiting first, at most `limit` of them.

        Left out: every page of a case that belongs to an eval run, and every
        page of a case that takes no decision (`domain/decisions.py`: failed,
        completed, or told to stop after the gate). `has_more` says that more
        such pages wait than are listed. Each page says how it came to wait
        (`domain/queue.py`).
        """
        ...


class EngineState(StrEnum):
    """What the engine holds for a case after a start."""

    CREATED = "created"  # this call created the orchestration
    ACTIVE = "active"  # it existed and is still to run, or running
    COMPLETED = "completed"
    # It failed or was terminated: nothing will run the case any more.
    DEAD = "dead"


class LifecycleEngine(Protocol):
    """The orchestration engine (AD-5)."""

    async def ensure_started(self, case: CaseRecord) -> EngineState:
        """Make sure the case has its one orchestration, whose instance id is the `case_id`.

        Returns the state that orchestration is in. A case that already has
        one is left exactly as it is, whatever that state.
        """
        ...

    async def decision_made(
        self, case_id: str, page_id: str, awaited: PageStatus, decision: Decision
    ) -> None:
        """Tell the case's orchestration of a stored decision, by an external event (AD-5).

        `awaited` is the status the page had for the decision. Telling it
        twice does no harm, and neither does telling an orchestration that
        has ended. Raises if the engine could not be told.
        """
        ...


class StageServices(Protocol):
    """The stage commands `workflow` sends (AD-2, AD-6). Each is idempotent on its key.

    A command answers with the stage's stored result, done or failed. It
    raises `in_progress` while the stage still works on the same command,
    `not_found` when the stage does not hold what the command names, and
    `upstream_unavailable` when no answer could be had.
    """

    async def redact_document(
        self,
        case_id: str,
        *,
        eval_run_id: str | None,
        trace_context: Mapping[str, str],
    ) -> RedactionResult:
        """AD-21: have `intake` redact the case's document and split it into pages."""
        ...

    async def classify_page(
        self,
        case_id: str,
        page_id: str,
        contender: ClassifierContender,
        *,
        eval_run_id: str | None,
        trace_context: Mapping[str, str],
    ) -> ClassificationResult:
        """AD-13: have `classification` say what one page is, with the case's contender.

        Besides the errors above it raises `validation_failed` when the
        stage cannot run that contender; like `not_found`, no repeat mends it.
        """
        ...
