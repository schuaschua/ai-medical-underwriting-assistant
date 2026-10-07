"""A local stand-in for the Foundry model deployments (stories 1.8, 2.2 and 2.4).

The Azure environment is down while the stories are built, so the services'
model gateways are proven against this: an HTTP app with the routes the
gateways call. On `POST /openai/v1/chat/completions` it answers, in the shape
of a chat completion, what the request's structured output asks for: for
`classification` a page type and a one-line reason, read from the page text
in the request; for `retrieval`'s ingestion job the context line of one rule
of the manual, built from the section and part the request names; for
`extraction` the facts of one page, each a statement and a quote, read from
the page text in the request. On `POST /openai/v1/embeddings` it answers with
one vector per text.

A dev tool only. It is part of `synthdata`, which no service depends on, so it
is in no service image; and the services refuse a plain-HTTP model endpoint
that is not on loopback, so it cannot stand in for the models in Azure.

It is not a model. It tells the page types apart by the headings the
generator prints on the synthetic pages; it does not look at the picture.
Its context line repeats the headings it was given. Its facts are the labelled
values and the table rows the generator prints on the medical pages: it knows
those labels and nothing of medicine. Its vectors count words:
each word of a text adds to one of the vector's dimensions, picked by a hash
of the word, so two texts are close when they share words and the same text
always gets the same vector; they know nothing of meaning. What the real
deployments answer, and whether repeated runs differ at all, is checked in
Azure.

Run it: `uv run python -m synthdata.foundry_standin` (see README, 'Run locally').
"""

import argparse
import hashlib
import json
import math
import re
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
from contracts.text import has_mask_token, normalise

COMPLETIONS_PATH = "/openai/v1/chat/completions"
EMBEDDINGS_PATH = "/openai/v1/embeddings"
DEFAULT_PORT = 5101
# The name of the structured output `retrieval`'s ingestion job asks for, and
# the one field of its answer.
CONTEXT_SCHEMA_NAME = "chunk_context"
CONTEXT_FIELD = "context_line"
# The size of a vector of `text-embedding-3-large`.
EMBEDDING_DIMENSIONS = 3072
# A local name for the embedding deployment; the real one is a setting of the
# `app` stack.
LOCAL_EMBEDDING_DEPLOYMENT = "local-stand-in-embedding"
# The name of the structured output `extraction` asks for, and the one field
# of its answer (story 2.4).
EXTRACTION_SCHEMA_NAME = "extraction_output"
EXTRACTION_FIELD = "facts"
# What the stand-in adds in the `quote_not_on_page` mode: a fact no synthetic
# page holds, so its quote is found on none of them.
FACT_NOT_ON_THE_PAGE = {
    "statement": "Resting heart rate 61 bpm",
    "quote": "Resting heart rate 61 bpm",
}
# What it adds in the `masked_value` mode: a masked value proposed as a fact,
# which the service must leave out.
MASKED_VALUE_FACT = {"statement": "Name: [Person]", "quote": "[Person]"}
# The labels the generator prints above a medical value, as the stored page
# text has them: the label on one line, its value on the next.
_FACT_LABELS = frozenset(
    {
        "height",
        "weight",
        "body mass index (bmi)",
        "smoking status",
        "alcohol",
        "condition",
        "current treatment",
        "family history",
        "latest hba1c",
    }
)
# The tests of the laboratory report's table: a row is the test, its result
# and its unit, each cell on a line of its own, in that order.
_LAB_TESTS = frozenset(
    {
        "hba1c",
        "fasting plasma glucose",
        "total cholesterol",
        "ldl cholesterol",
        "hdl cholesterol",
        "creatinine",
        "egfr",
    }
)
# The header rows of the two tables of the attending physician's statement.
_DIAGNOSES_HEADER = ("diagnosis", "diagnosed", "current treatment")
_BLOOD_PRESSURE_HEADER = ("date", "systolic (mmhg)", "diastolic (mmhg)")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_WORD = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
_SECTION_LINE = re.compile(r"^Section: (\d+) (.+)$", re.MULTILINE)
_PART_LINE = re.compile(r"^Part: ([\d.]+) (.+)$", re.MULTILINE)
# A deployment name for the local run. It is what the audit trail then names
# as the model; the real name is a setting of the `app` stack.
LOCAL_DEPLOYMENT = "local-stand-in"
# In the `disagree` mode, of every this many runs of one page ...
DISAGREE_CYCLE = 5
# ... the last this many name another page type.
DISAGREE_MINORITY = 2

# In the `mixed` mode, the page types whose runs disagree: one medical type
# and one that is not. A page's type is read from its own text, so the same
# page is always treated the same way.
MIXED_UNSURE_TYPES = frozenset({PageType.LAB_REPORT, PageType.ID_DOCUMENT})

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
    # Runs differ as in `disagree`, but only on pages of the types in
    # `MIXED_UNSURE_TYPES`: one case then has pages the classifier is sure of
    # and pages it is not, and shows every route of the gate.
    MIXED = "mixed"
    # The answer is prose, not the JSON object that was asked for.
    INVALID = "invalid"
    # Every call is answered 429 with a `Retry-After`.
    THROTTLED = "throttled"
    # Extraction (story 2.4): beside the facts of the page, one whose quote
    # is not on the page. Pages are classified as in `ok`.
    QUOTE_NOT_ON_PAGE = "quote_not_on_page"
    # Extraction: beside the facts of the page, a masked value proposed as a
    # fact. Pages are classified as in `ok`.
    MASKED_VALUE = "masked_value"


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


