"""Story 1.7: the Language and read model REST adapters, the PDF adapter and the settings.

Unit tests: no network, no Azure. Azure AI Language and Document
Intelligence are transports that answer in the services' REST shapes. One
test feeds a trimmed copy of what the real services answered in the Azure
session of 2026-10-10 (`fixtures/read-real-page-1.json`, synthetic data).
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pymupdf
import pytest
from pydantic import ValidationError

from contracts.text import MASK_TOKEN_PATTERN, find_quote
from intake.adapters.blob import BlobOriginalStore
from intake.adapters.db import EntraToken
from intake.adapters.language import (
    COGNITIVE_SERVICES_SCOPE,
    LanguageRedaction,
    language_token_for,
)
from intake.adapters.pdf import PdfPageSplitter
from intake.adapters.read import DocumentRead, pages_of, read_token_for
from intake.domain.entities import JobOutput, LayerWord, PageSheet
from intake.domain.ports import RedactionJobError
from intake.domain.reading import page_reading
from intake.domain.redaction import mask_names
from intake.settings import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = REPOSITORY_ROOT / "data" / "cases"
REAL_ANSWERS = Path(__file__).parent / "fixtures" / "read-real-page-1.json"
ENDPOINT = "https://lang-aiuw-demo-wus3.cognitiveservices.azure.com"
READ_ENDPOINT = "https://di-aiuw-demo-wus3.cognitiveservices.azure.com"
RESULT_ID = "1b2c3d4e-0000-1111-2222-333333333333"
ORIGINALS = "https://staiuwdemowus3.blob.core.windows.net/originals"
CASES_URL = "https://staiuwdemowus3.blob.core.windows.net/cases"
CASE_ID = "01999999-0000-7000-8000-000000000001"
OTHER_CASE_ID = "01999999-0000-7000-8000-000000000002"
JOB_ID = "c0ffee00-1111-2222-3333-444444444444"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def job_state(status: str, *locations: str, errors: bool = False) -> dict[str, Any]:
    """A job as the service reports it."""
    return {
        "jobId": JOB_ID,
        "status": status,
        "tasks": {
            "items": [
                {
                    "kind": "PiiEntityRecognitionLROResults",
                    "status": status,
                    "results": {
                        "documents": [
                            {
                                "id": CASE_ID,
                                "targets": [
                                    {"kind": "AzureBlob", "location": location}
                                    for location in locations
                                ],
                            }
                        ],
                        "errors": [{"id": CASE_ID}] if errors else [],
                    },
                }
            ]
        },
    }


def text_empty_state(inner_message: str) -> dict[str, Any]:
    """A job that succeeded with no document and one error for it, as seen in Azure on 2026-10-10."""
    state = job_state("succeeded")
    results = state["tasks"]["items"][0]["results"]
    results["documents"] = []
    results["errors"] = [
        {
            "id": CASE_ID,
            "error": {
                "code": "InvalidRequest",
                "message": "Invalid Document in request.",
                "details": [
                    {
                        "code": "InvalidArgument",
                        "message": "Invalid Document in request.",
                        "innererror": {
                            "code": "InvalidDocument",
                            "message": inner_message,
                        },
                    }
                ],
            },
        }
    ]
    return state


OUTPUT = (
    f"{CASES_URL}/{CASE_ID}/{JOB_ID}/PiiEntityRecognition/0001/doc.pdf",
    f"{CASES_URL}/{CASE_ID}/{JOB_ID}/PiiEntityRecognition/0001/doc.result.json",
)


@dataclass
class FakeService:
    """Stands in for the Language endpoint: it answers each call from a script."""

    # What each look at the job answers, first to last; the last one repeats.
    states: list[httpx.Response] = field(
        default_factory=lambda: [
            httpx.Response(200, json=job_state("succeeded", *OUTPUT))
        ]
    )
    submit: httpx.Response = field(
        default_factory=lambda: httpx.Response(
            202,
            headers={
                "operation-location": f"{ENDPOINT}/language/analyze-documents/jobs/"
                f"{JOB_ID}?api-version=2026-05-01"
            },
        )
    )
    cancel_status: int = 202
    # What the next submits answer, first to last, before `submit` applies.
    submits: list[httpx.Response] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)
    slept: list[float] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST" and request.url.path.endswith(":cancel"):
            return httpx.Response(self.cancel_status)
        if request.method == "POST":
            return self.submits.pop(0) if self.submits else self.submit
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    async def sleep(self, seconds: float) -> None:
        # Nothing waits: the pause between two looks is only noted.
        self.slept.append(seconds)

    def adapter(self, token: EntraToken | None = None) -> LanguageRedaction:
        return LanguageRedaction(
            httpx.AsyncClient(
                base_url=ENDPOINT, transport=httpx.MockTransport(self.handle)
            ),
            api_version="2026-05-01",
            originals_url=ORIGINALS,
            cases_url=CASES_URL,
            poll_seconds=1.5,
            token=token,
            sleep=self.sleep,
        )


class FakeCredential:
    def __init__(self) -> None:
        self.scopes: list[str] = []

    def get_token(self, *scopes: str, **_: object) -> Any:
        self.scopes.extend(scopes)

        @dataclass
        class Token:
            token: str = "entra-token-value"  # noqa: S105 - a made-up value
            expires_on: int = 4_102_444_800

        return Token()


# --- The Language job, over REST ---------------------------------------------------


def test_story_1_7_the_job_is_submitted_with_the_entity_mask_the_categories_and_blob_addresses() -> (
    None
):
    service = FakeService()

    job_id = asyncio.run(
        service.adapter().start(
            f"{CASE_ID}/doc.pdf", CASE_ID, ("Person", "PhoneNumber")
        )
    )

    (request,) = service.requests
    assert job_id == JOB_ID
    assert (request.method, request.url.path) == (
        "POST",
        "/language/analyze-documents/jobs",
    )
    # The API version is a setting, sent on every call.
    assert request.url.params["api-version"] == "2026-05-01"
    body = json.loads(request.content)
    (document,) = body["analysisInput"]["documents"]
    # The service is told where the original is and where to write: it reads
    # and writes the blobs itself. No file content is sent.
    assert document["source"] == {"location": f"{ORIGINALS}/{CASE_ID}/doc.pdf"}
    assert document["target"] == {"location": f"{CASES_URL}/{CASE_ID}"}
    (task,) = body["tasks"]
    assert task["kind"] == "PiiEntityRecognition"
    assert task["parameters"] == {
        "redactionPolicies": [{"policyKind": "entityMask", "isDefault": True}],
        "piiCategories": ["Person", "PhoneNumber"],
    }
    assert b"%PDF" not in request.content
    # The stand-in takes no credential, so none is sent to it.
    assert "authorization" not in request.headers


def test_story_1_7_language_is_called_with_the_service_identity_and_no_key() -> None:
    service = FakeService()
    credential = FakeCredential()
    token = EntraToken(credential, scope=COGNITIVE_SERVICES_SCOPE)

    async def scenario() -> None:
        adapter = service.adapter(token)
        await adapter.output(
            await adapter.start("a/b.pdf", CASE_ID, ("Person",)), CASE_ID
        )
        await adapter.cancel(JOB_ID)

    asyncio.run(scenario())

    assert credential.scopes == ["https://cognitiveservices.azure.com/.default"]
    assert len(service.requests) == 3
    for request in service.requests:
        assert request.headers["authorization"] == "Bearer entra-token-value"
        # No key header, on any call.
        assert "ocp-apim-subscription-key" not in request.headers
    # In Azure the token source is built; for the stand-in there is none.
    in_azure = Settings(
        language_endpoint=ENDPOINT,
        language_entra_auth=True,
        read_endpoint=READ_ENDPOINT,
        read_entra_auth=True,
    )
    assert isinstance(language_token_for(in_azure), EntraToken)
    on_this_machine = Settings(
        language_endpoint="http://127.0.0.1:5100",
        read_endpoint="http://127.0.0.1:5102",
    )
    assert language_token_for(on_this_machine) is None


def test_story_1_7_the_job_is_looked_at_until_it_ends_and_its_files_are_named() -> None:
    service = FakeService(
        states=[
            httpx.Response(200, json=job_state("notStarted")),
            # The service cannot say just now: the job is looked at again.
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(200, json=job_state("running")),
            httpx.Response(200, json=job_state("succeeded", *OUTPUT)),
        ]
    )

    output = asyncio.run(service.adapter().output(JOB_ID, CASE_ID))

    # Names inside the `cases` container; nothing else of the answer is kept.
    assert output == JobOutput(
        redacted_blob_name=f"{CASE_ID}/{JOB_ID}/PiiEntityRecognition/0001/doc.pdf",
        result_blob_name=f"{CASE_ID}/{JOB_ID}/PiiEntityRecognition/0001/doc.result.json",
    )
    assert len(service.requests) == 5
    assert {request.url.path for request in service.requests} == {
        f"/language/analyze-documents/jobs/{JOB_ID}"
    }
    # The pause between two looks is the setting; the test did not wait for it.
    assert service.slept == [1.5, 1.5, 1.5, 1.5]


def test_story_1_7_a_job_that_does_not_end_with_usable_files_is_an_error() -> None:
    for state, reason in [
        (httpx.Response(200, json=job_state("failed")), "job_failed"),
        (httpx.Response(200, json=job_state("cancelled")), "job_cancelled"),
        (
            httpx.Response(200, json=job_state("partiallyCompleted")),
            "job_partiallycompleted",
        ),
        (httpx.Response(404), "job_status_404"),
        (httpx.Response(200, content=b"<html>"), "job_not_json"),
        (httpx.Response(200, json=[]), "job_not_an_object"),
        (httpx.Response(200, json={"status": "succeeded"}), "job_output_missing"),
        (
            httpx.Response(200, json=job_state("succeeded", OUTPUT[0])),
            "job_output_incomplete",
        ),
        (
            httpx.Response(200, json=job_state("succeeded", *OUTPUT, errors=True)),
            "job_document_error",
        ),
        (
            httpx.Response(
                200,
                json=job_state(
                    "succeeded", f"{ORIGINALS}/{CASE_ID}/doc.pdf", OUTPUT[1]
                ),
            ),
            "job_output_outside_case",
        ),
        (
            httpx.Response(
                200, json=job_state("succeeded", f"{CASES_URL}-other/x.pdf", OUTPUT[1])
            ),
            "job_output_outside_case",
        ),
        # A document without any text, as the real service answers it: told
        # apart from every other document error, which stays a failure.
        (
            httpx.Response(200, json=text_empty_state("Document text is empty.")),
            "job_document_text_empty",
        ),
        (
            httpx.Response(200, json=text_empty_state("Document is corrupted.")),
            "job_document_error",
        ),
        # Another case's blob is never read, copied or removed as this
        # case's: output under another case's prefix, or under none, is refused.
        *(
            (
                httpx.Response(200, json=job_state("succeeded", location, OUTPUT[1])),
                "job_output_outside_case",
            )
            for location in (
                f"{CASES_URL}/{OTHER_CASE_ID}/doc.pdf",
                f"{CASES_URL}/doc.pdf",
                f"{CASES_URL}/{CASE_ID}/../{OTHER_CASE_ID}/doc.pdf",
                f"{CASES_URL}/{CASE_ID}/",
            )
        ),
    ]:
        service = FakeService(states=[state])

        with pytest.raises(RedactionJobError) as raised:
            asyncio.run(service.adapter().output(JOB_ID, CASE_ID))

        assert raised.value.reason == reason


# --- No code reads an original ---------------------------------------------


def test_story_1_7_nothing_in_the_service_can_read_an_original() -> None:
    # The store of originals writes and deletes; it has no way to read.
    assert {name for name in vars(BlobOriginalStore) if not name.startswith("_")} == {
        "put",
        "delete",
    }
    source = REPOSITORY_ROOT / "services" / "intake" / "src" / "intake"
    readers = [
        path.name
        for path in source.rglob("*.py")
        if re.search(r"download_blob|readall", path.read_text())
    ]
    assert readers == ["blob.py"]
    blob = (source / "adapters" / "blob.py").read_text()
    # Two places download a blob. One is built for the `cases` container
    # only. The other is the one read of an original `intake` may make
    # (owner's decision of 2026-10-10): of a document the redaction service
    # found no text in, to draw its pages as pictures for the read model.
    assert blob.count("download_blob") == 2
    pages, case_files = (
        blob.index("class BlobOriginalPages"),
        blob.index("class BlobCaseFiles"),
    )
    assert pages < blob.index("download_blob") < case_files
    # That reader is given to the redaction and to nothing else: the app
    # factory names it once, and no route module does.
    users = sorted(
        path.name
        for path in source.rglob("*.py")
        if "BlobOriginalPages" in path.read_text()
    )
    assert users == ["app.py", "blob.py"]
    assert (source / "adapters" / "http" / "app.py").read_text().count(
        "BlobOriginalPages("
    ) == 1
    redaction = (source / "domain" / "redaction.py").read_text()
    assert redaction.count("ports.originals.read(") == 1


# --- The sheets of the redacted PDF (AD-14) --------------------------------------------


def test_story_1_7_each_page_of_the_pdf_is_a_sheet_with_its_size_picture_and_layer_words() -> (
    None
):
    pdf = (CASES_DIR / "case-003.pdf").read_bytes()

    pages = asyncio.run(PdfPageSplitter(thumbnail_width_px=200).split(pdf))

    assert len(pages) == 4
    first, blank, rotated, _ = pages
    # What the file's own text layer holds, where it is on the page: of a
    # redacted PDF that is the masks' labels. No page text is made of it.
    assert not hasattr(first, "text")
    assert [word.text for word in first.layer_words[:3]] == [
        "Example",
        "Mutual",
        "Life",
    ]
    for page in pages:
        for word in page.layer_words:
            assert 0 <= word.x0 <= word.x1 <= page.width
            assert 0 <= word.y0 <= word.y1 <= page.height
        assert page.thumbnail.startswith(PNG_SIGNATURE)
        # PNG: the width is the first field of the header chunk.
        assert int.from_bytes(page.thumbnail[16:20]) == 200
    # A blank page has no words, and still a picture.
    assert blank.layer_words == ()
    # A page turned on its side is given as it is shown: wider than high,
    # with its words turned with it.
    assert (rotated.width, rotated.height) == (842.0, 595.0)
    assert max(word.x1 for word in rotated.layer_words) > 595.0


# --- The read model, over REST (AD-14) --------------------------------------------------


def _read_adapter(
    answers: list[httpx.Response],
    requests: list[httpx.Request],
    slept: list[float],
    token: EntraToken | None = None,
) -> DocumentRead:
    """The adapter on a transport that answers each call from a script; the last answer repeats."""

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return answers.pop(0) if len(answers) > 1 else answers[0]

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    return DocumentRead(
        httpx.AsyncClient(
            base_url=READ_ENDPOINT, transport=httpx.MockTransport(handle)
        ),
        api_version="2024-11-30",
        model="prebuilt-read",
        poll_seconds=0.5,
        max_retries=2,
        token=token,
        sleep=sleep,
    )


def test_story_1_7_the_redacted_pdf_is_sent_to_the_read_model_with_the_identity_and_read_when_done(
    caplog: pytest.LogCaptureFixture,
) -> None:
    real = json.loads(REAL_ANSWERS.read_text())["read"]
    accepted = httpx.Response(
        202,
        headers={
            "operation-location": f"{READ_ENDPOINT}/documentintelligence/documentModels/"
            f"prebuilt-read/analyzeResults/{RESULT_ID}?api-version=2024-11-30"
        },
    )
    requests: list[httpx.Request] = []
    slept: list[float] = []
    credential = FakeCredential()
    adapter = _read_adapter(
        [
            # Refused for now, with a wait named: the submit is sent again.
            httpx.Response(429, headers={"retry-after": "2"}),
            accepted,
            httpx.Response(200, json={"status": "running"}),
            httpx.Response(503),
            httpx.Response(200, json=real),
        ],
        requests,
        slept,
        EntraToken(credential, scope=COGNITIVE_SERVICES_SCOPE),
    )

    with caplog.at_level("DEBUG"):
        (page,) = asyncio.run(adapter.read(b"%PDF-1.7 the redacted file"))

    submit = requests[1]
    assert (submit.method, submit.url.path) == (
        "POST",
        "/documentintelligence/documentModels/prebuilt-read:analyze",
    )
    # The PDF itself is the body: the service is given no way into storage.
    assert submit.headers["content-type"] == "application/pdf"
    assert submit.content == b"%PDF-1.7 the redacted file"
    assert {request.url.path for request in requests[2:]} == {
        f"/documentintelligence/documentModels/prebuilt-read/analyzeResults/{RESULT_ID}"
    }
    assert credential.scopes == ["https://cognitiveservices.azure.com/.default"]
    for request in requests:
        assert request.url.params["api-version"] == "2024-11-30"
        assert request.headers["authorization"] == "Bearer entra-token-value"
        assert "ocp-apim-subscription-key" not in request.headers
    # The waits: what the service asked for, then the setting between looks.
    assert slept == [2.0, 0.5, 0.5]
    # The real answer's page: inches, not turned, lines of words in reading order.
    assert (page.page_number, page.angle, page.width) == (1, 0.0, 8.2639)
    assert [word.content for word in page.lines[1]] == ["Full", "name"]
    assert [[word.content for word in line] for line in page.lines[2:4]] == [
        ["PER"],
        ["1"],
    ]
    # Logs: an id and counts; nothing the page says.
    assert f"redacted document read: result_id={RESULT_ID} pages=1" in caplog.text
    assert "Applicant" not in caplog.text and "entra-token-value" not in caplog.text

    # An analysis that fails, an answer that is not a result, and a service
    # that stays away each end the reading with a code, never with no text.
    for answers, reason, calls_made in (
        (
            [
                accepted,
                httpx.Response(
                    200,
                    json={
                        "status": "failed",
                        "error": {"code": "InvalidContent", "message": "secret"},
                    },
                ),
            ],
            "read_failed_InvalidContent",
            2,
        ),
        (
            [accepted, httpx.Response(200, json={"status": "succeeded"})],
            "read_result_missing",
            2,
        ),
        (
            [
                accepted,
                httpx.Response(
                    200,
                    json={"status": "succeeded", "analyzeResult": {"pages": [{}]}},
                ),
            ],
            "read_result_malformed",
            2,
        ),
        (
            [
                accepted,
                httpx.Response(
                    200,
                    json={"status": "succeeded", "analyzeResult": {"pages": ["x"]}},
                ),
            ],
            "read_result_malformed",
            2,
        ),
        ([httpx.Response(401)], "read_submit_status_401", 1),
        # Sent again twice, as the setting says, and no more.
        ([httpx.Response(503)], "read_submit_status_503", 3),
        # Five looks in a row without an answer end it.
        ([accepted, httpx.Response(500)], "read_poll_status_500", 6),
    ):
        calls: list[httpx.Request] = []
        with pytest.raises(RedactionJobError) as raised:
            asyncio.run(_read_adapter(answers, calls, []).read(b"%PDF"))
        assert (raised.value.reason, len(calls)) == (reason, calls_made)

    # The settings: the real account over TLS with the identity, or the
    # stand-in on loopback, which is sent no token.
    in_azure = Settings(read_endpoint=READ_ENDPOINT, read_entra_auth=True)
    assert isinstance(read_token_for(in_azure), EntraToken)
    assert (in_azure.read_model, in_azure.read_api_version) == (
        "prebuilt-read",
        "2024-11-30",
    )
    assert read_token_for(Settings(read_endpoint="http://127.0.0.1:5102")) is None
    for refused in (
        {"read_endpoint": READ_ENDPOINT},
        {"read_endpoint": "http://di.example.com"},
        {"read_endpoint": "http://127.0.0.1:5102", "read_entra_auth": True},
        {"read_endpoint": READ_ENDPOINT, "read_entra_auth": True, "read_model": "a/b"},
        # Redaction without the reading would store pages with no text.
        {"language_endpoint": "http://127.0.0.1:5100"},
    ):
        with pytest.raises(ValidationError):
            Settings(**refused)


# --- From the read model's answer to the stored page (AD-14, AD-21) ---------------------


def test_story_1_7_the_real_read_answer_becomes_page_text_with_mask_tokens_and_boxes_on_the_page() -> (
    None
):
    real = json.loads(REAL_ANSWERS.read_text())
    # The redacted page as the real service wrote it: one picture, and a
    # text layer of nothing but each mask's label and number.
    with pymupdf.open() as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        document.new_page(width=595, height=842)
        (blank,) = asyncio.run(
            PdfPageSplitter(thumbnail_width_px=100).split(document.tobytes())
        )
    sheet = PageSheet(
        width=blank.width,
        height=blank.height,
        thumbnail=blank.thumbnail,
        layer_words=tuple(LayerWord(*word) for word in real["layer_words"]),
    )
    names = mask_names(json.dumps(real["result_file"]).encode())
    (read,) = pages_of(real["read"]["analyzeResult"])

    page = page_reading(read, sheet, names)

    # The read model gave the masks as `PER` and `1`, `ADR2`, `EML4` and `4`,
    # `PHN5`, `PHHealth` (a mask drawn over the heading's number), `DrPER®`
    # and `PHN1` with `.7`. Each is one token of the contracts' shape, named
    # by the result file; the text beside a mask is kept. A word that goes
    # on in letters after a label's start (`PHHealth`) is a word of the
    # page: it is kept whole beside the token, never cut.
    assert page.text == (
        "1. Applicant\nFull name\n[Person]\nDate of birth\n1974-03-18\n"
        "Home address\n[Address]\nEmail\n[Email]\n"
        "National identity number\n000-12-3456\nPolicy number\n[PhoneNumber]\n"
        "[PhoneNumber] PHHealth declaration\nCondition\n"
        "Type 2 diabetes mellitus, diagnosed 2019-05-06\n"
        "3. Attending physician\nPhysician\nDr [Person] Exampleby\nPractice\n"
        "Practice telephone\nExampleton Family Practice\n[PhoneNumber]"
    )
    assert re.findall(MASK_TOKEN_PATTERN, page.text) == [
        "[Person]",
        "[Address]",
        "[Email]",
        "[PhoneNumber]",
        "[PhoneNumber]",
        "[Person]",
        "[PhoneNumber]",
    ]
    # Seven masks have their token; the eighth (the telephone, whose line
    # the fixture leaves out) was not read, and is not made up.
    assert (page.masks, page.masks_unread) == (7, 1)
    assert not re.search(r"PER|ADR|EML|PHN|®", page.text)
    # A word per box, with offsets into the text, in PDF points on the page.
    assert [page.text[word.char_start : word.char_end] for word in page.words] == (
        page.text.split()
    )
    for word in page.words:
        assert 0 <= word.x0 <= word.x1 <= sheet.width
        assert 0 <= word.y0 <= word.y1 <= sheet.height
    # A token's box is its mask's own: label and number together, as the
    # redacted PDF places them.
    person = next(
        word
        for word in page.words
        if page.text[word.char_start : word.char_end] == "[Person]"
    )
    assert (person.x0, person.y0, person.x1, person.y1) == (
        219.7,
        141.09,
        242.38,
        155.0,
    )
    # A line of the form is found by the quote finder where it reads, and its
    # words lie in the row it stands in (inches to points: 72 to the inch).
    found = find_quote(page.text, "Type 2 diabetes mellitus, diagnosed 2019-05-06")
    assert found is not None
    quoted = [word for word in page.words if found.start <= word.char_start < found.end]
    assert len(quoted) == 6
    assert quoted[0].x0 == pytest.approx(219.6, abs=1.0)
    assert all(424 <= word.y0 < word.y1 <= 438 for word in quoted)
    # The result file's found text is never read: names only.
    assert "text" not in real["result_file"]["entities"][0]
    assert names.category_of_entity[("PER", "1")] == "Person"
