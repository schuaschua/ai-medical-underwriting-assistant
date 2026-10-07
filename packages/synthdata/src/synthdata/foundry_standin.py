"""A local stand-in for the Foundry chat deployment that classifies pages (story 1.8).

The Azure environment is down while the stories are built, so
`classification`'s model gateway is proven against this: an HTTP app with the
route the gateway calls (`POST /openai/v1/chat/completions`). It reads the
page text out of the request and answers, in the shape of a chat completion,
with a page type and a one-line reason as the classifier's prompt asks for
them.

A dev tool only. It is part of `synthdata`, which no service depends on, so it
is in no service image; and `classification` refuses a plain-HTTP model
endpoint that is not on loopback, so it cannot stand in for the model in
Azure.

It is not a model. It tells the page types apart by the headings the
generator prints on the synthetic pages; it does not look at the picture.
What the real deployment answers, and whether its repeated runs differ at
all, is checked in Azure.

Run it: `uv run python -m synthdata.foundry_standin` (see README, 'Run locally').
"""

import argparse
import hashlib
import json
import threading
import time
import uuid
from collections import Counter
from enum import StrEnum
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from contracts.enums import PageType
from contracts.text import normalise

COMPLETIONS_PATH = "/openai/v1/chat/completions"
DEFAULT_PORT = 5101
# A deployment name for the local run. It is what the audit trail then names
# as the model; the real name is a setting of the `app` stack.
LOCAL_DEPLOYMENT = "local-stand-in"
# In the `disagree` mode, of every this many runs of one page ...
DISAGREE_CYCLE = 5
# ... the last this many name another page type.
DISAGREE_MINORITY = 2

# The heading that tells a page type, as the generator prints it, in the
# order they are looked for. A page with none of them is `other`: a payslip,
# a utility bill, a blank page.
_HEADINGS: tuple[tuple[str, PageType], ...] = (
    ("attending physician statement", PageType.ATTENDING_PHYSICIAN_STATEMENT),
    ("laboratory report", PageType.LAB_REPORT),
    ("application form", PageType.APPLICATION_FORM),
    ("passport", PageType.ID_DOCUMENT),
    ("identity card", PageType.ID_DOCUMENT),
    ("driving licence", PageType.ID_DOCUMENT),
    ("invoice", PageType.INVOICE),
)
# One reason per page type: a sentence of the stand-in's own, never page text.
_REASONS: dict[PageType, str] = {
    PageType.ATTENDING_PHYSICIAN_STATEMENT: (
        "The page is headed as a statement by the attending physician."
    ),
    PageType.LAB_REPORT: "The page is headed as a laboratory report with test results.",
    PageType.APPLICATION_FORM: "The page is headed as an insurance application form.",
    PageType.ID_DOCUMENT: "The page is headed as an identity document.",
    PageType.INVOICE: "The page is headed as an invoice with amounts to pay.",
    PageType.OTHER: "The page has none of the headings of the other page types.",
}


class Mode(StrEnum):
    """What the stand-in does with a call; tests switch it to see failures."""

    OK = "ok"
    # Repeated runs of one page differ: three of every five agree.
    DISAGREE = "disagree"
    # The answer is prose, not the JSON object that was asked for.
    INVALID = "invalid"
    # Every call is answered 429 with a `Retry-After`.
    THROTTLED = "throttled"


def classify_text(text: str) -> PageType:
    """The page type the stand-in gives a page, by the heading in its text."""
    normalised = normalise(text)
    for heading, page_type in _HEADINGS:
        if heading in normalised:
            return page_type
    return PageType.OTHER


def other_than(page_type: PageType) -> PageType:
    """The page type a disagreeing run names instead."""
    return PageType.INVOICE if page_type is PageType.OTHER else PageType.OTHER


def answer_for(page_type: PageType) -> str:
    """The classifier's answer for a page type, as the prompt asks for it."""
    return json.dumps({"page_type": page_type.value, "reason": _REASONS[page_type]})


def page_text_of(body: dict[str, Any]) -> str | None:
    """The page text of a classify request: the text part of its user message.

    None when the request is not what the gateway sends: a user message whose
    content holds a text part and a picture, and a JSON schema for the answer.
    """
    try:
        if body["response_format"]["type"] != "json_schema":
            return None
        (user,) = (message for message in body["messages"] if message["role"] == "user")
        parts = user["content"]
        texts = [part["text"] for part in parts if part["type"] == "text"]
        images = [part for part in parts if part["type"] == "image_url"]
    except (KeyError, TypeError, ValueError):
        return None
    if len(texts) != 1 or len(images) != 1 or not isinstance(texts[0], str):
        return None
    return texts[0]


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code
    )


class FoundryStandIn:
    """The stand-in's state and its HTTP app. Tests look at what it was asked."""

    def __init__(self, mode: Mode = Mode.OK, retry_after_seconds: int = 0) -> None:
        self.mode = mode
        # What a throttled call is told to wait; none by default, so that a
        # test or a local run fails at once instead of waiting.
        self.retry_after_seconds = retry_after_seconds
        # What it was asked, for tests: every request body, in order.
        self.requests: list[dict[str, Any]] = []
        # How often each page was run, by a digest of its text.
        self._runs: Counter[str] = Counter()
        self._lock = threading.Lock()

    @property
    def calls(self) -> int:
        """How many calls it has had, answered or not."""
        return len(self.requests)

    def _run_number(self, text: str) -> int:
        """Which run of this page a call is, counted from 0."""
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self._lock:
            number = self._runs[digest]
            self._runs[digest] += 1
        return number

    def answer(self, text: str) -> str:
        """The content of the completion for one run of one page."""
        if self.mode is Mode.INVALID:
            # What a model does when it ignores the format it was given.
            return "This page looks like a medical document to me."
        page_type = classify_text(text)
        run = self._run_number(text)
        if (
            self.mode is Mode.DISAGREE
            and run % DISAGREE_CYCLE >= DISAGREE_CYCLE - DISAGREE_MINORITY
        ):
            page_type = other_than(page_type)
        return answer_for(page_type)

    def complete(self, body: dict[str, Any]) -> Response:
        """Answer one chat completion request."""
        with self._lock:
            self.requests.append(body)
        if self.mode is Mode.THROTTLED:
            return JSONResponse(
                {"error": {"code": "429", "message": "Rate limit reached."}},
                status_code=429,
                headers={"retry-after": str(self.retry_after_seconds)},
            )
        text = page_text_of(body)
        model = body.get("model")
        if text is None or not isinstance(model, str) or not model:
            return _error(400, "invalid_request", "Not a page classification request.")
        return JSONResponse(
            {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": self.answer(text),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }
        )

    def app(self) -> FastAPI:
        """The HTTP app: the one route the gateway calls."""
        app = FastAPI(
            title="foundry-stand-in", docs_url=None, redoc_url=None, openapi_url=None
        )

        @app.post(COMPLETIONS_PATH)
        def complete(body: dict[str, Any]) -> Response:
            return self.complete(body)

        return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="synthdata.foundry_standin", description=__doc__
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--mode",
        type=Mode,
        choices=list(Mode),
        default=Mode.OK,
        help="what happens to every call (default: ok)",
    )
    args = parser.parse_args(argv)
    stand_in = FoundryStandIn(args.mode)
    # Loopback only: it is never reachable from another machine.
    uvicorn.run(stand_in.app(), host="127.0.0.1", port=args.port, server_header=False)


if __name__ == "__main__":
    main()
