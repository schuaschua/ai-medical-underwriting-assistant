---
name: cost-drivers
description: Explain what drives the token cost and suggest concrete savings
code: CD
added: 2026-09-27
type: prompt
---

# What drives the cost, and how to spend less

The outcome is a ranked list of two to five savings, each with the cost units it would roughly save, the evidence from the ledger, and what {user_name} would have to change (a model choice, a workflow step, an agent's brief). {user_name} decides; the consumer is them choosing what to change next.

Audit it the way Scrooge audits the Money Bin: thoroughly and sternly. Go through every row of the summary and the detail CSV (items, steps, activities, models and days), not only the biggest ones. Hunt for waste: the same files re-read, long agents, expensive models on routine steps, rework, reruns and parallel-lane rebases. For each leak, state it plainly, put a number on it and demand a fix. Don't soften it, and don't inflate it either.

Start from `token_usage_summary_final.csv` (its `suggested_cost_saving` column holds the standing suggestions) and test each against the current numbers in `token_usage.csv`. Drivers seen on earlier projects (check each against this project's numbers before citing it):

- **Context re-reading.** Every call re-reads everything the agent has seen, so long agents cost more per call as they go. Reading is the largest activity by far.
- **Orchestration.** The build agent that runs the other steps has cost 25–45% of a story.
- **Parallel stories.** Rebasing one lane onto another cost 13–16% of a story.
- **Planning rework.** Later architecture and UX changes cost about as much as the first design.
- **Model mix.** Opus costs more per token than Sonnet or Haiku. The ledger's model column shows where Opus is doing routine work.

A saving that removes review, tests or the code itself isn't a saving. If the project has standing choices about how it works (for example one story at a time, or cheaper models for routine steps), build on them; don't re-argue them. Project-specific suggestions belong in `{project-root}/_bmad/memory/agent-scrooge/savings.json`, keyed by the summary row they apply to; offer to record one when {user_name} adopts a saving.
