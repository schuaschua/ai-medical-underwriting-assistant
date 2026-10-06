#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""List the regional regulation sources for this project's region.

Reads `region` from [modules.org] in {project-root}/_bmad/config.toml, then applies
overrides from _bmad/custom/config.toml and _bmad/custom/config.user.toml (later files
win), and prints that region's entry from assets/regions.toml as JSON: name, regulator,
the web domains Jules may read for it, and the source documents with their URLs.

An unset region resolves to `none` (no sources). --region skips the config lookup,
for org-setup runs whose values were entered for that run only.

Usage:
    uv run region-sources.py <project-root> [--region <code>] [--registry <regions.toml>]

Exit codes: 0=success, 1=unknown region or bad arguments, 2=unreadable config or registry
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

DEFAULT_REGISTRY = Path(__file__).resolve().parent.parent / "assets" / "regions.toml"
CONFIG_FILES = [
    Path("_bmad") / "config.toml",
    Path("_bmad") / "custom" / "config.toml",
    Path("_bmad") / "custom" / "config.user.toml",
]


def configured_region(project_root: Path) -> tuple[str, str]:
    """Return (region, the config file it came from); ('none', '') when unset."""
    region, origin = "none", ""
    for rel in CONFIG_FILES:
        path = project_root / rel
        if not path.is_file():
            continue
        value = tomllib.loads(path.read_text(encoding="utf-8")).get("modules", {}).get("org", {}).get("region")
        if isinstance(value, str) and value.strip():
            region, origin = value.strip().lower(), rel.as_posix()
    return region, origin


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_root", help="Project root (where _bmad/ lives)")
    parser.add_argument("--region", help="Region code to use instead of the configured one")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY), help="Region registry (default: assets/regions.toml)")
    args = parser.parse_args(argv)

    try:
        regions = tomllib.loads(Path(args.registry).read_text(encoding="utf-8")).get("regions", {})
        if args.region:
            region, origin = args.region.strip().lower(), "--region"
        else:
            region, origin = configured_region(Path(args.project_root))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 2

    if region not in regions:
        print(json.dumps({"status": "error", "message": f"unknown region '{region}'", "known": sorted(regions)}))
        return 1

    entry = regions[region]
    print(json.dumps({
        "status": "success",
        "region": region,
        "from": origin or "default",
        "name": entry.get("name", ""),
        "regulator": entry.get("regulator", ""),
        "domains": entry.get("domains", []),
        "sources": entry.get("sources", []),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
