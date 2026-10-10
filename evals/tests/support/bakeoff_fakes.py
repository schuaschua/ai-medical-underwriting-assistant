"""A stand-in for `web` that the runner's tests drive, and a small made-up case set.

The fake answers the routes the runner uses as `web` answers them: it checks
the demo role of every call, keeps a case's pages and their statuses, takes a
decision only from the role that owns it, and answers a search with what a
test told it to. It knows nothing of the answer key: a test says what the
system would do, and the runner is scored against that.

Every name, number and reading here is invented for these tests.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from bakeoff.settings import Settings
from contracts.decisions import DECISION_RULES
from contracts.enums import Decision, PageStatus, PageType
from contracts.errors import DomainError, ErrorCode
from contracts.ids import new_id
from contracts.models.verdict import SUGGESTION_LABEL
from contracts.rules import is_medical

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
_FINAL_PAGES = frozenset(
    {PageStatus.EXTRACTED, PageStatus.DISCARDED, PageStatus.DENIED}
)


def key_entry(
    case_key: str,
    *,
    verdict: str = "standard",
    loading_pct: int | None = None,
    facts: tuple[tuple[str, tuple[str, ...]], ...] = (),
    medical: tuple[bool, ...] = (True,),
    identifiers: tuple[tuple[str, str], ...] = (),
    may_also_be_redacted: tuple[str, ...] = (),
) -> dict[str, Any]:
    """One answer key entry, with the fields the generator writes."""
    return {
        "case_id": case_key,
        "file_name": f"{case_key}.pdf",
        "summary": "A made-up case.",
        "pages": [
            {
                "page_number": number,
                "page_type": "lab_report" if is_medical else "other",
                "is_medical": is_medical,
            }
            for number, is_medical in enumerate(medical, start=1)
        ],
        "identifiers": [
            {"category": category, "value": value, "pages": [1]}
            for category, value in identifiers
        ],
        "may_also_be_redacted": list(may_also_be_redacted),
        "expected_facts": [
            {"kind": "reading", "statement": statement, "rule_ids": list(rule_ids)}
            for statement, rule_ids in facts
        ],
        "expected_rule_ids": sorted({rule for _, rules in facts for rule in rules}),
        "expected_verdict": {"verdict": verdict, "loading_pct": loading_pct},
    }


def write_case_set(data_dir: Path, entries: list[dict[str, Any]]) -> None:
    """Write a case set as `data/` holds one: a PDF per case, its answer key entry, and the page set."""
    (data_dir / "cases").mkdir(parents=True)
    (data_dir / "answer-key" / "cases").mkdir(parents=True)
    # The scored page set is the whole case set, as in `data/`.
    (data_dir / "answer-key" / "page-set.json").write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "case_id": entry["case_id"],
                        "file_name": entry["file_name"],
                        "mixed": False,
                        "pages": [
                            {**page, "kind": "medical"} for page in entry["pages"]
                        ],
                    }
                    for entry in entries
                ]
            }
        ),
        encoding="utf-8",
    )
    for entry in entries:
        key = entry["case_id"]
        # No real document: the fake tells the cases apart by these bytes.
        (data_dir / "cases" / f"{key}.pdf").write_bytes(f"%PDF-1.7 {key}".encode())
        (data_dir / "answer-key" / "cases" / f"{key}.json").write_text(
            json.dumps(entry), encoding="utf-8"
        )


def settings_for(tmp_path: Path, **changes: Any) -> Settings:
    """The runner's settings for a test: its own folders, and no waiting between tries."""
    values: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "output_dir": tmp_path / "out",
        "state_dir": tmp_path / "state",
        "retry_seconds": 0.0,
        "poll_seconds": 1.0,
        **changes,
    }
    return Settings(**values)


