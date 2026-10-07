"""Story 1.8: the local stand-in for the Foundry chat deployment that classifies pages.

Unit tests: what it answers, its modes, and that it stays a dev tool. The
real model gateway of `classification` is what calls it here, so the request
shape it accepts is the one the service sends.
"""

import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from synthdata_stack import CASES_DIR, REPOSITORY_ROOT, answer_key

from classification.adapters.model import (
    OPENAI_API_PATH,
    ModelGateway,
    build_model_client,
)
from classification.domain.entities import PageContent
from classification.domain.ports import ModelUnavailable
from classification.settings import Settings
from contracts.enums import PageType
from contracts.models.classification import ClassifierOutput
from intake.adapters.pdf import read_pages
from intake.settings import DEFAULT_REDACTION_CATEGORIES
from synthdata.cases import CASES
from synthdata.foundry_standin import (
    COMPLETIONS_PATH,
    DEFAULT_PORT,
    LOCAL_DEPLOYMENT,
    FoundryStandIn,
    Mode,
    answer_for,
    classify_text,
    main,
    other_than,
    page_text_of,
)
from synthdata.language_standin import redact_pdf

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic"
SETTINGS = Settings(
    applicationinsights_connection_string=None,
    model_endpoint=f"http://127.0.0.1:{DEFAULT_PORT}",
    chat_deployment=LOCAL_DEPLOYMENT,
)


def gateway_to(stand_in: FoundryStandIn, **options: Any) -> ModelGateway:
    """The service's own gateway, with the stand-in where the deployment would be."""

    async def no_wait(seconds: float) -> None:
        return None

    return ModelGateway(
        build_model_client(SETTINGS, httpx2.ASGITransport(app=stand_in.app())),
        deployment=LOCAL_DEPLOYMENT,
        sleep=no_wait,
        **options,
    )


def run_page(stand_in: FoundryStandIn, text: str, runs: int = 1) -> list[str]:
    async def scenario() -> list[str]:
        gateway = gateway_to(stand_in)
        try:
            return [
                await gateway.classify(PageContent(text=text, image=PNG))
                for _ in range(runs)
            ]
        finally:
            await gateway.aclose()

    return asyncio.run(scenario())


# --- What it answers ---------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_story_1_8_the_stand_in_gives_every_synthetic_page_the_type_of_the_answer_key(
    case: Any,
) -> None:
    key = answer_key(case.case_id)
    # The pages as a later stage reads them: after redaction.
    redacted, _ = redact_pdf(
        (CASES_DIR / case.file_name).read_bytes(), DEFAULT_REDACTION_CATEGORIES
    )
    texts = [page.text for page in read_pages(redacted, 100)]

    given = [classify_text(text).value for text in texts]

    assert given == [page["page_type"] for page in key["pages"]]


@pytest.mark.parametrize(
    ("text", "page_type"),
    [
        ("Example Lab\nLaboratory Report\nHbA1c 6.1 %", "lab_report"),
        ("ATTENDING   PHYSICIAN\nSTATEMENT", "attending_physician_statement"),
        ("Life Insurance Application Form", "application_form"),
        ("Republic of Example\nPASSPORT", "id_document"),
        ("Example Clinic\nInvoice\nTotal due 120.00", "invoice"),
        ("Example Power\nElectricity Bill", "other"),
        ("Payslip", "other"),
        ("", "other"),
    ],
)
def test_story_1_8_the_stand_in_tells_page_types_apart_by_their_headings(
    text: str, page_type: str
) -> None:
    assert classify_text(text).value == page_type


def test_story_1_8_the_stand_in_answers_the_gateways_request_with_a_contract_shaped_answer() -> (
    None
):
    stand_in = FoundryStandIn()

    (given,) = run_page(stand_in, "Example Lab\nLaboratory Report\nfor [Person]")

    # What the real gateway hands the domain parses into the contracts model.
    output = ClassifierOutput.model_validate_json(given)
    assert output.page_type is PageType.LAB_REPORT
    assert output.reason
    # The reason is a sentence of the stand-in's own: nothing of the page.
    assert "[Person]" not in output.reason
    # It was asked as the service asks the deployment.
    (request,) = stand_in.requests
    assert request["model"] == LOCAL_DEPLOYMENT
    assert page_text_of(request) == "Example Lab\nLaboratory Report\nfor [Person]"
    assert COMPLETIONS_PATH == f"{OPENAI_API_PATH}chat/completions"
    assert stand_in.calls == 1


def test_story_1_8_every_page_type_has_an_answer_the_contracts_accept() -> None:
    for page_type in PageType:
        output = ClassifierOutput.model_validate_json(answer_for(page_type))
        assert output.page_type is page_type
        assert other_than(page_type) is not page_type


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"model": LOCAL_DEPLOYMENT, "messages": []},
        # A prompt without a page, a page without a picture, no answer format.
        {
            "model": LOCAL_DEPLOYMENT,
            "messages": [{"role": "user", "content": "classify this"}],
            "response_format": {"type": "json_schema"},
        },
        {
            "model": LOCAL_DEPLOYMENT,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Invoice"}]}
            ],
            "response_format": {"type": "json_schema"},
        },
        {
            "model": LOCAL_DEPLOYMENT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Invoice"},
                        {"type": "image_url", "image_url": {"url": "data:,"}},
                    ],
                }
            ],
        },
        {
            "model": "",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Invoice"},
                        {"type": "image_url", "image_url": {"url": "data:,"}},
                    ],
                }
            ],
            "response_format": {"type": "json_schema"},
        },
    ],
    ids=["empty", "no-user", "no-parts", "no-picture", "no-format", "no-model"],
)
def test_story_1_8_the_stand_in_refuses_a_request_the_gateway_would_not_send(
    body: dict[str, Any],
) -> None:
    with TestClient(FoundryStandIn().app()) as client:
        response = client.post(COMPLETIONS_PATH, json=body)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


