"""Story 2.2: the local stand-in for Document Intelligence's layout model.

Unit tests: what it reads out of the manual, its modes, and that it stays a
dev tool. The real layout client of `retrieval` is what calls it here, so the
request it accepts and the result it gives are the ones the service works with.
"""

import asyncio
import base64
import re
from collections import Counter
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from synthdata_stack import MANUAL_PDF, REPOSITORY_ROOT

from contracts.rules import rule_ids_defined_in
from retrieval.adapters.layout import DocumentLayout, build_layout_http, layout_of
from retrieval.domain.entities import ParsedLayout
from retrieval.domain.ports import LayoutFailed
from retrieval.settings import Settings
from synthdata.layout_standin import (
    API_VERSION,
    DEFAULT_PORT,
    LAYOUT_MODEL,
    MODELS_PATH,
    LayoutStandIn,
    Mode,
    analyze_pdf,
    main,
)
from synthdata.manual import extracted_pages
from synthdata.manual_rules import MANUAL
from synthdata.render import FOOTER

SETTINGS = Settings(
    applicationinsights_connection_string=None,
    layout_endpoint=f"http://127.0.0.1:{DEFAULT_PORT}",
)
ANALYZE = f"{MODELS_PATH}/{LAYOUT_MODEL}:analyze?api-version={API_VERSION}"


@pytest.fixture(scope="module")
def manual_pdf() -> bytes:
    return MANUAL_PDF.read_bytes()


@pytest.fixture(scope="module")
def result(manual_pdf: bytes) -> dict[str, Any]:
    return analyze_pdf(manual_pdf)


def parse(stand_in: LayoutStandIn, pdf: bytes, **options: Any) -> ParsedLayout:
    """The service's own layout client, with the stand-in where the service would be."""

    async def scenario() -> ParsedLayout:
        layout = DocumentLayout(
            build_layout_http(SETTINGS, httpx2.ASGITransport(app=stand_in.app())),
            api_version=SETTINGS.layout_api_version,
            model=SETTINGS.layout_model,
            poll_seconds=0.001,
            deadline_seconds=options.get("deadline_seconds", 30.0),
            max_retries=1,
        )
        try:
            return await layout.parse(pdf)
        finally:
            await layout.aclose()

    return asyncio.run(scenario())


def body_of(pdf: bytes) -> dict[str, str]:
    return {"base64Source": base64.b64encode(pdf).decode()}


# --- What it reads -----------------------------------------------------------------------


def test_story_2_2_the_stand_in_answers_in_the_shape_of_the_services_result(
    result: dict[str, Any], manual_pdf: bytes
) -> None:
    pages = extracted_pages(manual_pdf)

    assert (result["modelId"], result["apiVersion"]) == (LAYOUT_MODEL, API_VERSION)
    assert [page["pageNumber"] for page in result["pages"]] == list(
        range(1, len(pages) + 1)
    )
    content = result["content"]
    for page, text in zip(result["pages"], pages, strict=True):
        assert page["unit"] == "inch"
        # A5, in inches, and every word of the page's text layer.
        assert (page["width"], page["height"]) == (5.8333, 8.2639)
        assert Counter(word["content"] for word in page["words"]) == Counter(
            text.split()
        )
        assert page["lines"]
    # Every span points at its own text in the document's content.
    for paragraph in result["paragraphs"][:400]:
        (span,) = paragraph["spans"]
        assert (
            content[span["offset"] : span["offset"] + span["length"]]
            == (paragraph["content"])
        )
        (region,) = paragraph["boundingRegions"]
        assert len(region["polygon"]) == 8
    word = result["pages"][11]["words"][5]
    offset, length = word["span"]["offset"], word["span"]["length"]
    assert content[offset : offset + length] == word["content"]


def test_story_2_2_a_rule_definition_is_one_paragraph_on_its_page(
    result: dict[str, Any],
) -> None:
    definitions = [
        paragraph
        for paragraph in result["paragraphs"]
        if rule_ids_defined_in(paragraph["content"])
    ]

    # One paragraph per definition, starting with its marker, with no role.
    assert len(definitions) == 111
    for paragraph in definitions:
        assert len(rule_ids_defined_in(paragraph["content"])) == 1
        assert paragraph["content"].startswith("Rule UW-")
        assert "role" not in paragraph
        assert FOOTER not in paragraph["content"]


def test_story_2_2_the_stand_in_tells_page_furniture_and_headings_by_their_place_and_type(
    result: dict[str, Any],
) -> None:
    by_role: dict[str, list[dict[str, Any]]] = {}
    for paragraph in result["paragraphs"]:
        by_role.setdefault(paragraph.get("role", ""), []).append(paragraph)
    page_count = len(result["pages"])

    # On every page: the running title, the page number and the footer.
    assert {p["content"] for p in by_role["pageHeader"]} == {MANUAL.title}
    assert [p["content"] for p in by_role["pageNumber"]] == [
        f"Page {number}" for number in range(1, page_count + 1)
    ]
    assert {p["content"] for p in by_role["pageFooter"]} == {FOOTER}
    assert len(by_role["pageHeader"]) == len(by_role["pageFooter"]) == page_count
    assert [p["content"] for p in by_role["title"]] == [MANUAL.title]
    headings = [p["content"] for p in by_role["sectionHeading"]]
    # Numbered sections and their parts, as the manual prints them.
    assert "2 Type 2 diabetes mellitus" in headings
    assert "2.4 Probable rating" in headings
    assert all(re.match(r"\d+(\.\d+)? \S|Contents$", heading) for heading in headings)


