"""Story 2.7: `web` passes the result view's six reads on to the services that own them.

The Dapr sidecar is a fake here: a transport that records what `web` sent and
answers as `extraction`, `verdict`, `retrieval` and `intake` would.
"""

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
from contracts.models.retrieval import RuleText
from contracts.models.verdict import SUGGESTION_LABEL, VerdictRunList
from web.adapters.http.app import create_app
from web.settings import Settings

CUSTOMER = {"X-Demo-Role": "customer"}
UNDERWRITER = {"X-Demo-Role": "underwriter"}
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"
# The first bytes of any PDF file; the rest is no document and need not be.
REDACTED_PDF = b"%PDF-1.7\nsynthetic redacted document"
RULE_ID = "UW-DM-002"


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

    def answers(self) -> dict[tuple[str, str], Any]:
        """What each owner answers, by app id and path."""
        return {
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
                        "verdict_run_id": new_id(),
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
    }


def test_story_2_7_the_underwriter_reads_each_part_of_a_result_from_its_owner_and_the_customer_none(
    client: TestClient, sidecar: FakeSidecar
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
        f"/api/pages/{result.page_id}/boxes?quote_start=5",
        f"/api/pages/{result.page_id}/boxes?quote_start=9&quote_end=5",
        f"/api/pages/{result.page_id}/boxes?quote_start=-1&quote_end=5",
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
