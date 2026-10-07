"""Story 2.4: the extraction answer of the local stand-in for the Foundry chat deployment.

Unit tests: the facts it reads from a page, its modes, and that the request
it answers is the one `extraction`'s own model gateway sends.
"""

import asyncio
import json
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from synthdata_stack import CASES_DIR, answer_key

from contracts.models.extraction import ExtractionOutput
from contracts.text import find_quote, has_mask_token, normalise
from extraction.adapters.model import (
    OUTPUT_SCHEMA_NAME,
    RESPONSE_FORMAT,
    ModelGateway,
    build_model_client,
)
from extraction.domain.ports import ModelUnavailable
from extraction.settings import Settings
from intake.adapters.pdf import read_pages
from intake.settings import DEFAULT_REDACTION_CATEGORIES
from synthdata.cases import CASES
from synthdata.foundry_standin import (
    COMPLETIONS_PATH,
    DEFAULT_PORT,
    EXTRACTION_SCHEMA_NAME,
    FACT_NOT_ON_THE_PAGE,
    LOCAL_DEPLOYMENT,
    MASKED_VALUE_FACT,
    FoundryStandIn,
    Mode,
    facts_in,
    page_to_extract_of,
)
from synthdata.language_standin import redact_pdf

SETTINGS = Settings(
    applicationinsights_connection_string=None,
    model_endpoint=f"http://127.0.0.1:{DEFAULT_PORT}",
    chat_deployment=LOCAL_DEPLOYMENT,
)
# A lab report as `intake` stores one: each cell of the table on its own line.
LAB_PAGE = (
    "Specimen Diagnostics Laboratory\nLaboratory Report\n"
    "Patient name\n[Person]\n"
    "Results\nTest\nResult\nUnit\nReference range\nFlag\n"
    "HbA1c\n7.4\n%\n4.0 to 5.6\nH\n"
    "Creatinine\n0.9\nmg/dL\n0.6 to 1.1"
)


def redacted_pages(case: Any) -> list[str]:
    """The pages of a synthetic case as a later stage reads them: after redaction."""
    redacted, _ = redact_pdf(
        (CASES_DIR / case.file_name).read_bytes(), DEFAULT_REDACTION_CATEGORIES
    )
    return [page.text for page in read_pages(redacted, 100)]


def extract(stand_in: FoundryStandIn, text: str) -> str:
    """One page through `extraction`'s own gateway, with the stand-in behind it."""

    async def no_wait(seconds: float) -> None:
        return None

    async def scenario() -> str:
        gateway = ModelGateway(
            build_model_client(SETTINGS, httpx2.ASGITransport(app=stand_in.app())),
            deployment=LOCAL_DEPLOYMENT,
            sleep=no_wait,
        )
        try:
            return (await gateway.extract(text)).text
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


def test_story_2_4_the_stand_in_answers_the_gateways_request_with_a_contract_shaped_answer() -> (
    None
):
    stand_in = FoundryStandIn()

    answer = extract(stand_in, LAB_PAGE)

    output = ExtractionOutput.model_validate_json(answer)
    assert [(fact.statement, fact.quote) for fact in output.facts] == [
        ("HbA1c 7.4 %", "HbA1c 7.4 %"),
        ("Creatinine 0.9 mg/dL", "Creatinine 0.9 mg/dL"),
    ]
    # The request is told by the name of the structured output the service
    # asks for: the two cannot drift apart.
    assert EXTRACTION_SCHEMA_NAME == OUTPUT_SCHEMA_NAME
    (request,) = stand_in.requests
    assert request["response_format"] == RESPONSE_FORMAT
    assert page_to_extract_of(request) == LAB_PAGE
    assert (stand_in.calls, stand_in.extraction_calls) == (1, 1)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_story_2_4_every_quote_of_the_stand_in_is_on_the_redacted_page_it_was_read_from(
    case: Any,
) -> None:
    key = answer_key(case.case_id)
    pages = redacted_pages(case)

    for text, expected in zip(pages, key["pages"], strict=True):
        facts = facts_in(text)
        # A medical page has facts; an invoice, a payslip or a blank page none.
        assert bool(facts) is expected["is_medical"], expected["page_type"]
        for fact in facts:
            match = find_quote(text, fact["quote"])
            assert match is not None, fact["quote"]
            assert normalise(text[match.start : match.end]) == normalise(fact["quote"])
            # A model copies a row's cells with spaces; the page has line breaks.
            assert "\n" not in fact["quote"] and "\n" not in fact["statement"]
            # No masked value is proposed, and no planted identifier is copied.
            assert not has_mask_token(fact["statement"])
            assert not has_mask_token(fact["quote"])
            for identifier in key["identifiers"]:
                assert identifier["value"] not in json.dumps(fact)


