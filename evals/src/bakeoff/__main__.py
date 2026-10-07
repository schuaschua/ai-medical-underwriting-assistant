"""The bake-off runner's command line: `uv run python -m bakeoff` (see `evals/README.md`)."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from bakeoff.runner import (
    ClassificationRunResult,
    RunResult,
    run,
    run_classification,
)
from bakeoff.settings import Settings
from bakeoff.state import RunRefused
from contracts.enums import ClassifierContender, RetrieverConfig

EXIT_OK = 0
# The redaction check found a planted identifier in a page text, or the
# classification bake-off found one in a stored reason.
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
        description="Run a bake-off through web and write its scoreboard files: "
        "the retrieval rows, or the classifier contenders.",
    )
    parser.add_argument(
        "--bake-off",
        choices=["retrieval", "classification"],
        help="which bake-off to run (retrieval when not given)",
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
    parser.add_argument(
        "--contenders",
        nargs="+",
        metavar="CONTENDER",
        choices=[contender.value for contender in ClassifierContender],
        help="only these classifier contenders (the classification bake-off)",
    )
    parser.add_argument("--case-concurrency", type=int, help="cases under way at once")
    parser.add_argument("--output-dir", help="where the files are written")
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


def _report_classification(result: ClassificationRunResult) -> None:
    board = result.classification
    kind = "stand-in figures, not results" if board.run.stand_ins else "results"
    print(f"eval_run_id {board.run.eval_run_id} ({kind})")
    for score in board.contenders:
        if not score.measured:
            print(f"  {score.contender.value}: not measured")
            continue
        print(
            f"  {score.contender.value}: "
            f"accuracy {score.right_pages}/{score.pages}, "
            f"calibration {score.confident_right_pages}/{score.confident_pages}, "
            f"queued {score.queued_pages}/{score.pages}, "
            f"{score.pages_not_classified} not classified"
        )
    print(f"  winner: {board.winner.value if board.winner else 'none'}")
    for tried in board.not_run:
        code = tried.error_code.value if tried.error_code else "no code"
        print(
            f"  cannot be run here: {tried.contender.value} (the case of "
            f"{tried.case_key}, {tried.case_id}, ended {tried.reason}, {code})"
        )
    for failed in board.unscored_cases:
        print(
            f"  not scored: {failed.case_key} with {failed.contender.value} "
            f"({failed.reason})"
        )
    for page in board.unclassified_pages:
        print(
            f"  not classified: {page.case_key} page {page.page_number} "
            f"with {page.contender.value}"
        )
    found = (
        "LEAK"
        if board.reason_leaks
        else "not clean"
        if board.reasons_not_checked
        else "clean"
    )
    print(f"reasons: {found}, {board.reasons_checked} checked")
    for leak in board.reason_leaks:
        print(
            f"  leak: {leak.case_key} page {leak.page_number} "
            f"with {leak.contender.value} ({leak.category})"
        )
    for unread in board.reasons_not_checked:
        print(f"  reasons not read: {unread.case_key} with {unread.contender.value}")
    if classification_is_incomplete(result):
        print(
            "incomplete: "
            f"{sum(score.measured for score in board.contenders)} contenders measured, "
            f"{len(board.unscored_cases)} files not scored, "
            f"{len(board.unclassified_pages)} pages not classified, "
            f"{len(board.reasons_not_checked)} files' reasons not read"
        )
    if result.classification_file is None:
        print("written: nothing (no contender was measured)")
    else:
        print(f"written: {result.classification_file}")


def classification_is_incomplete(result: ClassificationRunResult) -> bool:
    """Whether the run is not whole.

    No contender was measured; a file of a measured contender was not
    scored; a page has no result; or a file's reasons were not read.
    """
    board = result.classification
    return bool(
        not any(score.measured for score in board.contenders)
        or board.unscored_cases
        or board.unclassified_pages
        or board.reasons_not_checked
    )


def classification_exit_status(result: ClassificationRunResult) -> int:
    """As for the retrieval bake-off: a leak comes first, then a run that is not whole."""
    if result.classification.reason_leaks:
        return EXIT_LEAK
    return EXIT_INCOMPLETE if classification_is_incomplete(result) else EXIT_OK


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
    classifiers = settings.bake_off == "classification"
    result: RunResult | ClassificationRunResult
    try:
        if classifiers:
            result = asyncio.run(run_classification(settings, transport))
        else:
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
    if isinstance(result, ClassificationRunResult):
        _report_classification(result)
        return classification_exit_status(result)
    _report(result)
    return exit_status(result)


if __name__ == "__main__":
    raise SystemExit(main())
