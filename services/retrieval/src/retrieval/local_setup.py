"""Put the manual into the local blob emulator: `python -m retrieval.local_setup <pdf>`.

A developer tool only (see README, 'Run locally'). In Azure the container
belongs to the `foundation` stack and an operator uploads the manual
(infra/bootstrap/README.md, section 7).
"""

import argparse
from pathlib import Path

from retrieval.adapters.blob import upload_local_manual
from retrieval.settings import get_settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="retrieval.local_setup", description=__doc__)
    parser.add_argument("pdf", type=Path, help="the manual PDF to upload")
    args = parser.parse_args(argv)
    settings = get_settings()
    name = upload_local_manual(settings, args.pdf)
    print(f"Manual uploaded: {settings.manual_container}/{name}")


if __name__ == "__main__":
    main()
