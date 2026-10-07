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
from pydantic import ValidationError

from intake.adapters.blob import BlobOriginalStore
from intake.adapters.db import EntraToken
from intake.adapters.language import (
    COGNITIVE_SERVICES_SCOPE,
    LanguageRedaction,
    build_language_http,
    language_token_for,
)
from intake.adapters.pdf import PdfPageSplitter, read_pages
from intake.domain.entities import JobOutput
from intake.domain.ports import RedactionJobError
from intake.settings import DEFAULT_REDACTION_CATEGORIES, Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CASES_DIR = REPOSITORY_ROOT / "data" / "cases"
APP_STACK = REPOSITORY_ROOT / "infra" / "demo" / "app"
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
        "redactionPolicy": {"policyKind": "entityMask"},
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


def test_story_1_7_only_the_job_id_is_taken_from_the_address_the_service_names() -> (
    None
):
    service = FakeService(
        submit=httpx.Response(
            202,
            headers={
                "operation-location": "https://elsewhere.example/language/"
                f"analyze-documents/jobs/{JOB_ID}?api-version=x"
            },
        )
    )
    credential = FakeCredential()
    token = EntraToken(credential, scope=COGNITIVE_SERVICES_SCOPE)

    async def scenario() -> JobOutput:
        adapter = service.adapter(token)
        return await adapter.output(
            await adapter.start("a/b.pdf", CASE_ID, ("Person",)), CASE_ID
        )

    asyncio.run(scenario())

    # Every call, and so the token, went to the configured endpoint.
    assert {request.url.host for request in service.requests} == {
        "lang-aiuw-demo-wus3.cognitiveservices.azure.com"
    }


@pytest.mark.parametrize(
    ("submit", "reason"),
    [
        (
            httpx.Response(400, json={"error": {"message": "secret"}}),
            "submit_status_400",
        ),
        (httpx.Response(200), "submit_status_200"),
        (httpx.Response(202), "submit_no_job_id"),
        (
            httpx.Response(202, headers={"operation-location": f"{ENDPOINT}/jobs/a b"}),
            "submit_no_job_id",
        ),
    ],
)
def test_story_1_7_a_job_that_is_refused_is_an_error_with_a_code_and_no_message(
    submit: httpx.Response, reason: str
) -> None:
    service = FakeService(submit=submit)

    with pytest.raises(RedactionJobError) as raised:
        asyncio.run(service.adapter().start("a/b.pdf", CASE_ID, ("Person",)))

    assert raised.value.reason == reason
    assert "secret" not in str(raised.value)


def test_story_1_7_a_submit_that_cannot_reach_the_service_is_an_error_without_the_address() -> (
    None
):
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    adapter = LanguageRedaction(
        httpx.AsyncClient(
            base_url=ENDPOINT, transport=httpx.MockTransport(unreachable)
        ),
        api_version="2026-05-01",
        originals_url=ORIGINALS,
        cases_url=CASES_URL,
        poll_seconds=1,
    )

    with pytest.raises(RedactionJobError) as raised:
        asyncio.run(adapter.start("a/b.pdf", CASE_ID, ("Person",)))

    assert raised.value.reason == "submit_ConnectError"
    assert raised.value.__cause__ is None
    assert "cognitiveservices" not in str(raised.value)


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


@pytest.mark.parametrize(
    ("state", "reason"),
    [
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
        # A file outside the container the service was told to write to.
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
    ],
)
def test_story_1_7_a_job_that_does_not_end_with_usable_files_is_an_error(
    state: httpx.Response, reason: str
) -> None:
    service = FakeService(states=[state])

    with pytest.raises(RedactionJobError) as raised:
        asyncio.run(service.adapter().output(JOB_ID, CASE_ID))

    assert raised.value.reason == reason


def test_story_1_7_a_job_is_cancelled_at_the_services_cancel_route() -> None:
    service = FakeService()

    asyncio.run(service.adapter().cancel(JOB_ID))

    (request,) = service.requests
    assert (request.method, request.url.path) == (
        "POST",
        f"/language/analyze-documents/jobs/{JOB_ID}:cancel",
    )
    assert request.url.params["api-version"] == "2026-05-01"
    # A job that has ended has nothing left to cancel: not an error.
    for ended in (200, 404, 409):
        asyncio.run(FakeService(cancel_status=ended).adapter().cancel(JOB_ID))
    # Not signed in, refused for now, or failing: the job was not cancelled.
    for status in (401, 403, 429, 500):
        with pytest.raises(RedactionJobError) as raised:
            asyncio.run(FakeService(cancel_status=status).adapter().cancel(JOB_ID))
        assert raised.value.reason == f"cancel_status_{status}"


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


def test_story_1_7_a_submit_answered_429_or_5xx_is_sent_again_a_few_times() -> None:
    accepted = FakeService().submit
    service = FakeService()
    service.submits = [httpx.Response(429), httpx.Response(503), accepted]

    job_id = asyncio.run(service.adapter().start("a/b.pdf", CASE_ID, ("Person",)))

    assert job_id == JOB_ID
    assert len(service.requests) == 3
    assert service.slept == [1.5, 1.5]
    # Three more tries, then the refusal stands.
    always = FakeService(submit=httpx.Response(503))
    with pytest.raises(RedactionJobError) as raised:
        asyncio.run(always.adapter().start("a/b.pdf", CASE_ID, ("Person",)))
    assert raised.value.reason == "submit_status_503"
    assert len(always.requests) == 4
    # Any other refusal is not tried again.
    refused = FakeService(submit=httpx.Response(400))
    with pytest.raises(RedactionJobError):
        asyncio.run(refused.adapter().start("a/b.pdf", CASE_ID, ("Person",)))
    assert len(refused.requests) == 1


