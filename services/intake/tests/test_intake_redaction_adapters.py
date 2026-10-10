"""Story 1.7: the Language REST adapter, the PDF reader and the settings.

Unit tests: no network, no Azure. Azure AI Language is a transport that
answers in the service's REST shape.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from intake.adapters.blob import BlobOriginalStore
from intake.adapters.db import EntraToken
from intake.adapters.language import (
    COGNITIVE_SERVICES_SCOPE,
    LanguageRedaction,
    language_token_for,
)
from intake.adapters.pdf import PdfPageSplitter
from intake.domain.entities import JobOutput
from intake.domain.ports import RedactionJobError
from intake.settings import Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = REPOSITORY_ROOT / "data" / "cases"
ENDPOINT = "https://lang-aiuw-demo-wus3.cognitiveservices.azure.com"
ORIGINALS = "https://staiuwdemowus3.blob.core.windows.net/originals"
CASES_URL = "https://staiuwdemowus3.blob.core.windows.net/cases"
CASE_ID = "01999999-0000-7000-8000-000000000001"
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
    in_azure = Settings(language_endpoint=ENDPOINT, language_entra_auth=True)
    assert isinstance(language_token_for(in_azure), EntraToken)
    assert (
        language_token_for(Settings(language_endpoint="http://127.0.0.1:5100")) is None
    )


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
    ]:
        service = FakeService(states=[state])

        with pytest.raises(RedactionJobError) as raised:
            asyncio.run(service.adapter().output(JOB_ID, CASE_ID))

        assert raised.value.reason == reason


def test_story_1_7_output_under_another_cases_prefix_is_refused() -> None:
    other = "01999999-0000-7000-8000-000000000002"
    for location in (
        f"{CASES_URL}/{other}/doc.pdf",
        f"{CASES_URL}/doc.pdf",
        f"{CASES_URL}/{CASE_ID}/../{other}/doc.pdf",
        f"{CASES_URL}/{CASE_ID}/",
    ):
        service = FakeService(
            states=[
                httpx.Response(200, json=job_state("succeeded", location, OUTPUT[1]))
            ]
        )
        with pytest.raises(RedactionJobError) as raised:
            asyncio.run(service.adapter().output(JOB_ID, CASE_ID))
        # Another case's blob is never read, copied or removed as this case's.
        assert raised.value.reason == "job_output_outside_case", location


# --- Settings ------------------------------------------------------------------------


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
    # One place downloads a blob, and it is built for the `cases` container only.
    assert readers == ["blob.py"]
    blob = (source / "adapters" / "blob.py").read_text()
    assert blob.count("download_blob") == 1
    assert blob.index("class BlobCaseFiles") < blob.index("download_blob")


# --- The one reading of each page (AD-14) ----------------------------------------------


def test_story_1_7_each_page_is_read_once_into_text_word_boxes_and_a_thumbnail() -> (
    None
):
    pdf = (CASES_DIR / "case-003.pdf").read_bytes()

    pages = asyncio.run(PdfPageSplitter(thumbnail_width_px=200).split(pdf))

    assert len(pages) == 4
    first, blank, rotated, _ = pages
    # One text per page, with a box per word and offsets into that text.
    assert "Life Insurance Application Form" in first.text
    assert len(first.words) == len(first.text.split())
    assert [first.text[w.char_start : w.char_end] for w in first.words] == (
        first.text.split()
    )
    for page in pages:
        for word in page.words:
            assert 0 <= word.x0 <= word.x1 <= page.width
            assert 0 <= word.y0 <= word.y1 <= page.height
        assert page.thumbnail.startswith(PNG_SIGNATURE)
        # PNG: the width is the first field of the header chunk.
        assert int.from_bytes(page.thumbnail[16:20]) == 200
    # A blank page has no text and no boxes, and still a picture.
    assert (blank.text, blank.words) == ("", ())
    # A page turned on its side is given as it is shown: wider than high,
    # with its boxes turned with it.
    assert (rotated.width, rotated.height) == (842.0, 595.0)
    assert rotated.words
    assert max(word.x1 for word in rotated.words) > 595.0
