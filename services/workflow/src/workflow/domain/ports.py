"""What the lifecycle needs from the outside world; adapters provide it."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from contracts.audit import AuditRecord, RouteDetail
from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    Decision,
    PageStatus,
    RetrieverConfig,
    StageStatus,
)
from contracts.errors import ErrorCode
from contracts.models.classification import ClassificationResult
from contracts.models.extraction import FactSetResult
from contracts.models.intake import RedactionResult
from contracts.models.verdict import VerdictRunResult
from contracts.models.workflow import AuditTrail, CaseList, CaseProgress, PageQueue
from workflow.domain.entities import CaseRecord, PageDecision, SettledCase
from workflow.domain.recording import Decided, Recording, RecordOutcome


class CaseStore(Protocol):
    """Case and page status and the audit trail (schema `workflow`, AD-4)."""

    async def start(self, case: CaseRecord, started: AuditRecord) -> CaseRecord:
        """Store the case unless one with its id exists; return the stored one.

        `started` is the case's `case.started` event. It is written in the
        transaction that stores the case, and only then: a case that exists
        already keeps the event it has, whoever asks now.
        """
        ...

    async def status(self, case_id: str) -> CaseStatus | None:
        """The case's status, or None if the case is unknown."""
        ...

    async def case(self, case_id: str) -> CaseRecord | None:
        """The case as it is stored, with what it was started with; None if it is unknown."""
        ...

    async def complete_case(
        self, case_id: str, completed_at: datetime, trace_id: str | None
    ) -> SettledCase | None:
        """Complete a case whose pages are final and whose verdict runs are recorded (AD-15).

        In one transaction, under the lock on the case's row: the case is
        moved to `completed`, with its `case.completed` event, only if every
        page is final, the trail holds a `verdict.suggested` event for each
        retriever configuration the case was started with, and the status
        may follow the one the case has (`domain/transitions.py`). Otherwise
        nothing is written. Asking again changes nothing. The answer holds
        the status the case has afterwards and its pages as that
        transaction read them. None if the case is unknown.
        """
        ...

    async def record(
        self, recording: Recording, recorded_at: datetime
    ) -> RecordOutcome:
        """Write the status changes and the audit event in one transaction, or neither.

        A recording never names `completed` as the case status: one that
        does is a `ValueError`. A case is completed only where the
        `case.completed` event is written with the status: in
        `complete_case`, once its verdict runs are recorded, and in
        `settle_case` for a case told to stop after the gate. A recording
        that `follows_pages` (a done page stage result) gives the case the
        status its pages give it, which is never `completed`.
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
        eval run id. A decision never completes a case: one that makes the
        last page final leaves the case `running`, for its verdict runs. A page
        that holds the same decision already is answered
        with that one and nothing is written. A page that is in another
        status, or whose case takes no decision
        (`domain/decisions.py`), is left as it is.
        """
        ...

    async def settle_case(
        self, case_id: str, settled_at: datetime, trace_id: str | None
    ) -> SettledCase | None:
        """Give the case the status its pages give it after the gate; return the case as it is then.

        The status is worked out from the stored pages, in the transaction
        that sets it (`domain/case_status.py`), so asking again, however
        late, never undoes what a decision has changed since. A status that
        may not follow the one the case has (`domain/transitions.py`) is not
        set. Only a case told to stop after the gate is completed here, with
        its `case.completed` event in the same transaction, once: every
        other case has its verdict runs first. The answer holds the status the case
        has and the statuses of its pages as that transaction read them.
        None if the case is unknown.
        """
        ...

    async def mark_decision_told(self, decision_id: str, told_at: datetime) -> None:
        """Note that the case's orchestration was told of a stored decision (AD-5).

        A mark of its own, added once and never changed: marking a decision
        again does nothing.
        """
        ...

    async def decisions_not_told(
        self, decided_before: datetime, limit: int
    ) -> list[PageDecision]:
        """The stored decisions that carry no such mark, oldest first, at most `limit`.

        Only decisions made before `decided_before`: a younger one is still
        being told by the call that stored it. Decisions of a failed case
        are left out, since a failed case has no lifecycle to tell.
        """
        ...

    async def progress(self, case_id: str) -> CaseProgress | None:
        """The case's status and its pages, each with the code of its failure if a stage failed.

        None if the case is unknown.
        """
        ...

    async def audit_trail(self, case_id: str, limit: int) -> AuditTrail | None:
        """The case's first audit events, at most `limit`, or None if the case is unknown.

        In the order `workflow` wrote them, and by nothing else: never by
        `occurred_at`, which the clock of the service that did the work
        sets, nor by the record time. A `stage.failed` event
        carries the error code stored with it. `has_more` says that the case
        has more events than are listed.
        """
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

    async def case_list(self, limit: int) -> CaseList:
        """The cases, newest started first, at most `limit` of them.

        Left out: every case that belongs to an eval run. Each case says how
        many pages it has and how many of them wait for a person
        (`domain/case_list.py`). `has_more` says that more cases exist than
        are listed.
        """
        ...


