"""Payloads `web` answers the SPA with that no other service owns, the probe answer, and the scoreboard files."""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from contracts.base import (
    Confidence,
    ContractModel,
    Count,
    Milliseconds,
    NonEmptyStr,
    OneLine,
    PageNumber,
    UnitFloat,
    UtcDatetime,
)
from contracts.enums import (
    CaseStatus,
    ChunkSet,
    ClassifierContender,
    Decision,
    DemoRole,
    PageType,
    QueuedBy,
    RetrieverConfig,
)
from contracts.errors import ErrorCode
from contracts.ids import CaseId, DocumentId, EvalRunId, PageId
from contracts.rules import is_medical


class Health(ContractModel):
    """Response of every service's health and readiness routes."""

    status: Literal["ok"] = "ok"


class Me(ContractModel):
    """Response of `GET /api/me`: the demo role `web` read from the request."""

    role: DemoRole


class UploadedCase(ContractModel):
    """Response of `POST /api/cases`: the case `intake` created.

    It carries no status: the case is started by a call of its own
    (`POST /api/cases/{case_id}/start`), and `workflow` reports its status
    from then on (`GET /api/cases/{case_id}/progress`).
    """

    case_id: CaseId
    document_id: DocumentId


class PageDecisionRequest(ContractModel):
    """Request of `POST /api/cases/{case_id}/pages/{page_id}/decisions`.

    It names no actor: `web` passes the request's demo role on as the actor
    (AD-9), so a browser cannot say who decided.
    """

    decision: Decision


class TriagePage(ContractModel):
    """One page that waits for the underwriter, with what the classifier said of it.

    `web` composes it: the page from `workflow`'s queue, the reading from
    `classification`, for the classifier the case was started with. The four
    fields of the reading are null together when it could not be read; the
    page can be decided all the same.
    """

    case_id: CaseId
    page_id: PageId
    page_number: PageNumber
    # Where `web` serves the page's thumbnail (the redacted page, as PNG).
    thumbnail_path: NonEmptyStr
    page_type: PageType | None
    is_medical: bool | None
    confidence: Confidence | None
    reason: OneLine | None
    # How the page came to wait: the gate was unsure, or the customer kept it.
    queued_by: QueuedBy | None = None

    @model_validator(mode="after")
    def _reading_is_whole_or_absent(self) -> Self:
        reading = (self.page_type, self.is_medical, self.confidence, self.reason)
        if len({value is None for value in reading}) != 1:
            raise ValueError(
                "page_type, is_medical, confidence and reason are set together"
            )
        # AD-13: `is_medical` comes from the one mapping, here as everywhere.
        if self.page_type is not None and self.is_medical != is_medical(self.page_type):
            raise ValueError("is_medical must follow the page_type mapping")
        return self


class TriageQueue(ContractModel):
    """Response of `GET /api/triage`: the pages that wait for the underwriter, oldest first."""

    pages: list[TriagePage]
    # More pages wait than are listed.
    has_more: bool


class RetrieverPair(ContractModel):
    """Two ladder rows whose verdict runs on one case are shown side by side."""

    first: RetrieverConfig
    second: RetrieverConfig

    @model_validator(mode="after")
    def _two_rows(self) -> Self:
        if self.first is self.second:
            raise ValueError("a pair names two different rows")
        return self


class ComparePairs(ContractModel):
    """Response of `GET /api/compare-pairs`: the rows the Compare toggle shows (AD-11).

    A setting of `web`, answered as it is. `web` does not know which rows
    are built: the SPA asks for the default pair, and uses the fallback pair
    when `workflow` refuses a row of the default one as not available.
    """

    default_pair: RetrieverPair
    fallback_pair: RetrieverPair


# --- The scoreboard files (AD-17) ------------------------------------------------------
#
# The bake-off runner in `evals/` writes them and `web` serves them read-only;
# no service works a score out or stores one. Their shape is here so that the
# runner, `web` and the SPA hold one copy of it.

# A share of two counts, as the runner rounds it.
SCORE_DECIMALS = 4


def share(part: int, whole: int) -> float:
    """`part` of `whole` as a figure from 0 to 1, rounded as every scoreboard figure is."""
    return round(part / whole, SCORE_DECIMALS)


class ScoreboardRun(ContractModel):
    """When and where a bake-off run was made."""

    eval_run_id: EvalRunId
    started_at: UtcDatetime
    finished_at: UtcDatetime
    # The address of the `web` the runner drove.
    web_address: NonEmptyStr
    # True when local stand-ins stood where the AI services would be. The
    # figures of such a run prove the plumbing and are not results: the
    # stand-in's vectors count shared words and its agent is scripted.
    stand_ins: bool


