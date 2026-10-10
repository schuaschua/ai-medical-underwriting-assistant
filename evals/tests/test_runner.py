"""Stories 3.4 and 4.3: a bake-off run against a stand-in for `web`: what is sent, what is counted, what is written.

The stand-in (`support/bakeoff_fakes.py`) answers as `web` does; the case set
is made up here. The whole path against the real services is in
`test_whole_path.py`.
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from bakeoff_fakes import Clock, FakeWeb, key_entry, settings_for, write_case_set

from bakeoff.__main__ import (
    EXIT_INCOMPLETE,
    EXIT_LEAK,
    EXIT_OK,
    EXIT_REFUSED,
    main,
)
from bakeoff.runner import RunResult, run, run_classification
from bakeoff.settings import PUBLISHED_SCOREBOARDS, Settings
from bakeoff.state import RunRefused
from contracts.models.web import (
    ClassificationScoreboard,
    RedactionScoreboard,
    RetrievalScoreboard,
)

HBA1C = "Type 2 diabetes mellitus: HbA1c 7.4 %"
SMOKER = "smoking status: current smoker"
LDL = "LDL cholesterol 4.2 mmol/L"
QUERIES = {HBA1C, SMOKER, LDL}
OTHER_RULES = ["UW-HT-001", "UW-HT-002", "UW-HT-003", "UW-HT-004", "UW-HT-005"]
NAME = "Avery Specimendale"


def case_set(tmp_path: Path) -> None:
    first = key_entry(
        "case-901",
        verdict="loaded",
        loading_pct=75,
        # A medical page, a blank page, a medical page the gate was unsure of.
        medical=(True, False, True),
        facts=(
            (HBA1C, ("UW-DM-002",)),
            (SMOKER, ("UW-TOB-001",)),
            # Meets no rule: never searched for.
            ("body mass index 23.5 kg/m2", ()),
        ),
        identifiers=(
            ("person_name", NAME),
            ("policy_number", "POL-SYN-0009011"),
        ),
        may_also_be_redacted=("Avery", "Samplestead"),
    )
    # The first fact's own words on two pages. Page 1 still holds them; page
    # 3 has no stored text here, as if redaction had masked the line.
    first["expected_facts"][0]["places"] = [
        {"page_number": 1, "quote": "Town: Samplestead"},
        {"page_number": 3, "quote": "HbA1c 8.2 % at the Samplestead clinic"},
    ]
    write_case_set(
        tmp_path / "data",
        [
            first,
            key_entry(
                "case-902",
                medical=(True, True, False),
                facts=((LDL, ("UW-LDL-001", "UW-LDL-002")),),
                identifiers=(("phone_number", "(303) 555-0190"),),
            ),
        ],
    )


def system() -> FakeWeb:
    """The system as it would answer the made-up case set, with rows `r1` to `r3` built."""
    web = FakeWeb()
    # `r1` finds the first fact's rule sixth, `r2` finds one rule, `r3` all three.
    web.found = {
        ("r1", HBA1C): [*OTHER_RULES, "UW-DM-002"],
        ("r1", SMOKER): ["UW-TOB-001"],
        ("r1", LDL): ["UW-HT-001", "UW-LDL-002"],
        ("r2", HBA1C): ["UW-DM-002"],
        ("r3", HBA1C): ["UW-DM-002"],
        ("r3", SMOKER): ["UW-HT-001", "UW-TOB-001"],
        ("r3", LDL): ["UW-LDL-001"],
    }
    web.latency_ms = {"r1": 30, "r2": 20, "r3": 40}
    web.pages = {
        "case-901": ["extracted", "awaiting_customer", "awaiting_triage"],
        "case-902": ["awaiting_customer", "extracted", "awaiting_triage"],
    }
    web.texts = {"case-901": {1: "Applicant: [Person]\nTown: Samplestead"}}
    web.verdicts = {
        "case-901": {"r1": ("loaded", 50), "r2": ("loaded", 75), "r3": ("loaded", 75)},
        "case-902": {
            "r1": ("standard", None),
            "r2": ("standard", None),
            "r3": ("standard", None),
        },
    }
    return web


def make_run(settings: Settings, web: FakeWeb) -> RunResult:
    clock = Clock()
    return asyncio.run(run(settings, web.transport, clock.sleep, clock))


def test_story_3_4_a_run_scores_every_available_row_on_the_same_queries_and_cases_and_writes_both_files(
    tmp_path: Path,
) -> None:
    case_set(tmp_path)
    web = system()
    # One of `r2`'s searches gets no answer, however often it is sent.
    web.broken_searches = {("r2", SMOKER)}
    # One of `r1`'s is answered 503 once, and answered the next time.
    web.flaky_searches = {("r1", SMOKER)}

    result = make_run(settings_for(tmp_path), web)

    # The search that failed once was sent again, is a hit, and is not listed.
    assert web.flaky_searches == set()
    assert [
        (search["retriever_config"], search["query"]) for search in web.searches
    ].count(("r1", SMOKER)) == 2

    # Recall: the same three built queries to every row that answers, top 5.
    for row in ("r1", "r2", "r3"):
        sent = [search for search in web.searches if search["retriever_config"] == row]
        assert {search["query"] for search in sent} == QUERIES
        assert {search["top_k"] for search in sent} == {5}
    # A row that is not available is asked once, recorded as not measured,
    # and does not stop the run.
    assert sorted(web.refused_rows) == ["r4", "r5", "r6"]
    board = result.retrieval
    rows = {row.retriever_config.value: row for row in board.rows}
    assert [row.measured for row in board.rows] == [
        True,
        True,
        True,
        False,
        False,
        False,
    ]
    assert rows["r5"].rule_recall is None and rows["r5"].cases is None
    assert (rows["r5"].store, rows["r5"].chunk_set) == ("Azure AI Search", "smart")
    # A rule in sixth place is a miss; a search that fails is a miss and is listed.
    assert [(rows[row].recall_hits, rows[row].recall_searches) for row in rows][:3] == [
        (2, 3),
        (1, 3),
        (3, 3),
    ]
    assert (rows["r1"].rule_recall, rows["r3"].rule_recall) == (0.6667, 1.0)
    assert [failed.model_dump(mode="json") for failed in board.failed_searches] == [
        {
            "retriever_config": "r2",
            "case_key": "case-901",
            "fact_number": 2,
            "error_code": "upstream_unavailable",
        }
    ]
    # Latency is `retrieval`'s own figure, over the searches that answered.
    assert (rows["r2"].latency_ms_median, rows["r2"].latency_searches) == (20, 2)
    assert (rows["r3"].latency_ms_median, rows["r3"].latency_ms_p95) == (40, 40)

    # Verdict accuracy: each case uploaded once, started once with every
    # available row and the run's one `eval_run_id`.
    assert sorted(web.uploads) == ["case-901", "case-902"]
    started = [web.case(key).started_with for key in ("case-901", "case-902")]
    assert all(
        options
        == {
            "retriever_configs": ["r1", "r2", "r3"],
            "eval_run_id": board.run.eval_run_id,
        }
        for options in started
    )
    # The human waits were answered from the page labels, by the role that
    # owns each decision: the blank page discarded, the unsure medical page
    # accepted, a medical page the customer is asked about kept and then
    # accepted, a page in triage that is not medical denied.
    assert sorted(web.decisions) == [
        ("case-901", 2, "discard", "customer"),
        ("case-901", 3, "accept", "underwriter"),
        ("case-902", 1, "accept", "underwriter"),
        ("case-902", 1, "keep", "customer"),
        ("case-902", 3, "deny", "underwriter"),
    ]
    # `r1` loaded the case +50 where +75 was expected: wrong on the loading.
    assert [(rows[row].right_runs, rows[row].cases) for row in rows][:3] == [
        (1, 2),
        (2, 2),
        (2, 2),
    ]
    # Story 3.5: a wrong verdict is no failed run, and a row that was not
    # measured has no such count.
    assert [rows[row].failed_runs for row in rows] == [0, 0, 0, None, None, None]
    assert (rows["r1"].verdict_accuracy, rows["r2"].verdict_accuracy) == (0.5, 1.0)
    assert board.unscored_cases == []
    # `r2` and `r3` tie on accuracy: the higher recall wins.
    assert board.winner == "r3"
    # Cost and effort are stated in the static metrics, each with its source.
    assert rows["r3"].effort is not None and rows["r3"].effort.source

    # Redaction, in the same run: every page read, and nothing planted found.
    redaction = result.redaction
    assert redaction.clean and redaction.leaks == []
    assert (redaction.cases_checked, redaction.pages_checked) == (2, 6)
    assert redaction.identifiers_checked == 3
    assert (redaction.may_also_be_redacted, redaction.may_also_be_redacted_masked) == (
        2,
        1,
    )
    # Over-redaction is a figure of a clean report: one of the two quotes of
    # the expected facts is no longer in its page's text, and is named by
    # its place.
    assert (redaction.quotes_checked, redaction.quotes_not_found) == (2, 1)
    assert [quote.model_dump() for quote in redaction.quotes_not_found_at] == [
        {"case_key": "case-901", "page_number": 3, "fact_number": 1}
    ]

    # Both files are the contract's shape, say when and where the run was
    # made, and say that these are stand-in figures.
    assert result.retrieval_file == tmp_path / "out" / "retrieval.json"
    written = RetrievalScoreboard.model_validate_json(result.retrieval_file.read_text())
    report = RedactionScoreboard.model_validate_json(result.redaction_file.read_text())
    assert (written, report) == (board, redaction)
    assert written.run == report.run
    assert (written.run.stand_ins, written.run.web_address) == (
        True,
        "http://localhost:8000",
    )
    assert written.run.started_at <= written.run.finished_at
    # AD-9: every call carried a demo role, and only the upload and the
    # customer's own answers were made as the customer.
    assert {
        (method, path) for role, method, path in web.roles if role == "customer"
    } == {
        ("POST", "cases"),
        ("POST", "cases/pages/decisions"),
    }


def test_story_3_4_a_case_that_fails_or_hangs_is_wrong_for_every_row_and_a_resumed_run_uploads_nothing_again(
    tmp_path: Path,
) -> None:
    case_set(tmp_path)
    write_case_set(
        tmp_path / "more",
        [
            key_entry("case-903", facts=((LDL, ("UW-LDL-001",)),), medical=(True,)),
            key_entry("case-904", facts=((LDL, ("UW-LDL-001",)),), medical=(True,)),
        ],
    )
    for folder in ("cases", "answer-key/cases"):
        for file in (tmp_path / "more" / folder).iterdir():
            file.rename(tmp_path / "data" / folder / file.name)
    web = system()
    web.pages["case-903"] = ["extracted", "awaiting_triage"]
    web.verdicts["case-903"] = dict.fromkeys(("r1", "r2", "r3"), ("standard", None))
    web.failing = {"case-901"}
    web.hanging = {"case-902"}
    # `web` gives no usable answer to the start of one case, however often it is sent.
    web.refused_starts = {"case-904"}
    settings = settings_for(tmp_path, case_deadline_seconds=30.0, rows=["r1", "r3"])

    first = make_run(settings, web)

    board = first.retrieval
    # The run goes on: both cases are listed with their status, and count
    # as wrong for every row beside the case that is not in the key at all.
    assert [
        (case.case_key, case.case_status, case.reason, case.error_code)
        for case in board.unscored_cases
    ] == [
        ("case-901", "failed", "case_failed", "stage_failed"),
        ("case-902", "running", "not_final_in_time", None),
        # Page 2 waits in triage and the answer key has one page: the runner
        # decides nothing it has no label for.
        ("case-903", "awaiting_human", "wait_without_label", None),
        # A call to `web` failed: the case is lost, the others go on.
        ("case-904", None, "request_failed", "upstream_unavailable"),
    ]
    assert first.retrieval_file.is_file() and first.redaction_file.is_file()
    rows = {row.retriever_config.value: row for row in board.rows}
    assert [(rows[row].right_runs, rows[row].cases) for row in ("r1", "r3")] == [
        (0, 4),
        (0, 4),
    ]
    # Story 3.5: a case that was not scored is listed once, as such: its
    # runs are not counted again as runs that failed.
    assert (rows["r1"].failed_runs, rows["r3"].failed_runs) == (0, 0)
    # A row left out of the run is not measured and no case was started with it.
    assert not rows["r2"].measured
    assert web.case("case-901").started_with == {
        "retriever_configs": ["r1", "r3"],
        "eval_run_id": board.run.eval_run_id,
    }
    assert ("case-903", 2, "accept", "underwriter") not in web.decisions
    # The pages of a case that failed are checked all the same.
    assert first.redaction.cases_checked == 4 and first.redaction.clean

    # The run is started again with its id: the hanging case has finished
    # meanwhile, no case is uploaded or started a second time, and the
    # finished case is read and scored. The case whose start got no answer
    # is started now, for the first time. The case that failed stays failed.
    web.hanging = set()
    web.failing = set()
    web.refused_starts = set()
    web.verdicts["case-904"] = dict.fromkeys(("r1", "r3"), ("standard", None))
    # Story 3.5: one finished case holds no run of `r1` at all, and `r3`'s
    # run of another ended as failed.
    web.missing_runs = {("case-904", "r1")}
    # And `r1` now gets the hanging case wrong, where `case-901`, which
    # stays failed and so unscored, showed a wrong verdict before.
    web.verdicts["case-902"] = {**web.verdicts["case-902"], "r1": ("decline", None)}
    web.failed_runs = {("case-902", "r3")}
    # One page text of a case cannot be read: half a case is not checked as
    # if it were whole, so the case is left unchecked and the run not clean.
    web.broken_texts = {("case-903", 2)}
    uploads, starts = list(web.uploads), list(web.starts)
    again = make_run(
        settings.model_copy(update={"eval_run_id": board.run.eval_run_id}), web
    )

    assert web.uploads == uploads and len(uploads) == 4
    assert web.starts == [*starts, "case-904"] and len(starts) == 3
    starts = list(web.starts)
    assert again.retrieval.run.eval_run_id == board.run.eval_run_id
    assert again.redaction.cases_not_checked == ["case-903"]
    assert (again.redaction.cases_checked, again.redaction.clean) == (3, False)
    assert [case.case_key for case in again.retrieval.unscored_cases] == [
        "case-901",
        "case-903",
    ]
    resumed = {row.retriever_config.value: row for row in again.retrieval.rows}
    # Beside the two cases that were not scored, `r1` has a wrong verdict
    # (`case-902`) and a run that is missing (`case-904`), counted apart
    # from the wrong one. `r3` is right on `case-904`, and its run of
    # `case-902` failed.
    assert [
        (resumed[row].right_runs, resumed[row].failed_runs, resumed[row].cases)
        for row in ("r1", "r3")
    ] == [(0, 1, 4), (1, 1, 4)]
    assert again.retrieval.winner == "r3"

    # A case is started once: the run cannot be resumed with other rows, or
    # against another address, and no case is uploaded or started when it is tried.
    for changes in (
        {"rows": ["r1", "r2", "r3"]},
        {"web_address": "http://127.0.0.1:8000"},
    ):
        with pytest.raises(RunRefused):
            make_run(
                settings.model_copy(
                    update={"eval_run_id": board.run.eval_run_id, **changes}
                ),
                web,
            )
    assert web.uploads == uploads and web.starts == starts
    # Story 4.3: nor can it be resumed as a classification bake-off.
    clock = Clock()
    with pytest.raises(RunRefused):
        asyncio.run(
            run_classification(
                settings.model_copy(
                    update={
                        "eval_run_id": board.run.eval_run_id,
                        "bake_off": "classification",
                        "rows": None,
                    }
                ),
                web.transport,
                clock.sleep,
                clock,
            )
        )
    assert web.uploads == uploads and web.starts == starts


def _nothing_answers() -> httpx.MockTransport:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(refuse)


def test_story_3_4_a_planted_name_left_in_a_page_text_fails_the_command_and_is_reported_without_its_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    case_set(tmp_path)
    # A case whose only fact meets no rule: no search scores a row on it.
    entry = key_entry("case-905", facts=(("body mass index 23.5 kg/m2", ()),))
    (tmp_path / "data" / "cases" / "case-905.pdf").write_bytes(b"%PDF-1.7 case-905")
    (tmp_path / "data" / "answer-key" / "cases" / "case-905.json").write_text(
        json.dumps(entry)
    )
    web = system()
    web.available = set()
    for name, value in (
        ("EVALS_DATA_DIR", tmp_path / "data"),
        ("EVALS_STATE_DIR", tmp_path / "state"),
        ("EVALS_RETRY_SECONDS", 0),
        ("EVALS_POLL_SECONDS", 0.001),
    ):
        monkeypatch.setenv(name, str(value))
    out = ["--output-dir", str(tmp_path / "out")]

    # No row is available: nothing is uploaded, no row is measured, nobody
    # wins. Nothing was checked, which is no leak and is not clean either:
    # the command says the run is incomplete, with a status of its own.
    assert main(out, web.transport) == EXIT_INCOMPLETE
    nothing = RetrievalScoreboard.model_validate_json(
        (tmp_path / "out" / "retrieval.json").read_text()
    )
    unchecked = RedactionScoreboard.model_validate_json(
        (tmp_path / "out" / "redaction.json").read_text()
    )
    assert nothing.winner is None and not any(row.measured for row in nothing.rows)
    assert web.uploads == []
    assert not unchecked.clean and unchecked.leaks == []
    assert unchecked.cases_not_checked == ["case-901", "case-902", "case-905"]
    assert (
        "incomplete: 0 cases not scored, 3 cases not checked" in capsys.readouterr().out
    )

    # A case set with no fact that meets a rule: a row is asked once whether
    # it answers, is measured without a recall, and still judges the case.
    web.available = {"r1", "r3"}
    assert main([*out, "--cases", "case-905"], web.transport) == EXIT_OK
    unsearched = RetrievalScoreboard.model_validate_json(
        (tmp_path / "out" / "retrieval.json").read_text()
    )
    assert [row.measured for row in unsearched.rows] == [
        True,
        False,
        True,
        False,
        False,
        False,
    ]
    for row in (unsearched.rows[0], unsearched.rows[2]):
        assert (row.recall_searches, row.rule_recall, row.latency_ms_median) == (
            0,
            None,
            None,
        )
        assert (row.right_runs, row.cases) == (0, 1)
    started = web.case("case-905").started_with
    assert started is not None and started["retriever_configs"] == ["r1", "r3"]
    capsys.readouterr()

    # A run against the deployed environment says that its figures are results.
    web.available = {"r3"}
    deployed = [
        "--deployed",
        "--web-address",
        "https://web.example.test",
        "--output-dir",
        str(tmp_path / "deployed"),
    ]
    assert main(deployed, web.transport) == EXIT_OK
    for name in ("retrieval.json", "redaction.json"):
        written = json.loads((tmp_path / "deployed" / name).read_text())
        assert written["run"]["stand_ins"] is False
        assert written["run"]["web_address"] == "https://web.example.test"
    results = capsys.readouterr().out
    assert "(results)" in results and "stand-in" not in results

    # Redaction left the applicant's surname on one page of one case.
    web.available = {"r3"}
    web.texts["case-901"][3] = "Reviewed with Mr SPECIMENDALE on the telephone."
    assert (
        main([*out, "--cases", "case-901", "--rows", "r3"], web.transport) == EXIT_LEAK
    )

    report = RedactionScoreboard.model_validate_json(
        (tmp_path / "out" / "redaction.json").read_text()
    )
    assert not report.clean
    assert [leak.model_dump() for leak in report.leaks] == [
        {"case_key": "case-901", "page_number": 3, "category": "person_name"}
    ]
    assert (report.cases_checked, report.pages_checked) == (1, 3)
    # The value is in neither file and not in what the command printed.
    printed = capsys.readouterr()
    for text in (
        (tmp_path / "out" / "redaction.json").read_text(),
        (tmp_path / "out" / "retrieval.json").read_text(),
        printed.out,
        printed.err,
    ):
        assert "specimendale" not in text.lower()
    assert "leak: case-901 page 3 (person_name)" in printed.out
    assert "quotes not found in their page text: 1 of 2" in printed.out
    assert "stand-in figures, not results" in printed.out

    # A local run is refused the published folder, and so is a deployed run
    # narrowed to some cases or rows; a case the key does not hold is
    # refused, and an address that does not answer ends the run before
    # anything is sent. Nothing is written by any of them.
    calls = len(web.uploads)
    assert main(["--output-dir", str(PUBLISHED_SCOREBOARDS)], web.transport) == (
        EXIT_REFUSED
    )
    for narrowed in (["--cases", "case-901"], ["--rows", "r3"]):
        assert main([*deployed[:3], *narrowed], web.transport) == EXIT_REFUSED
    assert main([*out, "--cases", "case-999"], web.transport) == EXIT_REFUSED
    web.available = {"r3"}
    monkeypatch.setenv("EVALS_REQUEST_RETRIES", "0")
    assert (
        main([*out, "--web-address", "http://127.0.0.1:9"], _nothing_answers())
        == EXIT_REFUSED
    )
    # Static metrics that are no YAML, and a case document that is not
    # there, refuse the run before anything is sent, without a traceback.
    broken = tmp_path / "static-metrics.yaml"
    broken.write_text("rows: [", encoding="utf-8")
    monkeypatch.setenv("EVALS_STATIC_METRICS_FILE", str(broken))
    assert main(out, web.transport) == EXIT_REFUSED
    monkeypatch.delenv("EVALS_STATIC_METRICS_FILE")
    (tmp_path / "data" / "cases" / "case-902.pdf").unlink()
    assert main(out, web.transport) == EXIT_REFUSED
    assert len(web.uploads) == calls
    assert "Traceback" not in capsys.readouterr().err


SURE = "extracting"
ASK = "awaiting_customer"
TRIAGE = "awaiting_triage"
WHY = "The page shows laboratory results."


def test_story_4_3_the_page_set_is_taken_to_the_gate_once_per_contender_and_both_are_scored_on_what_was_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    case_set(tmp_path)
    web = FakeWeb()
    web.pages = {"case-901": ["uploaded"] * 3, "case-902": ["uploaded"] * 3}
    leaking = ("lab_report", 0.97, f"The report of Mr {NAME.split()[1]}.", SURE)
    # The expected labels: `case-901` medical, not, medical; `case-902`
    # medical, medical, not.
    web.classified = {
        # Five of six right; four pages scored 0.90 or more, all right; two
        # pages sent to triage by the gate.
        ("llm", "case-901"): [
            ("lab_report", 1.0, WHY, SURE),
            ("other", 1.0, WHY, ASK),
            ("other", 0.6, WHY, TRIAGE),
        ],
        ("llm", "case-902"): [
            ("application_form", 0.95, WHY, SURE),
            ("lab_report", 0.8, WHY, TRIAGE),
            ("invoice", 1.0, WHY, ASK),
        ],
        # One page of its first file fails, and with it that file's case.
        # Right on the second file, where the surname of the first case's
        # applicant is no planted identifier; `case-902`'s own telephone
        # number, copied into a reason, is.
        ("doc-intelligence", "case-901"): [
            ("lab_report", 0.97, WHY, SURE),
            None,
            leaking,
        ],
        ("doc-intelligence", "case-902"): [
            ("lab_report", 0.97, "Call (303) 555-0190 about the results.", SURE),
            ("lab_report", 0.97, WHY, SURE),
            ("other", 0.97, WHY, ASK),
        ],
    }
    for name, value in (
        ("EVALS_DATA_DIR", tmp_path / "data"),
        ("EVALS_STATE_DIR", tmp_path / "state"),
        ("EVALS_RETRY_SECONDS", 0),
        ("EVALS_POLL_SECONDS", 0.001),
    ):
        monkeypatch.setenv(name, str(value))
    run_it = ["--bake-off", "classification", "--output-dir", str(tmp_path / "out")]

    # A reason that holds a planted identifier fails the run like a leak.
    assert main(run_it, web.transport) == EXIT_LEAK

    file = tmp_path / "out" / "classification.json"
    board = ClassificationScoreboard.model_validate_json(file.read_text())
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == [file.name]
    # Each file was uploaded once per contender, as the customer, and
    # started with that contender, the run's id and the stop after the gate.
    # The first file of `doc-intelligence` failed for an ordinary reason (a
    # page failed): the contender is measured all the same, and its other
    # file was uploaded.
    assert sorted(web.uploads) == ["case-901", "case-901", "case-902", "case-902"]
    assert sorted(
        (case.case_key, *sorted((case.started_with or {}).items()))
        for case in web.cases.values()
    ) == sorted(
        (
            key,
            ("classifier_contender", contender),
            ("eval_run_id", board.run.eval_run_id),
            ("stop_after", "gate"),
        )
        for key in ("case-901", "case-902")
        for contender in ("llm", "doc-intelligence")
    )
    # No page waits for a person, nothing is searched and no verdict is read.
    assert web.decisions == [] and web.searches == []
    assert {call for call in web.roles if call[0] == "customer"} == {
        ("customer", "POST", "cases")
    }
    assert not any("verdict-runs" in path for _, _, path in web.roles)

    llm, classifier = board.contenders
    assert (llm.contender, classifier.contender) == ("llm", "doc-intelligence")
    assert (llm.right_pages, llm.pages, llm.accuracy) == (5, 6, 0.8333)
    assert (llm.confident_right_pages, llm.confident_pages, llm.calibration) == (
        4,
        4,
        1.0,
    )
    assert (llm.queued_pages, llm.queue_rate) == (2, 0.3333)
    assert llm.pages_not_classified == 0
    # The cost per page is the stated one, with its source (static metrics).
    assert llm.cost_per_page is not None and llm.cost_per_page.source
    # The file whose case failed counts every one of its pages as wrong.
    assert classifier.measured and board.not_run == []
    assert (classifier.right_pages, classifier.pages, classifier.accuracy) == (
        3,
        6,
        0.5,
    )
    assert (classifier.confident_right_pages, classifier.confident_pages) == (3, 3)
    assert (classifier.queued_pages, classifier.pages_not_classified) == (0, 3)
    # Neither is sure of 10 pages, the floor a calibration is taken from: a
    # set of six pages has no winner, however well a contender did.
    assert board.winner is None
    assert [case.model_dump(exclude={"case_id"}) for case in board.unscored_cases] == [
        {
            "contender": "doc-intelligence",
            "case_key": "case-901",
            "case_status": "failed",
            "reason": "case_failed",
            "error_code": "stage_failed",
        }
    ]
    assert [page.model_dump() for page in board.unclassified_pages] == [
        {
            "contender": "doc-intelligence",
            "case_key": "case-901",
            "page_number": 2,
            "error_code": "model_unavailable",
        }
    ]
    # Every stored reason was looked through, also those of the file that
    # failed: six of `llm`, five of the other.
    assert board.reasons_checked == 11 and board.reasons_not_checked == []
    assert [leak.model_dump() for leak in board.reason_leaks] == [
        {
            "contender": "doc-intelligence",
            "case_key": "case-901",
            "page_number": 3,
            "category": "person_name",
        },
        {
            "contender": "doc-intelligence",
            "case_key": "case-902",
            "page_number": 1,
            "category": "phone_number",
        },
    ]
    printed = capsys.readouterr()
    for text in (file.read_text(), printed.out, printed.err):
        assert "specimendale" not in text.lower() and "555-0190" not in text
    assert "leak: case-901 page 3 with doc-intelligence (person_name)" in printed.out
    assert "stand-in figures, not results" in printed.out
    assert board.run.stand_ins and board.run.web_address == "http://localhost:8000"

    # Started again with its id: no file is uploaded or started a second
    # time for a contender. The run is no retrieval bake-off.
    uploads, starts = len(web.uploads), len(web.starts)
    resume = [*run_it, "--eval-run-id", board.run.eval_run_id]
    assert main(resume, web.transport) == EXIT_LEAK
    assert (len(web.uploads), len(web.starts)) == (uploads, starts) == (4, 4)
    assert ClassificationScoreboard.model_validate_json(
        file.read_text()
    ).contenders == (board.contenders)
    assert main(resume[2:], web.transport) == EXIT_REFUSED
    assert len(web.uploads) == uploads

    # Without the leaks, in a new run: no leak, and the run is not whole,
    # because a file was not scored. The contender stays measured.
    web.classified["doc-intelligence", "case-901"][2] = ("lab_report", 0.97, WHY, SURE)
    web.classified["doc-intelligence", "case-902"][0] = ("lab_report", 0.97, WHY, SURE)
    capsys.readouterr()
    assert main(run_it, web.transport) == EXIT_INCOMPLETE
    unwhole = ClassificationScoreboard.model_validate_json(file.read_text())
    assert [score.measured for score in unwhole.contenders] == [True, True]
    assert [case.case_key for case in unwhole.unscored_cases] == ["case-901"]
    assert len(web.uploads) == uploads + 4
    said = capsys.readouterr().out
    assert "reasons: clean, 11 checked" in said
    assert "incomplete: 2 contenders measured, 1 files not scored" in said

    # A contender `classification` refuses is not measured and has no
    # numbers; the run goes on and the other is scored. Only its first file
    # was uploaded for it, and the file says which case showed the refusal
    # and what it ended with. Nothing else being amiss, the run is whole.
    web.contenders = {"llm"}
    uploads = len(web.uploads)
    assert main(run_it, web.transport) == EXIT_OK
    alone = ClassificationScoreboard.model_validate_json(file.read_text())
    assert [score.measured for score in alone.contenders] == [True, False]
    assert alone.contenders[0] == llm
    # The stated cost is no measurement: the file carries it as stated, and
    # the screen shows none for a contender that was not measured.
    numbers = alone.contenders[1].model_dump(
        exclude={"contender", "measured", "cost_per_page"}
    )
    assert set(numbers.values()) == {None}
    assert (alone.winner, alone.unscored_cases, alone.reason_leaks) == (None, [], [])
    assert len(web.uploads) == uploads + 3
    (refused,) = alone.not_run
    assert refused.case_id is not None
    assert refused.model_dump(exclude={"case_id"}) == {
        "contender": "doc-intelligence",
        "case_key": "case-901",
        "case_status": "failed",
        "reason": "case_failed",
        "error_code": "stage_failed",
    }
    said = capsys.readouterr().out
    assert "doc-intelligence: not measured" in said
    assert (
        f"cannot be run here: doc-intelligence (the case of case-901, {refused.case_id}"
        in said
    )
    # The same shape with `classification` not answering the read of the
    # case's classifications is an outage, not a refusal: the contender is
    # measured, its files are not scored and their reasons not read.
    web.classification_down = True
    assert (
        main([*run_it, "--contenders", "doc-intelligence"], web.transport)
        == EXIT_INCOMPLETE
    )
    outage = ClassificationScoreboard.model_validate_json(file.read_text())
    assert outage.contenders[1].measured and outage.not_run == []
    assert (outage.contenders[1].right_pages, outage.contenders[1].pages) == (0, 6)
    assert [case.case_key for case in outage.unscored_cases] == ["case-901", "case-902"]
    assert [unread.case_key for unread in outage.reasons_not_checked] == [
        "case-901",
        "case-902",
    ]
    assert "reasons: not clean" in capsys.readouterr().out
    web.classification_down = False
    # A run in which no contender is measured is incomplete and writes no
    # file: the one that stood there stays as it was.
    before = file.read_text()
    assert (
        main([*run_it, "--contenders", "doc-intelligence"], web.transport)
        == EXIT_INCOMPLETE
    )
    assert file.read_text() == before
    said = capsys.readouterr().out
    assert "incomplete: 0 contenders measured" in said
    assert "written: nothing" in said

    # One file's start gets no usable answer: that file is not scored, the
    # other is. A file that never becomes final is wrong on every page and
    # its reasons are not read. The command says the run is incomplete.
    web.refused_starts = {"case-901"}
    web.hanging = {"case-902"}
    monkeypatch.setenv("EVALS_CASE_DEADLINE_SECONDS", "0.01")
    assert main([*run_it, "--contenders", "llm"], web.transport) == EXIT_INCOMPLETE
    lost = ClassificationScoreboard.model_validate_json(file.read_text())
    assert [
        (case.case_key, case.reason, case.error_code) for case in lost.unscored_cases
    ] == [
        ("case-901", "request_failed", "upstream_unavailable"),
        ("case-902", "not_final_in_time", None),
    ]
    assert (lost.contenders[0].right_pages, lost.contenders[0].pages) == (0, 6)
    assert [unread.case_key for unread in lost.reasons_not_checked] == [
        "case-901",
        "case-902",
    ]
    web.refused_starts, web.hanging = set(), {"case-902"}
    assert main([*run_it, "--contenders", "llm"], web.transport) == EXIT_INCOMPLETE
    hung = ClassificationScoreboard.model_validate_json(file.read_text())
    assert [(case.case_key, case.reason) for case in hung.unscored_cases] == [
        ("case-902", "not_final_in_time")
    ]
    assert (hung.contenders[0].right_pages, hung.contenders[0].pages) == (2, 6)

    # The published folder takes the whole bake-off only, and only from a
    # run against the deployed environment.
    calls = len(web.uploads)
    published = ["--bake-off", "classification"]
    deployed = [*published, "--deployed", "--web-address", "https://web.example.test"]
    for refused_run in (
        [*published, "--output-dir", str(PUBLISHED_SCOREBOARDS)],
        [*deployed, "--contenders", "llm"],
        [*deployed, "--cases", "case-901"],
    ):
        assert main(refused_run, web.transport) == EXIT_REFUSED
    assert len(web.uploads) == calls
