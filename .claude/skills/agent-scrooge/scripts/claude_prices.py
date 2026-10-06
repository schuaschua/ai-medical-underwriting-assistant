#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Claude API list prices from Anthropic's pricing page, per model, in USD per million tokens.

Reads the "Model pricing" table of the pricing page (served as Markdown at the URL below) and prints,
per model, the price of base input, 5-minute cache writes, 1-hour cache writes, cache hits and output,
with the page URL and the date it was read. With --save it writes them to
<project>/_bmad/memory/agent-scrooge/claude-prices.json, where build_estimate.py picks them up.

These are the first-party Claude API list prices. Claude Code on a Pro, Max, Team or Enterprise
subscription is billed by seat, not by token, so a cost built from these prices is what the same work
would cost on the API, not what the subscription charged.

Usage:
    uv run claude_prices.py [<project-root> --save] [--url URL] [--file PAGE.md]

--file reads a saved copy of the page instead of fetching it (offline, tests).
Prints JSON: {"status", "source", "fetched", "currency": "USD", "unit": "per million tokens",
"models": {"claude-opus-5-5": {"name", "input", "cache_write_5m", "cache_write_1h", "cache_read",
"output"}, ...}}. Exit codes: 0 ok, 2 error (page unreachable, or no pricing table found).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"
DATA_DIR = Path("_bmad") / "memory" / "agent-scrooge"
COLUMNS = {"base input tokens": "input", "5m cache writes": "cache_write_5m", "1h cache writes": "cache_write_1h",
           "cache hits and refreshes": "cache_read", "output tokens": "output"}


def clean_name(name: str) -> str:
    """'Claude Opus 4.1 ([retired, except ...](...))' -> 'Claude Opus 4.1'."""
    return re.sub(r"<[^>]+>", "", name.split("(")[0]).strip()


def model_id(name: str) -> str:
    """'Claude Opus 5.5 ([retired ...](...))' -> 'claude-opus-5-5'."""
    name = clean_name(name)
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def log_model_id(model: str) -> str:
    """A model id as the logs write it -> the pricing id: 'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'."""
    model = re.sub(r"\[.*?\]", "", model.strip().lower())
    return re.sub(r"-\d{8}$", "", model)


def price(cell: str) -> float | None:
    m = re.search(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*MTok", re.sub(r"<[^>]+>", "", cell))
    return float(m.group(1)) if m else None


def parse(markdown: str) -> dict[str, dict]:
    section = markdown.split("## Model pricing", 1)
    if len(section) < 2:
        return {}
    models: dict[str, dict] = {}
    header: list[str] | None = None
    for line in section[1].splitlines():
        line = line.strip()
        if line.startswith("## "):
            break
        if not line.startswith("|"):
            if header and models:
                break
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if set("".join(cells)) <= set(":- "):
            continue
        row = dict(zip(header, cells))
        name = row.get("model", "")
        prices = {key: price(row.get(col, "")) for col, key in COLUMNS.items()}
        if name and all(v is not None for v in prices.values()):
            models[model_id(name)] = {"name": clean_name(name), **prices}
    return models


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("project_root", nargs="?")
    p.add_argument("--save", action="store_true", help="write claude-prices.json into the project's Scrooge folder")
    p.add_argument("--url", default=URL)
    p.add_argument("--file", type=Path)
    args = p.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.file:
            text, source = args.file.read_text(encoding="utf-8"), str(args.file)
        else:
            request = urllib.request.Request(args.url, headers={"User-Agent": "org-kit-scrooge/1.5 (+claude_prices.py)",
                                                                "Accept": "text/markdown, text/plain;q=0.9"})
            with urllib.request.urlopen(request, timeout=30) as response:
                text, source = response.read().decode("utf-8"), args.url
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(json.dumps({"status": "error", "message": f"cannot read the pricing page: {e}"}))
        return 2
    models = parse(text)
    if not models:
        print(json.dumps({"status": "error", "source": source,
                          "message": "no model pricing table found; the page layout may have changed"}))
        return 2
    result = {"status": "ok", "source": source.removesuffix(".md"), "fetched": date.today().isoformat(),
              "currency": "USD", "unit": "per million tokens", "models": models}
    if args.save:
        if not args.project_root:
            print(json.dumps({"status": "error", "message": "--save needs the project root"}))
            return 2
        out = Path(args.project_root).resolve() / DATA_DIR / "claude-prices.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({k: v for k, v in result.items() if k != "status"}, indent=2) + "\n", encoding="utf-8")
        result["saved"] = str(out)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
