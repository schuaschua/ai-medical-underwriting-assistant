"""Verdict accuracy: how often the whole agent is right with a row (spine AD-17).

Each case is uploaded once and started once, with every available row and
the bake-off run's `eval_run_id`, so every row judges the same extracted
facts. The runner stands in for the two people: it answers every human wait
from the answer key's page labels and from nothing else. When the case is
final it reads the case's verdict runs.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from bakeoff.answer_key import AnswerKeyEntry, ExpectedVerdict
from bakeoff.client import Sleep, WebClient, WebError, upload_key
from bakeoff.state import RunState
from contracts.decisions import STATUSES_AWAITING_A_DECISION
from contracts.enums import (
    CaseStatus,
    Decision,
    PageStatus,
    RetrieverConfig,
    StageStatus,
    Verdict,
)
from contracts.errors import ErrorCode
from contracts.models.verdict import VerdictRun
from contracts.models.web import UnscoredCase, UnscoredReason
from contracts.models.workflow import CaseProgress, StartCaseOptions

logger = logging.getLogger(__name__)

_FINAL = frozenset({CaseStatus.COMPLETED, CaseStatus.FAILED})

Clock = Callable[[], float]


def decision_for(page_status: PageStatus, is_medical: bool) -> Decision | None:
    """What the answer key's label says to a waiting page; None for a page that does not wait.

    A page that waits for the customer is kept if it is medical and
    discarded if not. A page in triage is accepted if it is medical and
    denied if not.
    """
    if page_status is PageStatus.AWAITING_CUSTOMER:
        return Decision.KEEP if is_medical else Decision.DISCARD
    if page_status is PageStatus.AWAITING_TRIAGE:
        return Decision.ACCEPT if is_medical else Decision.DENY
    return None


def run_is_right(run: VerdictRun | None, expected: ExpectedVerdict) -> bool:
    """Whether a row's run gave the expected verdict, and for a loaded case the expected loading."""
    if run is None or run.status is not StageStatus.DONE:
        return False
    if run.verdict is not expected.verdict:
        return False
    return (
        expected.verdict is not Verdict.LOADED
        or run.loading_pct == expected.loading_pct
    )


@dataclass
class CaseOutcome:
    """What one case gave: its runs by row, or why it counts as wrong for every row."""

    case_key: str
    case_id: str | None = None
    runs: dict[RetrieverConfig, VerdictRun] = field(default_factory=dict)
    unscored: UnscoredCase | None = None

    def right_rows(self, expected: ExpectedVerdict) -> set[RetrieverConfig]:
        """The rows whose run is right; none for a case that failed or did not finish."""
        if self.unscored is not None:
            return set()
        return {row for row, run in self.runs.items() if run_is_right(run, expected)}

    def run_failed(self, row: RetrieverConfig) -> bool:
        """Whether a scored case holds no finished run of this row: the system failed, not the verdict.

        False for a case that was not scored: it is listed as such, once.
        """
        if self.unscored is not None:
            return False
        run = self.runs.get(row)
        return run is None or run.status is not StageStatus.DONE


@dataclass(frozen=True)
class CaseRunner:
    """Runs one case through `web`, from its upload to its verdict runs."""

    client: WebClient
    state: RunState
    rows: Sequence[RetrieverConfig]
    cases_dir: Path
    deadline_seconds: float
    poll_seconds: float
    sleep: Sleep
    clock: Clock = time.monotonic

    async def run(self, entry: AnswerKeyEntry) -> CaseOutcome:
        outcome = CaseOutcome(entry.case_key, self.state.case_id(entry.case_key))
        status: CaseStatus | None = None
        try:
            if outcome.case_id is None:
                # Uploaded once: a run that is resumed finds the case here.
                pdf = (self.cases_dir / entry.file_name).read_bytes()
                uploaded = await self.client.upload(
                    pdf, upload_key(self.state.eval_run_id, entry.case_key)
                )
                outcome.case_id = uploaded.case_id
                self.state.record(entry.case_key, uploaded.case_id)
            progress = await self.client.progress(outcome.case_id)
            if progress is None:
                # Started once, with every available row and the run's id.
                await self.client.start(
                    outcome.case_id,
                    StartCaseOptions(
                        retriever_configs=list(self.rows),
                        eval_run_id=self.state.eval_run_id,
                    ),
                )
            progress, reason = await self._until_final(entry, outcome.case_id)
            status = progress.case_status if progress is not None else None
            if reason is None and status is CaseStatus.FAILED:
                reason = "case_failed"
            if reason is not None:
                outcome.unscored = self._unscored(
                    outcome,
                    status,
                    reason,
                    progress.error_code if progress is not None else None,
                )
                return outcome
            listed = await self.client.verdict_runs(outcome.case_id)
            outcome.runs = {run.retriever_config: run for run in listed.verdict_runs}
        except WebError as error:
            logger.warning("case not scored: case=%s %s", entry.case_key, error)
            outcome.unscored = self._unscored(
                outcome, status, "request_failed", error.code
            )
        except OSError as error:
            # The case's PDF or the run's state file: this case is lost, the
            # others go on. The error's type only: its message holds a path.
            logger.warning(
                "case not scored: case=%s type=%s",
                entry.case_key,
                type(error).__qualname__,
            )
            outcome.unscored = self._unscored(outcome, status, "request_failed", None)
        return outcome

    async def _until_final(
        self, entry: AnswerKeyEntry, case_id: str
    ) -> tuple[CaseProgress | None, UnscoredReason | None]:
        """Answer the case's waits until it is final; the last progress, and why it is not scored."""
        give_up_at = self.clock() + self.deadline_seconds
        answered: set[tuple[str, Decision]] = set()
        progress: CaseProgress | None = None
        while True:
            progress = await self.client.progress(case_id)
            if progress is not None and progress.case_status in _FINAL:
                return progress, None
            for page in progress.pages if progress is not None else ():
                if page.page_status not in STATUSES_AWAITING_A_DECISION:
                    continue
                label = entry.is_medical(page.page_number)
                if label is None:
                    # The key cannot answer this wait, and nothing else may.
                    return progress, "wait_without_label"
                decision = decision_for(page.page_status, label)
                if decision is None or (page.page_id, decision) in answered:
                    continue
                if await self._decide(case_id, page.page_id, decision):
                    answered.add((page.page_id, decision))
            if self.clock() >= give_up_at:
                return progress, "not_final_in_time"
            await self.sleep(self.poll_seconds)

    async def _decide(self, case_id: str, page_id: str, decision: Decision) -> bool:
        """Send one decision; whether it was taken."""
        try:
            await self.client.decide(case_id, page_id, decision)
        except WebError as error:
            # The page was not waiting for this when the answer came. It is
            # read again, and the decision sent again if it then still waits.
            if error.code is not ErrorCode.NOT_AWAITING_DECISION:
                raise
            return False
        return True

    @staticmethod
    def _unscored(
        outcome: CaseOutcome,
        status: CaseStatus | None,
        reason: UnscoredReason,
        code: ErrorCode | None,
    ) -> UnscoredCase:
        return UnscoredCase(
            case_key=outcome.case_key,
            case_id=outcome.case_id,
            case_status=status,
            reason=reason,
            error_code=code,
        )
