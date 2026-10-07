"""Story 1.12: a case's audit trail, read as the underwriter through `web`.

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
from fastapi.testclient import TestClient
from synthdata_stack import (
    CASES_DIR,
    PDF,
    LocalClassification,
    LocalIntake,
    RunningService,
    ServicesBehindSidecar,
    answer_key,
    end_lifecycle,
    web_service,
    workflow_service,
)
from workflow_local import wait_for_case_status

from contracts.audit import AuditAction, AuditRecord, RouteDetail
from contracts.decisions import DECISION_RULES
from contracts.enums import ActorKind, PageStatus
from contracts.ids import new_id
from contracts.models.workflow import AuditTrail, CaseProgress
from synthdata.foundry_standin import LOCAL_DEPLOYMENT, Mode
from workflow.domain.transitions import PAGE_TRANSITIONS
from workflow.settings import Settings

pytestmark = pytest.mark.integration

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}
REDACTION_ACTOR = "intake:azure-ai-language"
CLASSIFIER_ACTOR = f"classification:{LOCAL_DEPLOYMENT}"
GATE_ACTOR = "workflow:gate"
# The page status each human action leaves its page in (AD-10).
STATUS_AFTER_DECISION = {rule.action: rule.leaves for rule in DECISION_RULES.values()}


def decide(
    web: TestClient, case_id: str, page_id: str, decision: str, role: dict[str, str]
) -> Any:
    return web.post(
        f"/api/cases/{case_id}/pages/{page_id}/decisions",
        json={"decision": decision},
        headers=role,
    )


def status_after(event: AuditRecord) -> PageStatus:
    """The page status an event about a page reports (AD-8)."""
    if event.action is AuditAction.PAGE_CLASSIFIED:
        return PageStatus.CLASSIFIED
    if event.action is AuditAction.PAGE_ROUTED:
        assert isinstance(event.detail, RouteDetail)
        return PageStatus(event.detail.route)
    if event.action is AuditAction.STAGE_FAILED:
        return PageStatus.FAILED
    return STATUS_AFTER_DECISION[event.action]


def test_story_1_12_the_underwriter_reads_the_whole_trail_of_a_decided_case_in_causal_order(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
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
    behind_workflow = ServicesBehindSidecar(
        intake=intake.app(), classification=classification.app()
    )
    started: list[str] = []

    with workflow_service(workflow_service_settings, behind_workflow) as workflow:
        behind_web = ServicesBehindSidecar(
            intake=intake.app(),
            workflow=RunningService(workflow),
            classification=classification.app(),
        )
        with web_service(tmp_path, behind_web) as web:
            try:
                uploaded = web.post(
                    "/api/cases",
                    content=(CASES_DIR / "case-002.pdf").read_bytes(),
                    headers={**CUSTOMER, **PDF},
                )
                assert uploaded.status_code == 201
                case_id: str = uploaded.json()["case_id"]
                # Before the case is started nobody has a trail for it.
                not_started = web.get(
                    f"/api/cases/{case_id}/audit", headers=UNDERWRITER
                )
                started.append(case_id)
                assert (
                    web.post(
                        f"/api/cases/{case_id}/start", headers=CUSTOMER
                    ).status_code
                    == 200
                )
                waiting = wait_for_case_status(workflow, case_id, "awaiting_human", 90)
                page_ids = [page["page_id"] for page in waiting["pages"]]
                unsure, invoice, fifth, sixth = page_ids[2:]

                # One page discarded, one kept and then accepted; the two
                # others that wait are decided too, so the lifecycle ends.
                for page_id, decision, role in (
                    (fifth, "discard", CUSTOMER),
                    (invoice, "keep", CUSTOMER),
                    (invoice, "accept", UNDERWRITER),
                    (sixth, "discard", CUSTOMER),
                    (unsure, "deny", UNDERWRITER),
                ):
                    assert (
                        decide(web, case_id, page_id, decision, role).status_code == 200
                    )
                scheduler_client.wait_for_orchestration_completion(case_id, timeout=90)
                answered = web.get(f"/api/cases/{case_id}/audit", headers=UNDERWRITER)
                refused = web.get(f"/api/cases/{case_id}/audit", headers=CUSTOMER)
                unknown = web.get(f"/api/cases/{new_id()}/audit", headers=UNDERWRITER)
                progress = CaseProgress.model_validate(
                    web.get(
                        f"/api/cases/{case_id}/progress", headers=UNDERWRITER
                    ).json()
                )
            finally:
                # Also when the test fails on the way: no lifecycle is left waiting.
                for started_case in started:
                    end_lifecycle(scheduler_client, started_case)

    assert (not_started.status_code, not_started.json()["error"]["code"]) == (
        404,
        "not_found",
    )
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "not_found")
    # The customer is refused the trail.
    assert (refused.status_code, refused.json()["error"]["code"]) == (
        403,
        "role_not_allowed",
    )
    assert answered.status_code == 200
    trail = AuditTrail.model_validate(answered.json())
    assert (trail.case_id, trail.has_more) == (case_id, False)
    events = [
        (event.action.value, event.page_id, event.actor_kind, event.actor)
        for event in trail.events
    ]

    # The redaction first, by `intake` with Azure AI Language: counts per
    # category of what was redacted, and no value.
    assert events[0] == ("document.redacted", None, ActorKind.AI, REDACTION_ACTOR)
    counts = trail.events[0].detail
    assert isinstance(counts, dict) and counts
    assert all(isinstance(count, int) for count in counts.values())
    # Every expected event, each once: a classification and a route per
    # page, and the five decisions.
    ai_page_events = [
        (action, page_id, ActorKind.AI, actor)
        for page_id in page_ids
        for action, actor in (
            ("page.classified", CLASSIFIER_ACTOR),
            ("page.routed", GATE_ACTOR),
        )
    ]
    decisions = [
        ("page.discarded", fifth, ActorKind.HUMAN, "customer"),
        ("page.kept", invoice, ActorKind.HUMAN, "customer"),
        ("page.accepted", invoice, ActorKind.HUMAN, "underwriter"),
        ("page.discarded", sixth, ActorKind.HUMAN, "customer"),
        ("page.denied", unsure, ActorKind.HUMAN, "underwriter"),
    ]
    assert sorted(events[1:]) == sorted(ai_page_events + decisions)
    # The decisions are in the order they were made.
    assert [event for event in events if event[2] is ActorKind.HUMAN] == decisions
    # Causal order, page by page: its classification, its route, then what
    # people decided about it.
    chains = {
        page_id: [action for action, page, _, _ in events if page == page_id]
        for page_id in page_ids
    }
    assert chains == {
        page_ids[0]: ["page.classified", "page.routed"],
        page_ids[1]: ["page.classified", "page.routed"],
        unsure: ["page.classified", "page.routed", "page.denied"],
        invoice: ["page.classified", "page.routed", "page.kept", "page.accepted"],
        fifth: ["page.classified", "page.routed", "page.discarded"],
        sixth: ["page.classified", "page.routed", "page.discarded"],
    }
    # A route refers to the classification it followed, and names the
    # threshold the gate used.
    classified_refs = {
        event.page_id: event.ref
        for event in trail.events
        if event.action is AuditAction.PAGE_CLASSIFIED
    }
    for event in trail.events:
        if event.action is AuditAction.PAGE_ROUTED:
            assert event.ref == classified_refs[event.page_id]
            assert isinstance(event.detail, RouteDetail)
            assert event.detail.threshold == 0.9
    assert all(event.error_code is None for event in trail.events)

    # Every page status change has a matching event: walked from `uploaded`
    # (the redaction's event), the events lead each page, by allowed changes
    # only, to the status the case's progress reports for it.
    walked = {page_id: [PageStatus.UPLOADED] for page_id in page_ids}
    for event in trail.events[1:]:
        assert event.page_id is not None
        history = walked[event.page_id]
        after = status_after(event)
        assert after in PAGE_TRANSITIONS[history[-1]]
        history.append(after)
    assert {page.page_id: page.page_status for page in progress.pages} == {
        page_id: history[-1] for page_id, history in walked.items()
    }
    assert [status.value for status in walked[invoice]] == [
        "uploaded",
        "classified",
        "awaiting_customer",
        "awaiting_triage",
        "extracting",
    ]
    assert [history[-1].value for history in walked.values()] == [
        "extracting",
        "extracting",
        "denied",
        "extracting",
        "discarded",
        "discarded",
    ]
    # `web` read the trail from `workflow`, as the underwriter only.
    assert behind_web.paths("workflow").count(f"/cases/{case_id}/audit") == 2
