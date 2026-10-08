"""Stories 2.7, 2.8, 3.4 and 3.6: `web` passes the result view's six reads, the two reads of the agent's log, the bake-off runner's page text and eval search and Compare's request for one more verdict run on to the services that own them, and answers Compare's pairs of rows from its settings.

The Dapr sidecar is a fake here: a transport that records what `web` sent and
answers as `extraction`, `verdict`, `retrieval`, `intake` and `workflow` would.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.ids import new_id
from contracts.models.extraction import FactList
from contracts.models.intake import PageBoxes, PageList
from contracts.models.retrieval import RuleText, SearchResponse
from contracts.models.verdict import SUGGESTION_LABEL, VerdictRunList
from contracts.models.workflow import VerdictRunRequested
from web.adapters.http.app import create_app
from web.settings import Settings

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
# The first bytes of any PDF file; the rest is no document and need not be.
REDACTED_PDF = b"%PDF-1.7\nsynthetic redacted document"
RULE_ID = "UW-DM-002"
# Story 3.4: the eval search, as the bake-off runner sends it and `retrieval` answers it.
SEARCH = {"query": "HbA1c 7.4 %", "retriever_config": "r1", "top_k": 5}
SEARCH_ANSWER = {
    "retriever_config": "r1",
    "latency_ms": 17,
    "items": [
        {
            "chunk_id": "fixed-0031",
            "rule_ids": [RULE_ID],
            "rank": 1,
            "score": 0.81,
            "text": f"Rule {RULE_ID}: HbA1c from 7.0 % to 7.9 %: +50 %.",
            "manual_page": 31,
            "impairment": "Type 2 diabetes mellitus",
        }
    ],
}


# Story 3.6: the pairs of rows of the Compare toggle, as `web` answers them unset.
COMPARE_PAIRS = {
    "default_pair": {"first": "r4", "second": "r5"},
    "fallback_pair": {"first": "r3", "second": "r5"},
}


def refusal(code: ErrorCode, message: str, status: int | None = None) -> httpx.Response:
    failure = DomainError(code, message)
    return httpx.Response(
        status or failure.http_status,
        json=failure.to_body(TRACE_ID).model_dump(mode="json"),
    )


@dataclass
class Result:
    """One case as its owners hold it: a document of one page, one fact on it, one run."""

    case_id: str = field(default_factory=new_id)
    document_id: str = field(default_factory=new_id)
    page_id: str = field(default_factory=new_id)
    fact_id: str = field(default_factory=new_id)
    run_id: str = field(default_factory=new_id)

    def steps(self) -> dict[str, Any]:
        """The agent's log as `verdict` answers it: a refused read of a rule, a tool that does not exist, and more to come."""
        return {
            "steps": [
                {
                    "verdict_run_id": self.run_id,
                    "case_id": self.case_id,
                    "step_no": 3,
                    "tool": "read_rule",
                    "asked_tool": None,
                    "arguments": {"rule_id": RULE_ID},
                    "fact_id": None,
                    "rule_ids": [],
                    "outcome": "refused",
                    "error_code": "rule_not_seen",
                    "latency_ms": 2,
                    "occurred_at": "2026-10-08T09:00:00Z",
                },
                {
                    # Owner, 2026-10-08: the model asked for a tool that
                    # does not exist; the name is passed on as text.
                    "verdict_run_id": self.run_id,
                    "case_id": self.case_id,
                    "step_no": 4,
                    "tool": None,
                    "asked_tool": "delete_case",
                    "arguments": {},
                    "fact_id": None,
                    "rule_ids": [],
                    "outcome": "refused",
                    "error_code": "not_found",
                    "latency_ms": 0,
                    "occurred_at": "2026-10-08T09:00:01Z",
                },
            ],
            "has_more": True,
        }

    def run_requested(self) -> dict[str, Any]:
        """What `workflow` answers a request for one more run with on `r5`: under way."""
        return {
            "case_id": self.case_id,
            "retriever_config": "r5",
            "status": "running",
            "verdict_run_id": None,
            "error_code": None,
        }

    def answers(self) -> dict[tuple[str, str], Any]:
        """What each owner answers, by app id and path."""
        return {
            ("verdict", f"/verdict-runs/{self.run_id}/steps"): self.steps(),
            ("verdict", f"/cases/{self.case_id}/agent-steps"): self.steps(),
            ("extraction", f"/cases/{self.case_id}/facts"): {
                "case_id": self.case_id,
                "facts": [
                    {
                        "fact_id": self.fact_id,
                        "case_id": self.case_id,
                        "page_id": self.page_id,
                        "page_number": 1,
                        "statement": "HbA1c 7.4 %",
                        "quote": "HbA1c\n7.4\n%",
                        "quote_verified": True,
                        "quote_start": 120,
                        "quote_end": 131,
                    }
                ],
            },
            ("verdict", f"/cases/{self.case_id}/verdict-runs"): {
                "case_id": self.case_id,
                "verdict_runs": [
                    {
                        "verdict_run_id": self.run_id,
                        "case_id": self.case_id,
                        "retriever_config": "r3",
                        "status": "done",
                        "label": SUGGESTION_LABEL,
                        "verdict": "loaded",
                        "loading_pct": 50,
                        "confidence": 0.9,
                        "reasons": [
                            {
                                "rule_id": RULE_ID,
                                "fact_ids": [self.fact_id],
                                "effect": "debit",
                                "debit_pct": 50,
                            }
                        ],
                        "system_reasons": [],
                        "error_code": None,
                    }
                ],
                "has_more": False,
            },
            ("retrieval", f"/rules/{RULE_ID}"): {
                "rule_id": RULE_ID,
                "chunk_id": f"smart-{RULE_ID}",
                "chunk_set": "smart",
                "text": f"Rule {RULE_ID}: HbA1c from 7.0 % to 7.9 %: +50 %.",
                "manual_page": 31,
                "impairment": "Type 2 diabetes mellitus",
                "reference_rule_ids": ["UW-DM-001"],
            },
            ("intake", f"/cases/{self.case_id}/pages"): {
                "case_id": self.case_id,
                "pages": [
                    {
                        "page_id": self.page_id,
                        "case_id": self.case_id,
                        "document_id": self.document_id,
                        "page_number": 1,
                    }
                ],
            },
            ("intake", f"/pages/{self.page_id}/boxes"): {
                "page_id": self.page_id,
                "page_number": 1,
                "page_width": 612.0,
                "page_height": 792.0,
                "boxes": [
                    {
                        "char_start": 120,
                        "char_end": 125,
                        "x0": 72.0,
                        "y0": 300.0,
                        "x1": 110.5,
                        "y1": 312.0,
                    }
                ],
            },
            ("intake", f"/documents/{self.document_id}/file"): httpx.Response(
                200, content=REDACTED_PDF, headers={"Content-Type": "application/pdf"}
            ),
            ("intake", f"/pages/{self.page_id}/text"): {
                "page_id": self.page_id,
                "page_number": 1,
                "text": "Applicant: [Person]\nHbA1c\n7.4\n%",
            },
            ("retrieval", "/searches"): SEARCH_ANSWER,
        }


