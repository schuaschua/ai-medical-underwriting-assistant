"""The scoreboard files: the figures, the winners, and the writing (spine AD-17)."""

import math
from collections.abc import Iterable, Mapping, Sequence
from fractions import Fraction
from pathlib import Path

from bakeoff.answer_key import AnswerKeyEntry, PageSetDocument
from bakeoff.classifiers import DocumentOutcome, reason_leaks
from bakeoff.recall import RowRecall, percentile
from bakeoff.redaction import RedactionCheck
from bakeoff.settings import CLASSIFICATION_FILE, REDACTION_FILE, RETRIEVAL_FILE
from bakeoff.state import write_text_whole
from bakeoff.static_metrics import StaticMetrics
from bakeoff.verdicts import CaseOutcome
from contracts.base import ContractModel
from contracts.enums import ClassifierContender, PageStatus, RetrieverConfig
from contracts.models.web import (
    CALIBRATION_BAR,
    ClassificationScoreboard,
    ClassifierScore,
    ClassifierUnscoredCase,
    ReasonLeak,
    RedactionScoreboard,
    RetrievalRowScore,
    RetrievalScoreboard,
    ScoreboardRun,
    StatedFigure,
    UncheckedReasons,
    UnclassifiedPage,
    share,
)

MEDIAN = 0.5
NINETY_FIFTH = 0.95


def pick_winner(rows: Iterable[RetrievalRowScore]) -> RetrieverConfig | None:
    """The winning row: highest verdict accuracy, then rule recall, then lower latency.

    Among the measured rows only; None when no row was measured. Rows that
    tie on all three are told apart by their place on the ladder: the
    earlier, simpler row wins.
    """
    ladder = list(RetrieverConfig)
    measured = [row for row in rows if row.measured]
    if not measured:
        return None
    best = min(
        measured,
        key=lambda row: (
            -(row.verdict_accuracy or 0.0),
            -(row.rule_recall or 0.0),
            row.latency_ms_median if row.latency_ms_median is not None else math.inf,
            ladder.index(row.retriever_config),
        ),
    )
    return best.retriever_config


def right_runs(
    row: RetrieverConfig,
    entries: Sequence[AnswerKeyEntry],
    outcomes: Mapping[str, CaseOutcome],
) -> int:
    """The cases whose run with this row gave the answer key's expected verdict."""
    return sum(
        row in outcomes[entry.case_key].right_rows(entry.expected_verdict)
        for entry in entries
        if entry.case_key in outcomes
    )


def retrieval_scoreboard(
    run: ScoreboardRun,
    top_k: int,
    static: StaticMetrics,
    entries: Sequence[AnswerKeyEntry],
    recalls: Mapping[RetrieverConfig, RowRecall],
    outcomes: Mapping[str, CaseOutcome],
) -> RetrievalScoreboard:
    """Put the counts of a run together: one line per ladder row, and the winner."""
    rows: list[RetrievalRowScore] = []
    for config in RetrieverConfig:
        facts = static.rows[config]
        recall = recalls.get(config)
        numbers: dict[str, object] = dict.fromkeys(_NUMBERS)
        measured = recall is not None and recall.available
        if recall is not None and measured:
            right = right_runs(config, entries, outcomes)
            failed = sum(
                outcomes[entry.case_key].run_failed(config)
                for entry in entries
                if entry.case_key in outcomes
            )
            numbers = {
                "rule_recall": share(recall.hits, recall.searches)
                if recall.searches
                else None,
                "recall_hits": recall.hits,
                "recall_searches": recall.searches,
                # A case that failed or did not finish is wrong for every row.
                "verdict_accuracy": share(right, len(entries)) if entries else None,
                "right_runs": right,
                "cases": len(entries),
                "failed_runs": failed,
                "latency_ms_median": percentile(recall.latencies, MEDIAN),
                "latency_ms_p95": percentile(recall.latencies, NINETY_FIFTH),
                "latency_searches": len(recall.latencies),
            }
        rows.append(
            RetrievalRowScore.model_validate(
                {
                    "retriever_config": config,
                    "store": facts.store,
                    "chunk_set": facts.chunk_set,
                    "method": facts.method,
                    "measured": measured,
                    **numbers,
                    "cost": facts.cost,
                    "effort": facts.effort,
                }
            )
        )
    ran = [outcomes[entry.case_key] for entry in entries if entry.case_key in outcomes]
    return RetrievalScoreboard(
        run=run,
        top_k=top_k,
        rows=rows,
        winner=pick_winner(rows),
        failed_searches=[
            failed
            for config in RetrieverConfig
            if config in recalls
            for failed in recalls[config].failed
        ],
        unscored_cases=[
            outcome.unscored for outcome in ran if outcome.unscored is not None
        ],
    )