class StatedFigure(ContractModel):
    """A figure the runner cannot measure: stated by hand, with where it comes from."""

    amount: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
    # What the amount counts, in words: a currency and what it buys, or a unit of work.
    unit: OneLine
    source: OneLine


class RetrievalRowScore(ContractModel):
    """One ladder row on the retrieval scoreboard.

    A row that answered "not available" is not measured: it is listed with
    what it is and carries no figure and no count. A measured row carries
    the counts behind each figure; a figure is null when its count is 0.
    """

    retriever_config: RetrieverConfig
    store: OneLine
    chunk_set: ChunkSet
    method: OneLine
    measured: bool
    # Searches whose answer held an expected rule, over the searches made:
    # one per expected fact that meets a rule. A failed search is a miss.
    rule_recall: UnitFloat | None
    recall_hits: Count | None
    recall_searches: Count | None
    # Runs with the expected verdict (and loading), over the cases run.
    verdict_accuracy: UnitFloat | None
    right_runs: Count | None
    cases: Count | None
    # Runs that failed, or that a case that was scored does not hold: a
    # failure of the system, counted apart so that it is not read as a wrong
    # verdict. Such a run is not right, and it is not among `right_runs`.
    failed_runs: Count | None
    # `retrieval`'s own time for a search, over the searches it answered.
    latency_ms_median: Milliseconds | None
    latency_ms_p95: Milliseconds | None
    latency_searches: Count | None
    # Stated, not measured; null when nobody stated it.
    cost: StatedFigure | None
    effort: StatedFigure | None

    @model_validator(mode="after")
    def _figures_follow_counts(self) -> Self:
        counts = (
            self.recall_hits,
            self.recall_searches,
            self.right_runs,
            self.cases,
            self.latency_searches,
            self.failed_runs,
        )
        figures = (
            self.rule_recall,
            self.verdict_accuracy,
            self.latency_ms_median,
            self.latency_ms_p95,
        )
        if not self.measured:
            if any(value is not None for value in (*counts, *figures)):
                raise ValueError("a row that is not measured carries no numbers")
            return self
        if any(value is None for value in counts):
            raise ValueError("a measured row carries the counts behind its figures")
        self._check_share("rule_recall", self.rule_recall, counts[0], counts[1])
        self._check_share(
            "verdict_accuracy", self.verdict_accuracy, counts[2], counts[3]
        )
        if (self.right_runs or 0) + (self.failed_runs or 0) > (self.cases or 0):
            raise ValueError("more runs are right or failed than cases were run")
        timed = bool(self.latency_searches)
        if (self.latency_ms_median is not None) != timed or (
            self.latency_ms_p95 is not None
        ) != timed:
            raise ValueError("latency is set when, and only when, a search was timed")
        if timed and (self.latency_ms_median or 0) > (self.latency_ms_p95 or 0):
            raise ValueError("the median latency must not exceed the 95th percentile")
        return self

    @staticmethod
    def _check_share(
        name: str, figure: float | None, part: int | None, whole: int | None
    ) -> None:
        if part is None or whole is None or part > whole:
            raise ValueError(f"the counts behind {name} do not hold together")
        expected = share(part, whole) if whole else None
        if figure != expected:
            raise ValueError(f"{name} must be its counts' share")


class FailedSearch(ContractModel):
    """An eval search that got no answer: counted as a miss for its row."""

    retriever_config: RetrieverConfig
    # The synthetic case, by the name its file has in the case set (`case-006`).
    case_key: OneLine
    # The position of the fact among the case's expected facts, from 1.
    fact_number: Annotated[int, Field(ge=1)]
    # Null when the answer was not in the error shape, or never came.
    error_code: ErrorCode | None


UnscoredReason = Literal[
    # The case ended as failed.
    "case_failed",
    # The case was not final within the runner's deadline.
    "not_final_in_time",
    # A page waited for a person and the runner holds no expected label for it.
    "wait_without_label",
    # A call to `web` got no usable answer.
    "request_failed",
]


class UnscoredCase(ContractModel):
    """A case that counts as wrong for every row, and why."""

    case_key: OneLine
    # Null when the upload itself failed.
    case_id: CaseId | None
    # The last status read; null when none was.
    case_status: CaseStatus | None
    reason: UnscoredReason
    error_code: ErrorCode | None


