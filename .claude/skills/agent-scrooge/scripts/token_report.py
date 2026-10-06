#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Token usage for this project by work item (story or planning phase), step and activity.

Reads Claude Code's own session logs for the project (~/.claude/projects/<project root with every
non-alphanumeric character as "-">/: one .jsonl per session, plus subagents/agent-<id>.jsonl and
.meta.json per agent) and writes seven files to <out-dir> (default: the org config's
token_usage_folder, else <project>/docs/costing/token_usage/):

  token_usage.csv                one row per work item x day x step x activity x model
  token_usage_summary.csv        planning phases; each story as a total plus six activity rows
  token_usage_summary_final.csv  planning phases; all stories combined into six activity rows; each epic's
                                 stories; with the Claude API budget, actual (USD) and variance where budgeted
  report.md                      the same data as readable tables
  claude_usage.csv               tokens by type per work item x model, with first and last call time
                                 (for Claude API pricing, the project timeline and dashboards)
  claude_usage_daily.csv         the same per day, with cost_usd at Claude API list prices when
                                 <project>/_bmad/memory/agent-scrooge/claude-prices.json exists (claude_prices.py --save)
  agent_runs.csv                 one row per subagent: its work item, parent (main session or agent), launched_by
                                 (Agent, Workflow, or unknown), run_id (the launching call; a Workflow's agents
                                 share one), type, step, depth, first and last call, minutes, how many other agents
                                 of the session overlapped it (parallel_with), calls, cost units and cost_usd

Every API call is counted once (deduplicated by message id) and attributed three ways:
  work item  a story (a Jira key such as PROJ-18; the key comes from --key, else the org config's
             jira_project_key, else the story map), from the agent's description (Jira key or
             story number), the agent that started it, or the call's git branch; else a planning
             phase from the skill running at the time, the planning files a call touched, or a
             planning agent's description; else ops (merges, promotions, deploys on dev/main) or other.
  step       the agent's job: main session, build (orchestrating), implement, review, explore,
             context, testing, planning agent.
  activity   the tool the call used: writing code, writing tests, running tests & checks, writing
             docs/specs, reading/shell, git/PR/Jira, MCP tools, delegating, thinking/reporting.
  category   the six the summaries split each story into: AI comprehension (reading / shell), AI reasoning
             (thinking / reporting, docs/specs, delegating), Writing code, Writing tests, Running tests and
             checks, and MCP usage (git / Jira / PR and every MCP server call).

Cost units weight each token type against one fresh input token (cache read 0.1, 5-minute cache
write 1.25, 1-hour cache write 2, output 5): relative, not dollars, every model alike.

Every row of token_usage.csv, claude_usage.csv, claude_usage_daily.csv and agent_runs.csv ends with a person
column: whose Claude Code logs the calls came from. Your own logs are named after your git user.name.

Teams: each person's logs live in their own ~/.claude, so one person's ledger shows only their own sessions.
To see the whole team, everyone exports their logs to a shared folder (a synced drive, not the repo: the
logs hold whole conversations) and the ledger reads them all:
  --export [DIR]   copy your project log folder to DIR/<your name>/ (only new or changed files) and stop
  --team DIR       read your own logs plus every DIR/<person>/ folder in it
Both default to team_logs_folder in the org config; set it in _bmad/custom/config.user.toml under
[modules.org], since the shared folder's path differs per machine. The background refresh hooks then
include the team's exported logs too. A call exported by two people is still counted once.

Prints JSON to stdout: the files written, call and unit totals, the log folders read and, for more than
one person, the cost units per person; with --story KEY, that story's cost by step and activity plus a
one-line summary for its pull request and Jira comment.
Project data (optional, kept out of the installed skill so updates never overwrite it), in
<project>/_bmad/memory/agent-scrooge/:
  stories.json   {"stories": {"PROJ-18": ["2.3", "Choose a product"], ...}}: story numbers and titles
  savings.json   {"savings": {"<row>": "<suggestion>", ...}}: replaces or adds suggested savings
  token-budgets.json  {"currency": "USD", "phases": {"test design": 40, ...}, "epics": {"1": 150, ...}, "cite": "..."}:
                 the Claude API budget per planning phase and per epic, in USD at Claude API list prices; the
                 final summary and report.md compare it with the actual (needs claude-prices.json)

