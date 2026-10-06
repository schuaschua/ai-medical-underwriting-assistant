---
name: project-close-out
description: At the end of the project, record how long it took, how many Claude API calls it made and what they cost at Claude's list prices, in the estimate workbook and as files a Power BI dashboard can read
code: PC
added: 2026-09-28
type: prompt
---

# Project close-out

The outcome is the project's final account: how long it took from the first BMad stage to the last epic, how many Claude API calls it made, and what they would cost at Claude API list prices. It lands in the estimate workbook as the **Claude Usage**, **Claude Usage Detail**, **Claude Usage Daily**, **Claude Prices** and **Project Timeline** sheets, with a Claude line in the cost summary, and in files a dashboard can read.

1. **Refresh the ledger** (`uv run scripts/token_report.py {project-root}`). It also writes two files to the token usage folder. `claude_usage.csv` has calls and tokens by type, per work item and model, with the first and last call. `claude_usage_daily.csv` has the same per day, with the cost in USD once Claude's prices are saved (step 3). The background hooks refresh both after every turn, so the daily series is always current for a dashboard, not only at close-out.
2. **Read the timeline** (`uv run scripts/project_timeline.py {project-root}`). It gives each BMad stage's first and last commit, each custom agent's birth (from its sanctum), each epic's and story's start and completion (from the sprint status's git history), each work item's first and last Claude call, and the project's start, end, calendar days and working days. It writes `project_timeline.csv` beside the ledger. If epics are still open, the project is still running: say so and ask whether to close out anyway, as "so far". Point out any date that comes from a file's modification time rather than git, because checkouts reset those dates.
3. **Read Claude's prices** (`uv run scripts/claude_prices.py {project-root} --save`): per model, from Anthropic's pricing page, with the URL and the date read. If the page can't be read or its layout changed, say so and stop rather than price from memory.
4. **Ask how Claude was paid for.** On the Claude API, the cost belongs in the totals. On a Pro, Max, Team or Enterprise subscription, the bill was per seat: set `"in_totals": false` so the figure shows as a memo of the API-equivalent cost, not a cost the project paid. In a reporting currency other than USD, get the exchange rate and its source for `fx_rate` and `fx_source`.
5. **Collect the AI feedback.** `uv run scripts/ai_feedback.py {project-root} pending`: ask for any finished epic nobody has rated (`references/epic-feedback.md`) before building, so the **AI Feedback** sheet is complete.
6. **Add `close_out` to the estimate model** (the project's main estimate; if there's none, build a minimal one with the team and the environments as they ended up) and rebuild with `build_estimate.py`. Any model the build lists under `unpriced_models` has no price on the page; name it as a gap.
7. **Report**, figure first. If the estimate has epics, report forecast against actual for each epic: planned against actual working days (the timeline gives calendar dates; the workbook counts working days), the variance, and forecast against actual cost, including the Claude API budget against its actual. Say which actual team costs are timesheet figures and which are scaled from the days. Do the same sprint by sprint: planned against worked days, stories done, Claude API actual, and the variance, including any sprints past the plan. duration (start, end, calendar and working days), phase by phase and epic by epic; total Claude API calls; the Claude cost by model, and the most expensive work items; and how the actual delivery compared with the estimate's days of delivery. Then the token budgets, if any were set: each planning phase and epic, budget against actual and the variance, from the Phase Budgets and Epics sheets and the ledger's final summary, largest overrun first. Then the AI feedback: the average rating, each poor epic with what went wrong, and any reason that came back epic after epic. Finish with the savings audit, as always.

## For the dashboard

Every data range in the workbook is a named Excel table (`tblCostSummary`, `tblTeam`, `tblEnv<name>`, `tblPhaseBudgets`, `tblClaudeUsage`, `tblClaudeUsageDetail`, `tblTimeline`, `tblAIFeedback`, ...), so Power BI and Excel Online load them by name. Tell {user_name} the one catch: Power BI reads the values Excel last saved, and a freshly built workbook has formulas without saved values. Open and save it in Excel once (or keep it in OneDrive or SharePoint and open it there), or point Power BI at the plain-value files instead. These are `<name>.facts.csv` beside the workbook (every revenue and cost line per period: Build, then Year 1 to 5), `<name>.phases.csv` (phase budget against actual), and `claude_usage_daily.csv`, `claude_usage.csv` and `project_timeline.csv` in the token usage folder, and `ai-feedback.csv` in Scrooge's sanctum.

If BOND.md says Azure Cost Management is available, remind {user_name} that the actual cloud spend can sit beside the estimate in the same dashboard.

Record the close-out in `estimates.md` and the lessons (what ran long, what cost most against the estimate) in MEMORY.md, for the next project's estimate.
