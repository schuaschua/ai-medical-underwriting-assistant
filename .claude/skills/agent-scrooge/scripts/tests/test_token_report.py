"""Tests for token_report.py against a small synthetic log folder (no real session data)."""

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "token_report.py"
spec = importlib.util.spec_from_file_location("token_report", SCRIPT)
token_report = importlib.util.module_from_spec(spec)
sys.modules["token_report"] = token_report
spec.loader.exec_module(token_report)

STORIES = {"PROJ-18": ("2.3", "Choose a product"), "PROJ-19": ("3.1", "Check the proposal")}


def _assistant(msg_id, tools=(), branch="dev", stamp="2026-09-27T01:00:00Z", output=10, read=1000, write=100):
    content = [{"type": "tool_use", "id": f"tu-{msg_id}-{i}", "name": name, "input": inp}
               for i, (name, inp) in enumerate(tools)] or [{"type": "text", "text": "thinking"}]
    return {"type": "assistant", "timestamp": stamp, "gitBranch": branch,
            "message": {"id": msg_id, "model": "claude-sonnet-5", "content": content,
                        "usage": {"input_tokens": 1, "cache_read_input_tokens": read,
                                  "cache_creation_input_tokens": write, "output_tokens": output}}}


def _write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    root = tmp_path / "logs"
    session = "s1"
    _write_jsonl(root / f"{session}.jsonl", [
        {"type": "user", "timestamp": "2026-09-25T01:00:00Z", "gitBranch": "HEAD",
         "message": {"content": "<command-name>/bmad-brainstorming</command-name>"}},
        _assistant("m1", branch="HEAD", stamp="2026-09-25T01:01:00Z"),
        _assistant("m2", [("Edit", {"file_path": "/r/_bmad-output/planning-artifacts/ux-designs/DESIGN.md"})],
                   branch="HEAD", stamp="2026-09-25T02:00:00Z"),
        _assistant("m3", [("Agent", {"description": "PROJ-18 · build"})], branch="dev"),
        _assistant("m4", [("Bash", {"command": "gh pr merge 79 --merge"})], branch="dev"),
        # The same call split over two lines: counted once, with the larger usage.
        _assistant("m4", [("Bash", {"command": "gh pr merge 79 --merge"})], branch="dev", output=20),
    ])
    agents = root / session / "subagents"
    _write_jsonl(agents / "agent-b1.jsonl", [
        _assistant("b1", [("Agent", {"description": "PROJ-18 · implement"})], branch="PROJ-18-x"),
        _assistant("b2", [("Bash", {"command": "scripts/check.sh > .work/check/x.log"})], branch="PROJ-18-x"),
    ])
    (agents / "agent-b1.meta.json").write_text(json.dumps(
        {"description": "PROJ-18 · build", "toolUseId": "tu-m3-0", "spawnDepth": 1}))
    _write_jsonl(agents / "agent-i1.jsonl", [
        _assistant("i1", [("Write", {"file_path": "/r/api/domain/products.py"})], branch="PROJ-18-x"),
        _assistant("i2", [("Bash", {"command": "cat > api/tests/test_products.py <<'EOF'\nEOF"})], branch="PROJ-18-x"),
    ])
    (agents / "agent-i1.meta.json").write_text(json.dumps(
        {"description": "PROJ-18 · implement", "toolUseId": "tu-b1-0", "spawnDepth": 2}))
    _write_jsonl(agents / "agent-r1.jsonl", [_assistant("r1", branch="worktree-x")])
    (agents / "agent-r1.meta.json").write_text(json.dumps(
        {"description": "Edge case hunter review 3.1", "toolUseId": "tu-none", "spawnDepth": 1}))
    return root



def test_attribution(logs: Path) -> None:
    calls = token_report.collect(logs, STORIES)
    assert len(calls) == 9  # m4's two lines are one call
    kinds = {(c["kind"], c["item"], c["step"], c["activity"]) for c in calls}
    assert ("planning", "brainstorming", "main session", "thinking/reporting") in kinds
    assert ("planning", "UX design", "main session", "writing docs/specs") in kinds
    assert ("ops", "merges, promotions & deploys", "main session", "git/PR/Jira") in kinds
    assert ("story", "PROJ-18", "build (orchestrating)", "running tests & checks") in kinds
    assert ("story", "PROJ-18", "implement", "writing code") in kinds
    assert ("story", "PROJ-18", "implement", "writing tests") in kinds
    # A review agent named only by story number maps to its Jira key.
    assert ("story", "PROJ-19", "review", "thinking/reporting") in kinds


