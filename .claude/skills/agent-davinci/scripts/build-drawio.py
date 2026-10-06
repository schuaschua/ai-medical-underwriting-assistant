#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Validate a cited diagram model and render it as a stamped .drawio file (plus PNG/SVG).

Reads `<folder>/<name>.model.json` and writes `<folder>/<name>.drawio` beside it, then
`<name>.png` and `<name>.svg` when draw.io desktop is installed (or DRAWIO_CLI names it).
Nothing is drawn unless the whole model validates; every problem is reported at once so it
can be fixed in the model:

- `spine` must be an ARCHITECTURE-SPINE.md under _bmad-output/planning-artifacts/architecture/,
  and every other `sources` entry must be a file under _bmad-output/ (spec and UX docs)
- every node, group, edge, participant and message needs a `cite`: an AD id the spine
  defines (`AD-4`), or a reference starting with a source's file name (`SPEC.md §3.2`)
- ids are unique, edges join existing nodes, kinds suit the diagram type, data tags are PII/PHI

Model (`cite` is a string or a list of strings):

    {
      "type": "c4-context | c4-containers | deployment | data-flow | sequence",
      "title": "Chat service",
      "spine": "_bmad-output/planning-artifacts/architecture/<name>/ARCHITECTURE-SPINE.md",
      "sources": ["_bmad-output/specs/SPEC.md"],
      "direction": "TB | LR",                   optional; data-flow defaults to LR, others TB
      "page": {"size": "A4 | A3 | A2 | A1 | A0 | Letter", "orientation": "portrait | landscape"},   default A2 landscape
      "provider": "azure",                      deployment only; the stencil catalogue for `icon` (default azure)
      "groups": [{"id", "label", "kind", "parent"?, "icon"?, "cite"}],
      "nodes":  [{"id", "label", "kind", "group"?, "tech"?, "description"?, "data"?: ["PII", "PHI"],
                  "icon"?: "stencil id (stencils.py search): the provider's (app_services/App_Services) or library:id (kubernetes:pod);
                            or the https:// address of an SVG or PNG, fetched once and kept in icons/ beside the model",
                  "style"?: "raw draw.io style, only for a shape no catalogue holds",
                  "layer"?: 0, "cite"}],
      "edges":  [{"from", "to", "label", "tech"?, "data"?, "dashed"?: true, "cite"}],
      "participants": [{"id", "label", "kind", "cite"}],          sequence only
      "messages": [{"from", "to", "label", "return"?: true, "data"?, "cite"}],   sequence only
      "gaps": ["what the sources don't say that this diagram needs"]
    }

Kinds per type:
    c4-context     nodes person, system, system_ext; groups boundary
    c4-containers  nodes person, system_ext, container, container_ext, database; groups boundary
    deployment     nodes service, person, external; groups cloud, on_premises, region, subscription, resource_group, vnet, subnet, boundary
                   (two or more regions must share one enclosing group; groups may carry an icon too)
    data-flow      nodes external, process, store, person; groups trust_boundary
    sequence       participants person, system, system_ext, container, container_ext, service, external

Layout is layered (longest path over the edges, barycentre ordering) and compact: groups get their
own columns, and arrows share lanes between rows. Arrows may cross, and lie over each other when
the paper needs it; words never overlap anything. The page is the model's paper size and
orientation (default A2 landscape). The drawing is fitted
to one page, or two when one would shrink it too far; the result reports `page.pages` and
`page.scale`. Below 0.7 even on two pages the citations print too small: `page.readable` is
false and `other_orientation_scale` says whether turning the paper would help. Diagram-level metadata (type, drawn time, spine stamp, AD hashes, source hashes) sits
on the diagram's root cell, where draw.io keeps it when the file is edited and saved.

Usage:
    uv run build-drawio.py <project-root> <folder>/<name>.model.json [--force] [--export png,svg|none]

Exit codes: 0=success, 1=invalid model, or <name>.drawio exists without --force, 2=runtime error
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import struct
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spine as spine_mod  # noqa: E402
import stencils as stencils_mod  # noqa: E402

TYPES = {
    "c4-context": {"nodes": {"person", "system", "system_ext"}, "groups": {"boundary"}},
    "c4-containers": {"nodes": {"person", "system_ext", "container", "container_ext", "database"}, "groups": {"boundary"}},
    "deployment": {"nodes": {"service", "person", "external"},
                   "groups": {"cloud", "on_premises", "region", "subscription", "resource_group", "vnet", "subnet", "boundary"}},
    "data-flow": {"nodes": {"external", "process", "store", "person"}, "groups": {"trust_boundary"}},
    "sequence": {"nodes": {"person", "system", "system_ext", "container", "container_ext", "service", "external"}, "groups": set()},
}
TYPE_NAMES = {"c4-context": "C4 system context", "c4-containers": "C4 containers", "deployment": "Cloud deployment",
              "data-flow": "Data flow", "sequence": "Sequence"}
DATA_TAGS = {"PII", "PHI"}
AD_ID = re.compile(r"^AD-\d+$")
SPINE_DIR = "_bmad-output/planning-artifacts/architecture/"

BOX = "rounded=1;whiteSpace=wrap;html=1;arcSize=8;fontSize=12;"
NODE_STYLES = {
    "person": "shape=actor;html=1;fillColor=#08427B;strokeColor=#073B6F;verticalLabelPosition=bottom;verticalAlign=top;fontSize=12;",
    "system": BOX + "fillColor=#1168BD;strokeColor=#0B4884;fontColor=#FFFFFF;",
    "system_ext": BOX + "fillColor=#999999;strokeColor=#6B6B6B;fontColor=#FFFFFF;",
    "container": BOX + "fillColor=#438DD5;strokeColor=#3C7FC0;fontColor=#FFFFFF;",
    "container_ext": BOX + "fillColor=#999999;strokeColor=#6B6B6B;fontColor=#FFFFFF;",
    "database": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;fontSize=12;"
                "fillColor=#438DD5;strokeColor=#3C7FC0;fontColor=#FFFFFF;",
    "service": BOX + "fillColor=#FFFFFF;strokeColor=#0078D4;fontColor=#000000;",
    "external": "rounded=0;whiteSpace=wrap;html=1;fontSize=12;fillColor=#F5F5F5;strokeColor=#666666;",
    "process": "ellipse;whiteSpace=wrap;html=1;fontSize=12;fillColor=#DAE8FC;strokeColor=#6C8EBF;",
    "store": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;fontSize=12;"
             "fillColor=#FFF2CC;strokeColor=#D6B656;",
}
GROUP_BASE = ("rounded=0;whiteSpace=wrap;html=1;container=1;collapsible=0;recursiveResize=0;align=left;"
              "verticalAlign=top;spacingLeft=8;spacingTop=2;fillColor=none;fontStyle=1;fontSize=12;")
GROUP_STYLES = {
    "boundary": GROUP_BASE + "dashed=1;strokeColor=#444444;",
    "trust_boundary": GROUP_BASE + "dashed=1;dashPattern=8 4;strokeWidth=2;strokeColor=#B85450;fontColor=#B85450;",
    "cloud": GROUP_BASE + "strokeColor=#0078D4;strokeWidth=2;fontColor=#0078D4;fillColor=#FBFDFF;",
    "region": GROUP_BASE + "strokeColor=#0078D4;fontColor=#0078D4;",
    "on_premises": GROUP_BASE + "strokeColor=#6B6B6B;strokeWidth=2;fontColor=#444444;fillColor=#F7F7F7;",
    "subscription": GROUP_BASE + "dashed=1;strokeColor=#0078D4;",
    "resource_group": GROUP_BASE + "dashed=1;strokeColor=#7A7A7A;",
    "vnet": GROUP_BASE + "strokeColor=#5EA0EF;fillColor=#F2F8FF;",
    "subnet": GROUP_BASE + "dashed=1;strokeColor=#5EA0EF;",
}
EDGE_STYLE = ("edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;fontSize=10;"
              "labelBackgroundColor=#FFFFFF;strokeColor=#555555;")
SENSITIVE = "strokeColor=#B85450;strokeWidth=2;fontColor=#B85450;"

W, H = 170, 80            # node slot
PERSON = (44, 52)         # actor figure; its label sits below it
ICON = 56                 # library icon (e.g. Azure); its label sits below it
ICON_SLOT = 120           # width an icon or a person takes in its row
GROUP_ICON = 26           # icon in a group's header (e.g. the AKS cluster)
GAP_ACROSS = 24
ROW_MARGIN = 8            # space between a row and the arrow lanes beside it
PAD_START, PAD_END = 34, 12
TOP = 72                  # room for the title and stamp
PAPER = {"A4": (827, 1169), "A3": (1169, 1654), "A2": (1654, 2339), "A1": (2339, 3311), "A0": (3311, 4681),
         "Letter": (850, 1100)}  # page units, portrait
