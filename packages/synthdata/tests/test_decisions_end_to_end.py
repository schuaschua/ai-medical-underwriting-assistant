"""Story 1.10: the customer discards or keeps a non-medical page, through `web`.

`web`, `workflow`, `intake` and `classification` as they really run, against
a real PostgreSQL, the Durable Task Scheduler emulator and the blob emulator
(`docker compose up --detach --wait`), with the stand-ins of this package
where Azure AI Language and the Foundry chat deployment would be, and a
transport where the Dapr sidecars would be.

The test is here and not under `services/` because it names the stand-ins'
package and reads the answer key, which nothing there may do (spine AD-17).
"""

import json
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

from workflow.settings import Settings

pytestmark = pytest.mark.integration

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}
OTHER = {"discard": "keep", "keep": "discard"}
LEAVES = {"discard": "discarded", "keep": "awaiting_triage"}
ACTION = {"discard": "page.discarded", "keep": "page.kept"}


def decide(
    web: TestClient, case_id: str, page_id: str, decision: str, role: dict[str, str]
) -> Any:
    return web.post(
        f"/api/cases/{case_id}/pages/{page_id}/decisions",
        json={"decision": decision},
        headers=role,
    )


def progress_of(web: TestClient, case_id: str) -> dict[str, Any]:
    progress: dict[str, Any] = web.get(
        f"/api/cases/{case_id}/progress", headers=CUSTOMER
    ).json()
    return progress