class RetrievalScoreboard(ContractModel):
    """The file `retrieval.json`: every ladder row, scored on the same cases."""

    run: ScoreboardRun
    # How many results a search was asked for; a hit is an expected rule among them.
    top_k: Annotated[int, Field(ge=1)]
    # Every ladder row once, in ladder order.
    rows: list[RetrievalRowScore]
    # Highest verdict accuracy, then rule recall, then lower latency, among
    # the measured rows. Null when no row was measured.
    winner: RetrieverConfig | None
    failed_searches: list[FailedSearch]
    unscored_cases: list[UnscoredCase]

    @model_validator(mode="after")
    def _rows_are_the_ladder_and_the_winner_was_measured(self) -> Self:
        if [row.retriever_config for row in self.rows] != list(RetrieverConfig):
            raise ValueError("rows must be every ladder row once, in ladder order")
        measured = {row.retriever_config for row in self.rows if row.measured}
        if (self.winner is None) != (not measured) or (
            self.winner is not None and self.winner not in measured
        ):
            raise ValueError("the winner is a measured row, and there is one if any is")
        return self


class RedactionLeak(ContractModel):
    """A planted identifier found in a stored page text. Never the value itself."""

    case_key: OneLine
    page_number: PageNumber
    # The category of the planted identifier (`person_name`, `address`, ...).
    category: OneLine


class RedactionScoreboard(ContractModel):
    """The file `redaction.json`: whether redaction left a planted identifier behind."""

    run: ScoreboardRun
    # No planted identifier, and no part of a planted name, in any page text,
    # and every case's pages were read: a case that was not checked is not clean.
    clean: bool
    cases_checked: Count
    pages_checked: Count
    # The planted identifiers looked for, each on every page of its case.
    identifiers_checked: Count
    leaks: list[RedactionLeak]
    # The strings the runner allows redaction to mask without being wrong,
    # and how many of them are in no page text of their case any more.
    may_also_be_redacted: Count
    may_also_be_redacted_masked: Count
    # Cases none of whose pages could be read: nothing of them was checked.
    cases_not_checked: list[OneLine]

    @model_validator(mode="after")
    def _clean_means_no_leak(self) -> Self:
        if self.clean != (not self.leaks and not self.cases_not_checked):
            raise ValueError(
                "clean is true when, and only when, no leak was found and every "
                "case was checked"
            )
        if self.may_also_be_redacted_masked > self.may_also_be_redacted:
            raise ValueError("more strings masked than were listed")
        return self


# --- The classifier scoreboard (story 4.3) --------------------------------------------

# A page counts for calibration when its stored confidence is at least this,
# and a contender can win only when its calibration is at least this too.
CALIBRATION_BAR = 0.9
# The same bar in whole numbers, so that it is held against counts and not
# against a rounded figure: at least 9 in 10.
_BAR_PART, _BAR_WHOLE = 9, 10


def meets_calibration_bar(confident_right_pages: int, confident_pages: int) -> bool:
    """Whether the pages scored 0.90 or more were labelled correctly at least 9 times in 10.

    False when there is no such page: a contender without a calibration
    cannot win.
    """
    return (
        confident_pages > 0
        and confident_right_pages * _BAR_WHOLE >= confident_pages * _BAR_PART
    )


