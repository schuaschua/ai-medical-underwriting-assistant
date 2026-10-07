"""The classification bake-off: what each contender stored for every page of the scored set (spine AD-17, AD-13).

Each file of the page set is uploaded once per contender and started with
that contender, the run's `eval_run_id` and `stop_after` `gate`: the pages
are redacted, classified and routed, and nothing more. No page is extracted,
no verdict runs, and no page waits for a person, so the runner answers no
wait here. When the case has come to rest the runner reads two things
through `web`: the case's progress (where the gate routed each page) and its
stored classifications. It labels, routes and decides nothing itself.
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from bakeoff.answer_key import AnswerKeyEntry, PageSetDocument
from bakeoff.client import Sleep, WebClient, WebError, upload_key
from bakeoff.redaction import leaked_categories
from bakeoff.state import RunState
from bakeoff.verdicts import Clock
from contracts.enums import (
    CaseStatus,
    ClassifierContender,
    PageStatus,
    StageStatus,
    StopAfter,
)
from contracts.errors import ErrorCode
from contracts.models.classification import Classification
from contracts.models.web import (
    ClassifierUnscoredCase,
    ReasonLeak,
    UnscoredReason,
)
from contracts.models.workflow import CaseProgress, StartCaseOptions

logger = logging.getLogger(__name__)

_FINAL = frozenset({CaseStatus.COMPLETED, CaseStatus.FAILED})


def state_key(contender: ClassifierContender, case_key: str) -> str:
    """The name a file's case is remembered under: a case per contender."""
    return f"{contender.value}:{case_key}"


@dataclass(frozen=True)
class StoredPage:
    """One page as the system left it: where the gate put it, and this contender's stored result."""

    page_number: int
    page_status: PageStatus
    error_code: ErrorCode | None
    # None when the contender stored no result for the page.
    result: Classification | None


@dataclass
class DocumentOutcome:
    """What one file gave for one contender: its pages, or why they all count as wrong."""

    contender: ClassifierContender
    case_key: str
    case_id: str | None = None
    # By page number; what could be read, also of a case that is not scored.
    pages: dict[int, StoredPage] = field(default_factory=dict)
    unscored: ClassifierUnscoredCase | None = None
    # What `web` shows when `classification` refuses the contender's
    # commands: see `_refused`. Never set for `llm`.
    refused: bool = False
    # Whether the stored reasons of the file were read: the case was final
    # and its classifications were answered.
    reasons_read: bool = False


class StateNotKept(Exception):
    """The run's state file could not be written: a resume depends on it, so the run breaks off."""


def reason_leaks(entry: AnswerKeyEntry, outcome: DocumentOutcome) -> list[ReasonLeak]:
    """The stored reasons of a file that hold a planted identifier of its case.

    The redaction check's own rule, on the reason in place of the page
    text. The place and the kind, never what was found.
    """
    leaks: list[ReasonLeak] = []
    for number, page in sorted(outcome.pages.items()):
        if page.result is None:
            continue
        for category in sorted(leaked_categories(entry, page.result.reason)):
            logger.error(
                "planted identifier in a stored reason: case=%s page=%d "
                "contender=%s category=%s",
                outcome.case_key,
                number,
                outcome.contender.value,
                category,
            )
            leaks.append(
                ReasonLeak(
                    contender=outcome.contender,
                    case_key=outcome.case_key,
                    page_number=number,
                    category=category,
                )
            )
    return leaks


def _refused(
    contender: ClassifierContender, progress: CaseProgress, results: int
) -> bool:
    """Whether a case shows that `classification` refused its contender's commands.

    `classification` refuses a contender it cannot run before it stores
    anything, and `workflow` then fails the case by its lifecycle: redaction
    is done, the case carries the case-level `stage_failed`, every page is
    still `uploaded` and none has an error. The caller has also read the
    case's classifications from `classification` itself, so the service
    answers, and it holds none for the case. A `classification` that does
    not answer fails that read instead and is not taken for a refusal.
    The chat contender is the one every instance can run: never for it.
    """
    return (
        contender is not ClassifierContender.LLM
        and results == 0
        and progress.case_status is CaseStatus.FAILED
        and progress.error_code is ErrorCode.STAGE_FAILED
        and progress.redaction_status is StageStatus.DONE
        and bool(progress.pages)
        and all(
            page.page_status is PageStatus.UPLOADED and page.error_code is None
            for page in progress.pages
        )
    )


