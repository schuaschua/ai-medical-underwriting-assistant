"""Story 2.8: the agent's log, read as the underwriter through `web`.

`web`, `workflow`, `intake`, `classification`, `extraction`, `retrieval` and
`verdict` as they really run, against a real PostgreSQL, the Durable Task
Scheduler emulator and the blob emulator (`docker compose up --detach
--wait`), with the stand-ins of this package where Azure AI Language and the
Foundry deployments would be, and a transport where the Dapr sidecars would
be.

The test is here and not under `services/` because it names the stand-ins'
package, which nothing there may do (spine AD-17).
"""

from pathlib import Path
from typing import Any

import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from fastapi.testclient import TestClient
from synthdata_stack import (
    CASES_DIR,
    PDF,
    LocalClassification,
    LocalExtraction,
    LocalIntake,
    LocalVerdict,
    RunningService,
    ServicesBehindSidecar,
    completed,
    end_lifecycle,
    web_service,
    workflow_service,
)

from contracts.audit import AuditAction, VerdictDetail
from contracts.enums import RetrieverConfig, StepOutcome, ToolName
from contracts.ids import new_id
from contracts.models.verdict import AgentStep, AgentStepList
from contracts.models.workflow import AuditTrail
from workflow.settings import Settings

pytestmark = pytest.mark.integration

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}
# How many steps one answer of `verdict` holds here: fewer than a run makes,
# so the rest must be read with the cursor.
STEPS_PER_ANSWER = 5
RULE_ID = "UW-DM-002"


def read_all(web: TestClient, path: str, **asked: Any) -> list[AgentStep]:
    """Every step a read lists, answer after answer, as the screen's "more" reads them."""
    steps: list[AgentStep] = []
    cursor: dict[str, Any] = {}
    while True:
        answer = web.get(path, params={**asked, **cursor}, headers=UNDERWRITER)
        assert answer.status_code == 200, answer.text
        page = AgentStepList.model_validate(answer.json())
        assert len(page.steps) <= STEPS_PER_ANSWER
        steps += page.steps
        if not page.has_more:
            return steps
        last = page.steps[-1]
        cursor = {"after_step_no": last.step_no}
        if "/agent-steps" in path:
            cursor["after_verdict_run_id"] = last.verdict_run_id


def test_story_2_8_the_underwriter_opens_a_runs_steps_from_its_audit_event_and_narrows_them(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    tmp_path: Path,
) -> None:
    behind_workflow = ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(),
    )
    started: list[str] = []

    with workflow_service(workflow_service_settings, behind_workflow) as workflow:
        behind_web = ServicesBehindSidecar(
            intake=intake.app(),
            workflow=RunningService(workflow),
            verdict=verdict.app(step_list_limit=STEPS_PER_ANSWER),
        )
        with web_service(tmp_path, behind_web) as web:
            try:
                uploaded = web.post(
                    "/api/cases",
                    content=(CASES_DIR / "case-001.pdf").read_bytes(),
                    headers={**CUSTOMER, **PDF},
                )
                assert uploaded.status_code == 201
                case_id: str = uploaded.json()["case_id"]
                started.append(case_id)
                assert (
                    web.post(
                        f"/api/cases/{case_id}/start", headers=CUSTOMER
                    ).status_code
                    == 200
                )
                completed(scheduler_client, case_id)

                # The drill-down starts at the trail's "verdict suggested"
                # event: its reference is the run.
                trail = AuditTrail.model_validate(
                    web.get(f"/api/cases/{case_id}/audit", headers=UNDERWRITER).json()
                )
                (suggested,) = [
                    event
                    for event in trail.events
                    if event.action is AuditAction.VERDICT_SUGGESTED
                ]
                run_id = str(suggested.ref)
                run_path = f"/api/verdict-runs/{run_id}/steps"
                case_path = f"/api/cases/{case_id}/agent-steps"

                first_answer = web.get(run_path, headers=UNDERWRITER)
                of_run = read_all(web, run_path)
                of_case = read_all(web, case_path)
                searches = read_all(web, case_path, tool="search_rules")
                with_rule = read_all(web, case_path, rule_id=RULE_ID)
                reads_of_rule = read_all(
                    web, case_path, tool="read_rule", rule_id=RULE_ID
                )
                run_searches = read_all(web, run_path, tool="search_rules")

                unknown_run = web.get(
                    f"/api/verdict-runs/{new_id()}/steps", headers=UNDERWRITER
                )
                not_a_tool = web.get(
                    case_path, params={"tool": "delete_rule"}, headers=UNDERWRITER
                )
                not_a_rule = web.get(
                    case_path, params={"rule_id": "UW-dm-2"}, headers=UNDERWRITER
                )

                # AD-9: the customer is refused by `web` itself, which asks
                # no service.
                calls_before = len(behind_web.calls)
                refused = [
                    web.get(path, headers=CUSTOMER) for path in (run_path, case_path)
                ]
                calls_after = len(behind_web.calls)
            finally:
                for case in started:
                    end_lifecycle(scheduler_client, case)

    # The event names the retriever configuration of its run.
    assert suggested.detail == VerdictDetail(retriever_config=RetrieverConfig.R3)

    # One answer holds the first steps and says that more exist; with the
    # cursor every step is read, once, in order, each with all its fields.
    first = AgentStepList.model_validate(first_answer.json())
    assert first.has_more
    assert [step.step_no for step in first.steps] == [1, 2, 3, 4, 5]
    assert len(of_run) > STEPS_PER_ANSWER
    assert [step.step_no for step in of_run] == list(range(1, len(of_run) + 1))
    assert of_run == verdict.steps(run_id).steps
    assert {step.verdict_run_id for step in of_run} == {run_id}
    assert {step.case_id for step in of_run} == {case_id}
    assert of_run[0].tool is ToolName.LIST_FACTS
    assert all(step.outcome is StepOutcome.DONE for step in of_run)
    assert all(step.occurred_at and step.latency_ms >= 0 for step in of_run)
    # The case has this one run: its steps are the run's.
    assert of_case == of_run

    # Narrowed by `verdict`, not by the caller: only the matching steps.
    assert searches == [step for step in of_run if step.tool is ToolName.SEARCH_RULES]
    assert searches and all(step.fact_id for step in searches)
    assert all("query" in step.arguments for step in searches)
    assert run_searches == searches
    assert with_rule == [step for step in of_run if RULE_ID in step.rule_ids]
    assert {step.tool for step in with_rule} == {
        ToolName.SEARCH_RULES,
        ToolName.READ_RULE,
    }
    assert reads_of_rule == [
        step for step in with_rule if step.tool is ToolName.READ_RULE
    ]
    assert reads_of_rule

    assert unknown_run.status_code == 404
    assert unknown_run.json()["error"]["code"] == "not_found"
    for answer in (not_a_tool, not_a_rule):
        assert answer.status_code == 422
        assert answer.json()["error"]["code"] == "validation_failed"

    assert [answer.status_code for answer in refused] == [403, 403]
    assert {answer.json()["error"]["code"] for answer in refused} == {
        "role_not_allowed"
    }
    assert calls_after == calls_before
    assert ("verdict", "GET", f"/verdict-runs/{run_id}/steps") in behind_web.calls
    assert ("verdict", "GET", f"/cases/{case_id}/agent-steps") in behind_web.calls
