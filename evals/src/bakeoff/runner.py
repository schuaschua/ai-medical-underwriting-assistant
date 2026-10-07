"""One bake-off run: recall, verdict accuracy and the redaction check, then the two files (spine AD-17).

The run drives the running system through `web` only, over the synthetic
cases and their answer key:

1. rule recall and latency, row by row, with the fixed query of every
   expected fact that meets a rule;
2. every case uploaded once and started once with the rows that answered,
   its human waits answered from the answer key, its verdict runs read;
3. the text of every page of every case checked for planted identifiers;
4. `retrieval.json` and `redaction.json` written.

Retriever quality (1) and the agent's work with a row (2) are measured
apart. A row that is not available is recorded as not measured, and a case
that fails or hangs as wrong for every row; neither stops the run.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from bakeoff.answer_key import AnswerKeyEntry, read_answer_key
from bakeoff.client import Sleep, WebClient, WebError, build_http_client
from bakeoff.recall import RowRecall, answers, fact_searches, measure_row
from bakeoff.redaction import RedactionCheck, read_page_texts
from bakeoff.scoreboard import (
    redaction_scoreboard,
    retrieval_scoreboard,
    write_scoreboards,
)
from bakeoff.settings import Settings
from bakeoff.state import RunRefused, RunState
from bakeoff.static_metrics import read_static_metrics
from bakeoff.verdicts import CaseOutcome, CaseRunner, Clock
from contracts.enums import RetrieverConfig
from contracts.ids import new_id
from contracts.models.web import (
    RedactionScoreboard,
    RetrievalScoreboard,
    ScoreboardRun,
)
from contracts.query import build_fact_query

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    retrieval: RetrievalScoreboard
    redaction: RedactionScoreboard
    retrieval_file: Path
    redaction_file: Path


def _now() -> datetime:
    return datetime.now(UTC)


async def measure_recall(
    client: WebClient, settings: Settings, entries: list[AnswerKeyEntry]
) -> dict[RetrieverConfig, RowRecall]:
    """Every row's recall counts; a row left out of the run is not in the answer."""
    searches = fact_searches(entries)
    limit = asyncio.Semaphore(settings.search_concurrency)
    recalls: dict[RetrieverConfig, RowRecall] = {}
    for row in settings.rows or list(RetrieverConfig):
        if searches:
            recalls[row] = await measure_row(
                client, row, searches, settings.top_k, limit
            )
            continue
        # No fact of this case set meets a rule: nothing scores the row, and
        # one built query only shows whether it can be searched with.
        query = build_fact_query(entries[0].expected_facts[0].statement)
        recalls[row] = RowRecall(
            row, available=await answers(client, row, query, settings.top_k)
        )
    return recalls


async def run_cases(
    client: WebClient,
    settings: Settings,
    state: RunState,
    entries: list[AnswerKeyEntry],
    rows: list[RetrieverConfig],
    sleep: Sleep,
    clock: Clock,
) -> tuple[dict[str, CaseOutcome], RedactionCheck]:
    """Run every case, a few at a time, and check its page texts when it has come to rest."""
    runner = CaseRunner(
        client=client,
        state=state,
        rows=rows,
        cases_dir=settings.cases_dir,
        deadline_seconds=settings.case_deadline_seconds,
        poll_seconds=settings.poll_seconds,
        sleep=sleep,
        clock=clock,
    )
    limit = asyncio.Semaphore(settings.case_concurrency)
    outcomes: dict[str, CaseOutcome] = {}
    check = RedactionCheck()

    async def one(entry: AnswerKeyEntry) -> None:
        async with limit:
            outcome = await runner.run(entry)
            outcomes[entry.case_key] = outcome
            logger.info(
                "case done: case=%s case_id=%s scored=%s",
                entry.case_key,
                outcome.case_id,
                outcome.unscored is None,
            )
            # Whatever became of the case: every page text it left is checked.
            check.add(entry, await read_page_texts(client, outcome.case_id))

    await asyncio.gather(*(one(entry) for entry in entries))
    return outcomes, check


async def run(
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Sleep = asyncio.sleep,
    clock: Clock = time.monotonic,
) -> RunResult:
    """Make one bake-off run and write its two files."""
    # Everything the run reads from disk is read, or seen to be there,
    # before anything is sent: what is wrong with it refuses the run.
    try:
        entries = read_answer_key(settings.answer_key_dir, settings.cases)
        static = read_static_metrics(settings.static_metrics_file)
    except (ValueError, OSError) as error:
        raise RunRefused(str(error)) from error
    if not entries or not all(entry.expected_facts for entry in entries):
        raise RunRefused("the answer key holds no case, or a case without facts")
    missing = [
        entry.file_name
        for entry in entries
        if not (settings.cases_dir / entry.file_name).is_file()
    ]
    if missing:
        raise RunRefused(f"no case document {', '.join(missing)}")
    eval_run_id = settings.eval_run_id or new_id()
    try:
        state = RunState(settings.state_dir, eval_run_id, settings.web_address)
    except (ValueError, OSError) as error:
        raise RunRefused(f"the run's state cannot be read ({error})") from error
    started_at = _now()
    logger.info(
        "bake-off run: eval_run_id=%s cases=%d stand_ins=%s",
        eval_run_id,
        len(entries),
        not settings.deployed,
    )

    async with build_http_client(settings, transport) as http:
        client = WebClient(http, settings, sleep)
        try:
            await client.me()
        except WebError as error:
            raise RunRefused(f"web does not answer at its address ({error})") from None

        recalls = await measure_recall(client, settings, entries)
        rows = [row for row, recall in recalls.items() if recall.available]
        if rows:
            # Refused here when a resumed run would start cases with other rows.
            state.begin(rows)
            outcomes, check = await run_cases(
                client, settings, state, entries, rows, sleep, clock
            )
        else:
            # No row to start a case with: nothing is uploaded.
            outcomes, check = {}, RedactionCheck()
            check.cases_not_checked.extend(entry.case_key for entry in entries)

    run_made = ScoreboardRun(
        eval_run_id=eval_run_id,
        started_at=started_at,
        finished_at=_now(),
        web_address=settings.web_address,
        stand_ins=not settings.deployed,
    )
    retrieval = retrieval_scoreboard(
        run_made, settings.top_k, static, entries, recalls, outcomes
    )
    redaction = redaction_scoreboard(run_made, check)
    retrieval_file, redaction_file = write_scoreboards(
        settings.scoreboard_dir, retrieval, redaction
    )
    return RunResult(retrieval, redaction, retrieval_file, redaction_file)