def test_duplicate_lines_keep_the_largest_usage(logs: Path) -> None:
    merges = [c for c in token_report.collect(logs, STORIES) if c["item"] == "merges, promotions & deploys"]
    assert sorted(c["output"] for c in merges) == [10, 20]


def test_cost_units_weigh_token_types() -> None:
    parts = token_report._weigh({"input_tokens": 10, "cache_read_input_tokens": 100,
                                 "cache_creation_input_tokens": 8, "ephemeral_1h_input_tokens": 0,
                                 "output_tokens": 2})
    assert parts["units"] == pytest.approx(10 + 10 + 10 + 10)


@pytest.mark.parametrize(("command", "activity"), [
    ("cd api && python3 - <<'EOF'\np='adapters/settings.py'\nopen(p,'w').write('x')\nEOF", "writing code"),
    ("sed -i '' 's/a/b/' web/src/strings.ts", "writing code"),
    ("terraform providers schema -json > .work/schema.json", "reading/shell"),
    ("uv run pytest -q", "running tests & checks"),
    ("git status", "git/PR/Jira"),
])
def test_shell_activity(command: str, activity: str) -> None:
    assert token_report.activity_of([{"type": "tool_use", "name": "Bash", "input": {"command": command}}]) == activity


def test_main_writes_seven_files_and_a_story_line(logs: Path, tmp_path: Path, capsys) -> None:
    stories_file = tmp_path / "stories.json"
    stories_file.write_text(json.dumps({"stories": {k: list(v) for k, v in STORIES.items()}}))
    out = tmp_path / "out"
    prices = tmp_path / "_bmad" / "memory" / "agent-scrooge" / "claude-prices.json"
    prices.parent.mkdir(parents=True)
    prices.write_text(json.dumps({"models": {"claude-sonnet-5": {"input": 2, "cache_write_5m": 2.5, "cache_write_1h": 4,
                                                                  "cache_read": 0.2, "output": 10}}}))
    code = token_report.main([str(tmp_path), "--logs", str(logs), "-o", str(out), "--stories", str(stories_file),
                              "--story", "PROJ-18"])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert sorted(Path(p).name for p in result["written"]) == [
        "agent_runs.csv", "claude_usage.csv", "claude_usage_daily.csv", "report.md", "token_usage.csv",
        "token_usage_summary.csv", "token_usage_summary_final.csv"]
    assert result["story"]["line"].startswith("PROJ-18 (2.3): ")
    assert result["story"]["calls"] == 4  # m3, which started the build, ran on dev: ops

    summary = list(csv.reader((out / "token_usage_summary.csv").open()))
    story_rows = [r[0] for r in summary if r[0].startswith("Story 2.3")]
    assert len(story_rows) == 7  # the total plus six activity rows
    final = list(csv.reader((out / "token_usage_summary_final.csv").open()))
    assert any(r[0].startswith("All stories (2.3 to 3.1)") for r in final)
    usage = list(csv.DictReader((out / "claude_usage.csv").open()))
    assert sum(int(r["calls"]) for r in usage) == result["calls"]
    story = [r for r in usage if r["work_item"] == "PROJ-18"]
    assert story and all(r["first_call"] <= r["last_call"] for r in story)
    assert story[0]["story_no"] == "2.3" and story[0]["model"] == "claude-sonnet-5"
    daily = list(csv.DictReader((out / "claude_usage_daily.csv").open()))
    assert sum(int(r["calls"]) for r in daily) == result["calls"]
    assert daily == sorted(daily, key=lambda r: r["day"]) and daily[0]["day"] < daily[-1]["day"]
    sonnet = [r for r in daily if r["model"] == "claude-sonnet-5"]
    assert sonnet and all(float(r["cost_usd"]) > 0 for r in sonnet)
    total = int(final[-1][1])
    top_level = sum(int(r[1]) for r in final[1:-1] if " · " not in r[0] and not r[0].startswith("Epic "))
    assert abs(top_level - total) <= len(final)  # rows are rounded one by one


def test_missing_log_folder_is_an_error(tmp_path: Path, capsys) -> None:
    assert token_report.main([str(tmp_path), "--logs", str(tmp_path / "nope"), "-o", str(tmp_path / "o")]) == 2
    assert "error" in json.loads(capsys.readouterr().out)