class ClassifierScore(ContractModel):
    """One classifier contender on the classifier scoreboard.

    A contender that could not be run is not measured: it carries no figure
    and no count. A measured one carries the counts behind each figure, all
    over the same pages: every page of the scored set.
    """

    contender: ClassifierContender
    measured: bool
    # Every page of the scored set.
    pages: Count | None
    # Pages whose stored `is_medical` is the expected one, over all pages. A
    # page without a usable result is wrong.
    accuracy: UnitFloat | None
    right_pages: Count | None
    # Of the pages scored 0.90 or more, those labelled correctly. Null when
    # no page was scored that high.
    calibration: UnitFloat | None
    confident_pages: Count | None
    confident_right_pages: Count | None
    # Pages the gate routed to the underwriter's triage queue, over all pages.
    queue_rate: UnitFloat | None
    queued_pages: Count | None
    # Pages that count as wrong for want of a result: a failed result, none,
    # or a case that failed or did not finish.
    pages_not_classified: Count | None
    # Stated, not measured; null when nobody stated it.
    cost_per_page: StatedFigure | None

    @model_validator(mode="after")
    def _figures_follow_counts(self) -> Self:
        counts = (
            self.pages,
            self.right_pages,
            self.confident_pages,
            self.confident_right_pages,
            self.queued_pages,
            self.pages_not_classified,
        )
        figures = (self.accuracy, self.calibration, self.queue_rate)
        if not self.measured:
            if any(value is not None for value in (*counts, *figures)):
                raise ValueError("a contender that is not measured carries no numbers")
            return self
        if any(value is None for value in counts):
            raise ValueError(
                "a measured contender carries the counts behind its figures"
            )
        pages, right, confident, confident_right, queued, missing = (
            value or 0 for value in counts
        )
        if not pages:
            raise ValueError("a measured contender was scored on at least one page")
        if (
            right + missing > pages
            or confident + missing > pages
            or queued + missing > pages
            or confident_right > min(confident, right)
        ):
            raise ValueError("the counts of a contender do not hold together")
        expected = (
            share(right, pages),
            share(confident_right, confident) if confident else None,
            share(queued, pages),
        )
        if figures != expected:
            raise ValueError("each figure must be its counts' share")
        return self

    @property
    def can_win(self) -> bool:
        """Measured, and calibrated at least 9 times in 10 on the pages it was sure of."""
        return self.measured and meets_calibration_bar(
            self.confident_right_pages or 0, self.confident_pages or 0
        )


class ClassifierUnscoredCase(ContractModel):
    """A file of the page set whose pages all count as wrong for one contender, and why."""

    contender: ClassifierContender
    case_key: OneLine
    # Null when the upload itself failed.
    case_id: CaseId | None
    # The last status read; null when none was.
    case_status: CaseStatus | None
    reason: UnscoredReason
    error_code: ErrorCode | None


class UnclassifiedPage(ContractModel):
    """A page with a failed result, or none, for one contender: it counts as wrong."""

    contender: ClassifierContender
    case_key: OneLine
    page_number: PageNumber
    # The code of the page's failure; null when the page simply has no result.
    error_code: ErrorCode | None


class UncheckedReasons(ContractModel):
    """A file whose stored reasons could not be read for one contender: nothing of them was checked."""

    contender: ClassifierContender
    case_key: OneLine


class ReasonLeak(ContractModel):
    """A planted identifier found in a stored `reason`. Never the value itself."""

    contender: ClassifierContender
    case_key: OneLine
    page_number: PageNumber
    # The category of the planted identifier (`person_name`, `address`, ...).
    category: OneLine


class ClassificationScoreboard(ContractModel):
    """The file `classification.json`: both classifier contenders, scored on the same pages."""

    run: ScoreboardRun
    # Every contender once, in the order of the enum.
    contenders: list[ClassifierScore]
    # The more accurate contender among those whose calibration is at least
    # 0.90, then the one with the lower queue rate. Null when none qualifies.
    winner: ClassifierContender | None
    # For each contender that was tried and could not be run: the first file
    # of the set, the case it was uploaded as and what that case ended with,
    # which is all the run knows of why. No other file was uploaded for it.
    not_run: list[ClassifierUnscoredCase]
    unscored_cases: list[ClassifierUnscoredCase]
    # The pages with a failed result or none. The pages of an unscored case
    # are named by their case above, and only those that failed are here too.
    unclassified_pages: list[UnclassifiedPage]
    # The stored reasons read and looked through for planted identifiers,
    # and the ones that held one.
    reasons_checked: Count
    reason_leaks: list[ReasonLeak]
    # Files of a measured contender whose reasons were not read: the case
    # was not final, or its classifications could not be read. While there
    # is one, the reasons check is not clean, whatever `reason_leaks` holds.
    reasons_not_checked: list[UncheckedReasons]

    @model_validator(mode="after")
    def _contenders_are_all_and_the_winner_qualifies(self) -> Self:
        if [score.contender for score in self.contenders] != list(ClassifierContender):
            raise ValueError("contenders must be every contender once, in order")
        measured = {score.contender for score in self.contenders if score.measured}
        tried = [case.contender for case in self.not_run]
        if len(set(tried)) != len(tried) or measured & set(tried):
            raise ValueError(
                "not_run names a contender once, and none that was measured"
            )
        able = {score.contender for score in self.contenders if score.can_win}
        if (self.winner is None) != (not able) or (
            self.winner is not None and self.winner not in able
        ):
            raise ValueError(
                "the winner is a measured contender calibrated at 0.90 or more, "
                "and there is one if any qualifies"
            )
        return self
