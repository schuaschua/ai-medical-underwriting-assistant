"""Prepare the classifier's training pages: `uv run python -m bakeoff.training_pages` (story 4.2).

The Document Intelligence classifier is trained on pages that passed the
same redaction as case pages. The training job calls no other service, and
only `intake` may call the redaction service, so the pages are redacted
before the job, by the pipeline itself:

1. each page of `data/classifier-training/` is uploaded through `web` as a
   case of an eval run, started with `stop_after` `gate`, so it is redacted
   as any case page, hidden from the queues, and never extracted;
2. when the case has come to rest, the redacted file `web` serves is fetched;
3. it is written into a local folder under its page type, and one list
   (`redacted-pages.json`) names every page with its label, the case it was
   redacted as and the MD5 of the redacted file.

An operator then uploads that folder to the `classifier-training` container
(`infra/bootstrap/README.md`). The list is what vouches for a page to the
training job, which trains only on a container that holds exactly the
listed files with the listed content: neither the unredacted source folder
nor an unredacted page under a prepared page's name (the sources have the
same names) trains anything.

The tool talks to `web` only and is in no image. A page whose case fails
stops it, naming the page; nothing is listed until every page is prepared.

One kind of page is skipped instead (owner's decision of 2026-10-10): a page
without any text layer whose case failed at redaction. `intake` passes a
blank page on, and fails a page whose text is only in its picture
(handwriting, a scan), because nobody checked that text for identifiers.
Such a page cannot be a training page; it is left out of the list, logged by
name, and the others are prepared. `web` shows the failure as
`redaction_failed` and not `intake`'s own reason for it, so the tool tells
the case apart by what it can see itself: that code, and a source file with
no word in its text layer.
Run again with the `eval_run_id` it logged, it uploads no page a second
time, but for a page whose case had failed: a failed case stays failed, so
that page is uploaded again as a new case. PDFs an earlier run left in the
folder that this run's list does not name are removed.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pymupdf
from pydantic import BaseModel, ConfigDict, ValidationError

from bakeoff.client import Sleep, WebClient, WebError, build_http_client, upload_key
from bakeoff.settings import Settings
from bakeoff.state import RunRefused, RunState, write_text_whole
from bakeoff.verdicts import Clock
from contracts.enums import CaseStatus, PageType, StopAfter
from contracts.errors import ErrorCode
from contracts.ids import CaseId, DocumentId, EvalRunId, new_id
from contracts.models.workflow import CaseProgress, StartCaseOptions

logger = logging.getLogger(__name__)

SOURCE_FOLDER = "classifier-training"
# The generator's list of the unredacted pages, and the list this tool writes.
SOURCE_LIST = "pages.json"
MANIFEST_FILE = "redacted-pages.json"
# A page's place: its page type's folder, and a plain file name.
_FILE = re.compile(r"^(?P<folder>[a-z_]+)/[A-Za-z0-9][A-Za-z0-9._-]*\.pdf$")
_PDF_MAGIC = b"%PDF-"

EXIT_OK = 0
# A page could not be prepared: the tool stopped and listed nothing.
EXIT_PAGE_FAILED = 1
# The run was refused: its settings or files, or `web` does not answer.
EXIT_REFUSED = 2


class SourcePage(BaseModel):
    """One entry of the generator's list: a page and its label."""

    model_config = ConfigDict(extra="ignore")

    file: str
    page_type: PageType
    layout: str | None = None


class _SourceList(BaseModel):
    pages: list[SourcePage]


class PreparedPage(BaseModel):
    """One redacted training page: where it is, its label, and the case it was redacted as."""

    file: str
    page_type: PageType
    layout: str | None
    case_id: CaseId
    document_id: DocumentId
    # The hex MD5 of the redacted file as it was written, which Blob Storage
    # keeps of a blob too. It guards against a wrong file in the right
    # place (the unredacted source has the same name), not against an attacker.
    md5: str


class Manifest(BaseModel):
    """The list beside the prepared pages; the training job reads its labels."""

    eval_run_id: EvalRunId
    pages: list[PreparedPage]


# Why a page is skipped: its text is only in its picture, and was not checked.
SKIPPED_REASON = "text_not_checked"


class PageSkipped(Exception):
    """One page cannot be a training page and is left out; the run goes on."""

    def __init__(self, file: str) -> None:
        super().__init__(f"{file}: {SKIPPED_REASON}")
        self.file = file


def has_no_text(pdf: bytes) -> bool:
    """Whether a file is a PDF none of whose pages has a word in its text layer."""
    try:
        with pymupdf.open(stream=pdf, filetype="pdf") as document:  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            return not any(str(page.get_text()).strip() for page in document)
    except (RuntimeError, ValueError):
        # Not a PDF that can be opened: not the case this looks for.
        return False