PAPER_UP = ["A4", "A3", "A2", "A1", "A0"]  # sizes to suggest, smallest first
DEFAULT_PAGE = ("A2", "landscape")
ICON_TYPES = {"svg": "image/svg+xml", "png": "image/png"}  # what an icon from the web may be
ICON_MAX = 512 * 1024
MIN_READABLE_SCALE = 0.7  # below this the 9pt citations print under ~6pt, even across two pages


class ModelError(Exception):
    pass


def as_list(cite) -> list[str]:
    if isinstance(cite, str):
        cite = [cite]
    return [c.strip() for c in cite or [] if isinstance(c, str) and c.strip()]


def source_headings(path: Path) -> list[tuple[str, str]]:
    """(number, title) for each markdown heading: '## 3.2 Chat screen' -> ('3.2', 'Chat screen')."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = spine_mod.HEADING.match(line)
        if m:
            text = line[m.end():].strip().strip("#").strip()
            num = re.match(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$", text)
            out.append((num.group(1), num.group(2).strip()) if num else ("", text))
    return out


def is_workload_host(g: dict) -> bool:
    """A box drawn with a compute platform's icon (AKS, App Service plan, Container Apps environment)."""
    st, icon = g.get("_stencil"), g.get("icon", "")
    return bool(st) and (icon.startswith("kubernetes:") or st.get("category") in ("compute", "containers")
                         or "App_Service_Plans" in icon or "Container_App_Environments" in icon)


def ancestry(gid, groups: list[dict]) -> list[str]:
    parent = {g["id"]: g.get("parent") for g in groups}
    out = []
    while gid and gid not in out:
        out.append(gid)
        gid = parent.get(gid)
    return out


def gmap_(groups: list[dict], gid: str) -> dict:
    return next((g for g in groups if g["id"] == gid), {})


def icon_file(url: str) -> str:
    """The name (without extension) a web icon is kept under in the icons folder."""
    name = re.sub(r"[^a-z0-9]+", "-", Path(urllib.parse.urlparse(url).path).stem.lower()).strip("-")[:40]
    return f"{name or 'icon'}-{hashlib.sha256(url.encode()).hexdigest()[:8]}"


def web_icon(url: str, cache: Path | None) -> dict:
    """A stencil for an SVG or PNG at an https address, embedded in the drawing.

    The file is kept in `cache`, so a rebuild needs no network and draws the same picture.
    Raises ValueError saying what is wrong with the address or the file.
    """
    if not url.startswith("https://"):
        raise ValueError("is not an https:// address")
    kept = next((f for ext in ICON_TYPES if cache and (f := cache / f"{icon_file(url)}.{ext}").is_file()), None)
    if kept:
        data = kept.read_bytes()
    else:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "org-kit agent-davinci"})
            with urllib.request.urlopen(req, timeout=20) as res:
                data = res.read(ICON_MAX + 1)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ValueError(f"could not be fetched ({exc})") from exc
    if len(data) > ICON_MAX:
        raise ValueError(f"is larger than {ICON_MAX // 1024} KB")
    w = h = 64.0
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 24:
        ext = "png"
        w, h = struct.unpack(">II", data[16:24])
    elif (tag := re.search(rb"<svg\b[^>]*>", data)):
        ext = "svg"
        attrs = tag.group(0).decode("utf-8", "replace")
        size = [re.search(rf'\b{k}="([\d.]+)(?:px)?"', attrs) for k in ("width", "height")]
        box = re.search(r'\bviewBox="[\d.eE+-]+[\s,]+[\d.eE+-]+[\s,]+([\d.]+)[\s,]+([\d.]+)"', attrs)
        if all(size):
            w, h = (float(m.group(1)) for m in size)
        elif box:
            w, h = float(box.group(1)), float(box.group(2))
    else:
        raise ValueError("is neither an SVG nor a PNG")
    if cache and not kept:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{icon_file(url)}.{ext}").write_bytes(data)
    return {"id": url, "title": url, "category": "", "w": w or 64, "h": h or 64,
            "style": "aspect=fixed;html=1;points=[];align=center;image;fontSize=12;"
                     f"image=data:{ICON_TYPES[ext]},{base64.b64encode(data).decode('ascii')};"}


def section_exists(ref: str, headings: list[tuple[str, str]]) -> bool:
    """Does the part of a cite after the file name name one of the file's headings?"""
    ref = ref.strip().lstrip("#:").strip().lstrip("§").strip().strip('"\'“”').strip()
    if not ref:
        return False
    slug = lambda t: re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")  # noqa: E731
    for num, title in headings:
        full = f"{num} {title}".strip()
        if ref in (num, title, full) or ref.lower() in (title.lower(), full.lower()) or slug(ref) in (slug(title), slug(full)):
            return True
    return False


