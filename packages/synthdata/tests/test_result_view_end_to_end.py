"""Story 2.7: what the underwriter's result view reads, through `web`.

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

import pymupdf
import pytest
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
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

from contracts.enums import StageStatus, Verdict
from contracts.models.extraction import FactList
from contracts.models.intake import PageBoxes, PageList
from contracts.models.retrieval import RuleText
from contracts.models.verdict import VerdictRunList
from workflow.settings import Settings

pytestmark = pytest.mark.integration

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}


def test_story_2_7_the_underwriter_reads_a_cases_result_through_web_and_a_quotes_boxes_lie_on_its_page(
    workflow_service_settings: Settings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    tmp_path: Path,
) -> None:
    original = (CASES_DIR / "case-001.pdf").read_bytes()
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
            extraction=extraction.app(),
            verdict=verdict.app(),
            retrieval=verdict.retrieval.app(),
        )
        with web_service(tmp_path, behind_web) as web:
            try:
                uploaded = web.post(
                    "/api/cases", content=original, headers={**CUSTOMER, **PDF}
                )
                assert uploaded.status_code == 201
                case_id: str = uploaded.json()["case_id"]
                document_id: str = uploaded.json()["document_id"]
                file_path = f"/api/documents/{document_id}/file"

                # AD-21: until the document is redacted there is no page and
                # no file to show; the original is never the answer.
                no_pages_yet = web.get(
                    f"/api/cases/{case_id}/pages", headers=UNDERWRITER
                )
                not_redacted = web.get(file_path, headers=UNDERWRITER)

                started.append(case_id)
                assert (
                    web.post(
                        f"/api/cases/{case_id}/start", headers=CUSTOMER
                    ).status_code
                    == 200
                )
                completed(scheduler_client, case_id)

                # Every route the result view reads, as the underwriter.
                pages_answer = web.get(
                    f"/api/cases/{case_id}/pages", headers=UNDERWRITER
                )
                file_answer = web.get(file_path, headers=UNDERWRITER)
                facts_answer = web.get(
                    f"/api/cases/{case_id}/facts", headers=UNDERWRITER
                )
                runs_answer = web.get(
                    f"/api/cases/{case_id}/verdict-runs", headers=UNDERWRITER
                )
                facts = FactList.model_validate(facts_answer.json())
                runs = VerdictRunList.model_validate(runs_answer.json())
                rules = {
                    reason.rule_id: web.get(
                        f"/api/rules/{reason.rule_id}", headers=UNDERWRITER
                    )
                    for run in runs.verdict_runs
                    for reason in run.reasons
                }
                unknown_rule = web.get("/api/rules/UW-ZZ-999", headers=UNDERWRITER)
                boxes = {
                    fact.fact_id: web.get(
                        f"/api/pages/{fact.page_id}/boxes",
                        params={
                            "quote_start": fact.quote_start,
                            "quote_end": fact.quote_end,
                        },
                        headers=UNDERWRITER,
                    )
                    for fact in facts.facts
                    if fact.quote_verified
                }

                # AD-9: the customer is refused every one of them by `web`
                # itself, which asks no service.
                calls_before = len(behind_web.calls)
                a_fact = facts.facts[0]
                boxes_path = (
                    f"/api/pages/{a_fact.page_id}/boxes"
                    f"?quote_start={a_fact.quote_start}&quote_end={a_fact.quote_end}"
                )
                refused = [
                    web.get(path, headers=CUSTOMER)
                    for path in (
                        f"/api/cases/{case_id}/pages",
                        file_path,
                        f"/api/cases/{case_id}/facts",
                        f"/api/cases/{case_id}/verdict-runs",
                        f"/api/rules/{next(iter(rules))}",
                        boxes_path,
                    )
                ]
                calls_after = len(behind_web.calls)
            finally:
                for case in started:
                    end_lifecycle(scheduler_client, case)

    # Before redaction: an empty page list, and `not_redacted` for the file.
    assert no_pages_yet.status_code == 200
    assert PageList.model_validate(no_pages_yet.json()).pages == []
    assert not_redacted.status_code == 409
    assert not_redacted.json()["error"]["code"] == "not_redacted"

    # The pages, and the redacted PDF: a PDF of as many pages, served as one,
    # with the headers every response has, and not the file that was uploaded.
    pages = PageList.model_validate(pages_answer.json())
    assert [page.page_number for page in pages.pages] == [1, 2, 3]
    assert {page.document_id for page in pages.pages} == {document_id}
    assert file_answer.status_code == 200
    assert file_answer.headers["content-type"] == "application/pdf"
    assert file_answer.headers["x-content-type-options"] == "nosniff"
    assert file_answer.headers["cache-control"] == "no-store"
    assert file_answer.content.startswith(b"%PDF-")
    assert file_answer.content != original
    with pymupdf.open(  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        stream=file_answer.content, filetype="pdf"
    ) as redacted:
        page_sizes = {
            number: (page.rect.width, page.rect.height)
            for number, page in enumerate(redacted, start=1)
        }
    assert sorted(page_sizes) == [1, 2, 3]

    # The facts and the run, as their owners hold them: a suggestion with
    # its label, whose every reason cites facts of the case.
    assert facts.facts and all(fact.quote_verified for fact in facts.facts)
    assert facts == extraction.facts(case_id)
    (run,) = runs.verdict_runs
    assert run == verdict.runs(case_id).verdict_runs[0]
    assert run.label == "AI suggestion, not a decision"
    assert (run.status, run.verdict, run.loading_pct) == (
        StageStatus.DONE,
        Verdict.LOADED,
        50,
    )
    case_facts = {fact.fact_id for fact in facts.facts}
    assert run.reasons and all(
        set(reason.fact_ids) <= case_facts for reason in run.reasons
    )

    # Each cited rule opens the manual's own text of it; a rule the manual
    # does not hold is 404.
    assert sorted(rules) == ["UW-DM-002"]
    for rule_id, answer in rules.items():
        assert answer.status_code == 200
        rule = RuleText.model_validate(answer.json())
        assert rule.rule_id == rule_id
        assert f"Rule {rule_id}:" in rule.text
        assert rule.impairment and rule.manual_page >= 1
    assert unknown_rule.status_code == 404
    assert unknown_rule.json()["error"]["code"] == "not_found"

    # AD-14: a verified fact's offsets give boxes that lie inside its page,
    # on a page of the size the redacted PDF really has, and that span the
    # quote from its first character to its last.
    assert boxes
    for fact in facts.facts:
        answer = boxes[fact.fact_id]
        assert answer.status_code == 200, fact.statement
        page_boxes = PageBoxes.model_validate(answer.json())
        assert page_boxes.page_number == fact.page_number
        assert (page_boxes.page_width, page_boxes.page_height) == pytest.approx(
            page_sizes[fact.page_number]
        )
        assert page_boxes.boxes, fact.statement
        for box in page_boxes.boxes:
            assert 0 <= box.x0 < box.x1 <= page_boxes.page_width
            assert 0 <= box.y0 < box.y1 <= page_boxes.page_height
        assert fact.quote_start is not None and fact.quote_end is not None
        first = min(page_boxes.boxes, key=lambda box: box.char_start)
        last = max(page_boxes.boxes, key=lambda box: box.char_end)
        assert first.char_start <= fact.quote_start < first.char_end
        assert last.char_start < fact.quote_end <= last.char_end

    # The customer: refused, with nothing asked of any service.
    assert [answer.status_code for answer in refused] == [403] * 6
    assert {answer.json()["error"]["code"] for answer in refused} == {
        "role_not_allowed"
    }
    assert calls_after == calls_before
    assert not any(answer.content.startswith(b"%PDF") for answer in refused)

    # `web` read each part from the one service that owns it.
    assert ("extraction", "GET", f"/cases/{case_id}/facts") in behind_web.calls
    assert ("verdict", "GET", f"/cases/{case_id}/verdict-runs") in behind_web.calls
    assert ("retrieval", "GET", "/rules/UW-DM-002") in behind_web.calls
    assert ("intake", "GET", f"/documents/{document_id}/file") in behind_web.calls
    assert ("intake", "GET", f"/pages/{a_fact.page_id}/boxes") in behind_web.calls
