"""Tests for project_timeline.py in a throwaway git repository."""

import csv
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "project_timeline.py"
spec = importlib.util.spec_from_file_location("project_timeline", SCRIPT)
project_timeline = importlib.util.module_from_spec(spec)
sys.modules["project_timeline"] = project_timeline
spec.loader.exec_module(project_timeline)

STATUS = "development_status:\n  epic-1: {e1}\n  1-1-login: {s11}\n  1-2-profile: {s12}\n  epic-2: {e2}\n  2-1-search: {s21}\n"


def commit(root: Path, day: str, message: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": f"{day}T10:00:00", "GIT_COMMITTER_DATE": f"{day}T10:00:00",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", message], check=True, env=env)


def write_status(root: Path, **values) -> None:
    path = root / "_bmad-output/implementation-artifacts/sprint-status.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# generated\nproject: demo\n" + STATUS.format(**values))


def test_timeline_from_git_sanctums_and_ledger(tmp_path, capsys):
    root = tmp_path
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    spine = root / "_bmad-output/planning-artifacts/architecture/demo/ARCHITECTURE-SPINE.md"
    spine.parent.mkdir(parents=True)
    spine.write_text("# Spine\n")
    (root / "_bmad-output/specs").mkdir(parents=True)
    (root / "_bmad-output/specs/SPEC.md").write_text("# Spec\n")
    commit(root, "2026-09-01", "plan")
    b = dict(e1="backlog", s11="backlog", s12="backlog", e2="backlog", s21="backlog")
    write_status(root, **{**b, "e1": "in-progress", "s11": "in-progress"})
    commit(root, "2026-09-07", "start 1-1")
    write_status(root, **{**b, "e1": "in-progress", "s11": "done", "s12": "done", "s21": "in-progress"})
    commit(root, "2026-09-10", "1-1, 1-2 done")
    write_status(root, e1="done", s11="done", s12="done", e2="in-progress", s21="done")
    commit(root, "2026-09-15", "epic 1 done, 2-1 done")

    jules = root / "_bmad/memory/agent-jules"
    (jules / "sessions").mkdir(parents=True)
    (jules / "PERSONA.md").write_text("- **Born:** 2026-08-30\n")
    (jules / "sessions/2026-09-12.md").write_text("notes")
    ledger = root / "docs/costing/token_usage/claude_usage.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("kind,work_item,model,first_call,last_call\n"
                      "planning,brainstorming,m,2026-08-28T09:00:00Z,2026-08-28T11:00:00Z\n"
                      "story,PROJ-1,m,2026-09-07T09:00:00Z,2026-09-09T11:00:00Z\n")

    assert project_timeline.main([str(root)]) == 0
    out = json.loads(capsys.readouterr().out)
    rows = {(r["category"], r["name"]): r for r in out["rows"]}
    assert rows[("bmad stage", "architecture")]["started"] == "2026-09-01"
    assert rows[("agent", "agent-jules")]["started"] == "2026-08-30"
    epic1, epic2 = rows[("epic", "epic-1")], rows[("epic", "epic-2")]
    assert (epic1["status"], epic1["started"], epic1["completed"]) == ("done", "2026-09-07", "2026-09-15")
    # epic-2 has every story done but its own line still says in-progress: it completes with its last story
    assert (epic2["status"], epic2["started"], epic2["completed"]) == ("done", "2026-09-10", "2026-09-15")
    assert rows[("story", "1-2-profile")]["completed"] == "2026-09-10"
    assert rows[("claude", "brainstorming")]["started"] == "2026-08-28"
    project = out["project"]
    assert (project["status"], project["started"], project["completed"]) == ("complete", "2026-08-28", "2026-09-15")
    assert project["calendar_days"] == 19 and project["working_days"] == 13  # Fri 28 Aug to Tue 15 Sep
    written = list(csv.DictReader((root / "docs/costing/token_usage/project_timeline.csv").open()))
    assert written[0]["category"] == "project" and written[0]["days"] == "19"


def test_running_project_without_git(tmp_path, capsys):
    write_status(tmp_path, e1="in-progress", s11="done", s12="in-progress", e2="backlog", s21="backlog")
    assert project_timeline.main([str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["project"]["status"] == "in progress" and out["project"]["file_dates"] >= 1
