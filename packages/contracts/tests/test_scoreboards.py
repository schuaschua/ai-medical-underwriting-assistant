"""Story 3.4: the shape of the scoreboard files the bake-off runner writes (AD-17)."""

from typing import Any

import pytest
from pydantic import ValidationError

from contracts.models.web import (
    RedactionScoreboard,
    RetrievalRowScore,
    RetrievalScoreboard,
    share,
)

RUN = {
    "eval_run_id": "0199b7a0-0000-7000-8000-000000000006",
    "started_at": "2026-10-08T09:00:00Z",
    "finished_at": "2026-10-08T09:10:00Z",
    "web_address": "http://localhost:8000",
    "stand_ins": True,
}
LADDER = ("r1", "r2", "r3", "r4", "r5", "r6")


def row(config: str, measured: bool = True, **changes: Any) -> dict[str, Any]:
    numbers: dict[str, Any] = {
        "rule_recall": 0.6667,
        "recall_hits": 2,
        "recall_searches": 3,
        "verdict_accuracy": 0.5,
        "right_runs": 1,
        "cases": 2,
        "failed_runs": 0,
        "latency_ms_median": 20,
        "latency_ms_p95": 35,
        "latency_searches": 3,
    }
    return {
        "retriever_config": config,
        "store": "pgvector",
        "chunk_set": "smart",
        "method": "Vector only",
        "measured": measured,
        **(numbers if measured else dict.fromkeys(numbers)),
        "cost": None,
        "effort": None,
        **changes,
    }


def board(**changes: Any) -> dict[str, Any]:
    return {
        "run": RUN,
        "top_k": 5,
        "rows": [row(config, measured=config == "r3") for config in LADDER],
        "winner": "r3",
        "failed_searches": [],
        "unscored_cases": [],
        **changes,
    }


def test_story_3_4_a_scoreboard_row_has_numbers_only_when_measured_and_the_winner_is_a_measured_row() -> (
    None
):
    scoreboard = RetrievalScoreboard.model_validate(board())

    assert scoreboard.winner == "r3" and scoreboard.run.stand_ins is True
    assert [entry.measured for entry in scoreboard.rows] == [
        False,
        False,
        True,
        False,
        False,
        False,
    ]
    assert share(2, 3) == scoreboard.rows[2].rule_recall
    assert RetrievalRowScore.model_validate(row("r1", failed_runs=1)).failed_runs == 1
    # A row that is not measured shows no number at all; a measured one the
    # counts behind each figure, and figures that are those counts' shares.
    # A search that never answered leaves the row without a latency.
    assert RetrievalRowScore.model_validate(
        row("r1", latency_searches=0, latency_ms_median=None, latency_ms_p95=None)
    )
    assert RetrievalRowScore.model_validate(
        row("r1", recall_hits=0, recall_searches=0, rule_recall=None)
    )
    for wrong in (
        row("r4", measured=False, rule_recall=0.5),
        row("r4", measured=False, cases=2),
        row("r1", recall_searches=None),
        row("r1", rule_recall=0.9),
        row("r1", verdict_accuracy=None),
        row("r1", recall_hits=4),
        # Story 3.5: a run that failed is counted apart, and is not a right run.
        row("r1", failed_runs=None),
        row("r1", failed_runs=2),
        row("r1", latency_ms_median=None),
        row("r1", latency_ms_median=50),
        row("r1", cost={"amount": "-1", "unit": "USD", "source": "price list"}),
        row("r1", effort={"amount": "3", "unit": "stories", "source": " "}),
    ):
        with pytest.raises(ValidationError):
            RetrievalRowScore.model_validate(wrong)
    nothing_measured = [row(config, measured=False) for config in LADDER]
    assert RetrievalScoreboard.model_validate(board(rows=nothing_measured, winner=None))
    for changes in (
        # The winner must be a measured row, and there is one whenever a row was measured.
        {"winner": "r1"},
        {"winner": None},
        {"rows": nothing_measured},
        # Every ladder row once, in order.
        {"rows": board()["rows"][:5]},
        {"rows": list(reversed(board()["rows"]))},
    ):
        with pytest.raises(ValidationError):
            RetrievalScoreboard.model_validate(board(**changes))

    # The redaction report names where a leak is and what kind, never the value.
    report = {
        "run": RUN,
        "clean": True,
        "cases_checked": 2,
        "pages_checked": 9,
        "identifiers_checked": 18,
        "leaks": [],
        "may_also_be_redacted": 30,
        "may_also_be_redacted_masked": 0,
        "cases_not_checked": [],
    }
    leak = {"case_key": "case-002", "page_number": 3, "category": "person_name"}
    assert RedactionScoreboard.model_validate(report).clean
    assert not RedactionScoreboard.model_validate(
        {**report, "clean": False, "leaks": [leak]}
    ).clean
    for wrong in (
        {**report, "leaks": [leak]},
        {**report, "clean": False},
        # A case that was not checked is not clean.
        {**report, "cases_not_checked": ["case-003"]},
        {**report, "leaks": [{**leak, "value": "Kendall"}], "clean": False},
        {**report, "may_also_be_redacted_masked": 31},
    ):
        with pytest.raises(ValidationError):
            RedactionScoreboard.model_validate(wrong)