# --- Settings ------------------------------------------------------------------------


def test_story_1_7_settings_default_to_the_spines_limits_and_name_no_endpoint() -> None:
    settings = Settings()

    # AD-6: the stage's own deadline.
    assert settings.redaction_deadline_seconds == 180.0
    assert settings.redaction_categories == list(DEFAULT_REDACTION_CATEGORIES)
    assert settings.language_api_version == "2026-05-01"
    # The stand-in is never a default: without a setting there is no endpoint.
    assert settings.language_endpoint is None
    assert settings.language_entra_auth is False
    with pytest.raises(ValueError, match="INTAKE_LANGUAGE_ENDPOINT"):
        build_language_http(settings)


def test_story_1_7_settings_read_the_redaction_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTAKE_LANGUAGE_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("INTAKE_LANGUAGE_ENTRA_AUTH", "true")
    monkeypatch.setenv("INTAKE_LANGUAGE_API_VERSION", "2025-11-15-preview")
    monkeypatch.setenv("INTAKE_REDACTION_CATEGORIES", '["Person","Email"]')
    monkeypatch.setenv("INTAKE_REDACTION_DEADLINE_SECONDS", "120")

    settings = Settings()

    assert settings.language_endpoint == ENDPOINT
    assert settings.language_api_version == "2025-11-15-preview"
    assert settings.redaction_categories == ["Person", "Email"]
    assert settings.redaction_deadline_seconds == 120.0
    client = build_language_http(settings)
    assert str(client.base_url).rstrip("/") == ENDPOINT
    # The token never follows a redirect to another host.
    assert client.follow_redirects is False


@pytest.mark.parametrize(
    "values",
    [
        # The real service is reached with the identity, never without.
        {"language_endpoint": ENDPOINT},
        # Plain HTTP is the stand-in: loopback only, and no token is sent to it.
        {"language_endpoint": "http://lang.example.com"},
        {"language_endpoint": "http://10.0.0.4:5100"},
        {"language_endpoint": "http://127.0.0.1:5100", "language_entra_auth": True},
        {"language_endpoint": "ftp://127.0.0.1"},
        {"language_endpoint": "127.0.0.1:5100"},
        {"redaction_categories": []},
        {"redaction_categories": ["Person", "Person"]},
        {"redaction_categories": ["Avery Testwood"]},
        # Letters of another script are not a category name either.
        {"redaction_categories": ["Pers\u00f6n"]},
        {"redaction_cancel_seconds": 30},
    ],
)
def test_story_1_7_settings_that_would_misuse_the_stand_in_or_the_service_are_refused(
    values: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        Settings(**values)


def test_story_1_7_the_stand_in_is_accepted_only_on_loopback() -> None:
    for endpoint in ("http://127.0.0.1:5100", "http://localhost:5100"):
        assert Settings(language_endpoint=endpoint).language_endpoint == endpoint
    assert Settings(language_endpoint="  ").language_endpoint is None


def test_story_1_7_the_app_stack_gives_intake_the_language_settings_and_the_three_roles() -> (
    None
):
    main = (APP_STACK / "main.tf").read_text()
    names = set(re.findall(r'name\s*=\s*"(INTAKE_[A-Z0-9_]+)"', main))

    assert {
        "INTAKE_LANGUAGE_ENDPOINT",
        "INTAKE_LANGUAGE_ENTRA_AUTH",
        "INTAKE_LANGUAGE_API_VERSION",
        "INTAKE_REDACTION_CATEGORIES",
    } <= names
    assert {name.removeprefix("INTAKE_").lower() for name in names} <= set(
        Settings.model_fields
    )
    # In Azure the endpoint is the real account's, from the foundation stack.
    assert "local.foundation.language_endpoint" in main
    assert "127.0.0.1" not in main
    # intake may call Language; Language's own identity reads `originals` and
    # writes `cases` (azure.md, Runtime roles).
    for resource in (
        "intake_language_user",
        "language_originals_reader",
        "language_cases_contributor",
    ):
        assert f'resource "azurerm_role_assignment" "{resource}"' in main
    assert '"Cognitive Services User"' in main
    assert '"Storage Blob Data Reader"' in main
    # The categories the stack sets are valid for the settings.
    variables = (APP_STACK / "terraform.tfvars").read_text()
    listed = re.search(r"redaction_categories\s*=\s*\[([^\]]*)\]", variables)
    assert listed is not None
    assert Settings(
        redaction_categories=re.findall(r'"([^"]+)"', listed.group(1))
    ).redaction_categories == list(DEFAULT_REDACTION_CATEGORIES)


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


def test_story_1_7_a_document_of_too_many_pages_is_refused_and_a_long_page_stays_small() -> (
    None
):
    pdf = (CASES_DIR / "case-003.pdf").read_bytes()

    with pytest.raises(RedactionJobError) as raised:
        asyncio.run(PdfPageSplitter(200, max_pages=3).split(pdf))
    assert raised.value.reason == "too_many_pages"

    # A4 at 200 px wide would be 283 px high: the height limit wins.
    (first, *_) = read_pages(pdf, 200, thumbnail_max_height_px=100)
    width, height = (int.from_bytes(first.thumbnail[at : at + 4]) for at in (16, 20))
    assert height <= 100
    assert width < 200
    assert Settings().max_pages == 200


def test_story_1_7_a_file_that_is_not_a_pdf_cannot_be_split() -> None:
    with pytest.raises(Exception):  # noqa: B017 - whatever the reader raises fails the redaction
        read_pages(b"not a pdf", 200)
