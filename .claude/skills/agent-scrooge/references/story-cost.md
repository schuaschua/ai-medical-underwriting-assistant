---
name: story-cost
description: Break down what one story or planning phase cost
code: SC
added: 2026-09-27
type: prompt
---

# What a story or phase cost

The outcome is a short answer {user_name} can read in half a minute: the item's total cost units and share of the project, and one table each by step and by category, with percentages of the item. The six categories are AI comprehension (reading / shell), AI reasoning (thinking / reporting), Writing code, Writing tests, Running tests and checks, and MCP usage (git / Jira / PR and every MCP server call). Compare it with similar items (other stories in the same epic, or stories of similar size) when that makes the number meaningful.

For a story, `uv run scripts/token_report.py {project-root} --story <KEY>-<n>` returns its totals, step and activity percentages, and the one-line summary. For a planning phase, or for detail by day or model, read `token_usage.csv` in the token usage folder and sum its rows.

{user_name} may read "orchestration" as the build agent that runs the other steps. In the summary files it's folded into the category rows; only the detail CSV has the step column, so use that when asked about orchestration.