def rule_place_of(body: dict[str, Any]) -> str | None:
    """The user message of a context-line request: one rule and where it is printed.

    None when the request does not ask for the context line's structured
    output with one user message of plain text.
    """
    try:
        schema = body["response_format"]["json_schema"]
        if schema["name"] != CONTEXT_SCHEMA_NAME:
            return None
        (user,) = (message for message in body["messages"] if message["role"] == "user")
        content = user["content"]
    except (KeyError, TypeError, ValueError):
        return None
    return content if isinstance(content, str) and content.strip() else None


def context_line_for(rule_place: str) -> str:
    """The context line the stand-in gives a rule: the headings it was shown, in a sentence."""
    section = _SECTION_LINE.search(rule_place)
    part = _PART_LINE.search(rule_place)
    if section is None:
        return "A rule of the underwriting manual."
    line = f"From section {section.group(1)}, {section.group(2)}"
    if part is not None:
        line += f", part {part.group(1)} ({part.group(2)})"
    return f"{line}, of the underwriting manual: one rule of that section."


def page_to_extract_of(body: dict[str, Any]) -> str | None:
    """The page text of an extraction request: its user message, which is plain text.

    None when the request does not ask for the extraction's structured
    output with one user message of plain text.
    """
    try:
        schema = body["response_format"]["json_schema"]
        if schema["name"] != EXTRACTION_SCHEMA_NAME:
            return None
        (user,) = (message for message in body["messages"] if message["role"] == "user")
        content = user["content"]
    except (KeyError, TypeError, ValueError):
        return None
    return content if isinstance(content, str) else None


def _fact(statement: str, *quoted: str) -> dict[str, str]:
    # The quote is the page's own words, in the page's order, joined by one
    # space as a model would copy the cells of a row: the service's quote
    # check must find it across the line breaks of the stored text.
    return {"statement": statement, "quote": " ".join(quoted)}


def facts_in(text: str) -> list[dict[str, str]]:
    """The facts the stand-in reads from a page text, in the page's order.

    A labelled value (the label on one line, its value on the next), a row
    of the laboratory table (test, result, unit), a row of the diagnoses
    table (diagnosis, date, treatment) and a row of the blood pressure table
    (date, systolic, diastolic). A value that was masked by redaction is
    left alone. A page with none of these has no facts: an invoice, a
    payslip, a blank page.
    """
    lines = [line.strip() for line in text.split("\n")]
    folded = [line.casefold() for line in lines]
    facts: list[dict[str, str]] = []
    index = 0
    while index < len(lines):
        label = folded[index]
        if tuple(folded[index : index + 3]) == _DIAGNOSES_HEADER:
            index += 3
            while index + 2 < len(lines) and _ISO_DATE.fullmatch(lines[index + 1]):
                condition, diagnosed, treatment = lines[index : index + 3]
                facts.append(
                    _fact(
                        f"{condition}, diagnosed {diagnosed}, {treatment}",
                        condition,
                        diagnosed,
                        treatment,
                    )
                )
                index += 3
            continue
        if tuple(folded[index : index + 3]) == _BLOOD_PRESSURE_HEADER:
            index += 3
            while (
                index + 2 < len(lines)
                and _ISO_DATE.fullmatch(lines[index])
                and _NUMBER.fullmatch(lines[index + 1])
                and _NUMBER.fullmatch(lines[index + 2])
            ):
                taken_on, systolic, diastolic = lines[index : index + 3]
                facts.append(
                    _fact(
                        f"Blood pressure {systolic}/{diastolic} mmHg on {taken_on}",
                        taken_on,
                        systolic,
                        diastolic,
                    )
                )
                index += 3
            continue
        if (
            label in _LAB_TESTS
            and index + 2 < len(lines)
            and _NUMBER.fullmatch(lines[index + 1])
        ):
            test, value, unit = lines[index : index + 3]
            facts.append(_fact(f"{test} {value} {unit}", test, value, unit))
            index += 3
            continue
        if label in _FACT_LABELS and index + 1 < len(lines):
            value = lines[index + 1]
            # A value redaction masked is no fact, and neither is a label
            # that is followed by another label.
            if (
                value
                and not has_mask_token(value)
                and value.casefold() not in (_FACT_LABELS)
            ):
                facts.append(_fact(f"{lines[index]}: {value}", lines[index], value))
                index += 2
                continue
        index += 1
    return facts