Exit codes: 0 ok, 2 error (no log folder, unreadable input).
"""

from __future__ import annotations

import argparse
import getpass
import collections
import csv
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11: no org config, fall back to the story map and defaults
    tomllib = None

SKILL_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path("_bmad") / "memory" / "agent-scrooge"
WEIGHTS = {"input": 1.0, "cache_read": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 2.0, "output": 5.0}

# Skill (or slash command) -> planning phase while it runs. None: doesn't change the phase.
SKILL_PHASE = {
    "bmad-brainstorming": "brainstorming", "bmad-forge-idea": "brainstorming", "bmad-product-brief": "brainstorming",
    "bmad-prfaq": "brainstorming", "bmad-spec": "spec", "bmad-prd": "spec",
    "bmad-architecture": "architecture", "bmad-ux": "UX design",
    "bmad-create-epics-and-stories": "epics & stories", "bmad-sprint-planning": "epics & stories",
    "bmad-correct-course": "planning changes (correct course)",
    "bmad-testarch-test-design": "test design", "bmad-tea": "test design",
    "agent-jules": "governance (Jules)", "bmad-agent-builder": "tooling & setup",
    "bmad-project-context": "tooling & setup", "bmad-customize": "tooling & setup", "update-config": "tooling & setup",
    "bmad-module-builder": "tooling & setup", "agent-scrooge": "token reporting", "bmad-build": None,
    "agent-davinci": "architecture docs (Da Vinci)", "org-setup": "tooling & setup",
    "clear": None, "compact": None, "mcp": None, "agents": None, "cost": None, "usage": None,
}
SKILL_EXPIRY = timedelta(minutes=60)  # on a real branch, a skill's phase lapses after this long

# Planning agents' descriptions -> phase (first match wins; story keys and numbers are checked first).
DESCRIPTION_PHASE = [
    (r"brainstorm|keepsake", "brainstorming"),
    (r"\bux\b|design\.md|experience|mockup|theme|colou?r|provenance column", "UX design"),
    (r"spine|architecture|\bad-\d|tech currency|walkthrough deck|currency review|adversarial review of ad", "architecture"),
    (r"\btea\b|test design|testability", "test design"),
    (r"jules|governance|cloudassessment|criticality|architectureassessment|privacyassessment|airiskassessment", "governance (Jules)"),
    (r"leonardo|davinci|da vinci|principles\.md|drawio|\bhld\b|\blld\b|design doc", "architecture docs (Da Vinci)"),
    (r"standards|terraform\.md|azure\.md", "standards & conventions"),
    (r"mars rover", "bmad-build trial"),
    (r"epic|stories|story-level|jira|requirements", "epics & stories"),
    (r"inconsistenc|reconcile|spec\b", "spec"),
]

# Planning files a main-session call touches -> phase (sticky, like a skill).
ARTIFACT_PHASE = [
    (r"brainstorm", "brainstorming"),
    (r"ux-designs|DESIGN\.md|EXPERIENCE\.md|mockups", "UX design"),
    (r"ARCHITECTURE-SPINE|planning-artifacts/architecture", "architecture"),
    (r"epics\.md|mcp__atlassian|sprint-status|createJiraIssue|editJiraIssue", "epics & stories"),
    (r"test-design|testarch", "test design"),
    (r"skills/agent-jules|docs/governance|CloudAssessment|CriticalityAssessment|TechnicalArchitectureAssessment|DataGovernancePrivacyAssessment|EnterpriseAIRiskAssessment|_bmad/memory/jules", "governance (Jules)"),
    (r"planning-artifacts/spec|SPEC\.md", "spec"),
    (r"docs/standards", "standards & conventions"),
    (r"docs/architecture/(principles|diagrams|design)|skills/agent-davinci|_bmad/memory/agent-davinci",
     "architecture docs (Da Vinci)"),
]

PHASE_ORDER = ["brainstorming", "spec", "architecture", "UX design", "epics & stories", "test design",
               "standards & conventions", "governance (Jules)", "tooling & setup", "bmad-build trial",
               "planning changes (correct course)", "architecture docs (Da Vinci)", "token reporting"]
PHASE_NOTES = {
    "brainstorming": "Brainstorming the idea and the brainstorm keepsake page",
    "spec": "Condensing the brainstorm into SPEC.md",
    "architecture": "Architecture spine: the first design, then later changes to its decisions",
    "UX design": "DESIGN.md, EXPERIENCE.md, mockups and colour themes, including rethemes",
    "epics & stories": "Epics and stories with acceptance criteria, validation, and Jira project setup",
    "test design": "System test design (TEA) and testability requirements",
    "standards & conventions": "The project's engineering standards and conventions",
    "governance (Jules)": "The Jules governance agent and the IT governance assessments",
    "tooling & setup": "BMad project context, customisations, custom agents and settings",
    "bmad-build trial": "Trial run of bmad-build on a sample spec before the real stories",
    "planning changes (correct course)": "Sprint change proposals",
    "token reporting": "Scrooge McDuck token reports and cost estimates",
    "architecture docs (Da Vinci)": "Da Vinci's architecture principles, diagrams and HLD/LLD documents",
    "merges, promotions & deploys": "Merging PRs, promoting dev to main, deploying and checking deploys",
    "other": "Work that couldn't be attributed to a story or phase",
}
# The six categories every story (and all stories combined) is split into in the summaries. Each detailed
# activity (token_usage.csv) falls into one; docs/specs and delegating count as AI reasoning.
AI_COMPREHENSION, AI_REASONING, WRITING_CODE = "AI comprehension", "AI reasoning", "Writing code"
WRITING_TESTS, RUNNING_TESTS, MCP_USAGE = "Writing tests", "Running tests and checks", "MCP usage"
SUMMARY_ACTIVITY = {
    "reading/shell": AI_COMPREHENSION, "thinking/reporting": AI_REASONING,
    "writing docs/specs": AI_REASONING, "delegating": AI_REASONING,
    "writing code": WRITING_CODE, "writing tests": WRITING_TESTS,
    "running tests & checks": RUNNING_TESTS, "git/PR/Jira": MCP_USAGE, "MCP tools": MCP_USAGE,
}
ACTIVITY_NOTES = {
    AI_COMPREHENSION: "reading / shell: reading files, searching and shell commands; each call re-reads the agent's context",
    AI_REASONING: "thinking / reporting: planning, reasoning and reports with no tool; includes writing specs/sprint status and handing work to agents",
    WRITING_CODE: "Edits to application and infrastructure files",
    WRITING_TESTS: "Edits to test files (unit, API, end-to-end)",
    RUNNING_TESTS: "Check scripts, test runners (pytest, vitest, Playwright), lint and type checks",
    MCP_USAGE: "git / Jira / PR: branches, commits, rebases, pull requests, Jira updates and every MCP server call",
}
# Savings keyed by the categories' earlier names (before 1.10.0) still apply to the renamed rows.
OLD_CATEGORY = {"reading/shell": AI_COMPREHENSION, "thinking/reporting": AI_REASONING, "writing code": WRITING_CODE,
                "writing tests": WRITING_TESTS, "running tests & checks": RUNNING_TESTS, "git/PR/Jira": MCP_USAGE}

# The final summary's suggested_cost_saving column, keyed by row (phase, activity, or row label).
SAVINGS = {
    "brainstorming": "Usually cheap; stop once the idea is clear rather than polishing the output.",
    "spec": "Usually cheap; keep it.",
    "architecture": "Later changes to decisions cost about as much as the first design. Batch amendments into one run per sprint instead of one per change.",
    "UX design": "Render fewer mockup and theme variants (two, not four or more), and apply a retheme with one small agent instead of a full UX pass.",
    "epics & stories": "Validate epics once, after all changes, instead of after each round; update Jira in one batch at the end.",
    "test design": "Usually modest; keep it.",
    "standards & conventions": "Reuse the org kit's standards baselines instead of drafting per project.",
    "governance (Jules)": "Jules reloads its whole memory each session; keep governance sessions short and focused on one questionnaire at a time.",
    "tooling & setup": "One-off cost; nothing to save.",
    "bmad-build trial": "One-off cost; nothing to save.",
    "planning changes (correct course)": "Fold small course corrections into the next story brief instead of a separate proposal.",
    "architecture docs (Da Vinci)": "Redraw from the saved model instead of re-reading the spine, and batch spine amendments before a redraw.",
    "token reporting": "The hook refreshes the files for free; ask Scrooge only when you want an explanation.",
    "epic": "An epic over its Claude API budget: look for reruns and long review loops in its stories before the next epic, and set the next budget from this actual.",
    "all stories": "The biggest lever is shorter agent contexts: every call re-reads everything the agent has seen, so start each step in a fresh agent with only the story brief.",
    AI_COMPREHENSION: "Start each step (implement, review, fix) in a fresh agent that gets a one-page brief, not the full planning documents; use a small model for code searches; avoid re-reading large files.",
    AI_REASONING: "Run the orchestrating build agent on a cheaper model, or let the main session run the steps directly; ask agents for short final reports.",
    WRITING_CODE: "Core work; don't cut it.",
    WRITING_TESTS: "Core work; don't cut it.",
    RUNNING_TESTS: "Run only the checks the change touches while iterating and the full run once before pushing; fix flaky tests so they stop causing reruns.",
    MCP_USAGE: "Keep stories one at a time (parallel lanes pay extra in rebases); write PR bodies and Jira comments in one pass at the end, with a small model; call MCP servers for what only they can do.",
    "Merges, promotions & deploys": "Batch several stories per promotion; watch deploys with a background command, which costs no tokens.",
    "Other": "Too small to act on.",
    "Total": "Largest savings are usually: shorter agent contexts (reading), a cheaper orchestrator, fewer planning rework rounds, and no parallel story lanes.",
}

TEST_RE = re.compile(r"check\.sh|pytest|vitest|playwright|npm (run )?(test|lint|typecheck)|ruff|mypy|tsc\b|prettier|eslint|terraform (plan|validate|test)|tflint|gitleaks|actionlint|shellcheck")
GIT_RE = re.compile(r"^\s*(cd [^&;]+(&&|;)\s*)?(git|gh)\b")
TEST_FILE_RE = re.compile(r"(^|/)(tests?|e2e)/|\.(test|spec)\.[jt]sx?$|(^|/)test_[^/]*\.py$|(^|/)conftest\.py$|tftest\.hcl$")
DOC_FILE_RE = re.compile(r"\.(md|ya?ml|txt|html|docx|csv)$|(^|/)_bmad-output/|(^|/)docs/|/memory/")
STORY_NUMBER_RE = re.compile(r"(?<![\w.-])([1-9])\.(\d{1,2})(?!\d)")
# Files a shell command writes: redirects, tee, sed -i, and Python edit scripts (p='...' ... write).
WRITE_TARGET_RES = [
    re.compile(r"(?:^|[\s;&|])(?:cat|printf|echo)[^|;&]*?>\s*['\"]?([\w./@-]+)"),
    re.compile(r"\btee\s+(?:-a\s+)?['\"]?([\w./@-]+)"),
    re.compile(r"\bsed\s+-i\s*(?:''|\"\")?\s+(?:-E\s+)?(?:'[^']*'|\"[^\"]*\")\s+['\"]?([\w./@-]+)"),
    re.compile(r"open\(\s*['\"]([\w./@-]+)['\"]\s*,\s*['\"][wa]"),
    re.compile(r"\bp\s*=\s*['\"]([\w./@-]+\.\w+)['\"][\s\S]*?(?:open\(p\s*,\s*['\"]w|write_text|\.write\()"),
]
SCRATCH_RE = re.compile(r"(^|/)\.work/|^/tmp/|^/dev/|^/private/|\.log$")
COMMAND_RE = re.compile(r"<command-name>/?([\w:-]+)</command-name>")


def load_stories(path: Path) -> dict[str, tuple[str, str]]:
    """Jira key -> (story number, title). A missing file is an empty map."""
    if not path.is_file():
        return {}
    with open(path) as fh:
        return {key: (value[0], value[1]) for key, value in json.load(fh)["stories"].items()}


def load_savings(path: Path) -> dict[str, str]:
    """The project's own suggested savings, laid over the defaults. A missing file adds none."""
    if not path.is_file():
        return {}
    with open(path) as fh:
        return {OLD_CATEGORY.get(k, k): v for k, v in json.load(fh)["savings"].items()}


