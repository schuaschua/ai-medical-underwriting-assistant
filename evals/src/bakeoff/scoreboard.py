"""The two scoreboard files: the figures, the winner, and the writing (spine AD-17)."""

import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from bakeoff.answer_key import AnswerKeyEntry
from bakeoff.recall import RowRecall, percentile
from bakeoff.redaction import RedactionCheck
from bakeoff.settings import REDACTION_FILE, RETRIEVAL_FILE
from bakeoff.state import write_text_whole
from bakeoff.static_metrics import StaticMetrics
from bakeoff.verdicts import CaseOutcome
from contracts.base import ContractModel
from contracts.enums import RetrieverConfig
from contracts.models.web import (
    RedactionScoreboard,
    RetrievalRowScore,
    RetrievalScoreboard,
    ScoreboardRun,
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


def _as_file(board: ContractModel) -> str:
    return board.model_dump_json(indent=2) + "\n"
