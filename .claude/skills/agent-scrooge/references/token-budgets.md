---
name: token-budgets
description: Once the epics and sprint plan are done, set the Claude API budget for each epic, sprint and remaining planning phase, then track actual against budget in the token ledger and the estimate
code: TB
added: 2026-09-28
type: prompt
---

# Token budgets

The outcome is a Claude API budget, in money, for every piece of work still ahead, set by {user_name} as soon as the epics and the sprint plan exist:

- each **epic**;
- each planned **sprint**, if {user_name} wants sprint budgets;
- each **planning phase** still to run.

Each budget is then compared with what the work actually spends, in two places:

- The ledger's final summary, `token_usage_summary_final.csv` and `report.md`, refreshed after every turn.
- The estimate workbook, on the Epics, Sprints and Phase Budgets sheets.

`bmad-create-epics-and-stories` and `bmad-sprint-planning` offer this when they finish. On waking, Scrooge asks for it himself when both of these hold:

- the sprint status exists but the sanctum has no `token-budgets.json`;
- MEMORY.md doesn't record that {user_name} declined.

## What to read first

- **The epics** (`epics.md`): the numbers and names, and their stories.
- **The sprint plan**, if one exists: the sprint status, plus any stories-per-sprint plan in the estimate model's `sprints`.
- **The ledger:** refresh it (`uv run scripts/token_report.py {project-root}`). It gives the phases already spent and their actual cost. `token_usage_summary_final.csv` has an `actual_usd` column once Claude's prices are saved; run `uv run scripts/claude_prices.py {project-root} --save` if they aren't.
- **The main estimate model**, if there is one: the epics' and sprints' existing `api_budget`, and the reporting currency and `fx_rate`.

## Propose, then let {user_name} set

Budgets come from {user_name}, never from Scrooge. Help them choose with evidence and a proposal they can change. Then ask for the figures, most expensive first, in one round:

1. **Epics.** For each epic, give its story count and the actual cost per story so far. Use this project's finished stories if there are any; otherwise the sanctum's lessons from past projects, labelled as such. Propose stories × cost per story, rounded up, and say it is a proposal.
2. **Sprints**, only if {user_name} wants them. A sprint's budget is usually its planned stories × the same cost per story. Sprint budgets overlap the epic budgets, so never add the two together.
3. **Planning phases still to run.** List the phases the ledger names that haven't finished, and any the plan still needs. Typical ones:
   - `test design`;
   - `governance (Jules)`;
   - `architecture docs (Da Vinci)`;
   - `planning changes (correct course)`;
   - `token reporting`;
   - the ops row, `merges, promotions & deploys`.

   Give the spend of phases already finished (architecture, spec, epics & stories) as a guide. Name each phase exactly as the ledger does, or its actual will never match.

Each figure {user_name} gives is cited as `owner: <who>, <date>`. A phase or epic nobody wants to budget is left out, not set to zero. Zero means "must spend nothing".

## Record them

- **The ledger**: write `{project-root}/_bmad/memory/agent-scrooge/token-budgets.json`, in USD at Claude API list prices, the currency the ledger prices in:

  ```json
  {"currency": "USD", "set": "2026-09-28", "cite": "owner: Delivery Manager, 2026-09-28",
   "phases": {"test design": 40, "governance (Jules)": 30},
   "epics": {"1": 150, "2": 120},
   "sprints": {"1": 80, "2": 80}}
  ```

  Refresh the ledger. Its JSON output gains a `budgets` list, and the final summary gains these columns:
  - `actual_usd`, `budget_usd`, `variance_usd` and `variance_pct`;
  - one row per epic, a breakdown of All stories, so leave those rows out of any total.

  The ledger can't date sprints, so sprint budgets are compared only in the workbook.
- **The estimate**, if there is one: copy the figures into the model in its reporting currency (× `fx_rate` when it isn't USD, and say so):
  - the epics' `api_budget`;
  - the sprints' `api_budget` (add a `sprints` entry with its number if one is missing);
  - `phase_budgets` as `[{"phase": "test design", "api_budget": 40, "cite": "owner: ..."}]`.

  Then check and rebuild it (`references/cost-estimate.md`, Build and report). The **Phase Budgets** sheet (`tblPhaseBudgets`, and `<name>.phases.csv`) shows each phase's budget against its actual from the Claude Usage Detail sheet once the close-out sheets are in. Phase budgets are control figures: they never enter a total, because the close-out's Claude API usage line already holds the actual.

`token-budgets.json` is the source. When a budget changes, change it there first, then in the estimate, and note the change and its reason in MEMORY.md.

## Report

Figure first:
- the total budget;
- the spend so far against it;
- every phase or epic already over, or past 80% of its budget with work still to do, largest overrun first.

Then say where the overrun leaks, from the ledger's step and activity rows: reruns, long review loops, a bloated context. Price the saving, as always. If {user_name} declines budgets, record that in MEMORY.md so nobody asks again, and tell them they can ask any time.

Re-report budget against actual when an epic closes (with its AI feedback), at each refresh {user_name} asks for, and at the close-out.
