"""Stories 3.4, 3.7, 3.8 and 4.3: one bake-off run over synthetic cases, against the system as it really runs.

`web`, `workflow`, `intake`, `classification`, `extraction`, `retrieval` and
`verdict`, against a real PostgreSQL, the Durable Task Scheduler emulator and
the blob emulator (`docker compose up --detach --wait`), with the stand-ins
where Azure AI Language, the Foundry deployments and Azure AI Search would be,
and a transport where the Dapr sidecars would be. The runner is given `web` and nothing else.

The figures are stand-in figures: the stand-in's vectors count shared words
and its agent is scripted. The test proves the path, not the retrievers.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from synthdata_stack import (
    CLASSIFIER_ID,
    LocalClassification,
    LocalExtraction,
    LocalIntake,
    LocalVerdict,
    RunningService,
    ServicesBehindSidecar,
    answer_key,
    end_lifecycle,
    web_service,
    workflow_service,
)

from bakeoff.runner import ClassificationRunResult, run, run_classification
from bakeoff.settings import SCRATCH, Settings
from contracts.ids import new_id
from contracts.models.web import (
    CALIBRATION_FLOOR_PAGES,
    ClassificationScoreboard,
    RedactionScoreboard,
    RetrievalScoreboard,
    TriageQueue,
)
from contracts.models.workflow import AuditTrail, CaseList, CaseProgress
from synthdata.foundry_standin import Mode
from synthdata.search_standin import SearchStandIn
from workflow.settings import Settings as WorkflowSettings

pytestmark = pytest.mark.integration

UNDERWRITER = {"X-Demo-Role": "underwriter"}
# A case of medical pages only, and one with a blank page.
CASES = ["case-001", "case-003"]
# Story 3.7: `retrieval` is given the reranker deployment here (the
# stand-in), so it answers row `r4`, and `workflow` and `verdict` are told
# of the row.
# Story 3.8: it is given a search service as well (the stand-in, loaded by
# the job's last steps), so it answers `r5` and `r6`: all six rows.
ROWS = ["r1", "r2", "r3", "r4", "r5", "r6"]


def test_story_3_4_the_runner_scores_the_built_rows_over_synthetic_cases_through_web_and_writes_both_files(
    workflow_service_settings: WorkflowSettings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    tmp_path: Path,
) -> None:
    # The stand-in is unsure of laboratory reports: each case then has a
    # medical page in triage, and `case-003` a blank page for the customer.
    classification.model.mode = Mode.MIXED
    verdict.retrieval.search = SearchStandIn()
    verdict.retrieval.load_index()
    keys = {name: answer_key(name) for name in CASES}
    eval_run_id = new_id()
    settings = Settings(
        cases=CASES,
        eval_run_id=eval_run_id,
        output_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        poll_seconds=0.1,
        case_deadline_seconds=120.0,
    )
    assert settings.scoreboard_dir != SCRATCH / "scoreboards"
    behind_workflow = ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(available_retriever_configs=ROWS),
    )
    case_ids: dict[str, str] = {}
    workflow_settings = workflow_service_settings.model_copy(
        update={"available_retriever_configs": ROWS}
    )

    with workflow_service(workflow_settings, behind_workflow) as workflow:
        behind_web = ServicesBehindSidecar(
            intake=intake.app(),
            workflow=RunningService(workflow),
            classification=classification.app(),
            extraction=extraction.app(),
            verdict=verdict.app(available_retriever_configs=ROWS),
            retrieval=verdict.retrieval.app(),
        )
        with web_service(tmp_path, behind_web) as web:
            try:
                result = asyncio.run(run(settings, RunningService(web)))
            finally:
                state = tmp_path / "state" / f"{eval_run_id}.json"
                if state.is_file():
                    case_ids = json.loads(state.read_text())["cases"]
                for case_id in case_ids.values():
                    end_lifecycle(scheduler_client, case_id)
            # What the underwriter sees afterwards.
            queue = TriageQueue.model_validate(
                web.get("/api/triage", headers=UNDERWRITER).json()
            )
            listed = CaseList.model_validate(
                web.get("/api/cases", headers=UNDERWRITER).json()
            )
            progress = {
                name: CaseProgress.model_validate(
                    web.get(
                        f"/api/cases/{case_id}/progress", headers=UNDERWRITER
                    ).json()
                )
                for name, case_id in case_ids.items()
            }
            trail = AuditTrail.model_validate(
                web.get(
                    f"/api/cases/{case_ids['case-003']}/audit", headers=UNDERWRITER
                ).json()
            )

    board, redaction = result.retrieval, result.redaction
    rows = {row.retriever_config.value: row for row in board.rows}
    # The scoreboard shows all six rows, each measured: `r4` with its
    # reranker, `r5` on the search service and `r6` through its knowledge
    # base.
    assert [row.retriever_config.value for row in board.rows] == ROWS
    assert all(row.measured for row in board.rows)
    assert rows["r6"].method == (
        "Agentic retrieval (knowledge base with LLM query planning)"
    )
    assert (rows["r6"].store, rows["r6"].failed_runs) == ("Azure AI Search", 0)
    # The scoreboard names the reranker used.
    assert rows["r4"].method == "Hybrid, then Cohere Rerank v4.0 fast on Foundry"
    assert (rows["r4"].store, rows["r4"].chunk_set.value) == ("pgvector", "smart")
    # Its verdict runs, one per case, were made and none of them failed.
    assert (rows["r4"].cases, rows["r4"].failed_runs) == (2, 0)
    expected_searches = sum(
        bool(fact["rule_ids"])
        for key in keys.values()
        for fact in key["expected_facts"]
    )
    assert expected_searches >= 1
    for row in ROWS:
        scored = rows[row]
        # One search per expected fact that meets a rule, each answered and timed.
        assert scored.recall_searches == expected_searches
        assert scored.latency_searches == expected_searches
        assert scored.rule_recall is not None and scored.latency_ms_median is not None
        assert scored.cases == 2 and scored.verdict_accuracy is not None
    assert board.failed_searches == [] and board.unscored_cases == []
    # `r3` over the one-rule chunks gives both cases the answer key's verdict.
    assert rows["r3"].right_runs == 2
    assert board.winner in set(ROWS)

    # Each case was uploaded once, started with the six rows and the run's
    # id, and ran to its end with the waits answered from the page labels:
    # the laboratory report accepted, the blank page discarded.
    assert sorted(case_ids) == CASES
    assert behind_web.paths("intake").count("/cases") == 2
    assert {case.case_status.value for case in progress.values()} == {"completed"}
    for name, case in progress.items():
        for page, label in zip(case.pages, keys[name]["pages"], strict=True):
            wanted = "extracted" if label["is_medical"] else "discarded"
            assert page.page_status.value == wanted, (name, page.page_number)
    actions = [event.action.value for event in trail.events]
    assert "page.discarded" in actions and "page.accepted" in actions
    assert actions.count("verdict.suggested") == len(ROWS)
    assert {event.eval_run_id for event in trail.events} == {eval_run_id}
    # The runner's cases are in neither of the underwriter's lists.
    assert queue.pages == [] and listed.cases == []

    # Redaction: every page text read through `web`, nothing planted left.
    pages = sum(len(key["pages"]) for key in keys.values())
    assert redaction.clean and redaction.leaks == []
    assert (redaction.cases_checked, redaction.pages_checked) == (2, pages)
    assert redaction.identifiers_checked == sum(
        len(key["identifiers"]) for key in keys.values()
    )
    # Over-redaction: every quote of every expected fact was looked for on
    # its page, and the redaction stand-in took none of them away.
    assert redaction.quotes_checked == sum(
        len(fact["places"]) for key in keys.values() for fact in key["expected_facts"]
    )
    assert redaction.quotes_checked >= 1
    assert (redaction.quotes_not_found, redaction.quotes_not_found_at) == (0, [])
    assert behind_web.paths("intake").count("/cases") == 2
    assert sum(path.endswith("/text") for path in behind_web.paths("intake")) == pages

    # Both files are written in the contract's shape and marked as stand-in figures.
    written = RetrievalScoreboard.model_validate_json(
        (tmp_path / "out" / "retrieval.json").read_text()
    )
    report = RedactionScoreboard.model_validate_json(
        (tmp_path / "out" / "redaction.json").read_text()
    )
    assert (written, report) == (board, redaction)
    assert written.run.stand_ins and written.run.eval_run_id == eval_run_id


# A mixed file (medical pages, an invoice, a payslip, a utility bill) and a
# file with a blank page and a turned laboratory report.
PAGE_SET_FILES = ["case-002", "case-003"]


def test_story_4_3_the_runner_takes_the_page_set_to_the_gate_with_each_contender_and_writes_the_classifier_scoreboard(
    workflow_service_settings: WorkflowSettings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    tmp_path: Path,
) -> None:
    # The chat stand-in is unsure of laboratory reports.
    classification.model.mode = Mode.MIXED
    keys = {name: answer_key(name) for name in PAGE_SET_FILES}
    pages = sum(len(key["pages"]) for key in keys.values())
    lab_reports = sum(
        page["page_type"] == "lab_report"
        for key in keys.values()
        for page in key["pages"]
    )
    seen: dict[str, Any] = {}

    def bake_off(eval_run_id: str) -> ClassificationRunResult:
        """One run through `web`, with the services as `classification` is set up now."""
        settings = Settings(
            bake_off="classification",
            cases=PAGE_SET_FILES,
            eval_run_id=eval_run_id,
            output_dir=tmp_path / "out",
            state_dir=tmp_path / "state",
            poll_seconds=0.1,
            case_deadline_seconds=120.0,
        )
        behind_workflow = ServicesBehindSidecar(
            intake=intake.app(),
            classification=classification.app(),
            extraction=extraction.app(),
            verdict=verdict.app(),
        )
        case_ids: dict[str, str] = {}
        with workflow_service(workflow_service_settings, behind_workflow) as workflow:
            behind_web = ServicesBehindSidecar(
                intake=intake.app(),
                workflow=RunningService(workflow),
                classification=classification.app(),
            )
            with web_service(tmp_path, behind_web) as web:
                try:
                    result = asyncio.run(
                        run_classification(settings, RunningService(web))
                    )
                finally:
                    state = tmp_path / "state" / f"{eval_run_id}.json"
                    if state.is_file():
                        case_ids = json.loads(state.read_text())["cases"]
                    for case_id in case_ids.values():
                        end_lifecycle(scheduler_client, case_id)
                seen["queue"] = TriageQueue.model_validate(
                    web.get("/api/triage", headers=UNDERWRITER).json()
                )
                seen["listed"] = CaseList.model_validate(
                    web.get("/api/cases", headers=UNDERWRITER).json()
                )
                seen["progress"] = {
                    name: CaseProgress.model_validate(
                        web.get(
                            f"/api/cases/{case_id}/progress", headers=UNDERWRITER
                        ).json()
                    )
                    for name, case_id in case_ids.items()
                }
                seen["trail"] = AuditTrail.model_validate(
                    web.get(
                        f"/api/cases/{case_ids['llm:case-003']}/audit",
                        headers=UNDERWRITER,
                    ).json()
                )
        seen["case_ids"] = case_ids
        seen["uploads"] = behind_web.paths("intake").count("/cases")
        return result

    # --- 1. `classification` is given no classifier: it refuses the second
    # contender's commands, as the real service does. The runner reads that
    # through `web`, records the contender as not measured with the case
    # that showed it, uploads no other file for it, and scores `llm`.
    without = bake_off(new_id()).classification
    assert [score.measured for score in without.contenders] == [True, False]
    assert without.contenders[0].pages == pages
    assert without.contenders[0].pages_not_classified == 0
    (refused,) = without.not_run
    first = f"doc-intelligence:{PAGE_SET_FILES[0]}"
    assert sorted(seen["case_ids"]) == sorted(
        [first, *(f"llm:{name}" for name in PAGE_SET_FILES)]
    )
    assert (refused.contender.value, refused.case_key) == (
        "doc-intelligence",
        PAGE_SET_FILES[0],
    )
    assert refused.case_id == seen["case_ids"][first]
    assert (refused.reason, refused.error_code) == ("case_failed", "stage_failed")
    assert {page.page_status.value for page in seen["progress"][first].pages} == {
        "uploaded"
    }
    assert without.winner is None or without.winner.value == "llm"
    assert without.unscored_cases == [] and without.reasons_not_checked == []
    assert without.reasons_checked == pages

    # --- 2. The second contender has a classifier, as after the training
    # job (story 4.2 proves the training; the runner trains nothing). The
    # classifier stand-in is unsure of nothing.
    stand_in = classification.with_classifier()
    stand_in.classifiers[CLASSIFIER_ID] = {"classifierId": CLASSIFIER_ID}
    eval_run_id = new_id()
    result = bake_off(eval_run_id)
    case_ids, progress, trail = seen["case_ids"], seen["progress"], seen["trail"]

    # Each file was uploaded once per contender and started with it.
    assert sorted(case_ids) == sorted(
        f"{contender}:{name}"
        for contender in ("llm", "doc-intelligence")
        for name in PAGE_SET_FILES
    )
    assert seen["uploads"] == 4
    for name, case in progress.items():
        assert case.classifier_contender is not None
        assert case.classifier_contender.value == name.partition(":")[0]
        # Stopped at the gate: complete, every page left as it was routed.
        assert case.case_status.value == "completed"
        assert {page.page_status.value for page in case.pages} <= {
            "extracting",
            "awaiting_customer",
            "awaiting_triage",
        }
    # No extraction and no verdict ran, and no page waits for a person: the
    # cases are in neither of the underwriter's lists.
    actions = {event.action.value for event in trail.events}
    assert "page.routed" in actions
    assert not actions & {"facts.extracted", "verdict.suggested", "page.accepted"}
    assert {event.eval_run_id for event in trail.events} == {eval_run_id}
    assert extraction.reads_of_intake() == []
    assert seen["queue"].pages == [] and seen["listed"].cases == []

    board = result.classification
    llm, classifier = board.contenders
    assert (llm.contender.value, classifier.contender.value) == (
        "llm",
        "doc-intelligence",
    )
    # Both were scored on every page of the two files, each on its own
    # stored results: one per page and contender.
    for score in (llm, classifier):
        assert score.measured and score.pages == pages
        assert score.pages_not_classified == 0
        assert score.accuracy is not None and score.queue_rate is not None
        # Stand-in figures: both stand-ins read the generator's headings.
        assert score.right_pages == pages
    assert board.reasons_checked == 2 * pages and board.reason_leaks == []
    assert board.reasons_not_checked == [] and board.not_run == []
    assert board.unscored_cases == [] and board.unclassified_pages == []
    # The gate sent the pages the chat stand-in was unsure of to triage, and
    # none of the classifier's: the queue rate is where the gate put a page.
    assert (llm.queued_pages, classifier.queued_pages) == (lab_reports, 0)
    assert llm.confident_pages == pages - lab_reports
    assert classifier.confident_pages == pages
    # Equally accurate, and the classifier has the lower queue rate. It is
    # also the only one sure of 10 pages, the floor a calibration is taken
    # from: the chat stand-in, unsure of the laboratory reports, is not.
    assert pages >= CALIBRATION_FLOOR_PAGES > pages - lab_reports
    assert board.winner is not None and board.winner.value == "doc-intelligence"
    # The classifier was asked for each page once.
    assert len(stand_in.analysed) == pages

    written = ClassificationScoreboard.model_validate_json(
        (tmp_path / "out" / "classification.json").read_text()
    )
    assert written == board
    assert written.run.stand_ins and written.run.eval_run_id == eval_run_id