class EngineState(StrEnum):
    """What the engine holds for a case after a start."""

    CREATED = "created"  # this call created the orchestration
    ACTIVE = "active"  # it existed and is still to run, or running
    COMPLETED = "completed"
    # It failed or was terminated: nothing will run the case any more.
    DEAD = "dead"


class Told(StrEnum):
    """What became of telling a case's orchestration of a stored decision."""

    # The event was raised: the orchestration is there to take it.
    TOLD = "told"
    # The orchestration has ended as it should; nothing waits for the event.
    ENDED = "ended"
    # There is no orchestration for the case.
    MISSING = "missing"
    # It failed or was terminated: nothing will run the case any more.
    DEAD = "dead"

    @property
    def nothing_runs_the_case(self) -> bool:
        """Whether no orchestration is there to go on with the case."""
        return self in (Told.MISSING, Told.DEAD)


@dataclass(frozen=True, slots=True)
class VerdictRunState:
    """What the engine holds for a verdict run that was asked for on a finished case."""

    # `running` while its orchestration is under way; then what it ended with.
    status: StageStatus
    # The run's id at `verdict`, once the orchestration has its result.
    verdict_run_id: str | None = None
    # For a failed run: why, as the orchestration's output names it.
    error_code: ErrorCode | None = None


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
    ) -> Told:
        """Tell the case's orchestration of a stored decision, by an external event (AD-5).

        `awaited` is the status the page had for the decision. Telling it
        twice does no harm. Answers whether there was an orchestration to
        tell: one that has ended, is missing or is dead is told nothing,
        and the answer says which. Raises if the engine could not be reached.
        """
        ...

    async def ensure_verdict_run(
        self, case: CaseRecord, retriever_config: RetrieverConfig
    ) -> VerdictRunState:
        """Make sure the asked-for verdict run has its one orchestration; say what state it is in.

        The orchestration's instance id is `<case_id>:verdict:<retriever_config>`
        (AD-15): one per case and configuration, whatever is repeated. One
        that runs, or that ended with a run stored at `verdict` (done or
        failed), is left as it is, and its state is answered. One that ended
        without a stored run (it died, or the stage never answered) is
        replaced by a new one: the fault may have passed. Raises if the
        engine could not be reached.
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

    async def extract_facts(
        self,
        case_id: str,
        page_id: str,
        *,
        eval_run_id: str | None,
        trace_context: Mapping[str, str],
    ) -> FactSetResult:
        """AD-14: have `extraction` read the facts of one page that reached `extracting`."""
        ...

    async def run_verdict(
        self,
        case_id: str,
        retriever_config: RetrieverConfig,
        *,
        eval_run_id: str | None,
        trace_context: Mapping[str, str],
    ) -> VerdictRunResult:
        """AD-15: have `verdict` suggest a verdict for a case whose pages are final, with one retriever row.

        Besides the errors above it raises `retriever_not_available` when
        the row is not built; like `not_found`, no repeat mends it.
        """
        ...