def validate(model: dict, root: Path, icons: Path | None = None) -> list[Path]:
    """Raise ModelError listing every problem; return the source files (spine first).

    `icons` is the folder that keeps icons fetched from the web.
    """
    problems: list[str] = []
    kind = model.get("type")
    if kind not in TYPES:
        raise ModelError(f"type must be one of {sorted(TYPES)}")
    if not str(model.get("title", "")).strip():
        problems.append("title is required")

    spine_rel = str(model.get("spine", ""))
    spine_path = root / spine_rel
    if not (spine_rel.startswith(SPINE_DIR) and spine_rel.endswith("/ARCHITECTURE-SPINE.md") and spine_path.is_file()):
        raise ModelError(f"spine must be an existing {SPINE_DIR}<name>/ARCHITECTURE-SPINE.md, got {spine_rel!r}")
    sources = [spine_path]
    for rel in model.get("sources", []):
        path = (root / rel).resolve()
        try:
            inside = path.relative_to(root.resolve()).as_posix()
        except ValueError:
            inside = ""
        if not inside.startswith("_bmad-output/"):
            problems.append(f"source {rel!r} is outside _bmad-output/: diagrams come from the spine, spec and UX docs only")
        elif not path.is_file():
            problems.append(f"source not found: {rel!r}")
        else:
            sources.append(path)
    ads = spine_mod.parse_ads(spine_path.read_text(encoding="utf-8"))[0]
    names = sorted({p.name for p in sources})
    headings = {p.name: source_headings(p) for p in sources}

    def check(where: str, item: dict) -> None:
        cites = as_list(item.get("cite"))
        if not cites:
            problems.append(f"{where}: no cite; if no source states it, list it under gaps instead of drawing it")
        for c in cites:
            if AD_ID.match(c):
                if c not in ads:
                    problems.append(f"{where}: cites {c}, which the spine does not define (it defines {', '.join(ads) or 'no ADs'})")
            else:
                name = next((n for n in names if c == n or c.startswith((n + " ", n + "#", n + ":"))), None)
                if name is None:
                    problems.append(f"{where}: cite {c!r} is neither an AD id nor starts with a source file name ({', '.join(names)})")
                elif not section_exists(c[len(name):], headings[name]):
                    shown = "; ".join(f"{num + ' ' if num else ''}{title}" for num, title in headings[name][:12])
                    problems.append(f"{where}: cite {c!r} names no section of {name}; cite a heading's number or title "
                                    f"(its headings: {shown or 'none'})")
        for tag in item.get("data") or []:
            if tag not in DATA_TAGS:
                problems.append(f"{where}: data tag {tag!r} must be one of {sorted(DATA_TAGS)}")

    provider = model.get("provider", "azure")
    if kind == "deployment" and provider not in stencils_mod.providers():
        problems.append(f"provider {provider!r} has no stencil catalogue (have: {', '.join(stencils_mod.providers())})")
    libraries: dict[str, dict] = {}
    for item in model.get("nodes", []) + model.get("groups", []) + model.get("participants", []):
        if not item.get("icon"):
            continue
        if kind != "deployment":
            problems.append(f"{item.get('id')}: icons are for deployment diagrams")
            continue
        if item["icon"].startswith(("http://", "https://")):
            try:
                item["_stencil"] = web_icon(item["icon"], icons)
            except ValueError as exc:
                problems.append(f"{item.get('id')}: icon {item['icon']!r} {exc}; use an SVG or PNG under {ICON_MAX // 1024} KB "
                                "at an https:// address, or a catalogue stencil")
            continue
        library, _, sid = item["icon"].rpartition(":")
        library = library or provider
        if library not in libraries:
            try:
                libraries[library] = {s["id"]: s for s in stencils_mod.load(library)["stencils"]}
            except KeyError:
                problems.append(f"{item.get('id')}: icon library {library!r} has no catalogue (have: {', '.join(stencils_mod.providers())})")
                libraries[library] = {}
                continue
        catalogue = libraries[library]
        if sid in catalogue:
            item["_stencil"] = catalogue[sid]
        elif catalogue:
            near = stencils_mod.search({"stencils": list(catalogue.values())}, sid.split("/")[-1].replace("_", " "), 3)
            problems.append(f"{item.get('id')}: icon {item['icon']!r} is not in the {library} catalogue; "
                            f"closest: {', '.join(m['id'] for m in near) or 'none, draw a labelled box'}")

    allowed = TYPES[kind]
    if kind == "sequence":
        parts = model.get("participants", [])
        ids = [p.get("id") for p in parts]
        if not parts:
            problems.append("participants are required")
        if len(ids) != len(set(ids)):
            problems.append("participant ids must be unique")
        for p in parts:
            if p.get("kind") not in allowed["nodes"]:
                problems.append(f"participant {p.get('id')}: kind must be one of {sorted(allowed['nodes'])}")
            check(f"participant {p.get('id')}", p)
        if not model.get("messages"):
            problems.append("messages are required")
        for i, m in enumerate(model.get("messages", []), 1):
            for end in ("from", "to"):
                if m.get(end) not in ids:
                    problems.append(f"message {i}: {end} {m.get(end)!r} is not a participant")
            check(f"message {i}", m)
    else:
        groups, nodes = model.get("groups", []), model.get("nodes", [])
        gids, nids = [g.get("id") for g in groups], [n.get("id") for n in nodes]
        if not nodes:
            problems.append("nodes are required")
        if len(gids + nids) != len(set(gids + nids)):
            problems.append("node and group ids must be unique")
        parents = {g.get("id"): g.get("parent") for g in groups}
        for g in groups:
            if g.get("kind") not in allowed["groups"]:
                problems.append(f"group {g.get('id')}: kind must be one of {sorted(allowed['groups']) or 'none for this type'}")
            if g.get("parent") and g["parent"] not in gids:
                problems.append(f"group {g.get('id')}: parent {g['parent']!r} is not a group")
            if not any(n.get("group") == g.get("id") for n in nodes) and g.get("id") not in parents.values():
                problems.append(f"group {g.get('id')}: empty")
            seen, cur = set(), g.get("id")
            while cur:
                if cur in seen:
                    problems.append(f"group {g.get('id')}: parent cycle")
                    break
                seen.add(cur)
                cur = parents.get(cur)
            check(f"group {g.get('id')}", g)
        # Data services never sit inside a compute cluster (org drawing rule): not in a workload host's
        # box, and not in a subnet that holds one.
        hosts = {g["id"] for g in groups if is_workload_host(g)}
        holds_host = {a for h in hosts for a in ancestry(h, groups)}
        for n in nodes:
            st = n.get("_stencil")
            if not st or st.get("category") not in ("databases", "storage"):
                continue
            chain = ancestry(n.get("group"), groups)
            inside = [g for g in chain if g in hosts]
            shared = [g for g in chain if gmap_(groups, g).get("kind") == "subnet" and g in holds_host]
            if inside:
                problems.append(f"node {n.get('id')}: a data service sits inside the compute cluster {inside[0]!r}; "
                                "draw it beside the cluster, in its own subnet")
            elif shared:
                problems.append(f"node {n.get('id')}: a data service shares subnet {shared[0]!r} with a compute cluster; "
                                "give it its own subnet")
        regions = [g for g in groups if g.get("kind") == "region"]
        if len(regions) > 1 and (len({g.get("parent") for g in regions}) > 1 or not regions[0].get("parent")):
            problems.append("multi-region: put every region group inside one enclosing group (kind cloud), "
                            "with global services such as Front Door and DNS beside the regions in it")
        for n in nodes:
            if n.get("kind") not in allowed["nodes"]:
                problems.append(f"node {n.get('id')}: kind must be one of {sorted(allowed['nodes'])}")
            if n.get("group") and n["group"] not in gids:
                problems.append(f"node {n.get('id')}: group {n['group']!r} is not a group")
            check(f"node {n.get('id')}", n)
        for i, e in enumerate(model.get("edges", []), 1):
            for end in ("from", "to"):
                if e.get(end) not in nids:
                    problems.append(f"edge {i}: {end} {e.get(end)!r} is not a node")
            check(f"edge {i} ({e.get('from')} -> {e.get('to')})", e)
    page = model.get("page") or {}
    if page.get("size", DEFAULT_PAGE[0]) not in PAPER or page.get("orientation", DEFAULT_PAGE[1]) not in ("portrait", "landscape"):
        problems.append(f"page must be {{\"size\": one of {sorted(PAPER)}, \"orientation\": \"portrait\" or \"landscape\"}}")
    if not isinstance(model.get("gaps", []), list):
        problems.append("gaps must be a list of strings")
    if problems:
        raise ModelError("; ".join(problems))
    return sources


# --- text measurement ---------------------------------------------------------------------
# Widths are estimated generously (wider than draw.io's Helvetica renders), so a label that fits
# here fits on the page. Every wrap width used here is also written into the drawing
# (labelWidth, or explicit line breaks), so draw.io wraps where the layout measured.

LINE = 1.3                # line height as a multiple of the font size
LABEL_W = 150             # wrap width of a label under an icon or a person
EDGE_LABEL_W = 170        # wrap width of an arrow's label
TRACK_MARGIN = 5          # space around an arrow's lane
HEADER_H = 20             # one line of a box's header text


def text_width(text: str, px: float, bold: bool = False) -> float:
    return len(text) * px * (0.62 if bold else 0.56)


