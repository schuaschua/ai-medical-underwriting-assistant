---
name: story-closeout
description: One-line cost summary for a finished story's pull request and Jira comment
code: CL
added: 2026-09-27
type: prompt
---

# Story cost line

The outcome is one line that a reviewer skimming a pull request or a Jira comment understands without context. Run `uv run scripts/token_report.py {project-root} --story <KEY>-<n>` and use its `story.line` as written, for example:

> PROJ-18 (2.3): 12.5M cost units (4.7% of the project so far), 384 API calls. Steps: implement 47%, build (orchestrating) 44%, review 5%. Writing code and tests 15%, running tests & checks 8%, reading 54%.

Add "Cost units weight cache reads 0.1, output 5; not dollars." the first time the line appears on a PR, so nobody reads it as money.

Run it after the story's last commit, since later calls (rebases, merges) still count towards the story. If {user_name} or the orchestrator asks for it on an open PR, say that the number is the cost so far.
