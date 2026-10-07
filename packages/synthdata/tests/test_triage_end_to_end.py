"""Story 1.11: the underwriter's triage queue, through `web`.

`web`, `workflow`, `intake` and `classification` as they really run, against
a real PostgreSQL, the Durable Task Scheduler emulator and the blob emulator
(`docker compose up --detach --wait`), with the stand-ins of this package
where Azure AI Language and the Foundry chat deployment would be, and a
transport where the Dapr sidecars would be.

The test is here and not under `services/` because it names the stand-ins'
package and reads the answer key, which nothing there may do (spine AD-17).
"""

from pathlib import Path
from typing import Any

import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.client import OrchestrationStatus
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
    answer_key,
    end_lifecycle,
    wait_for_extractions,
    web_service,
    workflow_service,
)
from workflow_local import wait_for_case_status

from contracts.ids import new_id
from contracts.models.web import TriageQueue
from synthdata.foundry_standin import Mode
from workflow.settings import Settings

pytestmark = pytest.mark.integration

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def decide(
    web: TestClient, case_id: str, page_id: str, decision: str, role: dict[str, str]
) -> Any:
    return web.post(
        f"/api/cases/{case_id}/pages/{page_id}/decisions",
        json={"decision": decision},
        headers=role,
    )


def upload(web: TestClient, name: str) -> str:
    uploaded = web.post(
        "/api/cases",
        content=(CASES_DIR / name).read_bytes(),
        headers={**CUSTOMER, **PDF},
    )
    assert uploaded.status_code == 201
    case_id: str = uploaded.json()["case_id"]
    return case_id


def triage_queue(web: TestClient) -> TriageQueue:
    response = web.get("/api/triage", headers=UNDERWRITER)
    assert response.status_code == 200
    return TriageQueue.model_validate(response.json())


