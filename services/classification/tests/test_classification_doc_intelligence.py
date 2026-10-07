"""Story 4.2: the `doc-intelligence` contender and its training job.

Unit tests: the contender's path through the domain with in-memory stand-ins,
the client of Document Intelligence against a transport that stands in for
the service, and the training job with that transport and a fake container.
No database, no storage, no network.
"""

import asyncio
import base64
import dataclasses
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx2
import pytest
from classification_fakes import (
    ACTOR,
    CLASSIFIER_ACTOR,
    CLASSIFIER_ID,
    PAGE_FILE,
    REASON,
    TRACE_ID,
    FakePages,
    FakeTrainingPages,
    MemoryRepository,
    StubClassifier,
    StubModel,
    unavailable,
)
from fastapi.testclient import TestClient

from classification import train
from classification.adapters.blob import listed_of
from classification.adapters.classifier import (
    COGNITIVE_SERVICES_SCOPE,
    build_classifier,
)
from classification.adapters.http.app import classify_options
from classification.domain.classify import (
    ClassifyOptions,
    ClassifyPorts,
    classify_page,
    list_classifications,
)
from classification.domain.entities import (
    ClassificationKey,
    ClassifierAnswer,
    ListedPage,
    StoredBlob,
)
from classification.domain.ports import (
    ClassifierNotReady,
    ModelCallFailed,
    ModelUnavailable,
    TrainingFailed,
)
from classification.settings import Settings
from contracts.enums import ClassifierContender, PageType, StageStatus
from contracts.errors import DomainError, ErrorBody, ErrorCode
from contracts.models.classification import (
    Classification,
    ClassificationResult,
    ClassifyCommand,
)

OPTIONS = ClassifyOptions(actor=ACTOR, classifier_actor=CLASSIFIER_ACTOR)
ENDPOINT = "https://di-aiuw-demo-wus3.cognitiveservices.azure.com"
API_VERSION = "2024-11-30"
CLASSIFIER_PATH = f"/documentintelligence/documentClassifiers/{CLASSIFIER_ID}"


def command(case_id: str, page_id: str, contender: str) -> ClassifyCommand:
    return ClassifyCommand.model_validate(
        {"case_id": case_id, "page_id": page_id, "contender": contender}
    )


def run(
    command_: ClassifyCommand, ports: ClassifyPorts, **more: Any
) -> ClassificationResult:
    return asyncio.run(
        classify_page(
            command_, ports=ports, options=more.pop("options", OPTIONS), **more
        )
    )


# --- The contender's path to the same result ---------------------------------------------


