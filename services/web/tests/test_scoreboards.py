"""Stories 3.5 and 4.3: `web` serves the scoreboard files the bake-off runner wrote, read-only (AD-17)."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from contracts.errors import ErrorBody
from contracts.models.web import (
    ClassificationScoreboard,
    RedactionScoreboard,
    RetrievalScoreboard,
)
from web.adapters.http.app import create_app
from web.adapters.http.scoreboards import MAX_FILE_BYTES
from web.settings import Settings

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}
RETRIEVAL = "/api/scoreboards/retrieval"
REDACTION = "/api/scoreboards/redaction"
CLASSIFICATION = "/api/scoreboards/classification"

RUN = {
    "eval_run_id": "0199b7a0-0000-7000-8000-000000000006",
    "started_at": "2026-10-08T09:00:00Z",
    "finished_at": "2026-10-08T09:10:00Z",
    "web_address": "http://localhost:8000",
    "stand_ins": True,
}
NUMBERS: dict[str, Any] = {
    "rule_recall": 0.9744,
    "recall_hits": 38,
    "recall_searches": 39,
    "verdict_accuracy": 0.6818,
    "right_runs": 15,
    "cases": 22,
    "failed_runs": 2,
    "latency_ms_median": 20,
    "latency_ms_p95": 35,
    "latency_searches": 39,
}


def row(config: str, measured: bool) -> dict[str, Any]:
    return {
        "retriever_config": config,
        "store": "pgvector",
        "chunk_set": "smart",
        "method": "Vector only",
        "measured": measured,
        **(NUMBERS if measured else dict.fromkeys(NUMBERS)),
        "cost": None,
        "effort": {"amount": "3.00", "unit": "stories built", "source": "epics.md"},
    }


def retrieval_board() -> dict[str, Any]:
    return {
        "run": RUN,
        "top_k": 5,
        "rows": [row(f"r{n}", measured=n in (1, 2, 3, 5)) for n in range(1, 7)],
        "winner": "r5",
        "failed_searches": [],
        "unscored_cases": [],
    }


def redaction_report() -> dict[str, Any]:
    return {
        "run": RUN,
        "clean": False,
        "cases_checked": 21,
        "pages_checked": 90,
        "identifiers_checked": 180,
        "leaks": [
            {"case_key": "case-002", "page_number": 3, "category": "person_name"}
        ],
        "may_also_be_redacted": 30,
        "may_also_be_redacted_masked": 4,
        "cases_not_checked": ["case-007"],
        "quotes_checked": 212,
        "quotes_not_found": 1,
        "quotes_not_found_at": [
            {"case_key": "case-002", "page_number": 3, "fact_number": 2}
        ],
    }


def classification_board() -> dict[str, Any]:
    """The classifier scoreboard: `llm` measured and the winner, the other not run."""
    measured = {
        "contender": "llm",
        "measured": True,
        "pages": 94,
        "accuracy": 0.9574,
        "right_pages": 90,
        "calibration": 0.975,
        "confident_pages": 80,
        "confident_right_pages": 78,
        "queue_rate": 0.1277,
        "queued_pages": 12,
        "pages_not_classified": 1,
        "cost_per_page": {
            "amount": "0.01",
            "unit": "USD per page",
            "source": "price list",
        },
    }
    return {
        "run": RUN,
        "contenders": [
            measured,
            {
                **dict.fromkeys(measured),
                "contender": "doc-intelligence",
                "measured": False,
            },
        ],
        "winner": "llm",
        "not_run": [
            {
                "contender": "doc-intelligence",
                "case_key": "case-001",
                "case_id": "0199b7a0-0000-7000-8000-000000000001",
                "case_status": "failed",
                "reason": "case_failed",
                "error_code": "stage_failed",
            }
        ],
        "unscored_cases": [],
        "unclassified_pages": [
            {
                "contender": "llm",
                "case_key": "case-006",
                "page_number": 4,
                "error_code": None,
            }
        ],
        "reasons_checked": 93,
        "reason_leaks": [],
        "reasons_not_checked": [{"contender": "llm", "case_key": "case-007"}],
    }


def error_code(response_json: object) -> str:
    return ErrorBody.model_validate(response_json).error.code.value


def test_story_3_5_the_underwriter_reads_each_scoreboard_file_as_it_is_and_nothing_else_of_the_folder(
    client: TestClient,
    scoreboards_dir: Path,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # In a checkout the folder is the repository's `data/scoreboards`. Where
    # `web` looks is logged at start, as a warning when the folder is not there.
    repository = Path(__file__).resolve().parents[3]
    assert Settings.model_fields["scoreboards_dir"].default == (
        repository / "data" / "scoreboards"
    )
    nowhere = scoreboards_dir / "nowhere"
    with caplog.at_level(logging.INFO, logger="web"):
        create_app(settings)
        create_app(settings.model_copy(update={"scoreboards_dir": nowhere}))
    said = [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if record.getMessage().startswith("scoreboards are read from")
    ]
    assert [level for level, _ in said] == [logging.INFO, logging.WARNING]
    assert str(scoreboards_dir.resolve()) in said[0][1]
    assert str(nowhere.resolve()) in said[1][1]

    # No file yet: the bake-off has not been run. The folder's own note is
    # no scoreboard.
    (scoreboards_dir / "README.md").write_text("# Scoreboards\n", encoding="utf-8")
    for path in (RETRIEVAL, REDACTION, CLASSIFICATION):
        not_run = client.get(path, headers=UNDERWRITER)
        assert (not_run.status_code, error_code(not_run.json())) == (404, "not_found")

    (scoreboards_dir / "retrieval.json").write_text(json.dumps(retrieval_board()))
    # The redaction file is not there: the retrieval file is answered all the same.
    answered = client.get(RETRIEVAL, headers=UNDERWRITER)
    assert answered.status_code == 200
    assert client.get(REDACTION, headers=UNDERWRITER).status_code == 404

    # Answered as the file holds it: the contract's shape, every figure,
    # count and stated figure unchanged, with the count of failed runs.
    board = RetrievalScoreboard.model_validate(answered.json())
    assert board == RetrievalScoreboard.model_validate(retrieval_board())
    assert answered.json() == json.loads(board.model_dump_json())
    assert answered.json()["rows"][4] == row("r5", measured=True)
    assert answered.json()["rows"][3]["rule_recall"] is None
    assert (board.winner, board.run.stand_ins) == ("r5", True)

    (scoreboards_dir / "redaction.json").write_text(json.dumps(redaction_report()))
    report = client.get(REDACTION, headers=UNDERWRITER)
    assert report.status_code == 200
    assert report.json() == redaction_report()
    assert RedactionScoreboard.model_validate(report.json()).clean is False

    # Story 4.3: the classifier bake-off's file is served as the other two:
    # 404 until it is written, then as it is, checked against its model.
    assert client.get(CLASSIFICATION, headers=UNDERWRITER).status_code == 404
    (scoreboards_dir / "classification.json").write_text(
        json.dumps(classification_board())
    )
    classifiers = client.get(CLASSIFICATION, headers=UNDERWRITER)
    assert classifiers.status_code == 200
    assert classifiers.json() == classification_board()
    assert ClassificationScoreboard.model_validate(classifiers.json()).winner == "llm"

    # AD-9: the scoreboard is the underwriter's; AD-17: nothing takes a score.
    for path in (RETRIEVAL, REDACTION, CLASSIFICATION):
        refused = client.get(path, headers=CUSTOMER)
        assert (refused.status_code, error_code(refused.json())) == (
            403,
            "role_not_allowed",
        )
        for method in ("POST", "PUT", "DELETE"):
            written = client.request(
                method, path, headers=UNDERWRITER, json=retrieval_board()
            )
            assert written.status_code == 405
    # Only the three files are served, by their route: no name reaches the folder.
    for path in (
        "/api/scoreboards/classification.json",
        "/api/scoreboards",
        "/api/scoreboards/README.md",
        "/api/scoreboards/retrieval.json",
        "/api/scoreboards/..%2fspa%2findex.html",
    ):
        assert client.get(path, headers=UNDERWRITER).status_code == 404
    assert json.loads((scoreboards_dir / "retrieval.json").read_text()) == (
        retrieval_board()
    )


def test_story_3_5_a_file_that_does_not_fit_its_model_is_a_plain_500_and_the_log_names_the_file_only(
    client: TestClient, scoreboards_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A row that is not measured and carries a number all the same; a winner
    # that was not measured; and a file that is no JSON at all.
    with_a_number = retrieval_board()
    with_a_number["rows"][3]["rule_recall"] = 0.5
    wrong_winner = {**retrieval_board(), "winner": "r4"}
    for content in (
        json.dumps(with_a_number),
        json.dumps(wrong_winner),
        '{"run": {"web_address": "http://secret-host.example',
        "",
        # A valid board in a file far larger than a scoreboard is.
        json.dumps(retrieval_board()) + " " * MAX_FILE_BYTES,
    ):
        (scoreboards_dir / "retrieval.json").write_text(content, encoding="utf-8")
        caplog.clear()

        with caplog.at_level(logging.ERROR):
            response = client.get(RETRIEVAL, headers=UNDERWRITER)

        # No half of the file is answered: the error shape, and nothing of it.
        assert response.status_code == 500
        assert error_code(response.json()) == "internal_error"
        assert "rows" not in response.text and "secret-host" not in response.text
        (record,) = [r for r in caplog.records if r.name.startswith("web.")]
        line = record.getMessage()
        assert "file=retrieval.json" in line
        assert "secret-host" not in line and "0.5" not in line
        assert str(scoreboards_dir) not in line

    # A leak that names the value it found breaks the redaction model, and
    # the value is neither answered nor logged.
    leaking = redaction_report()
    leaking["leaks"][0]["value"] = "Specimendale"
    (scoreboards_dir / "redaction.json").write_text(json.dumps(leaking))
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        response = client.get(REDACTION, headers=UNDERWRITER)
    assert response.status_code == 500
    assert "Specimendale" not in response.text + caplog.text
    assert "file=redaction.json" in caplog.text
    # A report written before the quote count was built does not fit either:
    # the bake-off has to be run again.
    earlier = {k: v for k, v in redaction_report().items() if "quotes" not in k}
    (scoreboards_dir / "redaction.json").write_text(json.dumps(earlier))
    assert client.get(REDACTION, headers=UNDERWRITER).status_code == 500

    # Story 4.3: a classifier scoreboard whose winner was not measured.
    unmeasured_winner = {**classification_board(), "winner": "doc-intelligence"}
    (scoreboards_dir / "classification.json").write_text(json.dumps(unmeasured_winner))
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        response = client.get(CLASSIFICATION, headers=UNDERWRITER)
    assert (response.status_code, "contenders" in response.text) == (500, False)
    assert "file=classification.json" in caplog.text