@dataclass
class FakeSidecar:
    """Stands in for the Dapr sidecar and, behind it, the four services `web` reads."""

    answers: dict[tuple[str, str], Any] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        _, _, _, app_id, _, *rest = request.url.path.split("/")
        answer = self.answers.get((app_id, "/" + "/".join(rest)))
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        if answer is None:
            return refusal(ErrorCode.NOT_FOUND, "Not found.")
        return httpx.Response(200, json=answer)


@pytest.fixture
def sidecar() -> FakeSidecar:
    return FakeSidecar()


@pytest.fixture
def client(settings: Settings, sidecar: FakeSidecar) -> Iterator[TestClient]:
    app = create_app(settings, sidecar=httpx.MockTransport(sidecar))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def error_of(response: Any) -> tuple[int, str]:
    return response.status_code, ErrorBody.model_validate(
        response.json()
    ).error.code.value


def routes(result: Result) -> dict[tuple[str, str], str]:
    """Each owner's operation, and where `web` serves it."""
    return {
        ("extraction", f"/cases/{result.case_id}/facts"): (
            f"/api/cases/{result.case_id}/facts"
        ),
        ("verdict", f"/cases/{result.case_id}/verdict-runs"): (
            f"/api/cases/{result.case_id}/verdict-runs"
        ),
        ("retrieval", f"/rules/{RULE_ID}"): f"/api/rules/{RULE_ID}",
        ("intake", f"/cases/{result.case_id}/pages"): (
            f"/api/cases/{result.case_id}/pages"
        ),
        ("intake", f"/pages/{result.page_id}/boxes"): (
            f"/api/pages/{result.page_id}/boxes?quote_start=120&quote_end=131"
        ),
        ("intake", f"/documents/{result.document_id}/file"): (
            f"/api/documents/{result.document_id}/file"
        ),
        # Story 2.8: the agent's log, with its filters and its cursor.
        ("verdict", f"/verdict-runs/{result.run_id}/steps"): (
            f"/api/verdict-runs/{result.run_id}/steps"
            f"?tool=read_rule&rule_id={RULE_ID}&after_step_no=2"
        ),
        ("verdict", f"/cases/{result.case_id}/agent-steps"): (
            f"/api/cases/{result.case_id}/agent-steps?tool=search_rules"
            f"&after_verdict_run_id={result.run_id}&after_step_no=2"
        ),
        # Story 3.4: a page's stored text, for the runner's redaction check.
        ("intake", f"/pages/{result.page_id}/text"): (
            f"/api/pages/{result.page_id}/text"
        ),
    }


