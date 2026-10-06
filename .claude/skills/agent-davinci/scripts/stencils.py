#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Search a cloud provider's draw.io stencil catalogue, or extract one from draw.io's shape index.

Each shape library has a catalogue at assets/stencils/<library>.json: a cloud provider's
(azure) or a platform's (kubernetes). Every stencil has an exact `id` (category/file such as
`app_services/App_Services`, or the variant name such as `pod`), its title, draw.io style and
size. A deployment model names an icon by that id, qualified with its library when it isn't
the model's provider (`kubernetes:pod`), and build-drawio.py rejects an id the catalogue
doesn't hold.

    search   rank a provider's stencils against a service name; prints the best ids
    list     the providers that have a catalogue
    extract  build a catalogue from draw.io's search-index.json (the index behind the draw.io
             MCP server's search_shapes, https://cdn.jsdelivr.net/gh/jgraph/drawio-mcp@main/shape-search/search-index.json)
             for one library prefix, e.g. img/lib/azure2/ (Azure), mxgraph.kubernetes. (Kubernetes),
             mxgraph.aws4. (AWS)

Usage:
    uv run stencils.py search <library> <service name> [--limit 5]
    uv run stencils.py list
    uv run stencils.py extract --index <search-index.json or its https:// address> --provider <library> --prefix <library prefix> [-o <file>]

Exit codes: 0=success, 1=unknown provider or nothing matched, 2=unreadable input
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

CATALOGUES = Path(__file__).resolve().parent.parent / "assets" / "stencils"


def load(provider: str) -> dict:
    path = CATALOGUES / f"{provider}.json"
    if not path.is_file():
        raise KeyError(provider)
    return json.loads(path.read_text(encoding="utf-8"))


def providers() -> list[str]:
    return sorted(p.stem for p in CATALOGUES.glob("*.json"))


def words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


def stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def search(catalogue: dict, query: str, limit: int = 5) -> list[dict]:
    """Rank stencils: exact title, then every query word in the title, then word overlap."""
    q = [stem(w) for w in words(query)]
    q_flat = stem("".join(words(query)))
    ranked = []
    for s in catalogue["stencils"]:
        title = [stem(w) for w in words(s["title"])]
        t_flat = stem("".join(words(s["title"])))
        tags = {stem(w) for w in words(s.get("tags", ""))}
        if not q:
            break
        if title == q or t_flat == q_flat:  # "Power BI" finds "PowerBI", "CosmosDB" finds "Cosmos DB"
            score = 100
        elif q_flat in t_flat and len(q_flat) > 4:
            score = 85 - (len(title) - 1)
        elif all(w in title for w in q):
            score = 80 - (len(title) - len(q))
        else:
            hits = sum(w in title for w in q) * 10 + sum(w in tags for w in q) * 3
            score = hits - len(title) if hits else 0
        if score > 0:
            ranked.append((score, s["id"], s))
    ranked.sort(key=lambda r: (-r[0], r[1]))
    return [{"id": s["id"], "title": s["title"], "category": s["category"]} for _, _, s in ranked[:limit]]


def extract(index: list[dict], provider: str, prefix: str) -> dict:
    stencils, seen = [], set()
    for entry in index:
        style = entry.get("style", "")
        m = re.search(r"image=([^;]+)", style)
        ref = m.group(1) if m else (re.search(r"shape=([^;]+)", style) or [None, ""])[1]
        if not ref.startswith(prefix):
            continue
        variant = re.search(r"prIcon=([^;]+)", style)
        if variant:  # one shape drawing many icons (e.g. mxgraph.kubernetes.icon2;prIcon=pod)
            sid = variant.group(1)
        else:
            rest = ref[len(prefix):]
            rest = rest.rsplit(".", 1)[0] if rest.lower().endswith((".svg", ".png")) else rest
            sid = rest.strip("/").replace(".", "/")
        if sid in seen:
            continue
        seen.add(sid)
        category = sid.split("/", 1)[0] if "/" in sid else ""
        stencils.append({"id": sid, "title": entry.get("title", sid), "category": category, "style": style,
                         "w": entry.get("w", 64), "h": entry.get("h", 64),
                         "tags": " ".join(dict.fromkeys(words(entry.get("tags", ""))))})
    stencils.sort(key=lambda s: s["id"])
    return {"provider": provider, "library": prefix, "extracted": date.today().isoformat(),
            "source": "draw.io shape index (jgraph/drawio-mcp shape-search/search-index.json)", "stencils": stencils}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("search", help="Find a service's stencil ids")
    s.add_argument("provider")
    s.add_argument("query", nargs="+")
    s.add_argument("--limit", type=int, default=5)
    sub.add_parser("list", help="Providers with a catalogue")
    e = sub.add_parser("extract", help="Build a catalogue from draw.io's search-index.json")
    e.add_argument("--index", required=True)
    e.add_argument("--provider", required=True)
    e.add_argument("--prefix", required=True, help="Library prefix, e.g. img/lib/azure2/")
    e.add_argument("-o", "--output", help="Default: assets/stencils/<provider>.json")
    args = parser.parse_args(argv)

    if args.command == "list":
        print(json.dumps({"status": "success", "providers": providers()}))
        return 0
    if args.command == "search":
        try:
            found = search(load(args.provider), " ".join(args.query), args.limit)
        except KeyError:
            print(json.dumps({"status": "error", "message": f"no stencil catalogue for '{args.provider}'", "providers": providers()}))
            return 1
        print(json.dumps({"status": "success" if found else "error", "matches": found,
                          **({} if found else {"message": "no stencil matches; draw a labelled box"})}, indent=2))
        return 0 if found else 1
    try:
        if args.index.startswith("https://"):
            with urllib.request.urlopen(args.index, timeout=60) as res:
                index = json.loads(res.read().decode("utf-8"))
        else:
            index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 2
    catalogue = extract(index, args.provider, args.prefix)
    if not catalogue["stencils"]:
        print(json.dumps({"status": "error", "message": f"no shapes under {args.prefix}"}))
        return 1
    out = Path(args.output) if args.output else CATALOGUES / f"{args.provider}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalogue, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"status": "success", "file": str(out), "stencils": len(catalogue["stencils"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