def embed_text(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """A vector for a text: its words counted into hashed dimensions, at unit length.

    The same text always gets the same vector, and texts that share words
    are close (their cosine is high). A text without a word gets a vector
    along the first dimension, so that it still has a length.
    """
    vector = [0.0] * dimensions
    for word in _WORD.findall(text.lower()):
        digest = hashlib.sha256(word.encode()).digest()
        vector[int.from_bytes(digest[:8]) % dimensions] += 1.0
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0.0:
        vector[0] = 1.0
        return vector
    return [round(value / length, 8) for value in vector]


def texts_of(body: dict[str, Any]) -> list[str] | None:
    """The texts of an embedding request; None when it carries none."""
    given = body.get("input")
    texts = [given] if isinstance(given, str) else given
    if (
        not isinstance(texts, list)
        or not texts
        or not all(isinstance(text, str) and text for text in texts)
    ):
        return None
    return texts


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
        # What it was asked, for tests: every chat request body, in order,
        # and every embedding request body.
        self.requests: list[dict[str, Any]] = []
        self.embedding_requests: list[dict[str, Any]] = []
        # The size of the vectors it answers with; a test makes it wrong.
        self.embedding_dimensions = EMBEDDING_DIMENSIONS
        # How often each page was run, by a digest of its text.
        self._runs: Counter[str] = Counter()
        self._lock = threading.Lock()

    @property
    def calls(self) -> int:
        """How many chat calls it has had, answered or not."""
        return len(self.requests)

    @property
    def embedding_calls(self) -> int:
        """How many embedding calls it has had, answered or not."""
        return len(self.embedding_requests)

    def _throttled(self) -> Response:
        return JSONResponse(
            {"error": {"code": "429", "message": "Rate limit reached."}},
            status_code=429,
            headers={"retry-after": str(self.retry_after_seconds)},
        )

    def context_answer(self, rule_place: str) -> str:
        """The content of the completion for one rule's context line."""
        if self.mode is Mode.INVALID:
            # What a model does when it ignores the format it was given.
            return "This rule is about a medical impairment, I think."
        return json.dumps({CONTEXT_FIELD: context_line_for(rule_place)})

    def extraction_answer(self, page_text: str) -> str:
        """The content of the completion for one page's facts (story 2.4)."""
        if self.mode is Mode.INVALID:
            # What a model does when it ignores the format it was given.
            return "This page mentions a few medical findings, I think."
        facts = facts_in(page_text)
        if self.mode is Mode.QUOTE_NOT_ON_PAGE:
            facts.append(dict(FACT_NOT_ON_THE_PAGE))
        if self.mode is Mode.MASKED_VALUE:
            facts.append(dict(MASKED_VALUE_FACT))
        return json.dumps({EXTRACTION_FIELD: facts})

    @property
    def extraction_calls(self) -> int:
        """How many extraction calls it has had, answered or not."""
        return sum(page_to_extract_of(body) is not None for body in self.requests)

    def embed(self, body: dict[str, Any]) -> Response:
        """Answer one embedding request."""
        with self._lock:
            self.embedding_requests.append(body)
        if self.mode is Mode.THROTTLED:
            return self._throttled()
        texts = texts_of(body)
        model = body.get("model")
        if texts is None or not isinstance(model, str) or not model:
            return _error(400, "invalid_request", "Not an embedding request.")
        return JSONResponse(
            {
                "object": "list",
                "model": model,
                "data": [
                    {
                        "object": "embedding",
                        "index": index,
                        "embedding": embed_text(text, self.embedding_dimensions),
                    }
                    for index, text in enumerate(texts)
                ],
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            }
        )

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
        disagrees = self.mode is Mode.DISAGREE or (
            self.mode is Mode.MIXED and page_type in MIXED_UNSURE_TYPES
        )
        if disagrees and run % DISAGREE_CYCLE >= DISAGREE_CYCLE - DISAGREE_MINORITY:
            page_type = other_than(page_type)
        return answer_for(page_type)

    def complete(self, body: dict[str, Any]) -> Response:
        """Answer one chat completion request."""
        with self._lock:
            self.requests.append(body)
        if self.mode is Mode.THROTTLED:
            return self._throttled()
        model = body.get("model")
        rule_place = rule_place_of(body)
        to_extract = page_to_extract_of(body) if rule_place is None else None
        text = page_text_of(body) if rule_place is None and to_extract is None else None
        if (
            (rule_place is None and to_extract is None and text is None)
            or not isinstance(model, str)
            or not model
        ):
            return _error(
                400,
                "invalid_request",
                "Not a page classification, a context line or an extraction request.",
            )
        if to_extract is not None:
            content = self.extraction_answer(to_extract)
        else:
            content = (
                self.context_answer(rule_place)
                if rule_place is not None
                else self.answer(text or "")
            )
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
                            "content": content,
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
        """The HTTP app: the two routes the gateways call."""
        app = FastAPI(
            title="foundry-stand-in", docs_url=None, redoc_url=None, openapi_url=None
        )

        @app.post(COMPLETIONS_PATH)
        def complete(body: dict[str, Any]) -> Response:
            return self.complete(body)

        @app.post(EMBEDDINGS_PATH)
        def embed(body: dict[str, Any]) -> Response:
            return self.embed(body)

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