def test_story_2_7_the_underwriter_reads_each_part_of_a_result_from_its_owner_and_the_customer_none(
    client: TestClient,
    sidecar: FakeSidecar,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = Result()
    sidecar.answers = result.answers()

    # AD-9: the result view is the underwriter's. `web` refuses the customer
    # itself, and a request without a role, and asks no service.
    for path in routes(result).values():
        assert error_of(client.get(path, headers=CUSTOMER)) == (
            403,
            "role_not_allowed",
        )
        assert error_of(client.get(path)) == (400, "invalid_role")
    assert sidecar.requests == []

    for (app_id, owner_path), path in routes(result).items():
        response = client.get(path, headers={**UNDERWRITER, "traceparent": TRACEPARENT})

        assert response.status_code == 200, path
        asked = sidecar.requests[-1]
        # One operation of the one owner, the same resource path, and the
        # query as it was asked.
        assert (asked.method, asked.url.path) == (
            "GET",
            f"/v1.0/invoke/{app_id}/method{owner_path}",
        )
        assert asked.url.query.decode() == path.partition("?")[2]
        assert asked.headers["traceparent"] == TRACEPARENT
        assert "x-demo-role" not in asked.headers
        # The PDF has a longer deadline of its own; every other read a JSON call's.
        assert asked.extensions["timeout"]["read"] == (
            60.0 if owner_path.endswith("/file") else 20.0
        )
        answered = sidecar.answers[(app_id, owner_path)]
        if isinstance(answered, httpx.Response):
            # The redacted PDF, byte for byte, as a PDF.
            assert response.content == REDACTED_PDF
            assert response.headers["content-type"] == "application/pdf"
        else:
            # Passed on as it is: `web` adds, drops and works out nothing.
            assert response.json() == answered
        # The response headers every route has.
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"]
    assert len(sidecar.requests) == len(routes(result))

    # Story 3.4: the eval search is `retrieval`'s search passed through, with
    # the row it names, for the underwriter only.
    assert error_of(client.post("/api/searches", json=SEARCH, headers=CUSTOMER)) == (
        403,
        "role_not_allowed",
    )
    assert len(sidecar.requests) == len(routes(result))
    searched = client.post("/api/searches", json=SEARCH, headers=UNDERWRITER)
    asked = sidecar.requests.pop()
    assert (asked.method, asked.url.path) == (
        "POST",
        "/v1.0/invoke/retrieval/method/searches",
    )
    assert json.loads(asked.content) == SEARCH
    assert searched.status_code == 200 and searched.json() == SEARCH_ANSWER
    assert SearchResponse.model_validate(searched.json()).latency_ms == 17

    # Story 3.6: the pairs of Compare are `web`'s setting, answered as they
    # are with no service asked: `r4` and `r5`, falling back to `r3` and `r5`.
    asked_so_far = len(sidecar.requests)
    assert error_of(client.get("/api/compare-pairs", headers=CUSTOMER))[0] == 403
    pairs = client.get("/api/compare-pairs", headers=UNDERWRITER)
    assert pairs.status_code == 200 and pairs.json() == COMPARE_PAIRS
    monkeypatch.setenv("WEB_COMPARE_PAIR", '["r3","r5"]')
    monkeypatch.setenv("WEB_COMPARE_FALLBACK_PAIR", '["r1","r2"]')
    with TestClient(create_app(Settings(spa_dir=settings.spa_dir))) as other:
        assert other.get("/api/compare-pairs", headers=UNDERWRITER).json() == {
            "default_pair": {"first": "r3", "second": "r5"},
            "fallback_pair": {"first": "r1", "second": "r2"},
        }
    # A pair is two rows: one row twice is refused when the service starts.
    monkeypatch.setenv("WEB_COMPARE_PAIR", '["r5","r5"]')
    with pytest.raises(ValueError, match="two different rows"):
        Settings()

    # Story 3.6: the request for one more verdict run is `workflow`'s
    # operation passed on, with the row the body names, for the underwriter
    # only. The run list of the same path is still read from `verdict`.
    runs_path = f"/api/cases/{result.case_id}/verdict-runs"
    wanted = {"retriever_config": "r5"}
    assert error_of(client.post(runs_path, json=wanted, headers=CUSTOMER)) == (
        403,
        "role_not_allowed",
    )
    assert len(sidecar.requests) == asked_so_far
    sidecar.answers["workflow", f"/cases/{result.case_id}/verdict-runs"] = (
        result.run_requested()
    )
    requested = client.post(
        runs_path, json=wanted, headers={**UNDERWRITER, "traceparent": TRACEPARENT}
    )
    asked = sidecar.requests.pop()
    assert (asked.method, asked.url.path) == (
        "POST",
        f"/v1.0/invoke/workflow/method/cases/{result.case_id}/verdict-runs",
    )
    assert json.loads(asked.content) == wanted
    assert asked.headers["traceparent"] == TRACEPARENT
    assert requested.status_code == 200
    assert requested.json() == result.run_requested()
    assert VerdictRunRequested.model_validate(requested.json()).verdict_run_id is None

    # Each answer is the contract's shape, and the verdict carries its label.
    read = {
        owner: client.get(path, headers=UNDERWRITER)
        for owner, path in routes(result).items()
    }
    facts = FactList.model_validate(
        read["extraction", f"/cases/{result.case_id}/facts"].json()
    )
    runs = VerdictRunList.model_validate(
        read["verdict", f"/cases/{result.case_id}/verdict-runs"].json()
    )
    rule = RuleText.model_validate(read["retrieval", f"/rules/{RULE_ID}"].json())
    pages = PageList.model_validate(
        read["intake", f"/cases/{result.case_id}/pages"].json()
    )
    boxes = PageBoxes.model_validate(
        read["intake", f"/pages/{result.page_id}/boxes"].json()
    )
    assert runs.verdict_runs[0].label == "AI suggestion, not a decision"
    assert runs.verdict_runs[0].reasons[0].fact_ids == [facts.facts[0].fact_id]
    assert (rule.rule_id, rule.manual_page) == (RULE_ID, 31)
    assert pages.pages[0].document_id == result.document_id
    assert boxes.boxes[0].char_start == facts.facts[0].quote_start


def test_story_2_7_an_owners_refusal_of_the_request_is_passed_on_and_anything_else_is_502(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    result = Result()
    paths = routes(result)
    file_key = ("intake", f"/documents/{result.document_id}/file")
    boxes_key = ("intake", f"/pages/{result.page_id}/boxes")
    facts_key = ("extraction", f"/cases/{result.case_id}/facts")
    runs_key = ("verdict", f"/cases/{result.case_id}/verdict-runs")

    # Nothing is held: every owner answers 404, and so does `web`.
    for path in paths.values():
        assert error_of(client.get(path, headers=UNDERWRITER)) == (404, "not_found")

    # AD-21: the document is asked for before its redaction is done.
    sidecar.answers[file_key] = refusal(
        ErrorCode.NOT_REDACTED, "The document has not been redacted."
    )
    assert error_of(client.get(paths[file_key], headers=UNDERWRITER)) == (
        409,
        "not_redacted",
    )
    sidecar.answers[boxes_key] = refusal(
        ErrorCode.VALIDATION_FAILED, "The request is not valid."
    )
    assert error_of(client.get(paths[boxes_key], headers=UNDERWRITER)) == (
        422,
        "validation_failed",
    )

    # Story 3.4: a ladder row that cannot be searched with is the runner's to
    # know ("not measured"); a model that is down is not passed on as such.
    for refused, expected in (
        (
            refusal(ErrorCode.RETRIEVER_NOT_AVAILABLE, "Row r4 is not available."),
            (409, "retriever_not_available"),
        ),
        (
            refusal(ErrorCode.MODEL_UNAVAILABLE, "The model is not available."),
            (502, "upstream_unavailable"),
        ),
    ):
        sidecar.answers["retrieval", "/searches"] = refused
        assert (
            error_of(client.post("/api/searches", json=SEARCH, headers=UNDERWRITER))
            == expected
        )
    asked_so_far = len(sidecar.requests)
    for body in ({**SEARCH, "retriever_config": "r9"}, {**SEARCH, "top_k": 0}):
        assert (
            error_of(client.post("/api/searches", json=body, headers=UNDERWRITER))[0]
            == 422
        )
    assert len(sidecar.requests) == asked_so_far

    # Story 3.6: what `workflow` says of a request for one more run is the
    # caller's to know, as it is: the case is unknown, it is not finished, or
    # the row cannot be run here (the SPA then uses the other pair). A fault
    # of `workflow` is not passed on, and a row that is no row never reaches it.
    runs_path = f"/api/cases/{result.case_id}/verdict-runs"
    wanted = {"retriever_config": "r4"}
    assert error_of(client.post(runs_path, json=wanted, headers=UNDERWRITER)) == (
        404,
        "not_found",
    )
    for refused_code, expected in (
        (ErrorCode.PAGES_NOT_TERMINAL, (409, "pages_not_terminal")),
        (ErrorCode.VALIDATION_FAILED, (422, "validation_failed")),
        (ErrorCode.RETRIEVER_NOT_AVAILABLE, (409, "retriever_not_available")),
        (ErrorCode.UPSTREAM_UNAVAILABLE, (502, "upstream_unavailable")),
        (ErrorCode.IN_PROGRESS, (502, "upstream_unavailable")),
    ):
        sidecar.answers["workflow", f"/cases/{result.case_id}/verdict-runs"] = refusal(
            refused_code, "Refused."
        )
        assert (
            error_of(client.post(runs_path, json=wanted, headers=UNDERWRITER))
            == expected
        )
    asked_so_far = len(sidecar.requests)
    for unusable in (
        {"retriever_config": "r9"},
        {},
        {**wanted, "actor": "underwriter"},
    ):
        assert (
            error_of(client.post(runs_path, json=unusable, headers=UNDERWRITER))[0]
            == 422
        )
    assert len(sidecar.requests) == asked_so_far

    # Not the caller's own request: a fault of the service, a code this
    # read should never get, an unreachable sidecar, and a success that is
    # not the contract's shape.
    for key, answer in (
        (facts_key, refusal(ErrorCode.INTERNAL_ERROR, "Something went wrong.")),
        (runs_key, refusal(ErrorCode.IN_PROGRESS, "Still running.")),
        (file_key, httpx.ConnectError("connection refused")),
        (facts_key, {"case_id": result.case_id, "facts": [{"statement": "x"}]}),
        (runs_key, {"case_id": result.case_id, "verdict_runs": "none"}),
    ):
        sidecar.answers[key] = answer
        assert error_of(client.get(paths[key], headers=UNDERWRITER)) == (
            502,
            "upstream_unavailable",
        ), key

    # What cannot be an id, a rule or a range never reaches a service.
    asked = len(sidecar.requests)
    for path in (
        "/api/cases/not-a-case/facts",
        "/api/cases/not-a-case/verdict-runs",
        "/api/cases/not-a-case/pages",
        "/api/rules/UW-dm-2",
        "/api/rules/..%2Fcases",
        "/api/documents/not-a-document/file",
        "/api/pages/not-a-page/text",
        f"/api/pages/{result.page_id}/boxes?quote_start=5",
        f"/api/pages/{result.page_id}/boxes?quote_start=9&quote_end=5",
        f"/api/pages/{result.page_id}/boxes?quote_start=-1&quote_end=5",
        # Story 2.8: not a run, not a tool, not a rule id, not a cursor.
        "/api/verdict-runs/not-a-run/steps",
        f"/api/verdict-runs/{result.run_id}/steps?tool=delete_rule",
        f"/api/verdict-runs/{result.run_id}/steps?after_step_no=0",
        # Beyond what a step number can be: refused, not asked of the database.
        f"/api/verdict-runs/{result.run_id}/steps?after_step_no=2147483648",
        f"/api/cases/{result.case_id}/agent-steps?tool=delete_rule",
        f"/api/cases/{result.case_id}/agent-steps?rule_id=UW-dm-2",
        f"/api/cases/{result.case_id}/agent-steps?after_step_no=2",
        f"/api/cases/{result.case_id}/agent-steps?run=x",
    ):
        status, code = error_of(client.get(path, headers=UNDERWRITER))
        assert (status, code) in ((422, "validation_failed"), (404, "not_found")), path
    assert len(sidecar.requests) == asked


def test_story_2_7_anything_but_a_pdf_from_intake_is_never_handed_on_as_the_document(
    client: TestClient, sidecar: FakeSidecar
) -> None:
    result = Result()
    path = routes(result)["intake", f"/documents/{result.document_id}/file"]

    for answer in (
        httpx.Response(
            200,
            content=b"<html><script>alert(1)</script></html>",
            headers={"Content-Type": "text/html"},
        ),
        httpx.Response(200, content=b"", headers={"Content-Type": "application/pdf"}),
        httpx.Response(200, content=REDACTED_PDF),
    ):
        sidecar.answers["intake", f"/documents/{result.document_id}/file"] = answer

        response = client.get(path, headers=UNDERWRITER)

        assert error_of(response) == (502, "upstream_unavailable")
        assert b"script" not in response.content
        assert b"%PDF" not in response.content
