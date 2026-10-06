"""`uv run python -m synthdata`: regenerate the synthetic cases and their answer key."""

import argparse
from pathlib import Path

from synthdata.generate import write_all


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="synthdata", description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder to write into (default: ./data, so run from the repository root)",
    )
    args = parser.parse_args(argv)
    for path in write_all(args.data_dir):
        print(path)


if __name__ == "__main__":
    main()