class PageFailed(Exception):
    """One page could not be prepared; `reason` is a code, never page content."""

    def __init__(self, file: str, reason: str) -> None:
        super().__init__(f"{file}: {reason}")
        self.file = file
        self.reason = reason


def read_source(folder: Path) -> list[SourcePage]:
    """The pages to prepare, as the generator lists them; a list that cannot be used refuses the run."""
    try:
        listed = _SourceList.model_validate_json(
            (folder / SOURCE_LIST).read_text(encoding="utf-8")
        ).pages
    except (OSError, ValidationError) as error:
        raise RunRefused(
            f"the list of training pages cannot be read ({type(error).__qualname__})"
        ) from None
    for page in listed:
        place = _FILE.fullmatch(page.file)
        if place is None or place["folder"] != page.page_type.value:
            raise RunRefused(f"{page.file} is not in the folder of its page type")
        if not (folder / page.file).is_file():
            raise RunRefused(f"no training page {page.file}")
    if not listed or len({page.file for page in listed}) != len(listed):
        raise RunRefused("the list of training pages is empty or names a page twice")
    return listed


@dataclass(frozen=True)
class Preparer:
    """Takes one training page through the pipeline and writes its redacted file."""

    client: WebClient
    state: RunState
    source_dir: Path
    output_dir: Path
    deadline_seconds: float
    poll_seconds: float
    sleep: Sleep
    clock: Clock = time.monotonic

    async def prepare(self, page: SourcePage) -> PreparedPage:
        try:
            return await self._prepare(page)
        except WebError as error:
            raise PageFailed(page.file, f"request_failed ({error})") from None
        except ValidationError:
            # An id `web` answered with is none: not raised from the
            # validation error, which repeats the answer.
            raise PageFailed(page.file, "answer_not_valid") from None
        except OSError as error:
            # The page's file or the run's state file. The error's type
            # only: its message holds a path.
            raise PageFailed(page.file, f"file_{type(error).__qualname__}") from None

    async def _prepare(self, page: SourcePage) -> PreparedPage:
        case_id, progress = await self._case_of(page)
        if progress is None:
            # A case of an eval run, stopped after the gate: redacted and
            # classified like any case, in no queue, and never extracted.
            await self.client.start(
                case_id,
                StartCaseOptions(
                    eval_run_id=self.state.eval_run_id, stop_after=StopAfter.GATE
                ),
            )
        await self._until_final(page, case_id)
        listed = (await self.client.pages(case_id)).pages
        if len(listed) != 1:
            # A training page is one page; anything else is not what was sent.
            raise PageFailed(page.file, f"pages_{len(listed)}")
        document_id = listed[0].document_id
        redacted = await self.client.document_file(document_id)
        if not redacted.startswith(_PDF_MAGIC):
            raise PageFailed(page.file, "not_a_pdf")
        target = self.output_dir / page.file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(redacted)
        logger.info("page prepared: file=%s case_id=%s", page.file, case_id)
        return PreparedPage(
            file=page.file,
            page_type=page.page_type,
            layout=page.layout,
            case_id=case_id,
            document_id=document_id,
            md5=hashlib.md5(redacted, usedforsecurity=False).hexdigest(),
        )

    async def _case_of(self, page: SourcePage) -> tuple[str, CaseProgress | None]:
        """The case the page is prepared as, uploading it where it must be; and its progress, if it was started."""
        recorded = self.state.case_id(page.file)
        key = upload_key(self.state.eval_run_id, page.file)
        if recorded is not None:
            # Uploaded once: a run that is resumed finds the case here.
            progress = await self.client.progress(recorded)
            if progress is None or progress.case_status is not CaseStatus.FAILED:
                return recorded, progress
            if progress.error_code is ErrorCode.REDACTION_FAILED and has_no_text(
                (self.source_dir / page.file).read_bytes()
            ):
                # It would fail the same way again: not uploaded a second time.
                raise PageSkipped(page.file)
            # The case an earlier start of this run made for the page ended
            # failed, and a failed case stays failed: the page is uploaded
            # again as a new case. The key names the failed case, so this
            # upload too is made once however often it is sent.
            logger.info(
                "page uploaded again: file=%s failed_case_id=%s", page.file, recorded
            )
            key = upload_key(self.state.eval_run_id, f"{page.file}:after:{recorded}")
        uploaded = await self.client.upload(
            (self.source_dir / page.file).read_bytes(), key
        )
        self.state.record(page.file, uploaded.case_id)
        return uploaded.case_id, await self.client.progress(uploaded.case_id)

    async def _until_final(self, page: SourcePage, case_id: str) -> None:
        give_up_at = self.clock() + self.deadline_seconds
        while True:
            progress = await self.client.progress(case_id)
            status = progress.case_status if progress is not None else None
            if status is CaseStatus.COMPLETED:
                return
            if status is CaseStatus.FAILED:
                code = progress.error_code if progress is not None else None
                if code is ErrorCode.REDACTION_FAILED and has_no_text(
                    (self.source_dir / page.file).read_bytes()
                ):
                    raise PageSkipped(page.file)
                raise PageFailed(
                    page.file, f"case_failed ({code.value if code else 'no code'})"
                )
            if self.clock() >= give_up_at:
                raise PageFailed(page.file, "not_final_in_time")
            await self.sleep(self.poll_seconds)


