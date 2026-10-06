"""Prepare the local blob emulator: `python -m intake.local_setup`.

A developer tool only (see README, 'Run locally'). In Azure the containers
belong to the `foundation` stack.
"""

from intake.adapters.blob import ensure_local_containers
from intake.settings import get_settings


def main() -> None:
    names = ensure_local_containers(get_settings())
    print(f"Blob containers ready: {', '.join(names)}")


if __name__ == "__main__":
    main()