def wrap(text: str, px: float, bold: bool, max_w: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and text_width(trial, px, bold) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines or [""]


def node_lines(item: dict, with_tech: bool = True) -> list[tuple[str, float, bool]]:
    """A node label's logical lines as (plain text, font px, bold)."""
    head = (" ".join(item.get("data") or []) + " " if item.get("data") else "") + item.get("label", item["id"])
    lines = [(head, 12, True)]
    if with_tech and item.get("tech"):
        lines.append((f"[{item['tech']}]", 12, False))
    if item.get("description"):
        lines.append((item["description"], 10, False))
    lines.append(("; ".join(as_list(item.get("cite"))), 9, False))
    return lines


def block_size(lines: list[tuple[str, float, bool]], max_w: float) -> tuple[float, float]:
    width = height = 0.0
    for text, px, bold in lines:
        for part in wrap(text, px, bold, max_w):
            width = max(width, text_width(part, px, bold))
            height += px * LINE
    return min(width, max_w) + 8, height + 6


def node_label(item: dict, with_tech: bool = True) -> str:
    text = ""
    if item.get("data"):
        text += f'<font color="#B85450"><b>{"/".join(item["data"])}</b></font> '
    text += f"<b>{html.escape(item.get('label', item['id']))}</b>"
    if with_tech and item.get("tech"):
        text += f"<br><i>[{html.escape(item['tech'])}]</i>"
    if item.get("description"):
        text += f'<br><font style="font-size:10px">{html.escape(item["description"])}</font>'
    return text + f'<br><font style="font-size:9px" color="#555555">{html.escape("; ".join(as_list(item.get("cite"))))}</font>'


def edge_text(item: dict, prefix: str = "") -> tuple[str, tuple[float, float]]:
    """An arrow's label, wrapped with explicit breaks, and its size."""
    main = prefix + item.get("label", "") + (f" [{item['tech']}]" if item.get("tech") else "")
    tags = "/".join(item.get("data") or [])
    main_lines = wrap((tags + " " if tags else "") + main, 10, False, EDGE_LABEL_W)
    cite_lines = wrap("; ".join(as_list(item.get("cite"))), 9, False, EDGE_LABEL_W)
    rendered = []
    for i, line in enumerate(main_lines):
        esc = html.escape(line)
        if i == 0 and tags and line.startswith(tags):
            esc = f"<b>{html.escape(tags)}</b>" + esc[len(html.escape(tags)):]
        rendered.append(esc)
    rendered += [f'<font style="font-size:9px" color="#555555">{html.escape(c)}</font>' for c in cite_lines]
    size = block_size([(l, 10, False) for l in main_lines] + [(c, 9, False) for c in cite_lines], EDGE_LABEL_W)
    return "<br>".join(rendered), size


def header_width(g: dict) -> float:
    return (text_width(g["label"] + " ", 12, True) + text_width(f"({'; '.join(as_list(g['cite']))})", 9)
            + (34 if g.get("_stencil") else 8) + 12)


# --- layered layout -----------------------------------------------------------------------

def assign_layers(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    ids = [n["id"] for n in nodes]
    adj = {i: [] for i in ids}
    for e in edges:
        if e["from"] != e["to"]:
            adj[e["from"]].append(e["to"])
    state: dict[str, int] = {}
    preds = {i: [] for i in ids}

    def dfs(u: str) -> None:  # keep forward edges only, so a cycle can't break the layering
        state[u] = 1
        for v in adj[u]:
            if state.get(v) == 1:
                continue
            preds[v].append(u)
            if not state.get(v):
                dfs(v)
        state[u] = 2

    for i in ids:
        if not state.get(i):
            dfs(i)
    layer: dict[str, int] = {}

    def depth(u: str) -> int:
        if u not in layer:
            layer[u] = 0
            layer[u] = max((depth(p) + 1 for p in preds[u]), default=0)
        return layer[u]

    for i in ids:
        depth(i)
    for n in nodes:
        if isinstance(n.get("layer"), int) and n["layer"] >= 0:
            layer[n["id"]] = n["layer"]
    used = {lyr: i for i, lyr in enumerate(sorted(set(layer.values())))}  # close up empty layers
    return {nid: used[lyr] for nid, lyr in layer.items()}


def order_in_layers(nodes: list[dict], edges: list[dict], layer: dict[str, int]) -> dict[str, float]:
    pos = {n["id"]: float(i) for i, n in enumerate(nodes)}
    nbrs = {n["id"]: [] for n in nodes}
    for e in edges:
        nbrs[e["from"]].append(e["to"])
        nbrs[e["to"]].append(e["from"])
    for _ in range(2):
        for n in nodes:
            near = [pos[m] for m in nbrs[n["id"]] if layer[m] != layer[n["id"]]]
            if near:
                pos[n["id"]] = sum(near) / len(near)
        by_layer: dict[int, list[str]] = {}
        for n in nodes:
            by_layer.setdefault(layer[n["id"]], []).append(n["id"])
        for ids in by_layer.values():
            for rank, i in enumerate(sorted(ids, key=lambda k: pos[k])):
                pos[i] = float(rank)
    return pos


def node_spec(n: dict, with_tech: bool) -> dict:
    """Shape size, where the label sits, and the node's whole footprint (shape plus label)."""
    lines = node_lines(n, with_tech)
    stencil = n.get("_stencil")
    if stencil or n.get("style") or n.get("kind") == "person":
        if n.get("kind") == "person" and not (stencil or n.get("style")):
            sw, sh = PERSON
        else:
            sw, sh = (stencil["w"], stencil["h"]) if stencil else (ICON, ICON)
            k = ICON / max(sw, sh)
            sw, sh = round(sw * k), round(sh * k)
        lw, lh = block_size(lines, LABEL_W)
        vw, vh = max(sw, lw), sh + 4 + lh
        return {"shape": (sw, sh), "off": ((vw - sw) / 2, 0), "vis": (vw, vh), "below": True, "label_w": lw}
    inner = {"process": 0.7}.get(n.get("kind"), 1.0)
    extra = 14 if n.get("kind") in ("database", "store") else 0
    lw, lh = block_size(lines, W * inner - 16)
    bh = max(H, lh / inner + 18 + extra)
    return {"shape": (W, bh), "off": (0, 0), "vis": (W, bh), "below": False}


class Rect:
    __slots__ = ("x", "y", "w", "h", "what")

    def __init__(self, x, y, w, h, what=""):
        self.x, self.y, self.w, self.h, self.what = x, y, w, h, what

    def hits(self, o: "Rect", pad: float = 1) -> bool:
        return (self.x + pad < o.x + o.w and o.x + pad < self.x + self.w and
                self.y + pad < o.y + o.h and o.y + pad < self.y + self.h)

    def holds(self, o: "Rect") -> bool:
        return (self.x <= o.x + 0.5 and self.y <= o.y + 0.5 and
                o.x + o.w <= self.x + self.w + 0.5 and o.y + o.h <= self.y + self.h + 0.5)


def seg_hits(p: tuple, q: tuple, r: Rect, pad: float = 2) -> bool:
    """An axis-parallel segment against a rectangle (shrunk by pad)."""
    x0, x1 = sorted((p[0], q[0]))
    y0, y1 = sorted((p[1], q[1]))
    return x0 < r.x + r.w - pad and r.x + pad < x1 + 0.01 and y0 < r.y + r.h - pad and r.y + pad < y1 + 0.01


def plan_graph(model: dict, spread: float, shared: dict | None = None) -> dict:
    """Place nodes, groups and routed arrows; everything in absolute page units.

    `shared` (from share_lanes) puts several arrows in one lane; without it each arrow has its own.
    """
    nodes, edges, groups = model.get("nodes", []), model.get("edges", []), model.get("groups", [])
    lr = model.get("direction", "LR" if model["type"] == "data-flow" else "TB") == "LR"
    with_tech = model["type"] != "c4-context"
    spec = {n["id"]: node_spec(n, with_tech) for n in nodes}
    gap_across = GAP_ACROSS + spread
    # abstract axes: "across" runs along a row, "along" runs from row to row
    A = (lambda wh: wh[1]) if lr else (lambda wh: wh[0])
    B = (lambda wh: wh[0]) if lr else (lambda wh: wh[1])
    layer = assign_layers(nodes, edges)
    key = order_in_layers(nodes, edges, layer)
    gmap = {g["id"]: g for g in groups}
    children: dict = {None: [g["id"] for g in groups if not g.get("parent")]}
    direct: dict = {None: []}
    for g in groups:
        children.setdefault(g["id"], [])
        direct[g["id"]] = []
    for g in groups:
        if g.get("parent"):
            children[g["parent"]].append(g["id"])
    for n in nodes:
        direct[n.get("group")].append(n["id"])
    span: dict = {}

    def spans(gid) -> tuple[int, int]:
        ls = [layer[n] for n in direct[gid]] + [x for c in children[gid] for x in spans(c)]
        span[gid] = (min(ls), max(ls))
        return span[gid]

    for gid in children[None]:
        spans(gid)
    parent_of = {g["id"]: g.get("parent") for g in groups}

    def nested(gid, end: int, lyr: int) -> int:
        count = 0
        while gid and span[gid][end] == lyr:
            count, gid = count + 1, parent_of[gid]
        return count

    # --- arrows: which lanes they need in which gap (gap L lies below row L) ---
    top_layer = max(layer.values(), default=0)
    idx = {n["id"]: n for n in nodes}
    routes = []
    lanes: dict[int, list] = {}
    for i, e in enumerate(edges):
        text, size = edge_text(e)
        if e["from"] == e["to"]:
            routes.append({"i": i, "loop": True, "text": text, "size": size})
            continue
        a, b = e["from"], e["to"]
        up, down = (a, b) if layer[a] <= layer[b] else (b, a)
        r = {"i": i, "loop": False, "up": up, "down": down, "reversed": up != a, "text": text, "size": size,
             "same": layer[up] == layer[down]}
        first = layer[up]
        lanes.setdefault(first, []).append((r, "label", B(size) + 2 * TRACK_MARGIN))
        if layer[down] - layer[up] > 1:
            lanes.setdefault(layer[down] - 1, []).append((r, "jog", 12))
        routes.append(r)
    row_b = {l: max(B(spec[n]["vis"]) for n in idx if layer[n] == l) for l in range(top_layer + 1)}
    offsets, lane_y = [TOP], {}
    last_gap = top_layer if top_layer in lanes else top_layer - 1
    for lyr in range(last_gap + 1):
        ends = max((nested(g, 1, lyr) for g in span), default=0)
        starts = max((nested(g, 0, lyr + 1) for g in span), default=0) if lyr < top_layer else 0
        y = offsets[lyr] + row_b[lyr] + ends * PAD_END + ROW_MARGIN + spread / 2
        tracks: dict[int, float] = {}  # lane number -> its height
        number = {}
        for k, (r, role, h) in enumerate(lanes.get(lyr, [])):
            number[(r["i"], role)] = shared[(r["i"], role)] if shared else k
            tracks[number[(r["i"], role)]] = max(h, tracks.get(number[(r["i"], role)], 0))
        for t in sorted(tracks):
            for item in (i for i, n in number.items() if n == t):
                lane_y[item] = (y, y + tracks[t])
            y += tracks[t]
        offsets.append(y + ROW_MARGIN + spread / 2 + starts * PAD_START)
    along = lambda lyr: offsets[lyr]  # noqa: E731

    # --- boxes: header room; in TB a box's contents start right of its header text ---
    pad_as, pad_bs = {}, {}
    for g in groups:
        hw = header_width(g)
        if lr:  # header runs along the box's top edge, which is its across start
            length = along(span[g["id"]][1]) + row_b[span[g["id"]][1]] - along(span[g["id"]][0]) + PAD_START + PAD_END
            lines = max(1, -(-hw // max(length - 16, 60)))
            pad_as[g["id"]], pad_bs[g["id"]] = PAD_START + (lines - 1) * HEADER_H, PAD_START
        else:
            pad_as[g["id"]], pad_bs[g["id"]] = PAD_START, PAD_START
    if not lr:
        # Arrows reach a node from above, through the header bands of every box around it. A box that
        # holds nodes directly starts them right of the furthest-reaching of those headers; each box
        # sits at least one margin inside its parent, so an ancestor's header reaches less far in.
        def set_insets(gid, reach: list[float]) -> None:  # reach: header right edges, from this box's left
            reach = reach + [header_width(gmap[gid]) + 6]
            if direct[gid]:
                pad_as[gid] = max([PAD_START] + reach)
            for c in children[gid]:
                set_insets(c, [r - pad_as[gid] for r in reach])

        for gid in children[None]:
            set_insets(gid, [])

    def across(nid: str) -> float:
        return A(spec[nid]["vis"])

    def row_width(ids: list[str]) -> float:
        return sum(across(i) for i in ids) + gap_across * max(len(ids) - 1, 0)

    rows_of, side_of, width, core = {}, {}, {}, {}

    def pads(gid) -> tuple[float, float]:
        return (pad_as[gid], PAD_END) if gid else (0, 0)

    def measure(gid) -> float:
        kid_widths = [measure(c) for c in children[gid]]
        kid_layers = [span[c] for c in children[gid]]
        rows: dict[int, list[str]] = {}
        for nid in direct[gid]:
            rows.setdefault(layer[nid], []).append(nid)
        for ids in rows.values():
            ids.sort(key=lambda k: key[k])
        side = {l for l in rows if any(lo <= l <= hi for lo, hi in kid_layers)}
        side_w = max((row_width(rows[l]) for l in side), default=0)
        kids_w = sum(kid_widths) + gap_across * max(len(kid_widths) - 1, 0)
        core[gid] = side_w + (gap_across if side_w and kid_widths else 0) + kids_w
        free_w = max((row_width(rows[l]) for l in rows if l not in side), default=0)
        rows_of[gid], side_of[gid] = rows, side
        s0, s1 = pads(gid)
        width[gid] = max(core[gid], free_w) + s0 + s1
        if gid and not lr:
            width[gid] = max(width[gid], header_width(gmap[gid]) + PAD_END)
        return width[gid]

    measure(None)
    slot, box = {}, {}

    def place(gid, x0: float) -> tuple[float, float]:
        s0, s1 = pads(gid)
        inner_x, inner_w = x0 + s0, width[gid] - s0 - s1
        rows, side = rows_of[gid], side_of[gid]
        side_w = max((row_width(rows[l]) for l in side), default=0)
        cx = inner_x + (inner_w - core[gid]) / 2
        lo, hi = float("inf"), float("-inf")
        for lyr, ids in rows.items():
            left, room = (cx, side_w) if lyr in side else (inner_x, inner_w)
            x = left + (room - row_width(ids)) / 2
            for nid in ids:
                slot[nid] = (x, along(lyr))
                x += across(nid) + gap_across
            lo, hi = min(lo, along(lyr)), max(hi, along(lyr) + row_b[lyr])
        if side_w:
            cx += side_w + gap_across
        for c in children[gid]:
            clo, chi = place(c, cx)
            lo, hi = min(lo, clo), max(hi, chi)
            cx += width[c] + gap_across
        if gid:
            lo, hi = lo - pad_bs[gid], hi + PAD_END
            box[gid] = (x0, lo, width[gid], hi - lo)
        return lo, hi

    place(None, 20)

    def real(a, b, wa, wb):  # abstract (across, along) rectangle -> page (x, y, w, h)
        return (b, a, wb, wa) if lr else (a, b, wa, wb)

    def pt(a, b):
        return (b, a) if lr else (a, b)

    vis, shape = {}, {}
    for nid, (a, b) in slot.items():
        sp = spec[nid]
        vx, vy, _, _ = real(a, b, A(sp["vis"]), B(sp["vis"]))
        vis[nid] = Rect(vx, vy, sp["vis"][0], sp["vis"][1], f"node {nid}")
        shape[nid] = Rect(vx + sp["off"][0], vy + sp["off"][1], sp["shape"][0], sp["shape"][1], f"node {nid}")
    group_rect = {g: Rect(*real(*v), f"box {g}") for g, v in box.items()}
    headers = {}
    for gid, r in group_rect.items():
        hw = header_width(gmap[gid])
        lines = 1 if not lr else max(1, round((pad_as[gid] - PAD_START) / HEADER_H) + 1)
        headers[gid] = Rect(r.x + 4, r.y + 2, min(hw, r.w - 8) if lr else hw - 4, HEADER_H * lines, f"header of box {gid}")

    # --- arrows: attach points spread along the side they use ---
    def side_below(nid) -> str:  # where an arrow leaves toward the next row
        return "stub" if (not lr and spec[nid]["below"]) else "end"

    uses: dict = {}
    for r in routes:
        if r["loop"]:
            continue
        uses.setdefault((r["up"], side_below(r["up"])), []).append(r)
        uses.setdefault((r["down"], side_below(r["down"]) if r["same"] else "start"), []).append(r)

    def attach(nid, how, r) -> list[tuple]:
        """Points from the shape out to where the arrow turns along (first point on the shape)."""
        s, v = shape[nid], vis[nid]
        group_ = uses[(nid, how)]
        k = group_.index(r)
        f = (k + 1) / (len(group_) + 1)
        if how == "stub":  # leave from the icon's side, clear of its label below
            y = s.y + s.h * f
            return [(s.x + s.w, y), (v.x + v.w + 8 + 6 * k, y)]
        if how == "end":
            return [(s.x + s.w * f, s.y + s.h)] if not lr else [(s.x + s.w, s.y + s.h * f)]
        return [(s.x + s.w * f, s.y)] if not lr else [(s.x, s.y + s.h * f)]  # start

    placed_vertical = []
    for r in routes:
        if r["loop"]:
            continue
        top_pts = attach(r["up"], side_below(r["up"]), r)
        y1a, y1b = lane_y[(r["i"], "label")]
        ly = (y1a + y1b) / 2
        ax_ = (lambda p: p[1]) if lr else (lambda p: p[0])  # across coordinate of a point
        start_across = ax_(top_pts[-1])
        if r["same"]:
            end_pts = list(reversed(attach(r["down"], side_below(r["down"]), r)))
            end_across = ax_(end_pts[0])
            path = top_pts + [pt(start_across, ly), pt(end_across, ly)] + end_pts
        else:
            end_pts = attach(r["down"], "start", r)
            end_across = ax_(end_pts[0])
            if layer[r["down"]] - layer[r["up"]] == 1:
                path = top_pts + [pt(start_across, ly), pt(end_across, ly)] + end_pts
            else:
                y2a, y2b = lane_y[(r["i"], "jog")]
                jy = (y2a + y2b) / 2
                between = [vis[n] for n in vis if layer[r["up"]] < layer[n] < layer[r["down"]]]
                band = [h for h in headers.values() if (h.x if lr else h.y) > ly and (h.x if lr else h.y) < jy]
                obstacles = between + band

                def clear(c):
                    a0, a1 = pt(c, ly), pt(c, jy)
                    return not any(seg_hits(a0, a1, o, -6) for o in obstacles)

                cands = [end_across, start_across, (start_across + end_across) / 2]
                for o in obstacles:
                    lo_, hi_ = (o.y, o.y + o.h) if lr else (o.x, o.x + o.w)
                    cands += [lo_ - 12, hi_ + 12]
                chan = min((c for c in cands if clear(c)), key=lambda c: abs(c - end_across), default=None)
                if chan is None:
                    far = max(((o.y + o.h) if lr else (o.x + o.w)) for o in obstacles) + 20
                    chan = far
                path = top_pts + [pt(start_across, ly), pt(chan, ly), pt(chan, jy), pt(end_across, jy)] + end_pts
        clean = [path[0]]
        for q in path[1:]:
            if abs(q[0] - clean[-1][0]) > 0.01 or abs(q[1] - clean[-1][1]) > 0.01:
                clean.append(q)
        if r["reversed"]:
            clean.reverse()
        r["path"] = clean
        r["lane"] = ly
        r["jog"] = sum(lane_y[(r["i"], "jog")]) / 2 if (r["i"], "jog") in lane_y else None
    # --- arrow labels: on the arrow's own path, clear of every other arrow, text, icon and box ---
    segs = [(r["i"], p, q) for r in routes if not r["loop"] for p, q in zip(r["path"], r["path"][1:])]
    fixed = list(vis.values()) + list(headers.values())
    placed: list[Rect] = []
    for r in routes:
        if r["loop"]:
            continue
        lw, lh = r["size"]
        path, ly = r["path"], r["lane"]
        others = [(p, q) for i, p, q in segs if i != r["i"]]
        across_axis = 1 if lr else 0  # page axis a lane runs along

        def fits(rect: Rect) -> bool:
            return (not any(seg_hits(p, q, rect, 0) for p, q in others)
                    and not any(rect.hits(o) for o in fixed) and not any(rect.hits(o) for o in placed))

        cands = []  # (anchor point on the path, (dx, dy) offset of the label's centre)
        for p, q in zip(path, path[1:]):
            on_lane = abs(p[1 - across_axis] - ly) < 0.01 and abs(q[1 - across_axis] - ly) < 0.01
            length = abs(q[0] - p[0]) + abs(q[1] - p[1])
            steps = [t / max(length, 1) for t in range(0, int(length) + 1, 8)] or [0.0]
            steps.sort(key=lambda t: abs(t - 0.5))
            lane_first = 0 if on_lane else 1
            for t in steps:
                pt_ = (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
                if on_lane:
                    half = (lw if not lr else lh) / 2
                    for k in [0] + [d for j in range(1, int(half // 8) + 1) for d in (j * 8, -j * 8)]:
                        off = (k, 0) if not lr else (0, k)
                        cands.append((lane_first, pt_, off))
                else:  # beside a run between rows
                    side = (lw / 2 + 5, 0) if not lr else (0, lh / 2 + 5)
                    for sign in (1, -1):
                        cands.append((lane_first, pt_, (side[0] * sign, side[1] * sign)))
        cands.sort(key=lambda c: c[0])
        chosen = None
        for _, anchor, off in cands:
            rect = Rect(anchor[0] + off[0] - lw / 2, anchor[1] + off[1] - lh / 2, lw, lh, f"label of arrow {r['i'] + 1}")
            if fits(rect):
                chosen = (anchor, off, rect)
                break
        if chosen is None:  # nowhere clean: keep the lane's middle and let the overlap check report it
            (p, q) = next(((p, q) for p, q in zip(path, path[1:])
                           if abs(p[1 - across_axis] - ly) < 0.01), (path[0], path[1]))
            anchor = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
            chosen = (anchor, (0, 0), Rect(anchor[0] - lw / 2, anchor[1] - lh / 2, lw, lh, f"label of arrow {r['i'] + 1}"))
        r["label_point"], r["label_offset"], r["label_rect"] = chosen
        placed.append(chosen[2])
    for r in routes:
        if r["loop"]:  # draw.io draws the loop on the right; its label goes beside it
            nid = edges[r["i"]]["from"]
            lw, lh = r["size"]
            r["path"] = []
            r["label_rect"] = Rect(vis[nid].x + vis[nid].w + 30, shape[nid].y, lw, lh, f"label of arrow {r['i'] + 1}")
    return {"vis": vis, "shape": shape, "groups": group_rect, "headers": headers, "routes": routes,
            "spec": spec, "lr": lr, "layer": layer,
            "lanes": [[(r["i"], role) for r, role, _ in lanes[lyr]] for lyr in sorted(lanes)]}


def share_lanes(plan: dict, over: bool) -> dict:
    """Pack each gap's arrows into as few lanes as their words allow: {(arrow, role): lane number}.

    Arrows share a lane when they sit clear of each other along it. With `over` their lines may also
    lie over each other, so long as no line runs under another arrow's label.
    """
    al = 0 if plan["lr"] else 1  # index of the coordinate that runs from row to row
    routes = {r["i"]: r for r in plan["routes"] if not r["loop"]}

    def hit(u, v) -> bool:
        return bool(u and v) and u[0] < v[1] + 6 and v[0] < u[1] + 6

    shared = {}
    for items in plan["lanes"]:
        tracks: list[list[tuple]] = []  # per lane, the (line, label) extents already in it
        for i, role in items:
            r = routes[i]
            level = r["lane"] if role == "label" else r["jog"]
            on = [pt[1 - al] for p, q in zip(r["path"], r["path"][1:])
                  if abs(p[al] - level) < 0.01 and abs(q[al] - level) < 0.01 for pt in (p, q)]
            size = r["size"][1 - al] if role == "label" else 0
            if on:
                line = (min(on), max(on))
                mid = (line[0] + line[1]) / 2
                label = (mid - size / 2, mid + size / 2) if size else None
            else:  # a straight arrow: no run along the lane, and its label sits beside it
                c = r["path"][0][1 - al]
                line, label = (c, c), (c - size - 6, c + size + 6) if size else None
            hull = (min(line[0], label[0]), max(line[1], label[1])) if label else line
            for t, held in enumerate(tracks):
                if over:
                    clash = any(hit(label, lb) or hit(label, ln) or hit(lb, line) for ln, lb, _ in held)
                else:
                    clash = any(hit(hull, hl) for _, _, hl in held)
                if not clash:
                    break
            else:
                t = len(tracks)
                tracks.append([])
            tracks[t].append((line, label, hull))
            shared[(i, role)] = t
    return shared


def overlaps(model: dict, plan: dict) -> list[str]:
    """Every collision between texts, icons, boxes and arrow paths; empty means clean."""
    vis, shape, groups, headers = plan["vis"], plan["shape"], plan["groups"], plan["headers"]
    gmap = {g["id"]: g for g in model.get("groups", [])}
    group_of = {n["id"]: n.get("group") for n in model.get("nodes", [])}

    def ancestors(gid):
        out = []
        while gid:
            out.append(gid)
            gid = gmap[gid].get("parent")
        return out

    problems = []
    ids = list(vis)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if vis[a].hits(vis[b]):
                problems.append(f"{vis[a].what} overlaps {vis[b].what}")
    for nid in ids:
        mine = set(ancestors(group_of[nid]))
        for gid, gr in groups.items():
            if gid in mine and not gr.holds(vis[nid]):
                problems.append(f"{vis[nid].what} spills out of {gr.what}")
            elif gid not in mine and gr.hits(vis[nid]):
                problems.append(f"{vis[nid].what} overlaps {gr.what}")
        for gid, h in headers.items():
            if h.hits(vis[nid]):
                problems.append(f"{h.what} overlaps {vis[nid].what}")
    gids = list(groups)
    for i, a in enumerate(gids):
        for b in gids[i + 1:]:
            ra, rb = groups[a], groups[b]
            if ra.hits(rb) and not (ra.holds(rb) or rb.holds(ra)):
                problems.append(f"{ra.what} overlaps {rb.what}")
            if headers[a].hits(headers[b]):
                problems.append(f"{headers[a].what} overlaps {headers[b].what}")
    routes = [r for r in plan["routes"]]
    labels = [(r["i"], r["label_rect"]) for r in routes]
    for i, (ri, la) in enumerate(labels):
        for n in ids:
            if la.hits(vis[n]):
                problems.append(f"{la.what} overlaps {vis[n].what}")
        for h in headers.values():
            if la.hits(h):
                problems.append(f"{la.what} overlaps {h.what}")
        for rj, lb in labels[i + 1:]:
            if la.hits(lb):
                problems.append(f"{la.what} overlaps {lb.what}")
    edges = model.get("edges", [])
    for r in routes:
        own = {edges[r["i"]]["from"], edges[r["i"]]["to"]}
        for p, q in zip(r["path"], r["path"][1:]):
            for n in ids:
                if n in own:
                    s, v = shape[n], vis[n]
                    label_area = Rect(v.x, s.y + s.h + 2, v.w, v.y + v.h - (s.y + s.h + 2), v.what)
                    if plan["spec"][n]["below"] and label_area.h > 0 and seg_hits(p, q, label_area):
                        problems.append(f"arrow {r['i'] + 1} runs through the label of {v.what}")
                elif seg_hits(p, q, vis[n]):
                    problems.append(f"arrow {r['i'] + 1} runs through {vis[n].what}")
            for h in headers.values():
                if seg_hits(p, q, h):
                    problems.append(f"arrow {r['i'] + 1} runs through the {h.what}")
            for rj, lb in labels:
                if rj != r["i"] and seg_hits(p, q, lb, 0):
                    problems.append(f"arrow {r['i'] + 1} runs through the {lb.what}")
    return sorted(set(problems))


# --- XML ----------------------------------------------------------------------------------

def add_cell(root_el, cid: str, value: str, style: str, parent: str, geo=None, *, edge=False,
             source=None, target=None, cite=None, points=None, waypoints=None, label_x=None, label_offset=None):
    attrs = {"id": cid, "label": value}
    if cite is not None:
        joined = "; ".join(as_list(cite))
        attrs.update({"cite": joined, "tooltip": f"Source: {joined}"})
    obj = ET.SubElement(root_el, "object", attrs)
    mx = ET.SubElement(obj, "mxCell", {"style": style, "parent": parent, "edge" if edge else "vertex": "1"})
    if source:
        mx.set("source", source)
    if target:
        mx.set("target", target)
    g = ET.SubElement(mx, "mxGeometry", {"as": "geometry"})
    if geo:
        g.attrib.update(dict(zip(("x", "y", "width", "height"), (f"{v:g}" for v in geo))))
    else:
        g.set("relative", "1")
        if label_x is not None:
            g.set("x", f"{label_x:.4f}")
        if label_offset and any(label_offset):
            ET.SubElement(g, "mxPoint", {"x": f"{label_offset[0]:.1f}", "y": f"{label_offset[1]:.1f}", "as": "offset"})
    if points:
        (sx, sy), (tx, ty), mids = points
        ET.SubElement(g, "mxPoint", {"x": f"{sx:g}", "y": f"{sy:g}", "as": "sourcePoint"})
        ET.SubElement(g, "mxPoint", {"x": f"{tx:g}", "y": f"{ty:g}", "as": "targetPoint"})
        waypoints = mids
    if waypoints:
        arr = ET.SubElement(g, "Array", {"as": "points"})
        for x, y in waypoints:
            ET.SubElement(arr, "mxPoint", {"x": f"{x:.1f}", "y": f"{y:.1f}"})


def along_path(path: list[tuple], point: tuple) -> float:
    """draw.io's relative label position (-1..1 along the path) of a point on the path."""
    cx, cy = point
    lengths = [abs(q[0] - p[0]) + abs(q[1] - p[1]) for p, q in zip(path, path[1:])]
    total, run = sum(lengths) or 1, 0.0
    for (p, q), ln in zip(zip(path, path[1:]), lengths):
        x0, x1 = sorted((p[0], q[0]))
        y0, y1 = sorted((p[1], q[1]))
        if x0 - 0.5 <= cx <= x1 + 0.5 and y0 - 0.5 <= cy <= y1 + 0.5:
            return 2 * (run + abs(cx - p[0]) + abs(cy - p[1])) / total - 1
        run += ln
    return 0.0


def extent(plan: dict) -> tuple[float, float]:
    """The right and bottom edges of everything a plan draws."""
    rects = list(plan["vis"].values()) + list(plan["groups"].values()) + [r["label_rect"] for r in plan["routes"]]
    pts = [p for r in plan["routes"] for p in r["path"]]
    return (max([r.x + r.w for r in rects] + [p[0] for p in pts]),
            max([r.y + r.h for r in rects] + [p[1] for p in pts]) + 30)


def draw_graph(model: dict, root_el, fit_of) -> tuple[float, float]:
    problems: list[str] = []
    plan = None
    for spread in (0, 10, 20, 30, 45, 60, 80, 100, 150):  # spread the layout only as far as it takes for no words to collide
        own = plan_graph(model, spread)
        # tidiest first: arrows sharing lanes clear of each other, then lying over each other, then a lane each
        plans = [plan_graph(model, spread, share_lanes(own, over)) for over in (False, True)] + [own]
        clean = [p for p in plans if not overlaps(model, p)]
        if clean:
            fits = [fit_of(*extent(p)) for p in clean]
            whole = [p for p, f in zip(clean, fits) if f["pages"] == 1 and f["scale"] >= 1]
            # arrows lie over each other only when that is what gets the drawing onto the paper
            plan = whole[0] if whole else max(zip(clean, fits), key=lambda pf: (-pf[1]["pages"], pf[1]["scale"]))[0]
            problems = []
            break
        problems = overlaps(model, own)
    if problems:
        raise ModelError("could not lay the diagram out without overlaps (" + "; ".join(problems[:6]) +
                         (f"; and {len(problems) - 6} more" if len(problems) > 6 else "") +
                         "): split it, or shorten the longest labels")
    groups = {g["id"]: g for g in model.get("groups", [])}
    grect = plan["groups"]

    def origin(gid):
        return (grect[gid].x, grect[gid].y) if gid else (0, 0)

    drawn: list[str] = []

    def draw_group(gid: str) -> None:  # parents first, so draw.io nests children inside them
        if gid in drawn:
            return
        g = groups[gid]
        if g.get("parent"):
            draw_group(g["parent"])
        r = grect[gid]
        ox, oy = origin(g.get("parent"))
        label = (f"{html.escape(g['label'])} <font style=\"font-size:9px\" color=\"#555555\">"
                 f"({html.escape('; '.join(as_list(g['cite'])))})</font>")
        stencil = g.get("_stencil")
        style = GROUP_STYLES[g["kind"]] + ("spacingLeft=34;spacingTop=6;" if stencil else "")
        add_cell(root_el, f"g-{gid}", label, style, f"g-{g['parent']}" if g.get("parent") else "1",
                 (r.x - ox, r.y - oy, r.w, r.h), cite=g["cite"])
        if stencil:  # the hosting service's icon in the box's header
            k = GROUP_ICON / max(stencil["w"], stencil["h"])
            add_cell(root_el, f"g-{gid}-icon", "", stencil["style"].rstrip(";") + ";movable=0;resizable=0;",
                     f"g-{gid}", (6, 5, round(stencil["w"] * k), round(stencil["h"] * k)))
        drawn.append(gid)

    for gid in groups:
        draw_group(gid)
    for n in model["nodes"]:
        s, sp = plan["shape"][n["id"]], plan["spec"][n["id"]]
        stencil = n.get("_stencil")
        below = f"verticalLabelPosition=bottom;verticalAlign=top;whiteSpace=wrap;html=1;labelWidth={sp.get('label_w', LABEL_W):.0f};"
        if stencil or n.get("style"):
            style = (stencil["style"] if stencil else n["style"]).rstrip(";") + ";" + below
        elif n["kind"] == "person":
            style = NODE_STYLES["person"] + below + (SENSITIVE if n.get("data") else "")
        else:
            style = NODE_STYLES[n["kind"]] + (SENSITIVE if n.get("data") else "")
        ox, oy = origin(n.get("group"))
        add_cell(root_el, f"n-{n['id']}", node_label(n, with_tech=model["type"] != "c4-context"), style,
                 f"g-{n['group']}" if n.get("group") else "1", (s.x - ox, s.y - oy, s.w, s.h), cite=n["cite"])
    edges = model.get("edges", [])
    for r in plan["routes"]:
        e = edges[r["i"]]
        style = EDGE_STYLE + ("dashed=1;" if e.get("dashed") else "") + (SENSITIVE if e.get("data") else "")
        if r["loop"]:
            add_cell(root_el, f"e-{r['i'] + 1}", r["text"], style, "1", edge=True,
                     source=f"n-{e['from']}", target=f"n-{e['to']}", cite=e["cite"])
            continue
        path = r["path"]
        s0, s1 = plan["shape"][e["from"]], plan["shape"][e["to"]]
        ex, ey = (path[0][0] - s0.x) / s0.w, (path[0][1] - s0.y) / s0.h
        nx, ny = (path[-1][0] - s1.x) / s1.w, (path[-1][1] - s1.y) / s1.h
        style = style.replace("edgeStyle=orthogonalEdgeStyle;", "edgeStyle=none;") + \
            f"exitX={ex:.4f};exitY={ey:.4f};exitPerimeter=0;entryX={nx:.4f};entryY={ny:.4f};entryPerimeter=0;"
        add_cell(root_el, f"e-{r['i'] + 1}", r["text"], style, "1", edge=True, source=f"n-{e['from']}",
                 target=f"n-{e['to']}", cite=e["cite"], waypoints=path[1:-1],
                 label_x=along_path(path, r["label_point"]), label_offset=r["label_offset"])
    return extent(plan)


def draw_sequence(model: dict, root_el) -> tuple[float, float]:
    """Lifelines sized to their headers; each message on its own row, tall enough for its label."""
    msgs = model["messages"]
    first = {}
    for j, m in enumerate(msgs):
        for end in (m["from"], m["to"]):
            first.setdefault(end, j)
    parts = sorted(model["participants"], key=lambda p: first.get(p["id"], len(msgs)))  # left to right as they appear
    gap = 70 if any(m["from"] == m["to"] for m in msgs) else 30  # a self-message's label sits right of its lifeline
    head = max(60, max(block_size(node_lines(p), W - 16)[1] + 16 for p in parts))
    texts = [edge_text(m, prefix=f"{j}. ") for j, m in enumerate(msgs, 1)]
    step = max(36, max(size[1] for _, size in texts) + 12)
    height = head + step * (len(msgs) + 1)
    cx = {}
    for i, p in enumerate(parts):
        x = 20 + i * (W + gap)
        cx[p["id"]] = x + W / 2
        colours = "".join(re.findall(r"(?:fillColor|strokeColor|fontColor)=[^;]+;", NODE_STYLES[p["kind"]]))
        style = ("shape=umlLifeline;perimeter=lifelinePerimeter;whiteSpace=wrap;html=1;container=0;collapsible=0;"
                 f"recursiveResize=0;outlineConnect=0;size={head:.0f};fontSize=12;{colours}")
        add_cell(root_el, f"p-{p['id']}", node_label(p), style, "1", (x, TOP, W, height), cite=p["cite"])
    right = 20 + len(parts) * (W + gap) - gap
    for j, (m, (text, size)) in enumerate(zip(msgs, texts), 1):
        y = TOP + head + j * step
        style = ("html=1;verticalAlign=bottom;fontSize=10;labelBackgroundColor=#FFFFFF;"
                 + ("dashed=1;endArrow=open;endFill=0;" if m.get("return") else "endArrow=block;endFill=1;")
                 + (SENSITIVE if m.get("data") else ""))
        a, b = cx[m["from"]], cx[m["to"]]
        if a == b:
            pts = ((a, y - 10), (a, y + 10), [(a + 50, y - 10), (a + 50, y + 10)])
            style += "edgeStyle=none;align=left;"
            right = max(right, a + 56 + size[0])
        else:
            pts = ((a, y), (b, y), [])
        add_cell(root_el, f"m-{j}", text, style, "1", edge=True, cite=m["cite"], points=pts)
    return right, TOP + height + 40


def page_fit(width: float, height: float, size: str, orientation: str) -> dict:
    """Fit the drawing to the chosen paper: one page, else two (side by side or stacked, whichever is larger).

    Layout spacing never shrinks to fit a page. When the chosen paper can't carry the drawing readably,
    `suggest` names the smallest paper (size, orientation, pages) that can.
    """
    margin = 80

    def best_on(sz: str, orient: str) -> tuple[int, float, float, float]:
        short, long_ = PAPER[sz]
        pw, ph = (short, long_) if orient == "portrait" else (long_, short)

        def scale(w_avail: float, h_avail: float) -> float:
            return min(1.0, (w_avail - margin) / width, (h_avail - margin) / height)

        one = scale(pw, ph)
        two = max(scale(2 * pw, ph), scale(pw, 2 * ph))
        pages, sc = (1, one) if one >= MIN_READABLE_SCALE or one >= two else (2, two)
        return pages, sc, pw, ph

    pages, best, pw, ph = best_on(size, orientation)
    fit = {"size": size, "orientation": orientation, "pages": pages, "scale": round(best, 2),
           "readable": best >= MIN_READABLE_SCALE, "page_width": pw, "page_height": ph}
    if not fit["readable"]:
        start_at = PAPER_UP.index(size) if size in PAPER_UP else 0
        for sz in PAPER_UP[start_at:]:
            options = [(orient, *best_on(sz, orient)[:2]) for orient in ("portrait", "landscape")]
            readable = [o for o in options if o[2] >= MIN_READABLE_SCALE]
            if readable:
                orient, pg, sc = max(readable, key=lambda o: (o[0] == orientation, o[2]))
                fit["suggest"] = {"size": sz, "orientation": orient, "pages": pg, "scale": round(sc, 2)}
                break
    return fit


def stamp_line(st: dict, drawn: str) -> str:
    if st["spine_commit"]:
        src = f"@ {st['spine_commit'][:7]} ({st['spine_committed'][:10]})" + (" + uncommitted changes" if st["spine_dirty"] else "")
    else:
        src = f"modified {st['spine_modified'][:10]} (not in git)"
    return f"Drawn {drawn[:10]} from {st['spine']} {src}"


def build(model: dict, root: Path, icons: Path | None = None) -> tuple[bytes, dict]:
    sources = validate(model, root, icons)
    st = spine_mod.stamp(root, sources[0])
    drawn = datetime.now(timezone.utc).isoformat(timespec="seconds")
    items = [i for k in ("nodes", "groups", "edges", "participants", "messages") for i in model.get(k, [])]
    cited = sorted({c for i in items for c in as_list(i.get("cite")) if AD_ID.match(c)}, key=lambda a: int(a[3:]))

    mxfile = ET.Element("mxfile", {"host": "org-kit agent-davinci", "type": "device"})
    diagram = ET.SubElement(mxfile, "diagram", {"id": model["type"], "name": TYPE_NAMES[model["type"]]})
    graph = ET.SubElement(diagram, "mxGraphModel", {"grid": "1", "gridSize": "10", "guides": "1", "tooltips": "1",
                                                     "connect": "1", "arrows": "1", "fold": "1", "page": "1",
                                                     "pageScale": "1", "math": "0", "shadow": "0"})
    root_el = ET.SubElement(graph, "root")
    meta = ET.SubElement(root_el, "object", {
        "id": "0", "label": "", "arch_diagram": "1", "arch_type": model["type"], "arch_title": model["title"],
        "drawn": drawn, "spine": st["spine"], "spine_commit": st["spine_commit"],
        "spine_committed": st["spine_committed"], "spine_dirty": str(st["spine_dirty"]).lower(),
        "spine_modified": st["spine_modified"], "ad_hashes": json.dumps(st["ad_hashes"], separators=(",", ":")),
        "cited_ads": ",".join(cited),
        "source_hashes": json.dumps({p.resolve().relative_to(root.resolve()).as_posix(): spine_mod.file_hash(p)
                                     for p in sources[1:]}, separators=(",", ":")),
    })
    ET.SubElement(meta, "mxCell")
    ET.SubElement(root_el, "mxCell", {"id": "1", "parent": "0"})

    gaps = [str(g) for g in model.get("gaps", []) if str(g).strip()]
    notes = []
    if model["type"] == "data-flow":
        notes.append('<b>Key:</b> <font color="#B85450"><b>PII/PHI</b></font> marks personal or health data; '
                     "red dashed boxes are trust boundaries.")
    notes.append("<b>Gaps (not drawn):</b>" + ("<br>" + "<br>".join(f"• {html.escape(g)}" for g in gaps) if gaps else " none"))
    notes.append("Each element's source is in grey under its name and in its tooltip.")
    page = model.get("page") or {}

    def notes_height(right: float) -> float:
        return 24 + sum(block_size([(re.sub("<[^>]+>", "", part), 11, False)], right - 20 - 16)[1]
                        for n in notes for part in n.split("<br>"))

    def fit_of(right: float, bottom: float) -> dict:
        right = max(right, 520)
        return page_fit(right + 20, bottom + notes_height(right) + 20,
                        page.get("size", DEFAULT_PAGE[0]), page.get("orientation", DEFAULT_PAGE[1]))

    if model["type"] == "sequence":
        right, bottom = draw_sequence(model, root_el)
    else:
        right, bottom = draw_graph(model, root_el, fit_of)
    fit = fit_of(right, bottom)
    right = max(right, 520)
    notes_h = notes_height(right)
    add_cell(root_el, "title", f"<b>{html.escape(TYPE_NAMES[model['type']])}: {html.escape(model['title'])}</b>",
             "text;html=1;align=left;verticalAlign=top;fontSize=16;", "1", (20, 10, right - 20, 28))
    add_cell(root_el, "stamp", html.escape(stamp_line(st, drawn)),
             "text;html=1;align=left;verticalAlign=top;fontSize=10;fontColor=#555555;", "1", (20, 38, right - 20, 20))
    add_cell(root_el, "notes", "<br><br>".join(notes),
             "text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;fontSize=11;strokeColor=#CCCCCC;fillColor=#FAFAFA;spacing=8;",
             "1", (20, bottom, right - 20, notes_h))
    graph.attrib.update({"pageWidth": str(fit.pop("page_width")), "pageHeight": str(fit.pop("page_height"))})
    meta.set("page", f"{fit['size']} {fit['orientation']} x{fit['pages']} at {fit['scale']}")
    ET.indent(mxfile)
    report = {"type": model["type"], "stamp": stamp_line(st, drawn), "cited_ads": cited, "gaps": gaps, "page": fit,
              "elements": len(items)}
    return ET.tostring(mxfile, encoding="utf-8"), report


def export(target: Path, formats: list[str]) -> dict:
    if not formats:
        return {"skipped": "not requested"}
    cli = spine_mod.drawio_cli()
    if not cli:
        return {"skipped": "draw.io desktop not found (install it, or set DRAWIO_CLI to its executable)"}
    written, failed = [], []
    for fmt in formats:
        out = target.with_suffix(f".{fmt}")
        args = [cli, "--export", "--format", fmt, "--border", "10", *(["--scale", "2"] if fmt == "png" else []),
                "--output", str(out), str(target)]
        try:
            res = subprocess.run(args, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failed.append({"format": fmt, "error": str(exc)})
            continue
        if res.returncode == 0 and out.is_file():
            written.append(str(out))
        else:
            failed.append({"format": fmt, "error": (res.stderr or res.stdout).strip()[-400:]})
    return {"written": written, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project_root")
    parser.add_argument("model", help="<folder>/<name>.model.json; <name>.drawio is written beside it")
    parser.add_argument("--force", action="store_true", help="Replace an existing <name>.drawio (hand edits in it are lost)")
    parser.add_argument("--export", default="png,svg", help="Comma-separated export formats, or 'none' (default: png,svg)")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    model_path = Path(args.model)
    if not model_path.is_absolute() and not model_path.exists():
        model_path = root / model_path
    if not model_path.name.endswith(".model.json"):
        print(json.dumps({"status": "error", "message": "the model file must be named <name>.model.json"}))
        return 1
    target = model_path.with_name(model_path.name[: -len(".model.json")] + ".drawio")
    try:
        model = json.loads(model_path.read_text(encoding="utf-8"))
        xml_bytes, report = build(model, root, model_path.parent / "icons")
        if target.exists() and not args.force:
            print(json.dumps({"status": "error", "message": f"{target.name} exists; pass --force to replace it (hand edits in it are lost)"}))
            return 1
        target.write_bytes(xml_bytes)
    except ModelError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 2
    formats = [] if args.export.strip().lower() == "none" else [f.strip().lower() for f in args.export.split(",") if f.strip()]
    print(json.dumps({"status": "success", "file": str(target), **report, "exports": export(target, formats)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