@dataclass(frozen=True)
class ContenderRunner:
    """Takes one file of the page set to the gate with one contender, and reads what was stored."""

    client: WebClient
    state: RunState
    contender: ClassifierContender
    cases_dir: Path
    deadline_seconds: float
    poll_seconds: float
    sleep: Sleep
    clock: Clock = time.monotonic

    async def run(self, document: PageSetDocument) -> DocumentOutcome:
        key = state_key(self.contender, document.case_key)
        outcome = DocumentOutcome(
            self.contender, document.case_key, self.state.case_id(key)
        )
        status: CaseStatus | None = None
        try:
            if outcome.case_id is None:
                # Uploaded once per contender: a resumed run finds the case here.
                pdf = (self.cases_dir / document.file_name).read_bytes()
                uploaded = await self.client.upload(
                    pdf,
                    upload_key(
                        self.state.eval_run_id, document.case_key, self.contender
                    ),
                )
                outcome.case_id = uploaded.case_id
                try:
                    self.state.record(key, uploaded.case_id)
                except OSError as error:
                    # Not one file's failure: without it a resume would not
                    # find this case. The error's type only.
                    raise StateNotKept(type(error).__qualname__) from None
            if await self.client.progress(outcome.case_id) is None:
                # Stopped after the gate: no extraction, no verdict, no wait.
                await self.client.start(
                    outcome.case_id,
                    StartCaseOptions(
                        classifier_contender=self.contender,
                        stop_after=StopAfter.GATE,
                        eval_run_id=self.state.eval_run_id,
                    ),
                )
            progress, reason = await self._until_final(outcome.case_id)
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
            if progress is not None:
                results = await self._read_pages(outcome, progress)
                # A case that is not final may still store a reason.
                outcome.reasons_read = status in _FINAL
                outcome.refused = _refused(self.contender, progress, results)
        except WebError as error:
            logger.warning(
                "file not scored: case=%s contender=%s %s",
                document.case_key,
                self.contender.value,
                error,
            )
            # The reason first found stands: a case that failed stays that.
            outcome.unscored = outcome.unscored or self._unscored(
                outcome, status, "request_failed", error.code
            )
        except OSError as error:
            # The file's PDF could not be read. The error's type only: its
            # message holds a path.
            logger.warning(
                "file not scored: case=%s contender=%s type=%s",
                document.case_key,
                self.contender.value,
                type(error).__qualname__,
            )
            outcome.unscored = outcome.unscored or self._unscored(
                outcome, status, "request_failed", None
            )
        return outcome

    async def _until_final(
        self, case_id: str
    ) -> tuple[CaseProgress | None, UnscoredReason | None]:
        """Wait until the case is final; the last progress, and why it is not scored."""
        give_up_at = self.clock() + self.deadline_seconds
        while True:
            progress = await self.client.progress(case_id)
            if progress is not None and progress.case_status in _FINAL:
                return progress, None
            if self.clock() >= give_up_at:
                return progress, "not_final_in_time"
            await self.sleep(self.poll_seconds)

    async def _read_pages(
        self, outcome: DocumentOutcome, progress: CaseProgress
    ) -> int:
        """Read the pages as they were left, each with this contender's stored result; how many results the case holds."""
        if outcome.case_id is None:
            return 0
        listed = await self.client.classifications(outcome.case_id)
        # A page may hold a result of each contender: only this one's counts.
        own = {
            result.page_id: result
            for result in listed.classifications
            if result.contender is self.contender
        }
        outcome.pages = {
            page.page_number: StoredPage(
                page.page_number,
                page.page_status,
                page.error_code,
                own.get(page.page_id),
            )
            for page in progress.pages
        }
        return len(listed.classifications)

    def _unscored(
        self,
        outcome: DocumentOutcome,
        status: CaseStatus | None,
        reason: UnscoredReason,
        code: ErrorCode | None,
    ) -> ClassifierUnscoredCase:
        return ClassifierUnscoredCase(
            contender=self.contender,
            case_key=outcome.case_key,
            case_id=outcome.case_id,
            case_status=status,
            reason=reason,
            error_code=code,
        )