def test_story_2_4_a_table_row_is_one_fact_whose_quote_runs_across_its_cells() -> None:
    (_, statement_page, lab_page) = redacted_pages(CASES[0])

    lab = {fact["statement"]: fact["quote"] for fact in facts_in(lab_page)}
    statement = [fact["statement"] for fact in facts_in(statement_page)]

    # The laboratory table: test, result and unit sit in three cells.
    assert lab["HbA1c 7.4 %"] == "HbA1c 7.4 %"
    assert "HbA1c\n7.4\n%" in lab_page
    assert len(lab) == 7
    # The statement's two tables: a diagnosis row and a blood pressure row.
    assert statement[:3] == [
        "Type 2 diabetes mellitus, diagnosed 2019-05-06, Metformin 1000 mg twice daily",
        "Blood pressure 126/80 mmHg on 2026-06-08",
        "Blood pressure 128/82 mmHg on 2026-09-07",
    ]
    assert "Latest HbA1c: 7.4 % on 2026-09-07" in statement


@pytest.mark.parametrize(
    "text",
    ["", "   \n ", "Invoice\nTotal due\n94.74", "Height", "Full name\n[Person]"],
)
def test_story_2_4_a_page_with_nothing_medical_has_no_facts(text: str) -> None:
    assert facts_in(text) == []
    assert json.loads(FoundryStandIn().extraction_answer(text)) == {"facts": []}


def test_story_2_4_a_value_that_redaction_masked_is_not_proposed_as_a_fact() -> None:
    text = "Family history\n[Person] had a heart attack\nWeight\n78 kg\nHeight\nWeight"

    # The masked value is left alone, and a label followed by a label is no fact.
    assert [fact["statement"] for fact in facts_in(text)] == ["Weight: 78 kg"]


def test_story_2_4_in_quote_not_on_page_mode_one_fact_more_has_a_quote_that_is_nowhere() -> (
    None
):
    stand_in = FoundryStandIn(Mode.QUOTE_NOT_ON_PAGE)

    facts = ExtractionOutput.model_validate_json(extract(stand_in, LAB_PAGE)).facts

    assert [fact.statement for fact in facts] == [
        "HbA1c 7.4 %",
        "Creatinine 0.9 mg/dL",
        FACT_NOT_ON_THE_PAGE["statement"],
    ]
    # On none of the synthetic pages, whatever case is run.
    for case in CASES:
        for text in redacted_pages(case):
            assert find_quote(text, FACT_NOT_ON_THE_PAGE["quote"]) is None


def test_story_2_4_in_masked_value_mode_a_masked_value_is_proposed_as_a_fact() -> None:
    stand_in = FoundryStandIn(Mode.MASKED_VALUE)

    facts = ExtractionOutput.model_validate_json(extract(stand_in, LAB_PAGE)).facts

    assert (facts[-1].statement, facts[-1].quote) == (
        MASKED_VALUE_FACT["statement"],
        MASKED_VALUE_FACT["quote"],
    )
    assert has_mask_token(facts[-1].statement)
    assert len(facts) == 3


def test_story_2_4_in_invalid_mode_the_answer_is_not_the_object_that_was_asked_for() -> (
    None
):
    answer = extract(FoundryStandIn(Mode.INVALID), LAB_PAGE)

    with pytest.raises(ValueError):
        ExtractionOutput.model_validate_json(answer)


def test_story_2_4_in_throttled_mode_the_gateway_gives_up_as_model_unavailable() -> (
    None
):
    stand_in = FoundryStandIn(Mode.THROTTLED)

    with pytest.raises(ModelUnavailable):
        extract(stand_in, LAB_PAGE)

    # The call and its three retries (AD-16).
    assert stand_in.extraction_calls == 4


@pytest.mark.parametrize(
    "body",
    [
        # Another structured output's name.
        {
            "response_format": {"type": "json_schema", "json_schema": {"name": "x"}},
            "messages": [{"role": "user", "content": "text"}],
        },
        # No user message, or two.
        {"response_format": RESPONSE_FORMAT, "messages": []},
        # A user message that is not plain text.
        {
            "response_format": RESPONSE_FORMAT,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}],
        },
    ],
)
def test_story_2_4_a_request_the_extraction_gateway_would_not_send_is_no_extraction_request(
    body: dict[str, Any],
) -> None:
    assert page_to_extract_of(body) is None
    with TestClient(FoundryStandIn().app()) as client:
        response = client.post(COMPLETIONS_PATH, json={**body, "model": "m"})
    assert response.status_code == 400


def test_story_2_4_the_classification_modes_leave_extraction_as_in_ok() -> None:
    for mode in (Mode.DISAGREE, Mode.MIXED):
        facts = ExtractionOutput.model_validate_json(
            extract(FoundryStandIn(mode), LAB_PAGE)
        ).facts
        assert len(facts) == 2
