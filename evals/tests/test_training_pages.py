"""Story 4.2: the classifier's training pages are redacted by the pipeline before a classifier is trained on them.

The tool that prepares them, first against a stand-in for `web`, then with
the system as it really runs: `web`, `workflow`, `intake` and
`classification` against a real PostgreSQL, the Durable Task Scheduler
emulator and the blob emulator (`docker compose up --detach --wait`), with
the stand-ins where Azure AI Language, the Foundry chat deployment and
Document Intelligence would be. The prepared pages are then uploaded as an
operator uploads them, the training job is run, and a case is started with
the classifier it built.
"""

import asyncio
import hashlib
import json
import logging
from pathlib import Path

import pymupdf
import pytest
from bakeoff_fakes import FakeWeb
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from fastapi.testclient import TestClient
from synthdata_stack import (
    CLASSIFIER_ID,
    LocalClassification,
    LocalExtraction,
    LocalIntake,
    LocalVerdict,
    RunningService,
    ServicesBehindSidecar,
    end_lifecycle,
    start_and_wait,
    web_service,
    workflow_service,
)

from bakeoff import training_pages
from bakeoff.settings import Settings
from classification.adapters.blob import build_blob_service
from contracts.enums import PageType
from contracts.ids import new_id
from contracts.models.workflow import CaseList
from synthdata.classifier_standin import Mode
from synthdata.training import TRAINING_SUBJECTS
from workflow.settings import Settings as WorkflowSettings

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRAINING_SET = REPOSITORY_ROOT / "data" / "classifier-training"
UNDERWRITER = {"X-Demo-Role": "underwriter"}


def write_source(data_dir: Path, *files: str) -> None:
    """A made-up training set as `data/classifier-training/` holds one."""
    folder = data_dir / "classifier-training"
    for file in files:
        (folder / file).parent.mkdir(parents=True, exist_ok=True)
        # No real document: the fake tells the pages apart by these bytes.
        (folder / file).write_bytes(f"%PDF-1.7 {file}".encode())
    (folder / "pages.json").write_text(
        json.dumps(
            {
                "pages": [
                    {"file": file, "page_type": file.split("/")[0], "layout": "x"}
                    for file in files
                ]
            }
        )
    )


def test_story_4_2_each_training_page_is_uploaded_as_an_eval_case_and_its_redacted_file_written_with_its_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = ("lab_report/train-a.pdf", "invoice/train-b.pdf")
    write_source(tmp_path / "data", *files)
    out = tmp_path / "pages"
    for name, value in {
        "DATA_DIR": tmp_path / "data",
        "STATE_DIR": tmp_path / "state",
        "TRAINING_PAGES_DIR": out,
        "POLL_SECONDS": "0.01",
        "RETRY_SECONDS": "0",
    }.items():
        monkeypatch.setenv(f"EVALS_{name}", str(value))
    eval_run_id = new_id()
    web = FakeWeb(failing={"invoice/train-b.pdf"})
    # A PDF an earlier run left in the folder.
    left_over = out / "other" / "train-old.pdf"
    left_over.parent.mkdir(parents=True)
    left_over.write_bytes(b"%PDF-1.7 from an earlier run")

    def tool() -> int:
        return training_pages.main(["--eval-run-id", eval_run_id], web.transport)

    # A page whose case fails stops the tool, naming the page; no list is
    # written, so the folder trains nothing.
    assert tool() == training_pages.EXIT_PAGE_FAILED
    assert "Stopped at invoice/train-b.pdf: case_failed" in capsys.readouterr().err
    assert not (out / "redacted-pages.json").exists()

    # Run again with the same id once the cause is gone. The failed case
    # stays failed, so its page is uploaded again as a new case; the other
    # page is not uploaded a second time. Every page is written with its
    # label, and a third run uploads nothing more.
    web.failing.clear()
    assert tool() == training_pages.EXIT_OK
    assert tool() == training_pages.EXIT_OK
    assert sorted(web.uploads) == [files[1], files[1], files[0]]
    assert web.case(files[1]).failed and len(web.cases) == 3
    listed = json.loads((out / "redacted-pages.json").read_text())
    assert listed["eval_run_id"] == eval_run_id
    assert [(page["file"], page["page_type"]) for page in listed["pages"]] == [
        ("lab_report/train-a.pdf", "lab_report"),
        ("invoice/train-b.pdf", "invoice"),
    ]
    for page in listed["pages"]:
        case = web.cases[page["case_id"]]
        assert (case.case_key, case.failed) == (page["file"], False)
        # A case of an eval run, stopped after the gate: redacted like any
        # case page, in no queue, and never extracted or given a verdict.
        assert case.started_with == {"eval_run_id": eval_run_id, "stop_after": "gate"}
        # What is written is the redacted file `web` serves, not the upload.
        written = (out / page["file"]).read_bytes()
        assert written == f"%PDF-1.7 redacted {page['file']}".encode()
        # The list vouches for that content: the job refuses any other.
        assert page["md5"] == hashlib.md5(written, usedforsecurity=False).hexdigest()
    # The folder holds this run's pages and their list, and nothing else.
    assert sorted(path.name for path in out.rglob("*") if path.is_file()) == [
        "redacted-pages.json",
        "train-a.pdf",
        "train-b.pdf",
    ]
    # Uploaded as the customer, started and read as the underwriter; and
    # `web` is the only thing the tool talks to.
    assert ("customer", "POST", "cases") in web.roles
    assert ("underwriter", "GET", "documents/file") in web.roles
    # The prepared pages never go into `data/`, beside the unredacted ones.
    monkeypatch.setenv("EVALS_TRAINING_PAGES_DIR", str(tmp_path / "data" / "out"))
    assert tool() == training_pages.EXIT_REFUSED