def org_config(root: Path) -> dict:
    """The [modules.org] section of _bmad/config.toml, with _bmad/custom overrides; {} without one."""
    merged: dict = {}
    if tomllib is None:
        return merged
    for path in (root / "_bmad" / "config.toml", root / "_bmad" / "custom" / "config.toml",
                 root / "_bmad" / "custom" / "config.user.toml"):
        if path.is_file():
            with open(path, "rb") as fh:
                merged.update(tomllib.load(fh).get("modules", {}).get("org", {}))
    return merged


def key_pattern(key: str | None, stories: dict[str, tuple[str, str]]) -> re.Pattern | None:
    """The Jira key regex: the given key, else the story map's prefix; None matches story numbers only."""
    if not key and stories:
        key = next(iter(stories)).rsplit("-", 1)[0]
    return re.compile(rf"\b({re.escape(key)})-(\d+)") if key else None


def project_root(start: Path) -> Path:
    """The main checkout's root, even when run from a worktree: Claude Code logs every worktree's
    agents under the project it was launched from."""
    try:
        common = subprocess.run(["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
        return Path(common).parent
    except (OSError, subprocess.CalledProcessError):
        return start.resolve()


def log_dir_for(root: Path) -> Path:
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root))


def whoami(root: Path) -> str:
    """Your name for the person column and your export folder: git user.name, else the login name."""
    try:
        name = subprocess.run(["git", "-C", str(root), "config", "user.name"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        name = ""
    if not name:
        try:
            name = getpass.getuser()
        except (OSError, KeyError):
            name = ""
    return name or "me"


def folder_name(name: str) -> str:
    """A person's name as a folder name on any OS."""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", name).strip(" .") or "unknown"


def _log_files(log_dir: Path) -> list[Path]:
    """The files the ledger reads from one log folder: session logs, and each session's agents and their metadata."""
    return [Path(p) for p in sorted(glob.glob(str(log_dir / "*.jsonl")) + glob.glob(str(log_dir / "*" / "subagents" / "*.jsonl"))
                                    + glob.glob(str(log_dir / "*" / "subagents" / "*.meta.json")))]


def export_logs(log_dir: Path, team_dir: Path, name: str) -> dict:
    """Copy one log folder into team_dir/<name>/, skipping files already copied and unchanged since."""
    dest = team_dir / folder_name(name)
    copied = unchanged = 0
    for src in _log_files(log_dir):
        target = dest / src.relative_to(log_dir)
        stat = src.stat()
        if target.is_file() and target.stat().st_size == stat.st_size and target.stat().st_mtime >= stat.st_mtime:
            unchanged += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, target)  # a teammate's refresh never reads half a file
        copied += 1
    return {"exported_to": str(dest), "copied": copied, "unchanged": unchanged}


def parse_logs_arg(spec: str) -> tuple[str, Path]:
    """--logs [NAME=]FOLDER -> (name or "", folder); a folder whose own name holds "=" is taken whole."""
    name, sep, path = spec.partition("=")
    return (name, Path(path)) if sep and name and not Path(spec).exists() else ("", Path(spec))


def team_sources(team_dir: Path, skip: str | None = None) -> list[tuple[str, Path]]:
    """(person, log folder) for every person's folder in a team folder, except the one named skip."""
    return [(p.name, p) for p in sorted(team_dir.iterdir())
            if p.is_dir() and not p.name.startswith(".") and p.name != skip]


def story_from_text(text: str | None, number_to_key: dict[str, str], key_re: re.Pattern | None = None) -> str | None:
    if not text:
        return None
    match = key_re.search(text) if key_re else None
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    match = STORY_NUMBER_RE.search(text)
    if match and re.search(r"story|review|implement|spec|hunter|gap|context|epic", text, re.I):
        return number_to_key.get(f"{match.group(1)}.{match.group(2)}")
    return None


SPAWN_TOOLS = ("Agent", "Workflow")  # tools that start subagents


def phase_from_description(text: str | None) -> str | None:
    lowered = (text or "").lower()
    return next((phase for pattern, phase in DESCRIPTION_PHASE if re.search(pattern, lowered)), None)


def step_of(description: str | None, agent_type: str | None) -> str:
    if description is None:
        return "main session"
    d = re.sub(r"^[a-z][a-z0-9]*-\d+\s*·\s*", "", description.lower())
    if agent_type == "Explore" or d.startswith(("explore", "find ")):
        return "explore"
    if "review" in d or "hunter" in d or "verification gap" in d:
        return "review"
    if d.startswith("build") or "bmad-build" in d:
        return "build (orchestrating)"
    if "implement" in d or "review fixes" in d or d.startswith("fix"):
        return "implement"
    if d.startswith(("test", "check", "qa ")) or "tests" in d:
        return "testing"
    if "context" in d:
        return "context"
    return "planning agent"


def _file_activity(path: str) -> str:
    if TEST_FILE_RE.search(path):
        return "writing tests"
    if DOC_FILE_RE.search(path):
        return "writing docs/specs"
    return "writing code"


def activity_of(content: list) -> str:
    tools = [c for c in content if c.get("type") == "tool_use"]
    if not tools:
        return "thinking/reporting"
    name, inp = tools[0].get("name", ""), tools[0].get("input") or {}
    if name in ("Edit", "Write", "NotebookEdit"):
        return _file_activity(str(inp.get("file_path") or inp.get("notebook_path") or ""))
    if name in ("Agent", "SendMessage", "Skill", "Workflow"):
        return "delegating"
    if name.startswith("mcp__"):
        return "MCP tools"
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        for pattern in WRITE_TARGET_RES:
            for target in pattern.findall(cmd):
                if not SCRATCH_RE.search(target) and "." in os.path.basename(target):
                    return _file_activity(target)
        if TEST_RE.search(cmd):
            return "running tests & checks"
        if GIT_RE.search(cmd):
            return "git/PR/Jira"
    return "reading/shell"


def _parse_time(stamp: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _weigh(usage: dict) -> dict:
    write_1h = usage.get("ephemeral_1h_input_tokens", 0)
    parts = {
        "input": usage.get("input_tokens", 0), "cache_read": usage.get("cache_read_input_tokens", 0),
        "cache_write_5m": max(usage.get("cache_creation_input_tokens", 0) - write_1h, 0),
        "cache_write_1h": write_1h, "output": usage.get("output_tokens", 0),
    }
    parts["units"] = sum(parts[k] * WEIGHTS[k] for k in WEIGHTS)
    return parts


def collect(log_dirs: Path | list[tuple[str, Path]], stories: dict[str, tuple[str, str]], since: str | None = None,
            key: str | None = None) -> list[dict]:
    """Every API call once, attributed to a work item, step, activity and person.

    log_dirs is one log folder, or (person, log folder) pairs: the first folder holding a call names its person."""
    if isinstance(log_dirs, (str, Path)):
        log_dirs = [("", Path(log_dirs))]
    key_re = key_pattern(key, stories)
    number_to_key = {number: key for key, (number, _) in stories.items() if number}
    metas: dict[str, dict] = {}
    spawn_context: dict[str, tuple[str | None, str | None]] = {}
    launches: dict[str, dict] = {}  # Agent or Workflow tool_use id -> the tool and who called it
    calls: dict[str, dict] = {}
    ordered: list[tuple[str, str]] = []
    for person, log_dir in log_dirs:
        for path in glob.glob(str(log_dir / "*" / "subagents" / "*.meta.json")):
            with open(path) as fh:
                metas[os.path.basename(path)[len("agent-"):-len(".meta.json")]] = json.load(fh)
        # Parents first (sessions, then agents by depth) so an agent inherits the story or phase that
        # was current in its parent when it was started.
        main_logs = sorted(glob.glob(str(log_dir / "*.jsonl")))
        sub_logs = sorted(glob.glob(str(log_dir / "*" / "subagents" / "*.jsonl")),
                          key=lambda p: metas.get(os.path.basename(p)[6:-6], {}).get("spawnDepth") or 9)
        ordered += [(person, path) for path in main_logs + sub_logs]

    for person, path in ordered:
        agent_id = os.path.basename(path)[6:-6] if f"{os.sep}subagents{os.sep}" in path else None
        meta = metas.get(agent_id, {}) if agent_id else {}
        description = meta.get("description") if agent_id else None
        step = step_of(description, meta.get("agentType"))
        agent_story, agent_phase = None, None
        agent_info = None
        if agent_id:
            launch = launches.get(meta.get("toolUseId"), {})
            agent_info = {"session": Path(path).parent.parent.name, "parent": launch.get("by", ""),
                          "launched_by": launch.get("tool", "unknown"), "run": meta.get("toolUseId") or "",
                          "agent_type": meta.get("agentType") or "", "depth": meta.get("spawnDepth") or "",
                          "description": description or "", "person": person}
            inherited_story, inherited_phase = spawn_context.get(meta.get("toolUseId"), (None, None))
            agent_story = story_from_text(description, number_to_key, key_re) or inherited_story
            agent_phase = phase_from_description(description) or inherited_phase
        skill_phase, skill_branch, skill_at = None, None, None

        with open(path) as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a session still being written can end mid-line
                kind, stamp, branch = record.get("type"), record.get("timestamp"), record.get("gitBranch")
                if kind == "user" and not agent_id:
                    content = (record.get("message") or {}).get("content")
                    text = content if isinstance(content, str) else json.dumps(content)[:4000]
                    for command in COMMAND_RE.findall(text):
                        phase = SKILL_PHASE.get(command.split(":")[-1])
                        if phase:
                            skill_phase, skill_branch, skill_at = phase, branch, _parse_time(stamp)
                    continue
                if kind != "assistant":
                    continue
                message = record.get("message") or {}
                content = [c for c in message.get("content") or [] if isinstance(c, dict)]
                if not agent_id:
                    for c in content:
                        if c.get("type") == "tool_use" and c.get("name") == "Skill":
                            phase = SKILL_PHASE.get(str((c.get("input") or {}).get("skill", "")).split(":")[-1])
                            if phase:
                                skill_phase, skill_branch, skill_at = phase, branch, _parse_time(stamp)
                    touched = json.dumps([[c.get("name"), c.get("input")] for c in content if c.get("type") == "tool_use"])
                    for pattern, artifact_phase in ARTIFACT_PHASE:
                        # After planning, Jira and sprint-status touches are story upkeep, not planning.
                        if artifact_phase == "epics & stories" and branch not in (None, "HEAD"):
                            continue
                        if touched != "[]" and re.search(pattern, touched):
                            skill_phase, skill_branch, skill_at = artifact_phase, branch, _parse_time(stamp)
                            break

                # A phase holds through the planning session (branch HEAD); on a real branch it lapses
                # when the branch changes or after SKILL_EXPIRY.
                now = _parse_time(stamp)
                if skill_phase and branch not in (None, "HEAD") and (
                    branch != skill_branch or (now and skill_at and now - skill_at > SKILL_EXPIRY)
                ):
                    skill_phase = None
                branch_story = story_from_text(branch, number_to_key, key_re) if branch not in (None, "HEAD", "dev", "main", "master") else None
                story = agent_story or branch_story
                phase = agent_phase or skill_phase
                for c in content:
                    if c.get("type") == "tool_use" and c.get("name") in SPAWN_TOOLS:
                        spawn_context[c["id"]] = (story, phase)
                        launches[c["id"]] = {"tool": c["name"], "by": f"agent-{agent_id}" if agent_id else "main session"}

                usage, msg_id = message.get("usage"), message.get("id")
                if not usage or not msg_id or (since and (stamp or "") < since):
                    continue
                if story:
                    item, item_kind = story, "story"
                elif phase:
                    item, item_kind = phase, "planning"
                elif branch in ("dev", "main"):
                    item, item_kind = "merges, promotions & deploys", "ops"
                else:
                    item, item_kind = "other", "other"
                # One call can span several log lines (one per content block); keep the largest usage.
                entry = calls.setdefault(msg_id, {"usage": {}, "content": [], "model": message.get("model") or "",
                                                  "item": item, "kind": item_kind, "step": step,
                                                  "day": (stamp or "")[:10], "stamp": stamp or "",
                                                  "agent": agent_id or "", "agent_info": agent_info,
                                                  "person": person})
                for key, value in usage.items():
                    if isinstance(value, int):
                        entry["usage"][key] = max(entry["usage"].get(key, 0), value)
                cache = usage.get("cache_creation") or {}
                for key in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"):
                    entry["usage"][key] = max(entry["usage"].get(key, 0), cache.get(key, 0))
                entry["content"].extend(content)

    result = []
    for entry in calls.values():
        parts = _weigh(entry["usage"])
        result.append({"kind": entry["kind"], "item": entry["item"], "step": entry["step"], "day": entry["day"],
                       "stamp": entry.get("stamp", ""), "agent": entry["agent"], "agent_info": entry["agent_info"],
                       "model": entry["model"], "activity": activity_of(entry["content"]), "person": entry["person"],
                       **parts})
    return result


def _item_order(kind: str, item: str, stories: dict[str, tuple[str, str]]) -> tuple:
    if kind == "story":
        number = stories.get(item, ("", ""))[0]
        major, _, minor = number.partition(".")
        return (0, int(major) if major.isdigit() else 99, int(minor) if minor.isdigit() else 99, item)
    return ({"planning": 1, "ops": 2}.get(kind, 3), 0, 0, item)


def _story_name(item: str, stories: dict[str, tuple[str, str]]) -> str:
    number, title = stories.get(item, ("", ""))
    if number:
        return f"Story {number} {title} ({item})"
    return f"{item} {title}".strip()


def _sum(calls: list[dict], key_fn) -> dict:
    totals: dict = collections.defaultdict(collections.Counter)
    for call in calls:
        counter = totals[key_fn(call)]
        counter.update({k: call[k] for k in ("units", "input", "cache_read", "cache_write_5m", "cache_write_1h", "output")})
        counter["calls"] += 1
    return totals


def _write_atomic(path: Path, write) -> None:
    """Write through a temp file and rename, so a reader or a concurrent run never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            write(fh)
        os.chmod(tmp, 0o644)  # mkstemp creates 0600; these are ordinary readable reports
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def detail_rows(calls: list[dict], stories: dict[str, tuple[str, str]]) -> list[list]:
    grand = sum(c["units"] for c in calls) or 1
    per_item = _sum(calls, lambda c: (c["kind"], c["item"]))
    grouped = _sum(calls, lambda c: (c["kind"], c["item"], c["day"], c["step"], c["activity"], c["model"], c["person"]))
    rows = [["kind", "work_item", "story_no", "title", "day", "step", "activity", "category", "model", "calls", "cost_units",
             "pct_of_item", "pct_of_total", "output_tokens", "cache_read_tokens", "cache_write_tokens",
             "fresh_input_tokens", "person"]]
    ordered = sorted(grouped.items(), key=lambda kv: (_item_order(kv[0][0], kv[0][1], stories), kv[0][2], -kv[1]["units"]))
    for (kind, item, day, step, activity, model, person), v in ordered:
        number, title = stories.get(item, ("", ""))
        rows.append([kind, item, number, title, day, step, activity, SUMMARY_ACTIVITY.get(activity, AI_REASONING), model,
                     v["calls"], round(v["units"]),
                     f"{v['units'] / (per_item[(kind, item)]['units'] or 1):.1%}", f"{v['units'] / grand:.2%}",
                     v["output"], v["cache_read"], v["cache_write_5m"] + v["cache_write_1h"], v["input"], person])
    return rows


def usage_rows(calls: list[dict], stories: dict[str, tuple[str, str]]) -> list[list]:
    """Tokens by type per work item and model, with the first and last call: for pricing and timelines."""
    totals: dict = collections.defaultdict(collections.Counter)
    times: dict = {}
    for c in calls:
        key = (c["kind"], c["item"], c["model"], c["person"])
        totals[key]["calls"] += 1
        for k in ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output", "units"):
            totals[key][k] += c[k]
        if c.get("stamp"):
            first, last = times.get(key, (c["stamp"], c["stamp"]))
            times[key] = (min(first, c["stamp"]), max(last, c["stamp"]))
    rows = [["kind", "work_item", "story_no", "title", "model", "first_call", "last_call", "calls", "input_tokens",
             "cache_write_5m_tokens", "cache_write_1h_tokens", "cache_read_tokens", "output_tokens", "cost_units",
             "person"]]
    for key in sorted(totals, key=lambda k: (_item_order(k[0], k[1], stories), k[2], k[3])):
        kind, item, model, person = key
        g, (first, last) = totals[key], times.get(key, ("", ""))
        number, title = stories.get(item, ("", ""))
        rows.append([kind, item, number, title, model, first, last, g["calls"], g["input"], g["cache_write_5m"],
                     g["cache_write_1h"], g["cache_read"], g["output"], round(g["units"]), person])
    return rows


def _pricing_id(model: str) -> str:
    """A model id as the logs write it -> the pricing page's id: 'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'."""
    return re.sub(r"-\d{8}$", "", re.sub(r"\[.*?\]", "", model.strip().lower()))


def load_prices(path: Path) -> dict:
    """Claude list prices per model (USD per million tokens) as claude_prices.py --save wrote them; {} without."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("models", {})
    except (OSError, json.JSONDecodeError):
        return {}


def daily_rows(calls: list[dict], stories: dict[str, tuple[str, str]], prices: dict) -> list[list]:
    """Tokens and cost per day, work item and model: the day-to-day series a dashboard charts.

    cost_usd is at Claude API list prices when claude-prices.json exists, else blank."""
    totals: dict = collections.defaultdict(collections.Counter)
    for c in calls:
        key = (c["day"], c["kind"], c["item"], c["model"], c["person"])
        totals[key]["calls"] += 1
        for k in ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output", "units"):
            totals[key][k] += c[k]
    rows = [["day", "kind", "work_item", "story_no", "title", "model", "calls", "input_tokens", "cache_write_5m_tokens",
             "cache_write_1h_tokens", "cache_read_tokens", "output_tokens", "cost_units", "cost_usd", "person"]]
    for key in sorted(totals, key=lambda k: (k[0], _item_order(k[1], k[2], stories), k[3], k[4])):
        day, kind, item, model, person = key
        g = totals[key]
        number, title = stories.get(item, ("", ""))
        price = prices.get(_pricing_id(model))
        usd = "" if not price else round(sum(g[k] * price[k] for k in
                                             ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")) / 1e6, 4)
        rows.append([day, kind, item, number, title, model, g["calls"], g["input"], g["cache_write_5m"],
                     g["cache_write_1h"], g["cache_read"], g["output"], round(g["units"]), usd, person])
    return rows


def agent_runs(calls: list[dict], prices: dict) -> list[dict]:
    """One entry per subagent: who started it and how, its work item, span, calls and cost."""
    by_agent: dict[str, list[dict]] = collections.defaultdict(list)
    for c in calls:
        if c.get("agent"):
            by_agent[c["agent"]].append(c)
    runs = []
    for agent_id, mine in by_agent.items():
        units_by_item = collections.Counter()
        for c in mine:
            units_by_item[(c["kind"], c["item"])] += c["units"]
        stamps = sorted(c["stamp"] for c in mine if c["stamp"])
        usd = [call_usd(c, prices) for c in mine]
        runs.append({**mine[0]["agent_info"], "agent_id": agent_id, "step": mine[0]["step"],
                     "kind_item": units_by_item.most_common(1)[0][0],  # an agent can cross a branch; take its main item
                     "models": ";".join(sorted({c["model"] for c in mine})),
                     "first_call": stamps[0] if stamps else "", "last_call": stamps[-1] if stamps else "",
                     "calls": len(mine), "units": sum(c["units"] for c in mine),
                     "usd": None if not prices or None in usd else sum(usd)})
    for run in runs:  # other agents of the same session running at the same time
        run["parallel_with"] = sum(1 for other in runs if other is not run and other["session"] == run["session"]
                                   and run["first_call"] and other["first_call"]
                                   and other["first_call"] <= run["last_call"] and run["first_call"] <= other["last_call"])
    return runs


def _minutes(first: str, last: str) -> float | str:
    start, end = _parse_time(first), _parse_time(last)
    return round((end - start).total_seconds() / 60, 1) if start and end else ""


def agent_run_rows(calls: list[dict], stories: dict[str, tuple[str, str]], prices: dict) -> list[list]:
    """agent_runs.csv: one row per subagent, so orchestration (who started whom, with Agent or Workflow,
    how many at once) can be read per story or phase. Calls in the main session are not agents."""
    rows = [["kind", "work_item", "story_no", "title", "session", "agent_id", "parent", "launched_by", "run_id",
             "agent_type", "description", "step", "depth", "models", "first_call", "last_call", "minutes",
             "parallel_with", "calls", "cost_units", "cost_usd", "person"]]
    runs = agent_runs(calls, prices)
    for r in sorted(runs, key=lambda r: (_item_order(*r["kind_item"], stories), r["first_call"], r["agent_id"])):
        kind, item = r["kind_item"]
        number, title = stories.get(item, ("", ""))
        rows.append([kind, item, number, title, r["session"], r["agent_id"], r["parent"], r["launched_by"], r["run"],
                     r["agent_type"], r["description"], r["step"], r["depth"], r["models"], r["first_call"],
                     r["last_call"], _minutes(r["first_call"], r["last_call"]), r["parallel_with"], r["calls"],
                     round(r["units"]), "" if r["usd"] is None else round(r["usd"], 4), r["person"]])
    return rows


def _peak_parallel(runs: list[dict]) -> int:
    """The most agents running at once (a sweep over their first-to-last-call spans)."""
    events = sorted([(r["first_call"], 0) for r in runs if r["first_call"]] +
                    [(r["last_call"], 1) for r in runs if r["first_call"]])  # starts sort before ends at a tie
    peak = now = 0
    for _, is_end in events:
        now += -1 if is_end else 1
        peak = max(peak, now)
    return peak


def load_budgets(path: Path) -> dict:
    """Claude API budgets in USD: {"phases": {phase: usd}, "epics": {"1": usd}}; {} without the file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    budgets = {"phases": {str(k).strip().lower(): float(v) for k, v in (data.get("phases") or {}).items()},
               "epics": {str(k).strip(): float(v) for k, v in (data.get("epics") or {}).items()}}
    return budgets if budgets["phases"] or budgets["epics"] else {}


def call_usd(call: dict, prices: dict) -> float | None:
    """One call's cost at Claude API list prices; None when its model has no saved price."""
    price = prices.get(_pricing_id(call["model"]))
    if not price:
        return None
    return sum(call[k] * price[k] for k in ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")) / 1e6


def _epic_of(item: str, stories: dict[str, tuple[str, str]]) -> str:
    number = stories.get(item, ("", ""))[0]
    return number.partition(".")[0] if number else ""


def budget_rows(calls: list[dict], stories: dict[str, tuple[str, str]], prices: dict, budgets: dict) -> list[dict]:
    """Budget against actual (USD) for each budgeted phase and epic, then the total: for the final summary and report."""
    if not budgets or not prices:
        return []
    actual: collections.Counter = collections.Counter()
    for c in calls:
        usd = call_usd(c, prices) or 0.0
        actual["total"] += usd
        if c["kind"] == "planning":
            actual[("phase", c["item"].lower())] += usd
        elif c["kind"] == "story" and _epic_of(c["item"], stories):
            actual[("epic", _epic_of(c["item"], stories))] += usd
    rows = []
    for phase, budget in budgets.get("phases", {}).items():
        rows.append({"row": f"Planning: {phase}", "budget_usd": budget, "actual_usd": actual[("phase", phase)]})
    for epic, budget in sorted(budgets.get("epics", {}).items(), key=lambda kv: (not kv[0].isdigit(), int(kv[0]) if kv[0].isdigit() else 0, kv[0])):
        rows.append({"row": f"Epic {epic}", "budget_usd": budget, "actual_usd": actual[("epic", epic)]})
    rows.append({"row": "Total", "budget_usd": sum(r["budget_usd"] for r in rows), "actual_usd": actual["total"]})
    for r in rows:
        r["variance_usd"] = r["actual_usd"] - r["budget_usd"]
        r["variance_pct"] = r["actual_usd"] / r["budget_usd"] - 1 if r["budget_usd"] else None
    return rows


def summary_rows(calls: list[dict], stories: dict[str, tuple[str, str]], combine_stories: bool,
                 savings: dict[str, str] | None = None, prices: dict | None = None,
                 budgets: dict | None = None) -> list[list]:
    grand = sum(c["units"] for c in calls) or 1
    by_item: dict = collections.defaultdict(collections.Counter)
    usd_by_item: collections.Counter = collections.Counter()
    for call in calls:
        counter = by_item[(call["kind"], call["item"])]
        counter["total"] += call["units"]
        counter["calls"] += 1
        counter[SUMMARY_ACTIVITY.get(call["activity"], AI_REASONING)] += call["units"]
        if prices:
            usd_by_item[(call["kind"], call["item"])] += call_usd(call, prices) or 0.0

    def share(units: float) -> str:
        return f"{units / grand:.2%}"

    rows = [["phase", "cost_units", "total_share", "description"]]
    if combine_stories:
        rows[0].append("suggested_cost_saving")
    for phase in PHASE_ORDER + sorted(k[1] for k in by_item if k[0] == "planning" and k[1] not in PHASE_ORDER):
        v = by_item.get(("planning", phase))
        if v:
            rows.append([f"Planning: {phase}", round(v["total"]), share(v["total"]), PHASE_NOTES.get(phase, "")])
    story_keys = sorted((k for k in by_item if k[0] == "story"), key=lambda k: _item_order(k[0], k[1], stories))
    groups = []
    if combine_stories and story_keys:
        combined: collections.Counter = collections.Counter()
        for key in story_keys:
            combined.update(by_item[key])
        numbers = [stories.get(k[1], ("", ""))[0] for k in story_keys if stories.get(k[1], ("", ""))[0]]
        span = f" ({numbers[0]} to {numbers[-1]})" if numbers else ""
        groups.append((f"All stories{span}", combined, f"{len(story_keys)} stories combined, {combined['calls']} API calls"))
    else:
        for key in story_keys:
            v = by_item[key]
            groups.append((_story_name(key[1], stories), v, f"Story total, {v['calls']} API calls"))
    for name, v, note in groups:
        rows.append([name, round(v["total"]), share(v["total"]), note])
        for activity, activity_note in ACTIVITY_NOTES.items():
            rows.append([f"{name} · {activity}", round(v[activity]), share(v[activity]), activity_note])
    if combine_stories:  # each epic's stories, so the epic budgets have a row to sit on
        epics: dict = collections.defaultdict(list)
        for key in story_keys:
            if _epic_of(key[1], stories):
                epics[_epic_of(key[1], stories)].append(key)
        for epic in sorted(epics, key=lambda e: (not e.isdigit(), int(e) if e.isdigit() else 0, e)):
            v = sum((by_item[k] for k in epics[epic]), collections.Counter())
            rows.append([f"Epic {epic}", round(v["total"]), share(v["total"]),
                         f"Epic total, part of All stories: {len(epics[epic])} stories, {v['calls']} API calls"])
    for key, label in ((("ops", "merges, promotions & deploys"), "Merges, promotions & deploys"), (("other", "other"), "Other")):
        v = by_item.get(key)
        if v:
            rows.append([label, round(v["total"]), share(v["total"]), PHASE_NOTES[key[1]]])
    rows.append(["Total", round(grand), "100.00%",
                 "Cost units weight cache reads 0.1, cache writes 1.25-2, output 5 against fresh input (not dollars)"])
    if combine_stories:
        # Claude API cost in USD (list prices), and the budget and variance where one was set
        rows[0][4:4] = ["actual_usd", "budget_usd", "variance_usd", "variance_pct"]  # before suggested_cost_saving
        budget = {b["row"]: b for b in budget_rows(calls, stories, prices or {}, budgets or {})}
        for row in rows[1:]:
            label = row[0]
            if not prices:
                usd = None
            elif label == "Total":
                usd = sum(usd_by_item.values())
            elif label.startswith("Planning: "):
                usd = usd_by_item[("planning", label[len("Planning: "):])]
            elif label.startswith("Epic "):
                usd = sum(u for (kind, item), u in usd_by_item.items()
                          if kind == "story" and _epic_of(item, stories) == label[len("Epic "):])
            elif label.startswith("All stories") and " · " not in label:
                usd = sum(u for (kind, _), u in usd_by_item.items() if kind == "story")
            elif label == "Merges, promotions & deploys":
                usd = usd_by_item[("ops", "merges, promotions & deploys")]
            elif label == "Other":
                usd = usd_by_item[("other", "other")]
            else:
                usd = None  # activity rows: cost units only
            b = budget.get("Planning: " + label[len("Planning: "):].lower() if label.startswith("Planning: ") else label)
            row += ["" if usd is None else round(usd, 2), "" if not b else round(b["budget_usd"], 2),
                    "" if not b else round(b["variance_usd"], 2),
                    "" if not b or b["variance_pct"] is None else f"{b['variance_pct']:+.1%}"]
        seen = {r[0] if not r[0].startswith("Planning: ") else "Planning: " + r[0][10:].lower() for r in rows[1:]}
        for b in budget.values():  # a budgeted phase or epic with no spend yet still shows
            if b["row"] not in seen:
                rows.insert(len(rows) - 1, [b["row"], 0, share(0), "Budgeted, no Claude usage yet", 0.0,
                                            round(b["budget_usd"], 2), round(b["variance_usd"], 2),
                                            f"{b['variance_pct']:+.1%}" if b["variance_pct"] is not None else ""])
        for row in rows[1:]:
            label = row[0]
            if label.startswith("Planning: "):
                key = label[len("Planning: "):]
            elif label.startswith("All stories · "):
                key = label[len("All stories · "):]
            elif label.startswith("All stories"):
                key = "all stories"
            elif label.startswith("Epic "):
                key = "epic"
            else:
                key = label
            row.append({**SAVINGS, **(savings or {})}.get(key, ""))
    return rows


def report_markdown(calls: list[dict], stories: dict[str, tuple[str, str]], source: str,
                    budget: list[dict] | None = None) -> str:
    grand = sum(c["units"] for c in calls) or 1

    def fmt(n: float) -> str:
        return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.0f}k"

    def label(key) -> str:
        if isinstance(key, tuple):
            kind, item, *rest = key
            return " · ".join([_story_name(item, stories) if kind == "story" else item, *rest])
        return str(key)

    def table(title: str, heading: str, key_fn) -> list[str]:
        rows = sorted(_sum(calls, key_fn).items(), key=lambda kv: -kv[1]["units"])
        out = [f"## {title}", "", f"| {heading} | cost units | share | calls | output | cache read | cache write |",
               "|---|---|---|---|---|---|---|"]
        for key, v in rows:
            out.append(f"| {label(key)} | {fmt(v['units'])} | {v['units'] / grand:.1%} | {v['calls']} | "
                       f"{fmt(v['output'])} | {fmt(v['cache_read'])} | {fmt(v['cache_write_5m'] + v['cache_write_1h'])} |")
        return out + [""]

    lines = ["# Token usage", "", f"Source: {source} ({len(calls)} API calls).",
             "Cost units = input×1 + cache read×0.1 + cache write×1.25 (5 min) or ×2 (1 h) + output×5; relative, not dollars.", ""]
    if budget:
        lines += ["## Claude API budget against actual (USD, Claude API list prices)", "",
                  "| phase or epic | budget | actual | variance | variance % |", "|---|---|---|---|---|"]
        for b in budget:
            pct = "" if b["variance_pct"] is None else f"{b['variance_pct']:+.1%}"
            lines.append(f"| {b['row']} | {b['budget_usd']:,.2f} | {b['actual_usd']:,.2f} | {b['variance_usd']:+,.2f} | {pct} |")
        lines += ["", "The total's actual counts all Claude usage, budgeted or not.", ""]
    lines += table("By kind of work", "kind", lambda c: c["kind"])
    lines += table("By work item (story or planning phase)", "work item", lambda c: (c["kind"], c["item"]))
    lines += table("By step", "step", lambda c: c["step"])
    lines += table("By activity", "activity", lambda c: c["activity"])
    lines += table("By work item and step", "work item · step", lambda c: (c["kind"], c["item"], c["step"]))
    lines += table("By work item and activity", "work item · activity", lambda c: (c["kind"], c["item"], c["activity"]))
    lines += table("By model", "model", lambda c: c["model"])
    if len({c["person"] for c in calls}) > 1:
        lines += table("By person", "person", lambda c: c["person"] or "unnamed")
        lines += table("By work item and person", "work item · person", lambda c: (c["kind"], c["item"], c["person"] or "unnamed"))
    runs = agent_runs(calls, {})
    if runs:
        lines += ["## Agents and orchestration", "",
                  "Subagents per work item (one row each in agent_runs.csv). Peak = the most running at once; "
                  "share = the item's cost units spent inside subagents rather than the main session.", "",
                  "| work item | agents | Agent | Workflow | runs | peak at once | deepest | share in agents |",
                  "|---|---|---|---|---|---|---|---|"]
        item_units = _sum(calls, lambda c: (c["kind"], c["item"]))
        by_item: dict = collections.defaultdict(list)
        for r in runs:
            by_item[r["kind_item"]].append(r)
        for key in sorted(by_item, key=lambda k: _item_order(*k, stories)):
            mine = by_item[key]
            share = sum(r["units"] for r in mine) / (item_units[key]["units"] or 1)
            launched = collections.Counter(r["launched_by"] for r in mine)
            depths = [r["depth"] for r in mine if isinstance(r["depth"], int)]
            lines.append(f"| {label(key)} | {len(mine)} | {launched['Agent']} | {launched['Workflow']} | "
                         f"{len({r['run'] for r in mine})} | {_peak_parallel(mine)} | {max(depths, default='')} | {share:.0%} |")
        lines.append("")
    return "\n".join(lines)


def story_summary(calls: list[dict], key: str, stories: dict[str, tuple[str, str]]) -> dict:
    """One story's cost by step and activity, plus a one-line summary for its PR and Jira comment."""
    mine = [c for c in calls if c["kind"] == "story" and c["item"] == key]
    grand = sum(c["units"] for c in calls) or 1
    total = sum(c["units"] for c in mine)
    steps = _sum(mine, lambda c: c["step"])
    activities = _sum(mine, lambda c: SUMMARY_ACTIVITY.get(c["activity"], AI_REASONING))

    def pct(units: float) -> float:
        return round(100 * units / total, 1) if total else 0.0

    step_pct = {s: pct(v["units"]) for s, v in sorted(steps.items(), key=lambda kv: -kv[1]["units"])}
    activity_pct = {a: pct(activities[a]["units"]) for a in ACTIVITY_NOTES}
    number = stories.get(key, ("", ""))[0]
    top_steps = ", ".join(f"{s} {p:.0f}%" for s, p in list(step_pct.items())[:3])
    code_and_tests = activity_pct[WRITING_CODE] + activity_pct[WRITING_TESTS]
    line = (f"{key}{f' ({number})' if number else ''}: {total / 1e6:.1f}M cost units ({100 * total / grand:.1f}% of the "
            f"project so far), {len(mine)} API calls. Steps: {top_steps}. Writing code and tests {code_and_tests:.0f}%, "
            f"running tests and checks {activity_pct[RUNNING_TESTS]:.0f}%, AI comprehension {activity_pct[AI_COMPREHENSION]:.0f}%.")
    return {"key": key, "story_no": number, "title": stories.get(key, ("", ""))[1], "cost_units": round(total),
            "calls": len(mine), "share_of_all_pct": round(100 * total / grand, 2), "steps_pct": step_pct,
            "activities_pct": activity_pct, "line": line}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", nargs="?", default=".", help="project root or any path inside it (default: .)")
    parser.add_argument("--logs", action="append", metavar="[NAME=]FOLDER",
                        help="a Claude Code log folder, optionally named for the person column; repeat for several "
                             "(default: this project's log folder, named after your git user.name)")
    parser.add_argument("--team", metavar="FOLDER", help="also read every person's folder in this team logs folder "
                        "(default: the org config's team_logs_folder, if set)")
    parser.add_argument("--export", nargs="?", const="", metavar="FOLDER",
                        help="copy your log folder to FOLDER/<your name>/ and stop (default FOLDER: team_logs_folder)")
    parser.add_argument("--as", dest="name", help="your name for the person column and --export (default: git user.name)")
    parser.add_argument("-o", "--out-dir", help="output folder (default: org config token_usage_folder, else docs/costing/token_usage)")
    parser.add_argument("--since", help="count only calls on or after this date (YYYY-MM-DD)")
    parser.add_argument("--story", help="also print this story's summary (e.g. PROJ-24)")
    parser.add_argument("--key", help="Jira project key (default: org config jira_project_key, else the story map's)")
    parser.add_argument("--stories", help=f"story map JSON (default: <project>/{DATA_DIR.as_posix()}/stories.json)")
    parser.add_argument("--savings", help=f"savings JSON (default: <project>/{DATA_DIR.as_posix()}/savings.json)")
    parser.add_argument("--verbose", action="store_true", help="progress on stderr")
    args = parser.parse_args(argv)

    root = project_root(Path(args.project))
    config = org_config(root)
    folder = str(config.get("token_usage_folder") or "docs/costing/token_usage").replace("{project-root}", "").lstrip("/\\")
    out_dir = Path(args.out_dir) if args.out_dir else root / folder

    def configured(path: str | None) -> Path | None:
        path = str(path or "").replace("{project-root}", str(root)).strip()
        return root / Path(path).expanduser() if path else None  # an absolute path replaces root

    me = args.name or whoami(root)
    own_logs = log_dir_for(root)
    team_dir = configured(args.team) or configured(config.get("team_logs_folder"))
    if args.export is not None:
        export_dir = configured(args.export) or team_dir
        if not export_dir:
            print(json.dumps({"error": "no folder to export to: pass --export FOLDER or set team_logs_folder"}))
            return 2
        source = parse_logs_arg(args.logs[0])[1] if args.logs else own_logs
        if not source.is_dir():
            print(json.dumps({"error": f"no Claude Code log folder at {source}"}))
            return 2
        try:
            print(json.dumps({**export_logs(source, export_dir, me), "person": me}, indent=2))
        except OSError as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
            return 2
        return 0

    if args.logs:
        sources = [(name or (me if len(args.logs) == 1 else path.name), path) for name, path in map(parse_logs_arg, args.logs)]
    else:
        sources = [(me, own_logs)] if own_logs.is_dir() or not team_dir else []
    if team_dir:
        if not team_dir.is_dir():
            print(json.dumps({"error": f"no team logs folder at {team_dir}"}))
            return 2
        # Your own export is a copy of logs already read: skip it (a stale one would add nothing anyway).
        sources += team_sources(team_dir, skip=folder_name(me) if sources else None)
    missing = [str(path) for _, path in sources if not path.is_dir()]
    if missing or not sources:
        print(json.dumps({"error": f"no Claude Code log folder at {', '.join(missing) or own_logs}"}))
        return 2
    log_source = ", ".join(str(path) for _, path in sources)
    try:
        stories = load_stories(Path(args.stories) if args.stories else root / DATA_DIR / "stories.json")
        savings = load_savings(Path(args.savings) if args.savings else root / DATA_DIR / "savings.json")
        calls = collect(sources, stories, args.since, args.key or config.get("jira_project_key") or None)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 2
    if args.verbose:
        print(f"{len(calls)} calls from {log_source}", file=sys.stderr)
    prices = load_prices(root / DATA_DIR / "claude-prices.json")
    budgets = load_budgets(root / DATA_DIR / "token-budgets.json")

    outputs = {
        "token_usage.csv": lambda fh: csv.writer(fh).writerows(detail_rows(calls, stories)),
        "token_usage_summary.csv": lambda fh: csv.writer(fh).writerows(summary_rows(calls, stories, combine_stories=False)),
        "token_usage_summary_final.csv": lambda fh: csv.writer(fh).writerows(
            summary_rows(calls, stories, combine_stories=True, savings=savings, prices=prices, budgets=budgets)),
        "report.md": lambda fh: fh.write(report_markdown(calls, stories, log_source, budget_rows(calls, stories, prices, budgets))),
        "claude_usage.csv": lambda fh: csv.writer(fh).writerows(usage_rows(calls, stories)),
        "claude_usage_daily.csv": lambda fh: csv.writer(fh).writerows(daily_rows(calls, stories, prices)),
        "agent_runs.csv": lambda fh: csv.writer(fh).writerows(agent_run_rows(calls, stories, prices)),
    }
    for name, write in outputs.items():
        _write_atomic(out_dir / name, write)

    result = {"written": [str(out_dir / name) for name in outputs], "calls": len(calls),
              "cost_units": round(sum(c["units"] for c in calls)),
              "logs": [{"person": person, "folder": str(path)} for person, path in sources]}
    people = collections.Counter()
    for c in calls:
        people[c["person"]] += c["units"]
    if len(people) > 1:
        result["people"] = {person: round(units) for person, units in people.most_common()}
    budget = budget_rows(calls, stories, prices, budgets)
    if budget:
        result["budgets"] = [{k: (round(v, 4) if k == "variance_pct" else round(v, 2)) if isinstance(v, float) else v
                              for k, v in b.items()} for b in budget]
    elif budgets and not prices:
        result["budgets"] = "budgets set but no Claude prices saved: run claude_prices.py --save to compare"
    if args.story:
        result["story"] = story_summary(calls, args.story, stories)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
