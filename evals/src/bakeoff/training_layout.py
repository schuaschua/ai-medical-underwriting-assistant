"""Make the layout result of every prepared training page: `uv run python -m bakeoff.training_layout`.

Document Intelligence builds a classifier only from a folder that holds,
beside every page, the result of its layout model for that page
(`<page>.pdf.ocr.json`); without them the build fails with
`TrainingContentMissing` (found in Azure on 2026-10-10). The training job
may not write the container, so an operator makes the files before the
upload (`infra/bootstrap/README.md`, section 10): this tool sends each
redacted page of `.work/classifier-training/` to the layout model, writes
the answer beside the page, and adds its MD5 to the list, which is how the
job knows the file.

Only redacted pages are sent: the ones the list names, each with the content
the list vouches for. The tool is in no image. It is given the account's
address and a token of the operator's own sign-in, and logs neither.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx
from pydantic import ValidationError

from bakeoff.client import Sleep
from bakeoff.settings import SCRATCH
from bakeoff.state import write_text_whole
from bakeoff.training_pages import MANIFEST_FILE, OCR_SUFFIX, Manifest

logger = logging.getLogger(__name__)

TOKEN_VARIABLE = "EVALS_DOC_INTELLIGENCE_TOKEN"  # noqa: S105 - the name of a variable
API_VERSION = "2024-11-30"
ANALYZE_PATH = "/documentintelligence/documentModels/prebuilt-layout:analyze"
POLL_SECONDS = 2.0
MAX_POLLS = 60

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2


class LayoutFailed(Exception):
    """A page's layout result could not be made; `reason` is a code, never page content."""

    def __init__(self, file: str, reason: str) -> None:
        super().__init__(f"{file}: {reason}")
        self.file = file
        self.reason = reason


async def layout_of(
    http: httpx.AsyncClient, file: str, pdf: bytes, sleep: Sleep
) -> bytes:
    """The layout model's whole answer for one page, as the service gave it."""
    submitted = await http.post(
        ANALYZE_PATH,
        json={"base64Source": base64.b64encode(pdf).decode("ascii")},
    )
    result = submitted.headers.get("operation-location", "")
    if submitted.status_code != 202 or not result.startswith(str(http.base_url)):
        raise LayoutFailed(file, f"submit_status_{submitted.status_code}")
    for _ in range(MAX_POLLS):
        await sleep(POLL_SECONDS)
        # The address without its query: the client adds the API version.
        answer = await http.get(result.partition("?")[0])
        if answer.status_code != 200:
            raise LayoutFailed(file, f"result_status_{answer.status_code}")
        status = str(answer.json().get("status", "")).lower()
        if status == "succeeded":
            return answer.content
        if status not in {"notstarted", "running"}:
            raise LayoutFailed(file, f"analysis_{status or 'without_status'}"[:40])
    raise LayoutFailed(file, "not_done_in_time")


async def run(
    folder: Path,
    endpoint: str,
    token: str,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Sleep = asyncio.sleep,
) -> int:
    """Write the layout result of every listed page and its MD5 into the list; how many."""
    manifest = Manifest.model_validate_json(
        (folder / MANIFEST_FILE).read_text(encoding="utf-8")
    )
    async with httpx.AsyncClient(
        base_url=endpoint.rstrip("/"),
        params={"api-version": API_VERSION},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
        transport=transport,
        follow_redirects=False,
        trust_env=False,
    ) as http:
        for page in manifest.pages:
            pdf = (folder / page.file).read_bytes()
            if hashlib.md5(pdf, usedforsecurity=False).hexdigest() != page.md5:
                # Not the redacted file the list vouches for: it is not sent.
                raise LayoutFailed(page.file, "page_content_differs")
            try:
                layout = await layout_of(http, page.file, pdf, sleep)
            except httpx.HTTPError as error:
                # The type only: the message can hold an address.
                raise LayoutFailed(page.file, type(error).__qualname__) from None
            (folder / f"{page.file}{OCR_SUFFIX}").write_bytes(layout)
            page.ocr_md5 = hashlib.md5(layout, usedforsecurity=False).hexdigest()
            logger.info("layout written: file=%s%s", page.file, OCR_SUFFIX)
    # The list last: it vouches for a layout result only when all are written.
    write_text_whole(
        folder / MANIFEST_FILE,
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n",
    )
    return len(manifest.pages)


def main(
    argv: Sequence[str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Sleep = asyncio.sleep,
) -> int:
    """Make the layout results; the exit status. Tests pass a transport that stands in for the service."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # The HTTP library logs every request with its address at this level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(
        prog="bakeoff.training_layout",
        description="Write the layout model's result beside every prepared "
        "training page and name it in the list.",
    )
    parser.add_argument(
        "--endpoint", required=True, help="the Document Intelligence account (https)"
    )
    parser.add_argument(
        "--training-pages-dir",
        type=Path,
        default=SCRATCH / "classifier-training",
        help="where the redacted pages and their list are",
    )
    given = parser.parse_args(argv)
    token = os.environ.get(TOKEN_VARIABLE, "")
    if not token or not given.endpoint.startswith("https://"):
        print(
            f"The run was refused: give an https --endpoint and a token in {TOKEN_VARIABLE}.",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    try:
        written = asyncio.run(
            run(given.training_pages_dir, given.endpoint, token, transport, sleep)
        )
    except (OSError, ValidationError) as error:
        print(
            f"The run was refused: the prepared pages or their list cannot be read "
            f"({type(error).__qualname__}).",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    except LayoutFailed as error:
        print(
            f"Stopped at {error.file}: {error.reason}. The list was not changed.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    print(f"layout results written: {written}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