@pytest.mark.parametrize("decision", ["discard", "keep"])
def test_story_1_10_the_customer_discards_or_keeps_a_sure_non_medical_page_through_web(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    tmp_path: Path,
    decision: str,
) -> None:
    key = answer_key("case-002")
    expected_types = [page["page_type"] for page in key["pages"]]
    assert expected_types[3:] == ["invoice", "other", "other"]
    behind_workflow = ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(),
    )

    with workflow_service(workflow_service_settings, behind_workflow) as workflow:
        behind_web = ServicesBehindSidecar(
            intake=intake.app(),
            workflow=RunningService(workflow),
            classification=classification.app(),
        )
        with web_service(tmp_path, behind_web) as web:
            # The customer uploads the document and its case is started.
            uploaded = web.post(
                "/api/cases",
                content=(CASES_DIR / "case-002.pdf").read_bytes(),
                headers={**CUSTOMER, **PDF},
            )
            assert uploaded.status_code == 201
            case_id = uploaded.json()["case_id"]
            try:
                assert (
                    web.post(
                        f"/api/cases/{case_id}/start", headers=CUSTOMER
                    ).status_code
                    == 200
                )
                wait_for_case_status(workflow, case_id, "awaiting_human", 90)
                # Story 2.4: the pages the gate sent on are extracted while
                # the others wait for the customer.
                wait_for_extractions(workflow, case_id)
                waiting = progress_of(web, case_id)
                classifications = web.get(
                    f"/api/cases/{case_id}/classifications", headers=CUSTOMER
                ).json()

                # The stand-in is sure of every page (1.0): the three medical
                # pages went on, the three others wait for the customer.
                statuses = [page["page_status"] for page in waiting["pages"]]
                assert statuses == ["extracted"] * 3 + ["awaiting_customer"] * 3
                invoice, second, third = (
                    page["page_id"] for page in waiting["pages"][3:]
                )
                # What the prompt names comes from the server: type and confidence.
                listed = {
                    item["page_id"]: (item["page_type"], item["confidence"])
                    for item in classifications["classifications"]
                }
                assert [listed[page["page_id"]] for page in waiting["pages"][3:]] == [
                    ("invoice", 1.0),
                    ("other", 1.0),
                    ("other", 1.0),
                ]

                # Not the customer's to say, and not the underwriter's yet.
                refused = [
                    decide(web, case_id, invoice, "accept", CUSTOMER),
                    decide(web, case_id, invoice, decision, UNDERWRITER),
                    decide(web, case_id, invoice, "deny", UNDERWRITER),
                ]
                # The customer answers for the invoice, and the other way for
                # the next page.
                answered = decide(web, case_id, invoice, decision, CUSTOMER)
                other = decide(web, case_id, second, OTHER[decision], CUSTOMER)
                after_two = progress_of(web, case_id)
                # The same answer again, and the other answer for the same page.
                again = decide(web, case_id, invoice, decision, CUSTOMER)
                changed_mind = decide(web, case_id, invoice, OTHER[decision], CUSTOMER)

                # The last page that waits for the customer is discarded, and
                # the underwriter denies the one that was kept.
                kept = invoice if decision == "keep" else second
                assert (
                    decide(web, case_id, third, "discard", CUSTOMER).status_code == 200
                )
                before_underwriter = progress_of(web, case_id)
                assert (
                    decide(web, case_id, kept, "deny", UNDERWRITER).status_code == 200
                )
                ended = scheduler_client.wait_for_orchestration_completion(
                    case_id, timeout=90
                )
                final = progress_of(web, case_id)
                trail = web.get(
                    f"/api/cases/{case_id}/audit", headers=UNDERWRITER
                ).json()
            finally:
                # Also when the test fails on the way: no lifecycle is left waiting.
                end_lifecycle(scheduler_client, case_id)

    assert [(r.status_code, r.json()["error"]["code"]) for r in refused] == [
        # Accept is the underwriter's; keep and discard are the customer's.
        (403, "role_not_allowed"),
        (403, "role_not_allowed"),
        # Deny is the underwriter's, but the page does not wait for it.
        (409, "not_awaiting_decision"),
    ]
    assert answered.status_code == 200
    assert answered.json()["actor"] == "customer"
    assert answered.json()["page_status"] == LEAVES[decision]
    assert other.json()["page_status"] == LEAVES[OTHER[decision]]
    # One page discarded, one kept for the underwriter, one still waiting:
    # the case waits for a human.
    assert [page["page_status"] for page in after_two["pages"]] == [
        "extracted",
        "extracted",
        "extracted",
        LEAVES[decision],
        LEAVES[OTHER[decision]],
        "awaiting_customer",
    ]
    assert after_two["case_status"] == "awaiting_human"
    assert again.status_code == 200
    assert again.json() == answered.json()
    assert (changed_mind.status_code, changed_mind.json()["error"]["code"]) == (
        409,
        "not_awaiting_decision",
    )
    # The kept page waits for the underwriter, so the case still waits.
    assert before_underwriter["case_status"] == "awaiting_human"
    # No page waits any more and the three medical pages are extracted
    # (story 2.4): every page is final, and the case follows.
    assert [page["page_status"] for page in final["pages"]][:3] == ["extracted"] * 3
    assert sorted(page["page_status"] for page in final["pages"][3:]) == [
        "denied",
        "discarded",
        "discarded",
    ]
    assert final["case_status"] == "completed"
    assert ended is not None
    assert ended.runtime_status is OrchestrationStatus.COMPLETED
    assert json.loads(ended.serialized_output or "") == {
        "case_id": case_id,
        "case_status": "completed",
    }
    assert trail["events"][-1]["action"] == "case.completed"

    # The trail: the customer's two answers first, each a human's, about
    # its page, after that page's route.
    # The start is a person's too (story 1.13): the customer's, first of all.
    first_event = trail["events"][0]
    assert (first_event["action"], first_event["actor"]) == ("case.started", "customer")
    decided = [
        event
        for event in trail["events"]
        if event["actor_kind"] == "human" and event["action"] != "case.started"
    ]
    assert [
        (event["action"], event["actor"], event["page_id"]) for event in decided
    ] == [
        (ACTION[decision], "customer", invoice),
        (ACTION[OTHER[decision]], "customer", second),
        ("page.discarded", "customer", third),
        ("page.denied", "underwriter", kept),
    ]
    assert decided[0]["ref"] == answered.json()["decision_id"]
    assert [
        event["action"] for event in trail["events"] if event["page_id"] == invoice
    ][:3] == ["page.classified", "page.routed", ACTION[decision]]
    # `web` reached `workflow` for decisions through its one operation, and
    # `classification` only to read; the AI services were never asked to decide.
    assert {
        (app_id, method, path.rsplit("/", 1)[-1])
        for app_id, method, path in behind_web.calls
    } == {
        ("intake", "POST", "cases"),
        ("workflow", "POST", "start"),
        ("workflow", "GET", "progress"),
        ("workflow", "GET", "audit"),
        ("workflow", "POST", "decisions"),
        ("classification", "GET", "classifications"),
    }
    assert {
        (app_id, method, path.rsplit("/", 1)[-1])
        for app_id, method, path in behind_workflow.calls
    } == {
        ("intake", "POST", "redaction"),
        ("classification", "POST", "classifications"),
        # Story 2.4: one command per page that reached `extracting`.
        ("extraction", "POST", "fact-sets"),
        # Stories 2.5 and 2.6: one run once every page is final.
        ("verdict", "POST", "verdict-runs"),
    }
