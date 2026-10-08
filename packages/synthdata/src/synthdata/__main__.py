"""`uv run python -m synthdata`: regenerate the cases, the manual and the answer key.

`uv run python -m synthdata review` prints how far the manual has been checked against
its sources, and what is left; with `--sync` it first brings the review file in line
with the manual's definition.
"""

import argparse
from pathlib import Path

from synthdata.generate import write_all
from synthdata.manual_review import REVIEW_FILE, dump_review, report, synced
from synthdata.manual_rules import REVIEW, WRITTEN


def _review(sync: bool) -> None:
    review = REVIEW
    if sync:
        review, changes = synced(WRITTEN, REVIEW)
        REVIEW_FILE.write_text(dump_review(review), encoding="utf-8")
        for change in changes:
            print(change)
        print(f"{REVIEW_FILE}: {len(changes)} changes\n")
    print(report(WRITTEN, review))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="synthdata", description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=("generate", "review"),
        default="generate",
        help="generate (the default) writes the data; review reports on the manual's review file",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder to write into (default: ./data, so run from the repository root)",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="with review: rewrite the review file as the list of what the manual holds, keeping every record that still applies",
    )
    args = parser.parse_args(argv)
    if args.command == "review":
        _review(args.sync)
        return
    for path in write_all(args.data_dir):
        print(path)


if __name__ == "__main__":
    main()
