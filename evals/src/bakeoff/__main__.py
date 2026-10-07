"""The bake-off runner's command line: `uv run python -m bakeoff` (see `evals/README.md`)."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from bakeoff.runner import RunResult, run
from bakeoff.settings import Settings
from bakeoff.state import RunRefused
from contracts.enums import RetrieverConfig

EXIT_OK = 0
# The redaction check found a planted identifier.
EXIT_LEAK = 1
# The run was refused: its settings or files, `web` does not answer, or a
# resume that is not the run it names. No case was uploaded by this command.
EXIT_REFUSED = 2
# The run was made and no leak was found, but it is not whole: a case was not
# scored, or a case's page texts were not checked.
EXIT_INCOMPLETE = 3
# The run broke off after it began; no file was written by this command.
EXIT_FAILED = 4


def _arguments(argv: Sequence[str] | None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(
        prog="bakeoff",
        description="Run the retrieval bake-off through web and write the scoreboard files.",
    )
    parser.add_argument("--web-address", help="where web is (EVALS_WEB_ADDRESS)")
    parser.add_argument(
        "--deployed",
        action="store_const",
        const=True,
        help="the run is against the deployed environment with the real AI "
        "services: its figures are results and are written to data/scoreboards",
    )
    parser.add_argument(
        "--eval-run-id", help="resume the run with this id instead of starting one"
    )
    parser.add_argument(
        "--cases", nargs="+", metavar="CASE", help="only these cases (case-001 ...)"
    )
    parser.add_argument(
        "--rows",
        nargs="+",
        metavar="ROW",
        choices=[row.value for row in RetrieverConfig],
        help="only these ladder rows",
    )
    parser.add_argument("--case-concurrency", type=int, help="cases under way at once")
    parser.add_argument("--output-dir", help="where the two files are written")
    given = vars(parser.parse_args(argv))
    # Only what was said on the command line overrides the environment.
    return {name: value for name, value in given.items() if value is not None}


def _report(result: RunResult) -> None:
    board = result.retrieval
    kind = "stand-in figures, not results" if board.run.stand_ins else "results"
    print(f"eval_run_id {board.run.eval_run_id} ({kind})")
    for row in board.rows:
        if not row.measured:
            print(f"  {row.retriever_config.value}: not measured")
            continue
        print(
            f"  {row.retriever_config.value}: "
            f"recall {row.recall_hits}/{row.recall_searches}, "
            f"verdicts {row.right_runs}/{row.cases}, "
            f"latency {_milliseconds(row.latency_ms_median)}"
            # A failure of the system, said apart from a wrong verdict.
            + (
                f", {row.failed_runs} {'run' if row.failed_runs == 1 else 'runs'}"
                " failed or missing"
                if row.failed_runs
                else ""
            )
        )
    print(f"  winner: {board.winner.value if board.winner else 'none'}")
    for failed in board.unscored_cases:
        print(f"  not scored: {failed.case_key} ({failed.reason})")
    if board.failed_searches:
        print(f"  searches that failed: {len(board.failed_searches)}")
    redaction = result.redaction
    found = "clean" if redaction.clean else "LEAK" if redaction.leaks else "not clean"
    print(
        f"redaction: {found}, "
        f"{redaction.pages_checked} pages of {redaction.cases_checked} cases checked"
    )
    for leak in redaction.leaks:
        print(f"  leak: {leak.case_key} page {leak.page_number} ({leak.category})")
    for case_key in redaction.cases_not_checked:
        print(f"  not checked: {case_key}")
    if is_incomplete(result):
        print(
            "incomplete: "
            f"{len(board.unscored_cases)} cases not scored, "
            f"{len(redaction.cases_not_checked)} cases not checked for redaction"
        )
    print(f"written: {result.retrieval_file} and {result.redaction_file}")


def _milliseconds(latency: int | None) -> str:
    return "not timed" if latency is None else f"{latency} ms"


def is_incomplete(result: RunResult) -> bool:
    """Whether a case of the run was not scored, or not checked for redaction."""
    return bool(result.retrieval.unscored_cases or result.redaction.cases_not_checked)


def exit_status(result: RunResult) -> int:
    """A leak comes first; then a run that is not whole; then all is well."""
    if result.redaction.leaks:
        return EXIT_LEAK
    return EXIT_INCOMPLETE if is_incomplete(result) else EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """Run the bake-off; the exit status. Tests pass a transport that stands in for `web`."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # The HTTP library logs every request with its address at this level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = Settings(**_arguments(argv))
    except ValidationError as error:
        print(f"The run was refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        result = asyncio.run(run(settings, transport))
    except RunRefused as error:
        print(f"The run was refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as error:  # noqa: BLE001 - whatever broke the run, the command ends with its own status, not a traceback or the leak status
        # The error's type only: its message can hold a path or an address.
        print(
            "The run broke off after it began and wrote no file "
            f"({type(error).__qualname__}). Cases it uploaded are kept: start it "
            "again with the eval_run_id it logged.",
            file=sys.stderr,
        )
        logging.getLogger(__name__).error(
            "run failed: type=%s", type(error).__qualname__
        )
        return EXIT_FAILED
    _report(result)
    return exit_status(result)


if __name__ == "__main__":
    raise SystemExit(main())
