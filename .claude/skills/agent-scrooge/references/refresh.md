---
name: refresh
description: Regenerate the token ledger and report what changed
code: RF
added: 2026-09-27
type: prompt
---

# Refresh the ledger

The outcome is six current files in the token usage folder (org config `token_usage_folder`, default `{project-root}/docs/costing/token_usage/`), and {user_name} knowing what moved since the last look.

Run `uv run scripts/token_report.py {project-root}` (`--help` explains every option; `--since YYYY-MM-DD` limits the count). It prints JSON with the files written, the call count and the total cost units. Read `token_usage_summary_final.csv` before and after when {user_name} asks what changed, and name the items that grew the most. When `token-budgets.json` is set, the output has a `budgets` list, and the final summary has `actual_usd`, `budget_usd` and `variance_usd` columns. Name any phase or epic over its budget, or past 80% of it.

The JSON's `logs` list names the folders read and whose they are. With more than one person, `people` gives each person's cost units, and `report.md` adds tables by person and by work item and person. Every detailed file ends with a `person` column.

If the script reports no log folder, say so plainly. Don't go looking through the logs by hand: they hold whole conversations, and the script is the only permitted way in.

## The team's ledger

Claude Code keeps each person's logs in their own home folder, so a plain refresh counts only {user_name}'s sessions. Say so when {user_name} asks about the team's spend, or when the ledger looks thin for the work done. Two sessions or windows on the same project are both counted, each call once.

To count the whole team, everyone shares their logs through one folder that everyone can reach, such as a synced OneDrive or SharePoint folder or a network share:

1. **Agree the folder.** It must be outside the repository: the logs hold whole conversations. Only the project team should have access. Each person sets it in their own `{project-root}/_bmad/custom/config.user.toml` (the path differs per machine):

   ```toml
   [modules.org]
   team_logs_folder = "/Users/<name>/OneDrive - <org>/<project>/claude-logs"
   ```

2. **Each person exports.** `uv run scripts/token_report.py {project-root} --export` copies their log folder to `<team_logs_folder>/<git user.name>/`, only new or changed files. `--as NAME` sets a different name. Export only when {user_name} asks, and say where the copy goes before running it. Offer to repeat it at the end of a working day, or before a report the team will read.
3. **Anyone refreshes.** With `team_logs_folder` set, every refresh, including the background hooks, reads {user_name}'s own logs plus every person's folder in it. {user_name}'s own exported copy is skipped. `--team FOLDER` does the same for one run. A call found in two folders counts once, under the first person.

The team's figures are only as current as each person's last export. Name the person whose export is oldest when it matters.

New stories or bugs without a row in `{project-root}/_bmad/memory/agent-scrooge/stories.json` still show up under their Jira key, just without a story number or title. Offer to add them (create the file if it's missing): `{"stories": {"PROJ-18": ["2.3", "Choose a product"]}}`.