# The fields of a row that only a measured row has.
_NUMBERS = (
    "rule_recall",
    "recall_hits",
    "recall_searches",
    "verdict_accuracy",
    "right_runs",
    "cases",
    "failed_runs",
    "latency_ms_median",
    "latency_ms_p95",
    "latency_searches",
)


def redaction_scoreboard(
    run: ScoreboardRun, check: RedactionCheck
) -> RedactionScoreboard:
    return RedactionScoreboard(
        run=run,
        # A case nobody could check is not a clean case.
        clean=not check.leaks and not check.cases_not_checked,
        cases_checked=check.cases_checked,
        pages_checked=check.pages_checked,
        identifiers_checked=check.identifiers_checked,
        leaks=check.leaks,
        may_also_be_redacted=check.may_also_be_redacted,
        may_also_be_redacted_masked=check.may_also_be_redacted_masked,
        cases_not_checked=sorted(check.cases_not_checked),
        quotes_checked=check.quotes_checked,
        quotes_not_found=len(check.quotes_not_found),
        # The cases run side by side, so they are put in order here.
        quotes_not_found_at=sorted(
            check.quotes_not_found,
            key=lambda quote: (quote.case_key, quote.page_number, quote.fact_number),
        ),
    )


def write_scoreboards(
    folder: Path, retrieval: RetrievalScoreboard, redaction: RedactionScoreboard
) -> tuple[Path, Path]:
    """Write the two files; where they are."""
    written = []
    for name, board in ((RETRIEVAL_FILE, retrieval), (REDACTION_FILE, redaction)):
        written.append(folder / name)
        write_text_whole(written[-1], _as_file(board))
    return written[0], written[1]


# --- The classifier scoreboard (story 4.3) --------------------------------------------


def pick_classifier_winner(
    scores: Iterable[ClassifierScore],
) -> ClassifierContender | None:
    """The winning contender: the more accurate, then the one with the lower queue rate.

    Among the contenders whose calibration is at least 0.90 over at least
    10 pages scored 0.90 or more only: one with a lower calibration, or with
    fewer such pages, cannot win however accurate it is. None when no contender qualifies. Contenders
    that tie on both figures are told apart by their order in the contracts.
    """
    order = list(ClassifierContender)
    able = [score for score in scores if score.can_win]
    if not able:
        return None
    # On the counts, as the calibration bar is: a rounded share could tie
    # two contenders that differ, or part two that do not.
    best = min(
        able,
        key=lambda score: (
            -Fraction(score.right_pages or 0, score.pages or 1),
            Fraction(score.queued_pages or 0, score.pages or 1),
            order.index(score.contender),
        ),
    )
    return best.contender