def test_story_4_2_a_page_is_sent_as_a_one_page_document_and_stored_with_the_llm_contenders_fields(
    ports: ClassifyPorts,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
    fixed_now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    classifier = StubClassifier()
    ports = dataclasses.replace(ports, classifier=classifier)
    page_id = pages.add(case_id)
    asked = command(case_id, page_id, "doc-intelligence")

    with caplog.at_level(logging.DEBUG):
        result = run(asked, ports, trace_id=TRACE_ID, now=lambda: fixed_now)
        again = run(asked, ports)

    assert (result.status, result.error_code) == (StageStatus.DONE, None)
    assert result.classification is not None
    stored = result.classification.model_dump(mode="json")
    assert stored == {
        "classification_id": result.classification_id,
        "case_id": case_id,
        "page_id": page_id,
        "contender": "doc-intelligence",
        "page_type": "lab_report",
        # AD-13: from the one mapping, never from the classifier.
        "is_medical": True,
        # The service's own confidence for the page's document type.
        "confidence": 0.93,
        # A fixed sentence that names the classifier and the type it gave.
        "reason": "The Document Intelligence classifier gave this page the type "
        "lab_report.",
    }
    # The same fields as the LLM contender's result, and no other.
    assert set(stored) == set(Classification.model_fields)
    # AD-8: the actor names the service and the classifier.
    assert (result.audit.action.value, result.audit.actor, result.audit.ref) == (
        "page.classified",
        CLASSIFIER_ACTOR,
        result.classification_id,
    )
    # The page went to the classifier as a one-page PDF, read from `intake`.
    # Its text and thumbnail were not read, and the chat model was not asked.
    assert classifier.documents == [PAGE_FILE]
    assert (pages.file_reads, pages.reads, model.calls) == ([page_id], [], 0)
    # AD-6: the repeat is answered with the stored result and asks nothing again.
    assert again == result
    assert repository.result_of(ClassificationKey(*key_fields(asked))) == result
    assert len(classifier.documents) == 1
    # Logs carry ids, codes, counts and timings: never the type or the page.
    assert f"page_id={page_id}" in caplog.text
    assert "contender=doc-intelligence runs=1 agreeing_runs=1" in caplog.text
    for secret in ("SECRET", "lab_report"):
        assert secret not in caplog.text


def key_fields(command_: ClassifyCommand) -> tuple[str, str, ClassifierContender]:
    return command_.case_id, command_.page_id, command_.contender


def test_story_4_2_a_page_classified_by_both_contenders_has_two_results_that_share_nothing(
    ports: ClassifyPorts,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    classifier = StubClassifier([ClassifierAnswer("invoice", 0.55)])
    ports = dataclasses.replace(ports, classifier=classifier)
    page_id = pages.add(case_id)

    by_model = run(command(case_id, page_id, "llm"), ports)
    by_classifier = run(command(case_id, page_id, "doc-intelligence"), ports)

    # Two stored results for the one page, one per contender, under ids of
    # their own.
    listed = asyncio.run(list_classifications(case_id, repository=repository))
    assert [
        (item.contender.value, item.page_type.value, item.confidence, item.reason)
        for item in listed.classifications
    ] == [
        ("llm", "lab_report", 1.0, REASON),
        (
            "doc-intelligence",
            "invoice",
            0.55,
            "The Document Intelligence classifier gave this page the type invoice.",
        ),
    ]
    assert len(repository.rows) == 2
    assert by_model.classification_id != by_classifier.classification_id
    # Nothing of one contender's result reaches the other: each answered
    # from what it read itself, and each is the actor of its own result.
    assert (by_model.audit.actor, by_classifier.audit.actor) == (
        ACTOR,
        CLASSIFIER_ACTOR,
    )
    assert (model.calls, len(classifier.documents)) == (5, 1)
    # A repeat of either command answers that contender's result.
    assert run(command(case_id, page_id, "llm"), ports) == by_model
    assert run(command(case_id, page_id, "doc-intelligence"), ports) == by_classifier


def test_story_4_2_an_answer_that_is_no_page_type_or_no_answer_is_a_stored_failed_result(
    ports: ClassifyPorts,
    pages: FakePages,
    repository: MemoryRepository,
    case_id: str,
) -> None:
    failures: list[tuple[ClassifierAnswer | Exception, ErrorCode]] = [
        # A document type that is no `page_type`.
        (ClassifierAnswer("newsletter", 0.99), ErrorCode.INVALID_MODEL_OUTPUT),
        # An answer that names no document.
        (ClassifierAnswer(None, None), ErrorCode.INVALID_MODEL_OUTPUT),
        # A confidence that is none.
        (ClassifierAnswer("lab_report", 1.7), ErrorCode.INVALID_MODEL_OUTPUT),
        # The service down, and an analysis it could not make.
        (unavailable(), ErrorCode.MODEL_UNAVAILABLE),
        (ModelCallFailed("classifier_failed"), ErrorCode.STAGE_FAILED),
    ]
    for given, error_code in failures:
        page_id = pages.add(case_id)
        result = run(
            command(case_id, page_id, "doc-intelligence"),
            dataclasses.replace(ports, classifier=StubClassifier([given])),
        )

        assert (result.status, result.error_code) == (StageStatus.FAILED, error_code)
        assert result.classification is None
        assert (result.audit.action.value, result.audit.actor) == (
            "stage.failed",
            CLASSIFIER_ACTOR,
        )
    # Past the classify deadline: the stage's own timeout, and nothing invented.
    slow = StubClassifier(hold=asyncio.Event())
    result = run(
        command(case_id, pages.add(case_id), "doc-intelligence"),
        dataclasses.replace(ports, classifier=slow),
        options=dataclasses.replace(OPTIONS, deadline_seconds=0.01),
    )
    assert (result.status, result.error_code) == (
        StageStatus.FAILED,
        ErrorCode.STAGE_TIMEOUT,
    )
    # What a repeat can mend is no failure of the page: without a trained
    # classifier, or while the service does not let the identity in, nothing
    # is stored and the key row is given up, so the command sent again runs.
    waiting = pages.add(case_id)
    with pytest.raises(DomainError) as not_ready:
        run(
            command(case_id, waiting, "doc-intelligence"),
            dataclasses.replace(
                ports,
                classifier=StubClassifier(
                    [ClassifierNotReady("classifier_submit_status_404")]
                ),
            ),
        )
    assert not_ready.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
    # A classifier without the actor that names it runs nothing: its result
    # is never stored under the chat deployment's name.
    with pytest.raises(DomainError) as no_actor:
        run(
            command(case_id, waiting, "doc-intelligence"),
            dataclasses.replace(ports, classifier=StubClassifier()),
            options=ClassifyOptions(actor=ACTOR),
        )
    assert no_actor.value.code is ErrorCode.VALIDATION_FAILED
    assert not [key for key in repository.rows if key.page_id == waiting]
    # Every failure is a stored answer; no classification is listed.
    assert len(repository.rows) == len(failures) + 1
    assert all(row.result_json is not None for row in repository.rows.values())
    assert repository.stored == {}


def test_story_4_2_without_an_endpoint_or_a_classifier_id_the_contender_is_refused(
    client: TestClient,
    pages: FakePages,
    model: StubModel,
    repository: MemoryRepository,
    case_id: str,
    settings: Settings,
) -> None:
    page_id = pages.add(case_id)
    body = {"case_id": case_id, "page_id": page_id, "contender": "doc-intelligence"}

    refused = client.post("/classifications", json=body)

    # Refused before any key row and before any read of `intake`.
    assert refused.status_code == 422
    assert (
        ErrorBody.model_validate(refused.json()).error.code
        is ErrorCode.VALIDATION_FAILED
    )
    assert (repository.rows, pages.listings) == ({}, [])
    # `llm` is unaffected.
    done = client.post("/classifications", json={**body, "contender": "llm"})
    assert (done.status_code, done.json()["status"], model.calls) == (200, "done", 5)
    # Available means both: an endpoint alone, or an id alone, names no actor.
    for half in (
        {"doc_intelligence_endpoint": "http://127.0.0.1:5102"},
        {"doc_intelligence_classifier_id": CLASSIFIER_ID},
    ):
        assert (
            classify_options(settings.model_copy(update=half)).classifier_actor is None
        )
    both = settings.model_copy(
        update={
            "doc_intelligence_endpoint": "http://127.0.0.1:5102",
            "doc_intelligence_classifier_id": CLASSIFIER_ID,
        }
    )
    assert classify_options(both).classifier_actor == CLASSIFIER_ACTOR


# --- Document Intelligence, behind a transport --------------------------------------------


@dataclass
class DocumentIntelligence:
    """Stands in for the service: an `httpx2` transport handler.

    `statuses` are answered first, one per call. Then: a classifier is there
    once it was built, a build and an analysis are `running` at the first
    look and ended at the second.
    """

    statuses: list[int] = field(default_factory=list)
    built: bool = False
    build_ends: str = "succeeded"
    # None: the analysis names no document.
    document: dict[str, Any] | None = field(
        default_factory=lambda: {"docType": "lab_report", "confidence": 0.93}
    )
    # A build of the classifier is under way already: a build request is
    # answered 409, and the classifier is there after this many reads of it.
    built_after_reads: int | None = None
    looks: int = 0
    requests: list[httpx2.Request] = field(default_factory=list)

    def sent(self, method: str, path_end: str) -> list[httpx2.Request]:
        return [
            request
            for request in self.requests
            if request.method == method and request.url.path.endswith(path_end)
        ]

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.statuses:
            return httpx2.Response(
                self.statuses.pop(0),
                headers={"retry-after": "7"},
                json={"error": {"code": "x", "message": "SECRET-SERVICE-MESSAGE"}},
            )
        path = request.url.path
        # The address the service names is on another host: only its id is used.
        elsewhere = "https://elsewhere.example/documentintelligence"
        if request.method == "POST" and path.endswith(":build"):
            if self.built_after_reads is not None:
                return httpx2.Response(409, json={"error": {"code": "Conflict"}})
            return httpx2.Response(
                202, headers={"operation-location": f"{elsewhere}/operations/op-1"}
            )
        if request.method == "POST" and path.endswith(":analyze"):
            return httpx2.Response(
                202,
                headers={
                    "operation-location": f"{elsewhere}/documentClassifiers/"
                    f"{CLASSIFIER_ID}/analyzeResults/result-1?api-version=x"
                },
            )
        if path == CLASSIFIER_PATH:
            if self.built_after_reads is not None:
                self.built_after_reads -= 1
                self.built = self.built_after_reads <= 0
            return httpx2.Response(200 if self.built else 404, json={})
        self.looks += 1
        if self.looks % 2:
            return httpx2.Response(200, json={"status": "running"})
        if path.endswith("/operations/op-1"):
            self.built = self.build_ends == "succeeded"
            return httpx2.Response(
                200,
                json={
                    "status": self.build_ends,
                    "error": {"code": "Training-Failed!", "message": "SECRET"},
                },
            )
        return httpx2.Response(
            200,
            json={
                "status": "succeeded",
                "analyzeResult": {
                    "documents": [self.document] if self.document else []
                },
            },
        )


class FakeCredential:
    """Stands in for the Azure identity library's credential."""

    def __init__(self) -> None:
        self.scopes: list[str] = []

    def get_token(self, *scopes: str, **options: Any) -> Any:
        self.scopes.extend(scopes)

        @dataclass
        class Token:
            token: str
            expires_on: float

        return Token("entra-token", time.time() + 3600.0)


def azure_settings() -> Settings:
    return Settings(
        applicationinsights_connection_string=None,
        doc_intelligence_endpoint=ENDPOINT,
        doc_intelligence_entra_auth=True,
        doc_intelligence_classifier_id=CLASSIFIER_ID,
        blob_account_url="https://staiuwdemowus3.blob.core.windows.net",
        azure_client_id="client-id",
    )


def on_classifier(service: DocumentIntelligence, work: Any) -> tuple[Any, list[float]]:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    async def scenario() -> Any:
        # The test's own sleep, in place of real waits.
        classifier = build_classifier(
            azure_settings(), httpx2.MockTransport(service.handle), sleep=sleep
        )
        try:
            return await work(classifier)
        finally:
            await classifier.aclose()

    return asyncio.run(scenario()), waits


def test_story_4_2_the_classifier_is_asked_over_rest_with_the_service_identity_and_no_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    credential = FakeCredential()
    monkeypatch.setattr(
        "classification.adapters.classifier.azure_credential",
        lambda settings: credential,
    )
    # Throttled once, then taken.
    service = DocumentIntelligence(statuses=[429], built=True)

    with caplog.at_level(logging.DEBUG, logger="classification"):
        answer, waits = on_classifier(
            service, lambda classifier: classifier.classify(PAGE_FILE)
        )

    # The service's own type and confidence, as it gave them.
    assert answer == ClassifierAnswer("lab_report", 0.93)
    (throttled, submit), looks = (
        service.sent("POST", ":analyze"),
        service.sent("GET", "/analyzeResults/result-1"),
    )
    assert str(submit.url) == (
        f"{ENDPOINT}{CLASSIFIER_PATH}:analyze?api-version={API_VERSION}&split=none"
    )
    # The page itself, as one document: its bytes, not a blob's address.
    assert json.loads(submit.content) == {
        "base64Source": base64.b64encode(PAGE_FILE).decode("ascii")
    }
    # Looked at until it ended, on the configured endpoint and never on the
    # address the service named. The throttled call waited as it was asked.
    assert len(looks) == 2
    assert {request.url.host for request in service.requests} == {
        "di-aiuw-demo-wus3.cognitiveservices.azure.com"
    }
    assert waits == [7.0, 1.0]
    # Identity only: an Entra token for Azure AI services on every call, no key.
    assert {request.headers["authorization"] for request in service.requests} == {
        "Bearer entra-token"
    }
    assert credential.scopes == [COGNITIVE_SERVICES_SCOPE]
    assert not any(
        "ocp-apim-subscription-key" in request.headers for request in service.requests
    )
    assert throttled.url == submit.url
    # Down after the retries, and refused: each its own error, no result invented.
    down = DocumentIntelligence(statuses=[503] * 20, built=True)
    with pytest.raises(ModelUnavailable):
        on_classifier(down, lambda classifier: classifier.classify(PAGE_FILE))
    assert len(down.requests) == 4
    # No such classifier (not trained yet) and not allowed (a role not yet
    # honoured) pass: an error of their own, which the stage does not store.
    for status in (404, 403):
        with pytest.raises(ClassifierNotReady) as not_ready:
            on_classifier(
                DocumentIntelligence(statuses=[status]),
                lambda classifier: classifier.classify(PAGE_FILE),
            )
        assert not_ready.value.reason == f"classifier_submit_status_{status}"
    with pytest.raises(ModelCallFailed) as refused:
        on_classifier(
            DocumentIntelligence(statuses=[400]),
            lambda classifier: classifier.classify(PAGE_FILE),
        )
    assert refused.value.reason == "classifier_submit_status_400"
    # An analysis that names no document is an answer without a type, which
    # the stage stores as failed.
    nothing, _ = on_classifier(
        DocumentIntelligence(built=True, document=None),
        lambda classifier: classifier.classify(PAGE_FILE),
    )
    assert nothing == ClassifierAnswer(None, None)
    # security rule 31: nothing of the page, the answer or the service's message.
    assert f"classifier_id={CLASSIFIER_ID}" in caplog.text
    for secret in ("SECRET", "lab_report", "JVBER"):
        assert secret not in caplog.text


# --- The training job ----------------------------------------------------------------------


MD5 = "0123456789abcdef0123456789abcdef"


def prepared(per_type: int, **other: int) -> list[ListedPage]:
    """`per_type` listed pages of every page type, each in its type's folder, but for those named."""
    return [
        ListedPage(f"{kind.value}/train-{number:03}.pdf", kind.value, MD5)
        for kind in PageType
        for number in range(other.get(kind.value, per_type))
    ]


def held(listed: list[ListedPage]) -> list[StoredBlob]:
    """The container as it should be: every listed page, once, with the listed content."""
    return [StoredBlob(page.file, page.md5) for page in listed]


def test_story_4_2_the_job_trains_the_classifier_once_and_refuses_pages_it_may_not_train_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        "classification.adapters.classifier.azure_credential",
        lambda settings: FakeCredential(),
    )
    container = FakeTrainingPages(prepared(5), held(prepared(5)))
    monkeypatch.setattr(train, "build_blob_service", lambda settings: None)
    monkeypatch.setattr(train, "BlobTrainingPages", lambda service, name: container)
    settings = azure_settings().model_copy(update={"training_poll_seconds": 0.001})
    service = DocumentIntelligence()

    def job() -> int:
        return train.main(settings, httpx2.MockTransport(service.handle))

    # No classifier yet: it is built from the container, and the job ends 0.
    with caplog.at_level(logging.INFO, logger="classification"):
        assert job() == train.OK
    (build,) = service.sent("POST", ":build")
    assert str(build.url) == (
        f"{ENDPOINT}/documentintelligence/documentClassifiers:build"
        f"?api-version={API_VERSION}"
    )
    body = json.loads(build.content)
    assert body["classifierId"] == CLASSIFIER_ID
    # One folder of the container per page type; the service reads it with
    # its own identity, so the address carries no key and no signature.
    assert body["docTypes"] == {
        kind.value: {
            "azureBlobSource": {
                "containerUrl": container.url,
                "prefix": f"{kind.value}/",
            }
        }
        for kind in PageType
    }
    assert "sig=" not in build.content.decode()
    assert build.headers["authorization"] == "Bearer entra-token"
    assert (
        f"training done: classifier_id={CLASSIFIER_ID} trained=yes pages=30 "
        "page_types=6" in caplog.text
    )

    # Again: the classifier exists, nothing is trained, the job ends 0.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="classification"):
        assert job() == train.OK
    assert len(service.sent("POST", ":build")) == 1
    assert "trained=no" in caplog.text

    # The container must hold exactly the listed pages, with the listed
    # content: anything else, anything missing or anything twice trains
    # nothing, and the job ends 1 with its own reason.
    good = prepared(5)
    first = good[0]
    other_content = StoredBlob(first.file, "f" * 32)
    refusals: list[tuple[list[ListedPage], list[StoredBlob], str]] = [
        # A page type with four pages.
        (
            prepared(5, invoice=4),
            held(prepared(5, invoice=4)),
            "reason=too_few_pages subject=invoice pages=4",
        ),
        # A PDF the list does not name, and a blob that is no PDF: the
        # service learns from whatever lies in a type's folder.
        (
            good,
            [*held(good), StoredBlob("lab_report/unlisted.pdf", None)],
            "reason=page_without_label subject=lab_report/unlisted.pdf",
        ),
        (
            good,
            [*held(good), StoredBlob("invoice/notes.txt", None)],
            "reason=page_without_label subject=invoice/notes.txt",
        ),
        # No list at all: the unredacted folder, uploaded as it is.
        ([], held(good), f"reason=page_without_label subject={first.file}"),
        # A listed page that is not there, and one that is listed twice.
        (good, held(good)[1:], f"reason=page_missing subject={first.file}"),
        ([*good, first], held(good), f"reason=page_listed_twice subject={first.file}"),
        # A page under a prepared page's name with other content: the
        # unredacted source has the same name.
        (
            good,
            [other_content, *held(good)[1:]],
            f"reason=page_content_differs subject={first.file}",
        ),
        # A page outside its type's folder.
        (
            [*good, ListedPage("other/train-900.pdf", "invoice", MD5)],
            [*held(good), StoredBlob("other/train-900.pdf", MD5)],
            "reason=page_outside_its_folder subject=other/train-900.pdf",
        ),
    ]
    for listed_pages, blobs, line in refusals:
        container.listed, container.blobs = listed_pages, blobs
        untrained = DocumentIntelligence()
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="classification"):
            status = train.main(settings, httpx2.MockTransport(untrained.handle))
        assert status == train.FAILED
        assert untrained.sent("POST", ":build") == []
        assert f"training failed: code=validation_failed {line}" in caplog.text
    container.listed, container.blobs = good, held(good)
    # A build that is under way already (the request was sent before, or
    # another run sent it) is waited for, inside the job's deadline.
    under_way = DocumentIntelligence(built_after_reads=3)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="classification"):
        assert train.main(settings, httpx2.MockTransport(under_way.handle)) == train.OK
    assert len(under_way.sent("GET", CLASSIFIER_PATH)) == 3
    assert "trained=yes" in caplog.text
    # A build the service fails ends the job 1, with the service's code only.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="classification"):
        failed = DocumentIntelligence(build_ends="failed")
        assert train.main(settings, httpx2.MockTransport(failed.handle)) == train.FAILED
    assert "reason=classifier_build_failed_TrainingFailed" in caplog.text
    assert "SECRET" not in caplog.text
    # Without its settings the job says which are missing and builds nothing.
    assert train.main(Settings(applicationinsights_connection_string=None)) == 1
    assert "reason=not_configured" in caplog.text

    # The list of prepared pages names each page with its label and the MD5
    # of the redacted file; a list in another shape is refused.
    entry = {"file": "invoice/a.pdf", "page_type": "invoice", "case_id": "c-1"}
    listed = json.dumps({"pages": [{**entry, "md5": MD5.upper()}]})
    assert listed_of(listed.encode()) == [ListedPage("invoice/a.pdf", "invoice", MD5)]
    for not_vouched_for in (
        # The generator's own list of unredacted pages names no case and no content.
        {"file": "invoice/a.pdf", "page_type": "invoice"},
        {**entry, "md5": MD5, "case_id": " "},
        entry,
    ):
        with pytest.raises(TrainingFailed) as malformed:
            listed_of(json.dumps({"pages": [not_vouched_for]}).encode())
        assert malformed.value.reason == "page_list_malformed"
