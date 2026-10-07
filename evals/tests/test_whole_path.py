"""Stories 3.4 and 3.7: one bake-off run over synthetic cases, against the system as it really runs.

`web`, `workflow`, `intake`, `classification`, `extraction`, `retrieval` and
`verdict`, against a real PostgreSQL, the Durable Task Scheduler emulator and
the blob emulator (`docker compose up --detach --wait`), with the stand-ins
where Azure AI Language and the Foundry deployments would be, and a transport
where the Dapr sidecars would be. The runner is given `web` and nothing else.

The figures are stand-in figures: the stand-in's vectors count shared words
and its agent is scripted. The test proves the path, not the retrievers.
"""

import asyncio
import json
from pathlib import Path

import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from synthdata_stack import (
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

from bakeoff.runner import run
from bakeoff.settings import SCRATCH, Settings
from contracts.ids import new_id
from contracts.models.web import (
    RedactionScoreboard,
    RetrievalScoreboard,
    TriageQueue,
)
from contracts.models.workflow import AuditTrail, CaseList, CaseProgress
from synthdata.foundry_standin import Mode
from workflow.settings import Settings as WorkflowSettings

pytestmark = pytest.mark.integration

UNDERWRITER = {"X-Demo-Role": "underwriter"}
# A case of medical pages only, and one with a blank page.
CASES = ["case-001", "case-003"]
# Story 3.7: `retrieval` is given the chat deployment here (the stand-in),
# so it answers row `r4`, and `workflow` and `verdict` are told of the row.
ROWS = ["r1", "r2", "r3", "r4"]


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
    # The rows that are available are measured, `r4` with its reranker
    # among them; `r5` without a search service and `r6`, which is not
    # built, are recorded as not measured.
    assert [row.measured for row in board.rows] == [
        True,
        True,
        True,
        True,
        False,
        False,
    ]
    # The scoreboard names the reranker used.
    assert rows["r4"].method == "Hybrid, then an LLM reranker on the chat deployment"
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

    # Each case was uploaded once, started with the four rows and the run's
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