async def run(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Sleep = asyncio.sleep,
    clock: Clock = time.monotonic,
) -> Path:
    """Prepare every training page; the list that was written."""
    source_dir = settings.data_dir / SOURCE_FOLDER
    output_dir = settings.training_pages_dir
    if output_dir.resolve().is_relative_to(settings.data_dir.resolve()):
        # `data/` holds the generator's output and nothing else.
        raise RunRefused("the prepared pages are not written into data/")
    pages = read_source(source_dir)
    eval_run_id = settings.eval_run_id or new_id()
    try:
        state = RunState(settings.state_dir, eval_run_id, settings.web_address)
        # A folder without its list trains nothing: the list is written
        # last, when every page in it is the redacted one.
        (output_dir / MANIFEST_FILE).unlink(missing_ok=True)
    except (ValueError, OSError) as error:
        raise RunRefused(
            f"the run's files cannot be used ({type(error).__qualname__})"
        ) from error
    logger.info("training pages: eval_run_id=%s pages=%d", eval_run_id, len(pages))
    async with build_http_client(settings, transport) as http:
        client = WebClient(http, settings, sleep)
        try:
            await client.me()
        except WebError as error:
            raise RunRefused(f"web does not answer at its address ({error})") from None
        preparer = Preparer(
            client=client,
            state=state,
            source_dir=source_dir,
            output_dir=output_dir,
            deadline_seconds=settings.case_deadline_seconds,
            poll_seconds=settings.poll_seconds,
            sleep=sleep,
            clock=clock,
        )
        limit = asyncio.Semaphore(settings.case_concurrency)

        async def one(page: SourcePage) -> PreparedPage | None:
            async with limit:
                try:
                    return await preparer.prepare(page)
                except PageSkipped:
                    logger.warning(
                        "page skipped: file=%s reason=%s", page.file, SKIPPED_REASON
                    )
                    return None

        tasks = [asyncio.create_task(one(page)) for page in pages]
        try:
            outcomes = list(await asyncio.gather(*tasks))
        finally:
            # The first page that fails stops the others.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    prepared = [page for page in outcomes if page is not None]
    skipped = [
        page.file
        for page, outcome in zip(pages, outcomes, strict=True)
        if outcome is None
    ]
    # Said once more at the end, where an operator looks: the list that is
    # written does not name these pages, and the classifier is not trained on them.
    logger.info(
        "training pages: prepared=%d skipped=%d skipped_files=%s",
        len(prepared),
        len(skipped),
        ",".join(skipped) or "none",
    )
    manifest = output_dir / MANIFEST_FILE
    try:
        # What an earlier run left: a PDF this run's list does not name
        # would be uploaded with the folder, and is no page of this run.
        named = {(output_dir / page.file).resolve() for page in prepared}
        for left in output_dir.rglob("*.pdf"):
            if left.is_file() and left.resolve() not in named:
                left.unlink()
        write_text_whole(
            manifest,
            json.dumps(
                Manifest(eval_run_id=eval_run_id, pages=prepared).model_dump(
                    mode="json"
                ),
                indent=2,
            )
            + "\n",
        )
    except OSError as error:
        raise PageFailed(MANIFEST_FILE, f"file_{type(error).__qualname__}") from None
    return manifest


def _arguments(argv: Sequence[str] | None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(
        prog="bakeoff.training_pages",
        description="Redact the classifier's training pages through web and "
        "write them, with their labels, into a local folder.",
    )
    parser.add_argument("--web-address", help="where web is (EVALS_WEB_ADDRESS)")
    parser.add_argument(
        "--eval-run-id", help="resume the run with this id instead of starting one"
    )
    parser.add_argument("--case-concurrency", type=int, help="pages under way at once")
    parser.add_argument(
        "--training-pages-dir", help="where the redacted pages are written"
    )
    given = vars(parser.parse_args(argv))
    return {name: value for name, value in given.items() if value is not None}


def main(
    argv: Sequence[str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """Prepare the pages; the exit status. Tests pass a transport that stands in for `web`."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # The HTTP library logs every request with its address at this level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = Settings(**_arguments(argv))
    except ValidationError as error:
        print(f"The run was refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        manifest = asyncio.run(run(settings, transport))
    except RunRefused as error:
        print(f"The run was refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except PageFailed as error:
        print(
            f"Stopped at {error.file}: {error.reason}. Nothing was listed. Pages "
            "already uploaded are kept: start again with the eval_run_id logged above.",
            file=sys.stderr,
        )
        return EXIT_PAGE_FAILED
    print(f"written: {manifest}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
