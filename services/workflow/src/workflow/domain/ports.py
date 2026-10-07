"""What the lifecycle needs from the outside world; adapters provide it."""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from contracts.enums import CaseStatus, ClassifierContender
from contracts.models.classification import ClassificationResult
from contracts.models.intake import RedactionResult
from contracts.models.workflow import AuditTrail, CaseProgress
from workflow.domain.entities import CaseRecord
from workflow.domain.recording import Recording, RecordOutcome


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

    async def progress(self, case_id: str) -> CaseProgress | None:
        """The case's status and its pages, or None if the case is unknown."""
        ...

    async def audit_trail(self, case_id: str) -> AuditTrail | None:
        """The case's audit events in time order, or None if the case is unknown."""
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