class Clock:
    """A clock a test moves: the runner's waits pass at once and still count."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class _Case:
    case_key: str
    case_id: str
    pages: list[dict[str, Any]]
    started_with: dict[str, Any] | None = None
    # Once a case was answered as failed it stays failed, as a real one does.
    failed: bool = False


def _refusal(code: ErrorCode, status: int | None = None) -> httpx.Response:
    error = DomainError(code, "Refused.")
    return httpx.Response(
        status or error.http_status,
        json=error.to_body(TRACE_ID).model_dump(mode="json"),
    )


@dataclass
class FakeWeb:
    """`web`, as far as the runner can see it."""

    # The rows `retrieval` can search with; any other answers "not available".
    available: set[str] = field(default_factory=lambda: {"r1", "r2", "r3"})
    # The rule ids a search answers, by row and query; nothing when not told.
    found: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    # Searches that get no usable answer, by row and query.
    broken_searches: set[tuple[str, str]] = field(default_factory=set)
    # Searches that get no usable answer the first time and are answered the next.
    flaky_searches: set[tuple[str, str]] = field(default_factory=set)
    # Cases whose start gets no usable answer, however often it is sent.
    refused_starts: set[str] = field(default_factory=set)
    latency_ms: dict[str, int] = field(default_factory=dict)
    # A case's pages as the gate left them: the status of each, by page number.
    pages: dict[str, list[str]] = field(default_factory=dict)
    # The stored text of a case's pages, by page number; empty when not told.
    texts: dict[str, dict[int, str]] = field(default_factory=dict)
    # What each row's run suggests for a case: the verdict and the loading.
    verdicts: dict[str, dict[str, tuple[str, int | None]]] = field(default_factory=dict)
    # Runs, by case and row, that ended as failed, and runs the case does not hold.
    failed_runs: set[tuple[str, str]] = field(default_factory=set)
    missing_runs: set[tuple[str, str]] = field(default_factory=set)
    # Cases that end as failed, and cases that never become final.
    failing: set[str] = field(default_factory=set)
    hanging: set[str] = field(default_factory=set)
    # Page texts that cannot be read, by case and page number.
    broken_texts: set[tuple[str, int]] = field(default_factory=set)

    # --- The classification bake-off (story 4.3) ---
    # The classifier contenders `classification` can run; a case started
    # with any other fails with no page classified.
    contenders: set[str] = field(default_factory=lambda: {"llm", "doc-intelligence"})
    # What a contender stored for each page of a case, in page order: the
    # page type, the confidence, the reason and where the gate put the page.
    # None for a page whose classification failed, which fails the case.
    # `classification` does not answer a read of a case's classifications.
    classification_down: bool = False
    classified: dict[tuple[str, str], list[tuple[str, float, str, str] | None]] = field(
        default_factory=dict
    )

    cases: dict[str, _Case] = field(default_factory=dict)
    uploads: list[str] = field(default_factory=list)
    starts: list[str] = field(default_factory=list)
    searches: list[dict[str, Any]] = field(default_factory=list)
    # One entry per search a row answered "not available" to.
    refused_rows: list[str] = field(default_factory=list)
    decisions: list[tuple[str, int, str, str]] = field(default_factory=list)
    roles: set[tuple[str, str, str]] = field(default_factory=set)
    _by_upload_key: dict[str, str] = field(default_factory=dict)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def case(self, case_key: str) -> _Case:
        return next(case for case in self.cases.values() if case.case_key == case_key)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        role = request.headers.get("X-Demo-Role")
        if role not in {"customer", "underwriter"}:
            return _refusal(ErrorCode.INVALID_ROLE)
        parts = request.url.path.removeprefix("/api/").split("/")
        route = (request.method, parts[0], parts[2] if len(parts) > 2 else "")
        self.roles.add((role, request.method, "/".join(parts[::2])))
        if route == ("GET", "me", ""):
            return httpx.Response(200, json={"role": role})
        if route == ("POST", "cases", "") and role == "customer":
            return self._upload(request)
        if role == "customer" and route != ("POST", "cases", "pages"):
            # Everything else but a decision is the underwriter's.
            return _refusal(ErrorCode.ROLE_NOT_ALLOWED)
        if route == ("POST", "searches", ""):
            return self._search(json.loads(request.content))
        if parts[0] == "pages" and route[2] == "text":
            return self._text(parts[1])
        if parts[0] == "documents" and route[2] == "file":
            return self._file(parts[1])
        case = self.cases.get(parts[1]) if parts[0] == "cases" else None
        if case is None:
            return _refusal(ErrorCode.NOT_FOUND)
        if route[2] == "start":
            return self._start(case, json.loads(request.content))
        if route[2] == "progress":
            return self._progress(case)
        if route == ("POST", "cases", "pages"):
            decision = Decision(json.loads(request.content)["decision"])
            return self._decide(case, parts[3], decision, role)
        if route[2] == "verdict-runs":
            return self._runs(case)
        if route[2] == "classifications":
            return self._classifications(case)
        if route == ("GET", "cases", "pages"):
            return httpx.Response(
                200,
                json={
                    "case_id": case.case_id,
                    "pages": [
                        {
                            "page_id": page["page_id"],
                            "case_id": case.case_id,
                            "document_id": case.case_id,
                            "page_number": page["page_number"],
                        }
                        for page in case.pages
                    ],
                },
            )
        return _refusal(ErrorCode.NOT_FOUND)

    def _upload(self, request: httpx.Request) -> httpx.Response:
        case_key = request.content.decode().removeprefix("%PDF-1.7 ")
        upload_key = request.headers["Idempotency-Key"]
        self.uploads.append(case_key)
        if upload_key not in self._by_upload_key:
            case_id = new_id()
            self._by_upload_key[upload_key] = case_id
            self.cases[case_id] = _Case(
                case_key,
                case_id,
                [
                    {"page_id": new_id(), "page_number": number, "page_status": status}
                    for number, status in enumerate(
                        self.pages.get(case_key, ["extracted"]), start=1
                    )
                ],
            )
        case_id = self._by_upload_key[upload_key]
        return httpx.Response(201, json={"case_id": case_id, "document_id": case_id})

    def _start(self, case: _Case, options: dict[str, Any]) -> httpx.Response:
        if not set(options.get("retriever_configs", [])) <= self.available:
            # As `web` answers a start `workflow` refuses for its rows.
            return _refusal(ErrorCode.UPSTREAM_UNAVAILABLE)
        if case.case_key in self.refused_starts:
            return _refusal(ErrorCode.UPSTREAM_UNAVAILABLE)
        self.starts.append(case.case_key)
        if case.started_with is None and options.get("stop_after") == "gate":
            self._classify(case, options.get("classifier_contender", "llm"))
        case.started_with = case.started_with or options
        return httpx.Response(200, json={})

    def _classify(self, case: _Case, contender: str) -> None:
        """Leave the pages of a case as classification and the gate would, for one contender."""
        if contender not in self.contenders:
            # Every command is refused: no page is classified, none fails.
            for page in case.pages:
                page["page_status"] = "uploaded"
            case.failed = True
            return
        # A case nothing was said of keeps its pages as they are.
        stored = self.classified.get((contender, case.case_key), [])
        for page, result in zip(case.pages, stored, strict=False):
            if result is None:
                page["page_status"] = "failed"
                page["error_code"] = "model_unavailable"
                case.failed = True
            else:
                page_type, confidence, reason, status = result
                page["page_status"] = status
                page["classification"] = {
                    "classification_id": new_id(),
                    "case_id": case.case_id,
                    "page_id": page["page_id"],
                    "contender": contender,
                    "page_type": page_type,
                    "is_medical": is_medical(PageType(page_type)),
                    "confidence": confidence,
                    "reason": reason,
                }

    def _classifications(self, case: _Case) -> httpx.Response:
        if self.classification_down:
            return _refusal(ErrorCode.UPSTREAM_UNAVAILABLE)
        return httpx.Response(
            200,
            json={
                "case_id": case.case_id,
                "classifications": [
                    page["classification"]
                    for page in case.pages
                    if "classification" in page
                ],
            },
        )

    def _status(self, case: _Case) -> str:
        if case.failed or case.case_key in self.failing:
            case.failed = True
            return "failed"
        if case.case_key in self.hanging:
            return "running"
        if (case.started_with or {}).get("stop_after") == "gate":
            # Told to stop after the gate: complete, whatever its pages wait for.
            return "completed"
        if all(page["page_status"] in _FINAL_PAGES for page in case.pages):
            return "completed"
        return "awaiting_human"

    def _progress(self, case: _Case) -> httpx.Response:
        if case.started_with is None:
            return _refusal(ErrorCode.NOT_FOUND)
        status = self._status(case)
        return httpx.Response(
            200,
            json={
                "case_id": case.case_id,
                "case_status": status,
                "redaction_status": "done",
                "pages": [
                    {
                        "page_id": page["page_id"],
                        "page_number": page["page_number"],
                        "page_status": page["page_status"],
                        "error_code": page.get("error_code"),
                    }
                    for page in case.pages
                ],
                "error_code": "stage_failed" if status == "failed" else None,
                "classifier_contender": (case.started_with or {}).get(
                    "classifier_contender", "llm"
                ),
            },
        )

    def _decide(
        self, case: _Case, page_id: str, decision: Decision, role: str
    ) -> httpx.Response:
        rule = DECISION_RULES[decision]
        page = next(page for page in case.pages if page["page_id"] == page_id)
        if rule.role.value != role:
            return _refusal(ErrorCode.ROLE_NOT_ALLOWED)
        if page["page_status"] != rule.awaits.value:
            return _refusal(ErrorCode.NOT_AWAITING_DECISION)
        self.decisions.append(
            (case.case_key, page["page_number"], decision.value, role)
        )
        # An accepted page is extracted at once here.
        left = rule.leaves if decision is not Decision.ACCEPT else PageStatus.EXTRACTED
        page["page_status"] = left.value
        return httpx.Response(200, json={})

    def _runs(self, case: _Case) -> httpx.Response:
        started = case.started_with or {}
        suggested = self.verdicts.get(case.case_key, {})
        runs = []
        for row in started.get("retriever_configs", []):
            if (case.case_key, row) in self.missing_runs:
                continue
            failed = (case.case_key, row) in self.failed_runs
            verdict: str | None
            loading: int | None
            verdict, loading = suggested.get(row, ("refer", None))
            if failed:
                verdict, loading = None, None
            runs.append(
                {
                    "verdict_run_id": new_id(),
                    "case_id": case.case_id,
                    "retriever_config": row,
                    "status": "failed" if failed else "done",
                    "label": SUGGESTION_LABEL,
                    "verdict": verdict,
                    "loading_pct": loading,
                    "confidence": None if failed else 0.9,
                    "reasons": [],
                    "system_reasons": ["no_matching_rule"]
                    if verdict == "refer"
                    else [],
                    "error_code": "model_unavailable" if failed else None,
                }
            )
        return httpx.Response(
            200,
            json={"case_id": case.case_id, "verdict_runs": runs, "has_more": False},
        )

    def _file(self, document_id: str) -> httpx.Response:
        """The redacted file of a document; here a document has its case's id."""
        case = self.cases.get(document_id)
        if case is None:
            return _refusal(ErrorCode.NOT_FOUND)
        return httpx.Response(
            200,
            content=f"%PDF-1.7 redacted {case.case_key}".encode(),
            headers={"content-type": "application/pdf"},
        )

    def _text(self, page_id: str) -> httpx.Response:
        for case in self.cases.values():
            for page in case.pages:
                if page["page_id"] == page_id:
                    number = page["page_number"]
                    if (case.case_key, number) in self.broken_texts:
                        return _refusal(ErrorCode.UPSTREAM_UNAVAILABLE)
                    text = self.texts.get(case.case_key, {}).get(number, "")
                    return httpx.Response(
                        200,
                        json={"page_id": page_id, "page_number": number, "text": text},
                    )
        return _refusal(ErrorCode.NOT_FOUND)

    def _search(self, search: dict[str, Any]) -> httpx.Response:
        row, query = search["retriever_config"], search["query"]
        if row not in self.available:
            self.refused_rows.append(row)
            return _refusal(ErrorCode.RETRIEVER_NOT_AVAILABLE)
        self.searches.append(search)
        if (row, query) in self.broken_searches:
            return _refusal(ErrorCode.UPSTREAM_UNAVAILABLE)
        if (row, query) in self.flaky_searches:
            self.flaky_searches.discard((row, query))
            return _refusal(ErrorCode.MODEL_UNAVAILABLE, 503)
        return httpx.Response(
            200,
            json={
                "retriever_config": row,
                "latency_ms": self.latency_ms.get(row, 10),
                "items": [
                    {
                        "chunk_id": f"smart-{rule_id}",
                        "rule_ids": [rule_id],
                        "rank": rank,
                        "score": 0.5,
                        "text": f"Rule {rule_id}: a made-up rule.",
                        "manual_page": 1,
                        "impairment": "Made-up impairment",
                    }
                    for rank, rule_id in enumerate(
                        self.found.get((row, query), []), start=1
                    )
                ],
            },
        )