def test_story_1_11_the_underwriter_accepts_and_denies_the_pages_of_the_triage_queue(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    tmp_path: Path,
) -> None:
    # The stand-in's runs differ on laboratory reports and identity documents
    # only: such a page is classified at 0.6, every other page at 1.0.
    classification.model.mode = Mode.MIXED
    assert [page["page_type"] for page in answer_key("case-002")["pages"]] == [
        "application_form",
        "attending_physician_statement",
        "lab_report",
        "invoice",
        "other",
        "other",
    ]
    assert answer_key("case-001")["pages"][2]["page_type"] == "lab_report"
    behind_workflow = ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(),
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
                # The customer's case, and a case that belongs to an eval run.
                case_id = upload(web, "case-002.pdf")
                started.append(case_id)
                assert (
                    web.post(
                        f"/api/cases/{case_id}/start", headers=CUSTOMER
                    ).status_code
                    == 200
                )
                eval_case_id = upload(web, "case-001.pdf")
                started.append(eval_case_id)
                assert (
                    web.post(
                        f"/api/cases/{eval_case_id}/start",
                        json={"eval_run_id": eval_run_id},
                        headers=UNDERWRITER,
                    ).status_code
                    == 200
                )
                wait_for_case_status(workflow, case_id, "awaiting_human", 90)
                # Story 2.4: the two pages the gate sent on are extracted
                # while the others wait.
                waiting = wait_for_extractions(workflow, case_id)
                eval_waiting = wait_for_case_status(
                    workflow, eval_case_id, "awaiting_human", 90
                )
                assert waiting["case_status"] == "awaiting_human"
                assert [page["page_status"] for page in waiting["pages"]] == [
                    "extracted",
                    "extracted",
                    "awaiting_triage",
                    "awaiting_customer",
                    "awaiting_customer",
                    "awaiting_customer",
                ]
                # The eval-run case has a page in triage as well.
                assert eval_waiting["pages"][2]["page_status"] == "awaiting_triage"
                unsure = waiting["pages"][2]["page_id"]
                invoice, fifth, sixth = (
                    page["page_id"] for page in waiting["pages"][3:]
                )

                before_keep = triage_queue(web)
                # The customer keeps the invoice and discards the two others.
                assert (
                    decide(web, case_id, invoice, "keep", CUSTOMER).status_code == 200
                )
                for page_id in (fifth, sixth):
                    assert (
                        decide(web, case_id, page_id, "discard", CUSTOMER).status_code
                        == 200
                    )

                queue = triage_queue(web)
                refused = web.get("/api/triage", headers=CUSTOMER)
                thumbnails = [
                    web.get(page.thumbnail_path, headers=UNDERWRITER)
                    for page in queue.pages
                ]
                with TestClient(intake.app()) as intake_client:
                    held = [
                        intake_client.get(f"/pages/{page.page_id}/thumbnail").content
                        for page in queue.pages
                    ]

                # The underwriter accepts the unsure page and denies the kept one.
                not_the_customers = decide(web, case_id, unsure, "accept", CUSTOMER)
                accepted = decide(web, case_id, unsure, "accept", UNDERWRITER)
                after_accept = triage_queue(web)
                # Decided in another tab: the same page, the other decision.
                elsewhere = decide(web, case_id, unsure, "deny", UNDERWRITER)
                denied = decide(web, case_id, invoice, "deny", UNDERWRITER)
                after = triage_queue(web)
                ended = scheduler_client.wait_for_orchestration_completion(
                    case_id, timeout=90
                )
                progress = web.get(
                    f"/api/cases/{case_id}/progress", headers=UNDERWRITER
                ).json()
                trail = web.get(
                    f"/api/cases/{case_id}/audit", headers=UNDERWRITER
                ).json()
            finally:
                # Also when the test fails on the way: no lifecycle is left
                # waiting. Nobody decides the eval-run case's page.
                for started_case in started:
                    end_lifecycle(scheduler_client, started_case)

    # Before the customer answers, only the page the gate was unsure of waits.
    assert [(page.page_id, page.queued_by) for page in before_keep.pages] == [
        (unsure, "gate")
    ]
    # Then the unsure page and the kept page, oldest waiting first, each with
    # what the classifier said. The eval-run case's page is not listed.
    assert [
        (
            page.case_id,
            page.page_id,
            page.page_number,
            page.page_type,
            page.is_medical,
            page.confidence,
            page.queued_by,
        )
        for page in queue.pages
    ] == [
        (case_id, unsure, 3, "lab_report", True, 0.6, "gate"),
        (case_id, invoice, 4, "invoice", False, 1.0, "customer"),
    ]
    assert queue.has_more is False
    assert all(page.reason for page in queue.pages)
    assert eval_case_id not in {page.case_id for page in queue.pages}
    # The customer role is refused the queue.
    assert (refused.status_code, refused.json()["error"]["code"]) == (
        403,
        "role_not_allowed",
    )
    # Each thumbnail is the PNG `intake` holds for that redacted page.
    for response, stored in zip(thumbnails, held, strict=True):
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.content.startswith(PNG_SIGNATURE)
        assert response.content == stored

    assert (
        not_the_customers.status_code,
        not_the_customers.json()["error"]["code"],
    ) == (403, "role_not_allowed")
    assert (accepted.status_code, accepted.json()["page_status"]) == (200, "extracting")
    assert accepted.json()["actor"] == "underwriter"
    # After a decision the page leaves the queue.
    assert [page.page_id for page in after_accept.pages] == [invoice]
    assert (elsewhere.status_code, elsewhere.json()["error"]["code"]) == (
        409,
        "not_awaiting_decision",
    )
    assert (denied.status_code, denied.json()["page_status"]) == (200, "denied")
    # Accepting one and denying the other empties the queue.
    assert (after.pages, after.has_more) == ([], False)

    # The accepted page is extracted like the two the gate sent on (story
    # 2.4); with every page final the case is completed.
    assert progress["case_status"] == "completed"
    assert [page["page_status"] for page in progress["pages"]] == [
        "extracted",
        "extracted",
        "extracted",
        "denied",
        "discarded",
        "discarded",
    ]
    assert ended is not None
    assert ended.runtime_status is OrchestrationStatus.COMPLETED
    # The trail holds the underwriter's two decisions, each a human's.
    by_underwriter = [
        (event["action"], event["actor_kind"], event["page_id"], event["ref"])
        for event in trail["events"]
        if event["actor"] == "underwriter"
    ]
    assert by_underwriter == [
        ("page.accepted", "human", unsure, accepted.json()["decision_id"]),
        ("page.denied", "human", invoice, denied.json()["decision_id"]),
    ]
    # `web` read the queue from `workflow`, the readings from `classification`
    # and the thumbnails from `intake`; decisions went through the one operation.
    assert {
        (app_id, method, path.rsplit("/", 1)[-1])
        for app_id, method, path in behind_web.calls
    } == {
        ("intake", "POST", "cases"),
        ("intake", "GET", "thumbnail"),
        ("workflow", "POST", "start"),
        ("workflow", "GET", "pages"),
        ("workflow", "GET", "progress"),
        ("workflow", "GET", "audit"),
        ("workflow", "POST", "decisions"),
        ("classification", "GET", "classifications"),
    }
    # Once per case with a waiting page, per read of the queue: never the
    # eval-run case, whose pages are not in the queue.
    assert set(behind_web.paths("classification")) == {
        f"/cases/{case_id}/classifications"
    }