def test_story_2_2_the_cells_of_a_table_are_paragraphs_of_their_own(
    result: dict[str, Any],
) -> None:
    texts = [paragraph["content"] for paragraph in result["paragraphs"]]
    row = texts.index("UW-DM-005")

    # A rating table's row: the id, the band, and a rating that wraps.
    assert texts[row : row + 3] == [
        "UW-DM-005",
        "HbA1c of 10.0 % or more",
        "decline as a postponement",
    ]
    # A contents line: the section on the left, its page on the right.
    entry = texts.index("2 Type 2 diabetes mellitus")
    assert texts[entry + 1].isdigit()


def test_story_2_2_without_roles_no_paragraph_says_what_it_is(
    manual_pdf: bytes, result: dict[str, Any]
) -> None:
    bare = analyze_pdf(manual_pdf, roles=False)

    assert not any("role" in paragraph for paragraph in bare["paragraphs"])
    assert [p["content"] for p in bare["paragraphs"]] == [
        p["content"] for p in result["paragraphs"]
    ]


# --- The routes and the modes --------------------------------------------------------------


def test_story_2_2_the_stand_in_serves_the_layout_client_of_the_service(
    manual_pdf: bytes, result: dict[str, Any]
) -> None:
    stand_in = LayoutStandIn()

    parsed = parse(stand_in, manual_pdf)

    assert parsed == layout_of(result)
    assert len(parsed.pages) == 206
    assert all(page.has_text for page in parsed.pages)
    assert (stand_in.submits, stand_in.looks) == (1, 1)


def test_story_2_2_the_stand_in_refuses_what_the_client_would_not_send(
    manual_pdf: bytes,
) -> None:
    with TestClient(LayoutStandIn().app()) as client:
        by_address = client.post(
            ANALYZE, json={"urlSource": "https://example.com/manual.pdf"}
        )
        not_base64 = client.post(ANALYZE, json={"base64Source": "not base64!"})
        other_model = client.post(
            ANALYZE.replace(LAYOUT_MODEL, "prebuilt-read"), json=body_of(manual_pdf)
        )
        unknown = client.get(f"{MODELS_PATH}/{LAYOUT_MODEL}/analyzeResults/none")
        accepted = client.post(ANALYZE, json=body_of(b"not a pdf at all"))
        failed = client.get(accepted.headers["operation-location"])

    assert (by_address.status_code, not_base64.status_code) == (400, 400)
    assert (other_model.status_code, unknown.status_code) == (404, 404)
    # Bytes that are no PDF are accepted and then fail, as an analysis does.
    assert accepted.status_code == 202
    assert failed.json()["status"] == "failed"
    assert "analyzeResult" not in failed.json()


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        (Mode.FAIL, "layout_failed_InternalServerError"),
        (Mode.REJECT, "layout_submit_status_400"),
        (Mode.THROTTLED, "layout_submit_status_429"),
        (Mode.HANG, "layout_timeout"),
    ],
)
def test_story_2_2_in_its_failure_modes_the_layout_client_gives_up_with_a_code(
    manual_pdf: bytes, mode: Mode, reason: str
) -> None:
    stand_in = LayoutStandIn(mode)

    with pytest.raises(LayoutFailed) as raised:
        parse(stand_in, manual_pdf, deadline_seconds=0.02)

    assert raised.value.reason == reason
    if mode is Mode.THROTTLED:
        # The first submit and one retry, as the client was told.
        assert stand_in.submits == 2


# --- The stand-in never ships ----------------------------------------------------------------


def test_story_2_2_the_stand_in_is_in_no_service_image_and_no_service_imports_it() -> (
    None
):
    folder = REPOSITORY_ROOT / "services" / "retrieval"
    assert "synthdata" not in (folder / "pyproject.toml").read_text()
    assert "synthdata" not in (folder / "Dockerfile").read_text()
    for source in (folder / "src").rglob("*.py"):
        assert not re.search(
            r"^\s*(from|import) synthdata", source.read_text(), re.MULTILINE
        )
    # The service refuses a plain-HTTP layout endpoint that is not on this
    # machine, so the stand-in cannot be the endpoint in Azure.
    with pytest.raises(ValueError, match="RETRIEVAL_LAYOUT_ENDPOINT"):
        Settings(layout_endpoint="http://layout-stand-in.internal:5102")
    # The mode switch exists only in the stand-in.
    assert Mode.OK.value == "ok"


def test_story_2_2_the_stand_in_listens_on_loopback_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, Any] = {}
    monkeypatch.setattr(
        "synthdata.layout_standin.uvicorn.run",
        lambda app, **options: started.update(options, app=app),
    )

    main(["--port", "5198", "--mode", "hang", "--no-roles"])

    # Never reachable from another machine.
    assert (started["host"], started["port"]) == ("127.0.0.1", 5198)
