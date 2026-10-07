"""Story 1.13: who started a case, that it was completed, and the underwriter's case list, through `web`.

`web`, `workflow`, `intake` and `classification` as they really run, against
a real PostgreSQL, the Durable Task Scheduler emulator and the blob emulator
(`docker compose up --detach --wait`), with the stand-ins of this package
where Azure AI Language and the Foundry chat deployment would be, and a
transport where the Dapr sidecars would be.

The test is here and not under `services/` because it names the stand-ins'
package and reads the answer key, which nothing there may do (spine AD-17).
"""

from pathlib import Path

import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from fastapi.testclient import TestClient
from synthdata_stack import (
    CASES_DIR,
    PDF,
    LocalClassification,
    LocalExtraction,
    LocalIntake,
    RunningService,
    ServicesBehindSidecar,
    answer_key,
    end_lifecycle,
    web_service,
    workflow_service,
)
from workflow_local import wait_for_case_status

from contracts.ids import new_id
from contracts.models.workflow import AuditTrail, CaseList
from synthdata.foundry_standin import Mode
from workflow.settings import Settings

pytestmark = pytest.mark.integration

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}


def upload(web: TestClient, name: str) -> str:
    uploaded = web.post(
        "/api/cases",
        content=(CASES_DIR / name).read_bytes(),
        headers={**CUSTOMER, **PDF},
    )
    assert uploaded.status_code == 201
    case_id: str = uploaded.json()["case_id"]
    return case_id


def case_list(web: TestClient) -> CaseList:
    response = web.get("/api/cases", headers=UNDERWRITER)
    assert response.status_code == 200
    return CaseList.model_validate(response.json())


def test_story_1_13_a_customers_case_run_to_its_last_decision_is_started_completed_and_listed(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    tmp_path: Path,
) -> None:
    # The stand-in's runs differ on every page: each is classified at 0.6,
    # so each waits for the underwriter, and denying them all ends the case.
    classification.model.mode = Mode.DISAGREE
    page_count = len(answer_key("case-001")["pages"])
    behind_workflow = ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
    )
    eval_run_id = new_id()
    started: list[str] = []

    with workflow_service(workflow_service_settings, behind_workflow) as workflow:
        behind_web = ServicesBehindSidecar(
            intake=intake.app(),
            workflow=RunningService(workflow),
            classification=classification.app(),
        )
        with web_service(tmp_path, behind_web) as web:
            try:
                nothing_yet = case_list(web)
                # The customer uploads a document and its case is started.
                case_id = upload(web, "case-001.pdf")
                started.append(case_id)
                first_start = web.post(f"/api/cases/{case_id}/start", headers=CUSTOMER)
                # A case of an eval run, started by the underwriter.
                eval_case_id = upload(web, "case-003.pdf")
                started.append(eval_case_id)
                eval_start = web.post(
                    f"/api/cases/{eval_case_id}/start",
                    json={"eval_run_id": eval_run_id, "stop_after": "gate"},
                    headers=UNDERWRITER,
                )
                waiting = wait_for_case_status(workflow, case_id, "awaiting_human", 90)
                wait_for_case_status(workflow, eval_case_id, "completed", 90)
                # The same start again, by the other role.
                again = web.post(f"/api/cases/{case_id}/start", headers=UNDERWRITER)
                while_waiting = case_list(web)
                refused = web.get("/api/cases", headers=CUSTOMER)
                no_role = web.get("/api/cases")

                # The underwriter denies every page: the last decision
                # completes the case.
                for page in waiting["pages"]:
                    decided = web.post(
                        f"/api/cases/{case_id}/pages/{page['page_id']}/decisions",
                        json={"decision": "deny"},
                        headers=UNDERWRITER,
                    )
                    assert decided.status_code == 200
                scheduler_client.wait_for_orchestration_completion(case_id, timeout=90)
                at_the_end = case_list(web)
                answered = web.get(f"/api/cases/{case_id}/audit", headers=UNDERWRITER)
                eval_answered = web.get(
                    f"/api/cases/{eval_case_id}/audit", headers=UNDERWRITER
                )
            finally:
                # Also when the test fails on the way: no lifecycle is left waiting.
                for started_case in started:
                    end_lifecycle(scheduler_client, started_case)

    assert (first_start.status_code, eval_start.status_code, again.status_code) == (
        200,
        200,
        200,
    )
    assert nothing_yet == CaseList(cases=[], has_more=False)
    assert [page["page_status"] for page in waiting["pages"]] == [
        "awaiting_triage"
    ] * page_count

    # The trail, read by the underwriter: it starts with the start, by the
    # customer who asked first, and ends with the completion.
    trail = AuditTrail.model_validate(answered.json())
    actions = [event.action.value for event in trail.events]
    assert (actions[0], actions[-1]) == ("case.started", "case.completed")
    assert (actions.count("case.started"), actions.count("case.completed")) == (1, 1)
    assert actions[-2] == "page.denied"
    started_event, completed_event = trail.events[0], trail.events[-1]
    assert (started_event.actor_kind.value, started_event.actor) == (
        "human",
        "customer",
    )
    assert (completed_event.actor_kind.value, completed_event.actor) == (
        "ai",
        "workflow:case-lifecycle",
    )
    for event in (started_event, completed_event):
        assert (event.page_id, event.ref, event.detail) == (None, case_id, None)
    assert trail.has_more is False

    # The underwriter's list: the case while it waited, and at the end with
    # its status. The eval-run case is in neither.
    (listed_waiting,) = while_waiting.cases
    assert (
        listed_waiting.case_id,
        listed_waiting.case_status.value,
        listed_waiting.page_count,
        listed_waiting.waiting_page_count,
    ) == (case_id, "awaiting_human", page_count, page_count)
    (listed_at_the_end,) = at_the_end.cases
    assert (
        listed_at_the_end.case_id,
        listed_at_the_end.case_status.value,
        listed_at_the_end.page_count,
        listed_at_the_end.waiting_page_count,
    ) == (case_id, "completed", page_count, 0)
    assert listed_at_the_end.started_at == started_event.occurred_at
    assert (while_waiting.has_more, at_the_end.has_more) == (False, False)

    # The eval-run case has its own two events all the same, with its run's id.
    eval_trail = AuditTrail.model_validate(eval_answered.json())
    assert (eval_trail.events[0].action.value, eval_trail.events[0].actor) == (
        "case.started",
        "underwriter",
    )
    assert eval_trail.events[-1].action.value == "case.completed"
    assert {event.eval_run_id for event in eval_trail.events} == {eval_run_id}

    # The customer is refused the list; `web` asked `workflow` only for the
    # underwriter.
    assert (refused.status_code, refused.json()["error"]["code"]) == (
        403,
        "role_not_allowed",
    )
    assert (no_role.status_code, no_role.json()["error"]["code"]) == (
        400,
        "invalid_role",
    )
    assert behind_web.calls.count(("workflow", "GET", "/cases")) == 3