def test_final_summary_has_a_saving_for_every_row(logs: Path) -> None:
    rows = token_report.summary_rows(token_report.collect(logs, STORIES), STORIES, combine_stories=True)
    assert rows[0][-1] == "suggested_cost_saving"
    assert all(len(r) == 9 and r[8] for r in rows[1:])
    per_story = token_report.summary_rows(token_report.collect(logs, STORIES), STORIES, combine_stories=False)
    assert all(len(r) == 4 for r in per_story)


def test_key_comes_from_the_org_config_and_data_from_the_project(logs: Path, tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    (project / "_bmad" / "memory" / "agent-scrooge").mkdir(parents=True)
    (project / "_bmad" / "config.toml").write_text(
        '[modules.org]\njira_project_key = "PROJ"\ntoken_usage_folder = "reports/tokens"\n')
    (project / "_bmad" / "memory" / "agent-scrooge" / "savings.json").write_text(
        json.dumps({"savings": {"Total": "Project-specific advice."}}))
    assert token_report.main([str(project), "--logs", str(logs), "--story", "PROJ-18"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert all(Path(p).parent == project / "reports" / "tokens" for p in result["written"])
    assert result["story"]["line"].startswith("PROJ-18")  # no story map: key from config, no number
    final = list(csv.reader((project / "reports" / "tokens" / "token_usage_summary_final.csv").open()))
    assert final[-1][-1] == "Project-specific advice."


def test_without_a_key_only_story_numbers_are_matched(logs: Path) -> None:
    assert token_report.key_pattern(None, {}) is None
    assert token_report.key_pattern(None, STORIES).pattern.startswith(r"\b(PROJ)")
    assert token_report.story_from_text("PROJ-7 · build", {}, None) is None
    assert token_report.story_from_text("PROJ-7 · build", {}, token_report.key_pattern("PROJ", {})) == "PROJ-7"


def test_final_summary_compares_budgets_with_actuals(logs: Path, tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    data = project / "_bmad" / "memory" / "agent-scrooge"
    data.mkdir(parents=True)
    (data / "stories.json").write_text(json.dumps({"stories": {k: list(v) for k, v in STORIES.items()}}))
    # prices scaled up so a synthetic call costs dollars, not fractions of a cent
    (data / "claude-prices.json").write_text(json.dumps({"models": {"claude-sonnet-5": {
        "input": 20000, "cache_write_5m": 25000, "cache_write_1h": 40000, "cache_read": 2000, "output": 100000}}}))
    (data / "token-budgets.json").write_text(json.dumps(
        {"currency": "USD", "phases": {"UX design": 10, "test design": 5}, "epics": {"2": 10, "9": 3}}))
    assert token_report.main([str(project), "--logs", str(logs)]) == 0
    result = json.loads(capsys.readouterr().out)
    budgets = {b["row"]: b for b in result["budgets"]}
    # each call: 1 input, 1000 cache read, 100 cache write (5m), 10 output at the prices above
    per_call = (1 * 20000 + 1000 * 2000 + 100 * 25000 + 10 * 100000) / 1e6  # 5.52
    assert budgets["Planning: ux design"]["actual_usd"] == pytest.approx(round(per_call, 2))
    assert budgets["Epic 2"]["actual_usd"] == pytest.approx(round(4 * per_call, 2))  # PROJ-18 (2.3): four calls
    assert budgets["Epic 2"]["variance_usd"] > 0 and budgets["Epic 9"]["actual_usd"] == 0
    assert budgets["Total"]["budget_usd"] == 28
    final = list(csv.DictReader((project / "docs" / "costing" / "token_usage" / "token_usage_summary_final.csv").open()))
    rows = {r["phase"]: r for r in final}
    assert list(final[0])[4:] == ["actual_usd", "budget_usd", "variance_usd", "variance_pct", "suggested_cost_saving"]
    assert rows["Planning: UX design"]["budget_usd"] == "10.0" and rows["Planning: UX design"]["variance_pct"].startswith("-")
    assert rows["Epic 2"]["budget_usd"] and rows["Epic 3"]["budget_usd"] == ""  # epic 3 has spend but no budget
    assert rows["Planning: test design"]["description"] == "Budgeted, no Claude usage yet"  # budgeted, not spent
    assert rows["Epic 9"]["actual_usd"] == "0.0"
    assert all(r["actual_usd"] == "" for p, r in rows.items() if " · " in p)  # activity rows: cost units only
    report = (project / "docs" / "costing" / "token_usage" / "report.md").read_text()
    assert "## Claude API budget against actual" in report and "| Epic 2 |" in report


def test_budgets_without_prices_say_so(logs: Path, tmp_path: Path, capsys) -> None:
    data = tmp_path / "_bmad" / "memory" / "agent-scrooge"
    data.mkdir(parents=True)
    (data / "token-budgets.json").write_text(json.dumps({"phases": {"test design": 5}}))
    assert token_report.main([str(tmp_path), "--logs", str(logs), "-o", str(tmp_path / "o")]) == 0
    assert "claude_prices.py --save" in json.loads(capsys.readouterr().out)["budgets"]


def test_summary_splits_each_story_into_the_six_categories(logs: Path) -> None:
    rows = token_report.summary_rows(token_report.collect(logs, STORIES), STORIES, combine_stories=False)
    story = [r for r in rows if r[0].startswith("Story 2.3 Choose a product (PROJ-18) · ")]
    assert [r[0].split(" · ")[1] for r in story] == [
        "AI comprehension", "AI reasoning", "Writing code", "Writing tests", "Running tests and checks", "MCP usage"]
    assert story[0][3].startswith("reading / shell:") and story[5][3].startswith("git / Jira / PR:")


@pytest.mark.parametrize(("tool", "category"), [
    ("mcp__atlassian__editJiraIssue", "MCP usage"),
    ("mcp__drawio__open_drawio_xml", "MCP usage"),
    ("Read", "AI comprehension"),
])
def test_every_mcp_server_call_is_mcp_usage(tool: str, category: str) -> None:
    activity = token_report.activity_of([{"type": "tool_use", "name": tool, "input": {}}])
    assert token_report.SUMMARY_ACTIVITY[activity] == category


def test_savings_under_the_old_category_names_still_apply(tmp_path: Path) -> None:
    path = tmp_path / "savings.json"
    path.write_text(json.dumps({"savings": {"reading/shell": "Mine.", "Total": "Also mine."}}))
    assert token_report.load_savings(path) == {"AI comprehension": "Mine.", "Total": "Also mine."}


def test_detail_rows_carry_the_category(logs: Path) -> None:
    rows = token_report.detail_rows(token_report.collect(logs, STORIES), STORIES)
    header = rows[0]
    assert header[header.index("activity") + 1] == "category"
    by = {(r[header.index("activity")], r[header.index("category")]) for r in rows[1:]}
    assert {("delegating", "AI reasoning"), ("git/PR/Jira", "MCP usage"), ("writing tests", "Writing tests")} <= by


def test_agent_runs_record_who_started_each_agent_and_how(logs: Path) -> None:
    # A second session runs a workflow whose two agents overlap in time.
    _write_jsonl(logs / "s2.jsonl", [
        _assistant("w0", [("Workflow", {"script": "..."})], branch="PROJ-19-y", stamp="2026-09-28T10:00:00Z")])
    agents = logs / "s2" / "subagents"
    for agent_id, first, last in (("w1", "10:01", "10:20"), ("w2", "10:05", "10:30")):
        _write_jsonl(agents / f"agent-{agent_id}.jsonl", [
            _assistant(f"{agent_id}a", branch="PROJ-19-y", stamp=f"2026-09-28T{first}:00Z"),
            _assistant(f"{agent_id}b", branch="PROJ-19-y", stamp=f"2026-09-28T{last}:00Z")])
        (agents / f"agent-{agent_id}.meta.json").write_text(json.dumps(
            {"description": f"review:{agent_id}", "toolUseId": "tu-w0-0", "spawnDepth": 1, "agentType": "general-purpose"}))
    calls = token_report.collect(logs, STORIES)
    rows = token_report.agent_run_rows(calls, STORIES, {})
    runs = {r[5]: dict(zip(rows[0], r)) for r in rows[1:]}
    assert set(runs) == {"b1", "i1", "r1", "w1", "w2"}  # main-session calls are not agents
    assert (runs["b1"]["parent"], runs["b1"]["launched_by"]) == ("main session", "Agent")
    assert (runs["i1"]["parent"], runs["i1"]["launched_by"], runs["i1"]["depth"]) == ("agent-b1", "Agent", 2)
    assert runs["r1"]["launched_by"] == "unknown"  # its launching call is not in the logs
    assert runs["w1"]["launched_by"] == runs["w2"]["launched_by"] == "Workflow"
    assert runs["w1"]["run_id"] == runs["w2"]["run_id"] == "tu-w0-0"
    assert runs["w1"]["work_item"] == "PROJ-19" and runs["w1"]["minutes"] == 19.0
    assert runs["w1"]["parallel_with"] == runs["w2"]["parallel_with"] == 1
    report = token_report.report_markdown(calls, STORIES, "logs")
    assert "| Story 3.1 Check the proposal (PROJ-19) | 3 | 0 | 2 | 2 | 2 | 1 |" in report


def _teammate_logs(root: Path) -> Path:
    """A teammate's log folder: one call of their own and one (m1) that is also in the shared fixture."""
    _write_jsonl(root / "t1.jsonl", [
        _assistant("t1", branch="PROJ-19-z", stamp="2026-09-29T09:00:00Z", output=100),
        _assistant("m1", branch="HEAD", stamp="2026-09-25T01:01:00Z")])
    return root


def test_several_people_are_counted_once_each_with_a_person_column(logs: Path, tmp_path: Path, capsys) -> None:
    mate = _teammate_logs(tmp_path / "mate-logs")
    out = tmp_path / "out"
    assert token_report.main([str(tmp_path), "--logs", f"Ada={logs}", "--logs", f"Bo={mate}", "-o", str(out),
                              "--key", "PROJ"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["calls"] == 10  # the fixture's nine plus t1; m1 appears in both folders but counts once
    assert set(result["people"]) == {"Ada", "Bo"}
    assert [s["person"] for s in result["logs"]] == ["Ada", "Bo"]
    for name in ("token_usage.csv", "claude_usage.csv", "claude_usage_daily.csv", "agent_runs.csv"):
        rows = list(csv.DictReader((out / name).open()))
        assert rows and all(r["person"] in ("Ada", "Bo") for r in rows), name
    usage = list(csv.DictReader((out / "claude_usage.csv").open()))
    assert {r["person"] for r in usage if r["work_item"] == "PROJ-18"} == {"Ada"}
    assert {r["person"] for r in usage if r["work_item"] == "PROJ-19"} == {"Bo"}
    assert {r["person"] for r in usage if r["work_item"] == "brainstorming"} == {"Ada"}  # the first folder holding m1
    report = (out / "report.md").read_text()
    assert "## By person" in report and "## By work item and person" in report


def test_one_person_gets_no_person_tables(logs: Path, tmp_path: Path, capsys) -> None:
    assert token_report.main([str(tmp_path), "--logs", str(logs), "--as", "Ada", "-o", str(tmp_path / "o")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert "people" not in result and result["logs"][0]["person"] == "Ada"
    assert "## By person" not in (tmp_path / "o" / "report.md").read_text()


def test_export_copies_only_new_or_changed_files(logs: Path, tmp_path: Path, capsys) -> None:
    team = tmp_path / "team"
    assert token_report.main([str(tmp_path), "--logs", str(logs), "--as", "Ada/Lovelace", "--export", str(team)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["copied"] == 7 and first["unchanged"] == 0  # one session, three agents and their metadata
    assert (team / "Ada-Lovelace" / "s1" / "subagents" / "agent-b1.meta.json").is_file()
    assert token_report.main([str(tmp_path), "--logs", str(logs), "--as", "Ada/Lovelace", "--export", str(team)]) == 0
    assert json.loads(capsys.readouterr().out)["copied"] == 0


def test_export_needs_a_folder(logs: Path, tmp_path: Path, capsys) -> None:
    assert token_report.main([str(tmp_path), "--logs", str(logs), "--export"]) == 2
    assert "team_logs_folder" in json.loads(capsys.readouterr().out)["error"]


def test_team_folder_from_the_personal_config_adds_everyone_but_your_own_export(logs: Path, tmp_path: Path,
                                                                                capsys) -> None:
    project, team = tmp_path / "project", tmp_path / "team"
    _teammate_logs(team / "Bo")
    (team / "Ada" / "stale.jsonl").parent.mkdir(parents=True)
    (team / "Ada" / "stale.jsonl").write_text(json.dumps(_assistant("old", stamp="2026-01-01T00:00:00Z")) + "\n")
    custom = project / "_bmad" / "custom"
    custom.mkdir(parents=True)
    (custom / "config.user.toml").write_text(f'[modules.org]\nteam_logs_folder = "{team.as_posix()}"\n')
    assert token_report.main([str(project), "--logs", str(logs), "--as", "Ada", "-o", str(tmp_path / "o")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [s["person"] for s in result["logs"]] == ["Ada", "Bo"]  # team/Ada is Ada's own export: skipped
    assert result["calls"] == 10


def test_missing_team_folder_is_an_error(logs: Path, tmp_path: Path, capsys) -> None:
    assert token_report.main([str(tmp_path), "--logs", str(logs), "--team", str(tmp_path / "nope")]) == 2
    assert "team logs folder" in json.loads(capsys.readouterr().out)["error"]