@pytest.mark.integration
def test_story_4_2_the_training_set_is_redacted_by_the_pipeline_a_classifier_is_trained_and_a_case_is_gated_on_its_result(
    workflow_service_settings: WorkflowSettings,
    scheduler_client: DurableTaskSchedulerClient,
    intake: LocalIntake,
    classification: LocalClassification,
    extraction: LocalExtraction,
    verdict: LocalVerdict,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stand_in = classification.with_classifier()
    eval_run_id = new_id()
    out = tmp_path / "pages"
    settings = Settings(
        eval_run_id=eval_run_id,
        state_dir=tmp_path / "state",
        training_pages_dir=out,
        poll_seconds=0.1,
        case_deadline_seconds=180.0,
        case_concurrency=4,
    )
    source = json.loads((TRAINING_SET / "pages.json").read_text())["pages"]
    behind_workflow = ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(),
    )
    case_ids: dict[str, str] = {}

    # --- 1. The training pages are prepared through `web`, and nothing else.
    with workflow_service(workflow_service_settings, behind_workflow) as workflow:
        behind_web = ServicesBehindSidecar(
            intake=intake.app(),
            workflow=RunningService(workflow),
            classification=classification.app(),
        )
        with web_service(tmp_path, behind_web) as web:
            try:
                with caplog.at_level(logging.INFO, logger="bakeoff"):
                    manifest = asyncio.run(
                        training_pages.run(settings, RunningService(web))
                    )
            finally:
                state = tmp_path / "state" / f"{eval_run_id}.json"
                if state.is_file():
                    case_ids = json.loads(state.read_text())["cases"]
                for case_id in case_ids.values():
                    end_lifecycle(scheduler_client, case_id)
            listed_cases = cases_listed(web)

    listed = json.loads(manifest.read_text())["pages"]
    # One page is left out (owner's decision of 2026-10-10): the handwritten
    # note is a picture with no text layer, the redaction service finds no
    # text in it, the read model finds words in its picture, and `intake`
    # fails it because nobody checked those words. The two blank pages have
    # no text either and pass. The tool skips the note, names it, and
    # prepares the others; every page type still has its five pages.
    note = "attending_physician_statement/train-001-p09-handwritten_note.pdf"
    assert [(page["file"], page["page_type"]) for page in listed] == [
        (page["file"], page["page_type"]) for page in source if page["file"] != note
    ]
    assert len(source) == 46 and len(listed) == 45
    assert {page["case_id"] for page in listed} == set(case_ids.values()) - {
        case_ids[note]
    }
    assert f"page skipped: file={note} reason=text_not_checked" in caplog.text
    assert f"prepared=45 skipped=1 skipped_files={note}" in caplog.text
    assert not (out / note).exists()
    assert {"other/train-002-p09-blank.pdf", "other/train-005-p09-blank.pdf"} <= {
        page["file"] for page in listed
    }
    per_type: dict[str, int] = {}
    for page in listed:
        per_type[page["page_type"]] = per_type.get(page["page_type"], 0) + 1
    assert min(per_type.values()) >= 5 and len(per_type) == len(PageType)
    # Each has passed the same redaction as a case page: it is the file of
    # record `web` serves, one page, with the people on it masked.
    people = {
        value for subject in TRAINING_SUBJECTS for _, value in subject.identifiers()
    }
    masked = 0
    for page in listed:
        with pymupdf.open(out / page["file"]) as redacted:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            assert redacted.page_count == 1
        # The redacted file is a picture with no text but its masks' labels:
        # what is checked is the page text `intake` stored for it, which is
        # what was read from that picture.
        (stored,) = intake.pages(page["case_id"]).pages
        text = " ".join(intake.page_text(stored.page_id).split())
        with pymupdf.open(TRAINING_SET / page["file"]) as original:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            planted = [
                value
                for value in people
                if value in " ".join(str(original[0].get_text()).split())
            ]
        assert not [value for value in planted if value in text], page["file"]
        # A page that had a person on it was read: its text is not empty.
        assert text or not planted, page["file"]
        masked += bool(planted)
    assert masked >= 30
    # The pages were cases of an eval run: none is in the underwriter's list.
    assert listed_cases == []

    # --- 2. An operator uploads the folder; the job trains the classifier once.
    assert classification.upload_training_pages(out) == 46
    assert stand_in.classifiers == {}
    # The job trains only on what the list vouches for. A PDF beside the
    # listed ones, and an unredacted page put over a prepared one (the
    # sources have the same names), each end it 1, and no build is sent.
    container = build_blob_service(classification.settings).get_container_client(
        classification.settings.training_container
    )
    tampered = listed[0]["file"]
    refusals = [
        ("invoice/extra.pdf", "reason=page_without_label subject=invoice/extra.pdf"),
        (tampered, f"reason=page_content_differs subject={tampered}"),
    ]
    for name, line in refusals:
        container.upload_blob(
            name, (TRAINING_SET / tampered).read_bytes(), overwrite=True
        )
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="classification"):
            assert classification.train() == 1
        assert f"training failed: code=validation_failed {line}" in caplog.text
        assert stand_in.build_requests == [] and stand_in.classifiers == {}
        # The operator uploads the prepared folder anew.
        assert classification.upload_training_pages(out) == 46
    assert classification.train() == 0
    assert list(stand_in.classifiers) == [CLASSIFIER_ID]
    (build,) = stand_in.build_requests
    assert set(build["docTypes"]) == {kind.value for kind in PageType}
    assert {
        source["azureBlobSource"]["containerUrl"].rpartition("/")[2]
        for source in build["docTypes"].values()
    } == {classification.settings.training_container}
    # Again: the classifier exists, nothing is trained, the job ends 0.
    assert classification.train() == 0
    assert len(stand_in.build_requests) == 1

    # --- 3. A case started with the classifier is gated on its result alone.
    # The classifier is unsure of every page; the chat model would be sure.
    stand_in.mode = Mode.UNSURE
    case_id, _ = intake.upload("case-001.pdf")
    sidecar = ServicesBehindSidecar(
        intake=intake.app(),
        classification=classification.app(),
        extraction=extraction.app(),
        verdict=verdict.app(),
    )
    progress, trail, _ = start_and_wait(
        workflow_service_settings,
        scheduler_client,
        sidecar,
        case_id,
        waits_for_a_human=True,
        classifier_contender="doc-intelligence",
    )
    assert progress.classifier_contender is not None
    assert progress.classifier_contender.value == "doc-intelligence"
    own = classification.listed(case_id).classifications
    assert [(item.contender.value, item.confidence) for item in own] == [
        ("doc-intelligence", 0.55)
    ] * 3
    # The pages are classified side by side: read in page order, not in the
    # order the results were stored.
    by_page = {item.page_id: item.page_type.value for item in own}
    assert [by_page[page.page_id] for page in progress.pages] == [
        "application_form",
        "attending_physician_statement",
        "lab_report",
    ]
    # Each page went to the classifier as a one-page document, cut by
    # `intake` from the redacted file; the chat model was not asked.
    files = [
        path for _, path in classification.reads_of_intake() if path.endswith("/file")
    ]
    assert len(files) == 3 and len(stand_in.analysed) >= 3
    for sent in stand_in.analysed[-3:]:
        with pymupdf.open(stream=sent, filetype="pdf") as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            assert document.page_count == 1
    model_calls = classification.model.calls
    # The other contender classifies one of the pages too: two stored
    # results for that page, one per contender.
    first = progress.pages[0].page_id
    with TestClient(classification.app()) as client:
        by_model = client.post(
            "/classifications",
            json={"case_id": case_id, "page_id": first, "contender": "llm"},
        ).json()
    assert by_model["classification"]["confidence"] == 1.0
    assert classification.model.calls == model_calls + 5
    both = [
        (item.contender.value, item.confidence)
        for item in classification.listed(case_id).classifications
        if item.page_id == first
    ]
    assert sorted(both) == [("doc-intelligence", 0.55), ("llm", 1.0)]
    # The gate used the one the case was started with: every page went to
    # triage on the classifier's 0.55, where the chat model's 1.0 would have
    # sent these medical pages on to extraction.
    assert [page.page_status.value for page in progress.pages] == [
        "awaiting_triage"
    ] * 3
    routed = [
        event.model_dump(mode="json")["detail"]
        for event in trail.events
        if event.action.value == "page.routed"
    ]
    assert routed == [{"route": "awaiting_triage", "threshold": 0.9}] * 3
    classified = [
        event.actor for event in trail.events if event.action.value == "page.classified"
    ]
    assert classified == [f"classification:{CLASSIFIER_ID}"] * 3


def cases_listed(web: TestClient) -> list[object]:
    """The underwriter's case list, as `web` answers it."""
    answer = web.get("/api/cases", headers=UNDERWRITER)
    return list(CaseList.model_validate(answer.json()).cases)
