"""Put a folder of prepared training pages into the local emulator: `python -m classification.local_setup <folder>`.

For a developer machine only (tools/train-local.sh). In Azure an operator
uploads the folder to the `classifier-training` container
(infra/bootstrap/README.md), and this refuses to run: it needs the
emulator's connection string.
"""

import sys
from pathlib import Path

from classification.adapters.blob import MANIFEST_BLOB_NAME, upload_local_training_pages
from classification.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python -m classification.local_setup <folder>", file=sys.stderr)
        return 2
    folder = Path(arguments[0])
    if not (folder / MANIFEST_BLOB_NAME).is_file():
        # Without its list no page has a label, and the job would train nothing.
        print(
            f"{folder} holds no {MANIFEST_BLOB_NAME}: prepare the pages first "
            "(uv run python -m bakeoff.training_pages).",
            file=sys.stderr,
        )
        return 1
    settings = get_settings()
    try:
        count = upload_local_training_pages(settings, folder)
    except ValueError as error:
        # Not the emulator of this machine: nothing was removed or uploaded.
        print(error, file=sys.stderr)
        return 1
    print(f"{count} files in the emulator's `{settings.training_container}` container.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