# --- Its modes -----------------------------------------------------------------------------


def test_story_1_9_in_mixed_mode_the_runs_differ_on_some_page_types_only() -> None:
    stand_in = FoundryStandIn(Mode.MIXED)

    def named(text: str) -> Counter[str]:
        answers = run_page(stand_in, text, runs=5)
        return Counter(json.loads(answer)["page_type"] for answer in answers)

    # Sure of these: every run names the same type.
    assert named("Example Clinic\nInvoice") == {"invoice": 5}
    assert named("Application form") == {"application_form": 5}
    assert named("Payslip") == {"other": 5}
    # Not sure of these: three of five agree, each time the page is run.
    assert named("Laboratory report") == {"lab_report": 3, "other": 2}
    assert named("Passport") == {"id_document": 3, "other": 2}
    assert named("Laboratory report\nSecond") == {"lab_report": 3, "other": 2}


def test_story_1_8_in_disagree_mode_three_of_five_runs_of_a_page_agree() -> None:
    stand_in = FoundryStandIn(Mode.DISAGREE)

    answers = run_page(stand_in, "Example Clinic\nInvoice", runs=5)
    other_page = run_page(stand_in, "Payslip", runs=5)

    named = Counter(json.loads(answer)["page_type"] for answer in answers)
    assert named == {"invoice": 3, "other": 2}
    # Counted for each page on its own; a page that is `other` is out-voted
    # by `other` too.
    assert Counter(json.loads(answer)["page_type"] for answer in other_page) == {
        "other": 3,
        "invoice": 2,
    }
    # Without the mode every run of a page says the same.
    same = run_page(FoundryStandIn(), "Example Clinic\nInvoice", runs=5)
    assert len(set(same)) == 1


def test_story_1_8_in_invalid_mode_the_answer_is_not_the_object_that_was_asked_for() -> (
    None
):
    (given,) = run_page(FoundryStandIn(Mode.INVALID), "Laboratory Report")

    with pytest.raises(ValueError, match="json"):
        ClassifierOutput.model_validate_json(given)


def test_story_1_8_in_throttled_mode_every_call_is_a_429_and_the_gateway_gives_up() -> (
    None
):
    stand_in = FoundryStandIn(Mode.THROTTLED, retry_after_seconds=3)

    with pytest.raises(ModelUnavailable):
        run_page(stand_in, "Laboratory Report")

    # The call and its three retries (AD-16).
    assert stand_in.calls == 4
    with TestClient(stand_in.app()) as client:
        response = client.post(COMPLETIONS_PATH, json={})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "3"


# --- The stand-in never ships ------------------------------------------------------------


def test_story_1_8_the_stand_in_is_in_no_service_image_and_no_service_imports_it() -> (
    None
):
    services = sorted(path.name for path in (REPOSITORY_ROOT / "services").iterdir())
    assert "classification" in services
    for service in services:
        folder = REPOSITORY_ROOT / "services" / service
        if not (folder / "pyproject.toml").exists():
            continue
        assert "synthdata" not in (folder / "pyproject.toml").read_text()
        assert "synthdata" not in (folder / "Dockerfile").read_text()
        for source in (folder / "src").rglob("*.py"):
            text = source.read_text()
            assert not re.search(r"^\s*(from|import) synthdata", text, re.MULTILINE)
            # Story 1.4's guard: nothing under `services/` reads the answer key.
            assert "answer-key" not in text
    # The mode switch exists only in the stand-in.
    assert [mode.value for mode in Mode] == [
        "ok",
        "disagree",
        "mixed",
        "invalid",
        "throttled",
    ]


def test_story_1_8_the_stand_in_listens_on_loopback_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, Any] = {}
    monkeypatch.setattr(
        "synthdata.foundry_standin.uvicorn.run",
        lambda app, **options: started.update(options),
    )

    main(["--port", "5198", "--mode", "disagree"])
    assert (started["host"], started["port"]) == ("127.0.0.1", 5198)
    main([])

    # Never reachable from another machine; by default on the port the local
    # run gives `classification`.
    assert (started["host"], started["port"]) == ("127.0.0.1", DEFAULT_PORT)
    run_file = (Path(REPOSITORY_ROOT) / "dapr.yaml").read_text()
    assert (
        f'CLASSIFICATION_MODEL_ENDPOINT: "http://127.0.0.1:{DEFAULT_PORT}"' in run_file
    )
    assert f'CLASSIFICATION_CHAT_DEPLOYMENT: "{LOCAL_DEPLOYMENT}"' in run_file
    dev = (Path(REPOSITORY_ROOT) / "tools" / "dev.sh").read_text()
    assert f"model_port={DEFAULT_PORT}" in dev
    assert "synthdata.foundry_standin" in dev
    # Story 1.9: the mode of the local stand-in is chosen by a variable.
    assert '--mode "${FOUNDRY_STANDIN_MODE:-ok}"' in dev