def score_contender(
    contender: ClassifierContender,
    documents: Sequence[PageSetDocument],
    outcomes: Mapping[str, DocumentOutcome],
    cost_per_page: StatedFigure | None,
) -> tuple[ClassifierScore, list[UnclassifiedPage]]:
    """One contender's line, over every page of the set, and the pages it has no result for.

    Scored on the stored result and the expected label, and on nothing
    else. A page with a failed result or none is wrong and in no
    calibration count; every page of a file that failed or did not finish
    is wrong too, whatever was stored for it.
    """
    pages = right = confident = confident_right = queued = missing = 0
    unclassified: list[UnclassifiedPage] = []
    for document in documents:
        outcome = outcomes.get(document.case_key)
        stored = outcome.pages if outcome is not None else {}
        whole = outcome is not None and outcome.unscored is None
        for expected in document.pages:
            pages += 1
            page = stored.get(expected.page_number)
            failed = page is not None and page.page_status is PageStatus.FAILED
            if not whole or page is None or page.result is None:
                missing += 1
                if whole or failed:
                    # Listed by page. The other pages of a file that is not
                    # scored are named by their file, once.
                    unclassified.append(
                        UnclassifiedPage(
                            contender=contender,
                            case_key=document.case_key,
                            page_number=expected.page_number,
                            error_code=page.error_code if page is not None else None,
                        )
                    )
                continue
            is_right = page.result.is_medical == expected.is_medical
            right += is_right
            if page.result.confidence >= CALIBRATION_BAR:
                confident += 1
                confident_right += is_right
            # Where the gate put the page; the runner routes nothing.
            queued += page.page_status is PageStatus.AWAITING_TRIAGE
    score = ClassifierScore(
        contender=contender,
        measured=True,
        pages=pages,
        accuracy=share(right, pages),
        right_pages=right,
        calibration=share(confident_right, confident) if confident else None,
        confident_pages=confident,
        confident_right_pages=confident_right,
        queue_rate=share(queued, pages),
        queued_pages=queued,
        pages_not_classified=missing,
        cost_per_page=cost_per_page,
    )
    return score, unclassified


def not_measured(
    contender: ClassifierContender, cost_per_page: StatedFigure | None
) -> ClassifierScore:
    """The line of a contender that could not be run: what it is, and no number."""
    return ClassifierScore(
        contender=contender,
        measured=False,
        pages=None,
        accuracy=None,
        right_pages=None,
        calibration=None,
        confident_pages=None,
        confident_right_pages=None,
        queue_rate=None,
        queued_pages=None,
        pages_not_classified=None,
        cost_per_page=cost_per_page,
    )


def classification_scoreboard(
    run: ScoreboardRun,
    static: StaticMetrics,
    documents: Sequence[PageSetDocument],
    entries: Mapping[str, AnswerKeyEntry],
    outcomes: Mapping[ClassifierContender, Mapping[str, DocumentOutcome]],
    not_run: Sequence[ClassifierUnscoredCase] = (),
) -> ClassificationScoreboard:
    """Put a run together: one line per contender, the winner, and the reasons check.

    `outcomes` holds the contenders that were run; any other contender is
    listed as not measured. `not_run` says, for each contender that was
    tried and refused, which case showed it.
    """
    scores: list[ClassifierScore] = []
    unclassified: list[UnclassifiedPage] = []
    leaks: list[ReasonLeak] = []
    reasons = 0
    for contender in ClassifierContender:
        cost = static.classifiers[contender].cost_per_page
        if contender not in outcomes:
            scores.append(not_measured(contender, cost))
            continue
        score, without_result = score_contender(
            contender, documents, outcomes[contender], cost
        )
        scores.append(score)
        unclassified.extend(without_result)
    # Every stored reason that was read is looked through, also those of a
    # file that is not scored.
    ran = [
        outcome
        for contender in ClassifierContender
        for outcome in outcomes.get(contender, {}).values()
    ]
    for outcome in ran:
        reasons += sum(page.result is not None for page in outcome.pages.values())
        leaks.extend(reason_leaks(entries[outcome.case_key], outcome))
    return ClassificationScoreboard(
        run=run,
        contenders=scores,
        winner=pick_classifier_winner(scores),
        not_run=list(not_run),
        unscored_cases=[
            outcome.unscored for outcome in ran if outcome.unscored is not None
        ],
        unclassified_pages=unclassified,
        reasons_checked=reasons,
        reason_leaks=leaks,
        # Reasons nobody read are not reasons that hold nothing.
        reasons_not_checked=[
            UncheckedReasons(contender=outcome.contender, case_key=outcome.case_key)
            for outcome in ran
            if not outcome.reasons_read
        ],
    )


def write_classification_scoreboard(
    folder: Path, board: ClassificationScoreboard
) -> Path:
    """Write `classification.json`; where it is."""
    written = folder / CLASSIFICATION_FILE
    write_text_whole(written, _as_file(board))
    return written


def _as_file(board: ContractModel) -> str:
    return board.model_dump_json(indent=2) + "\n"
