# Falcon Portal: a sample cost estimate (synthetic)

All figures are in US dollars.

A made-up project, so you can see what Scrooge McDuck's cost estimate and close-out look like before running them on a real one. Open `falcon-portal.xlsx` in Excel; it recalculates on opening.

- **What's real:** the Azure VM price (Microsoft's Retail Prices API, uaenorth, USD, 2026-09-28) and the Claude prices (`claude-prices.json`, read from Anthropic's pricing page the same day).
- **What's made up:** everything else. The other prices say "Synthetic sample price", the rate card is not a real one, and the spine, spec, Claude usage and timeline are invented.

## What's here

| File | What it is |
| --- | --- |
| `falcon-portal.xlsx` | The workbook: Overall (inputs, delivery team, run support, cost summary), Multi-Year (build and five years: revenue, costs, net, payback), Epics, Sprints, Phase Budgets (Claude API budget against actual per planning phase), Revenue, Rate Card, Other Costs, Run Costs, Dev/Test/Prod, Claude Prices, Claude Usage (+ Detail, Daily), Project Timeline, Sources & Gaps |
| `falcon-portal.facts.csv` | Every revenue and cost line per period (Build, Year 1..5) as plain values, for Power BI |
| `falcon-portal.epics.csv`, `.sprints.csv`, `.phases.csv` | Forecast against actual per epic and sprint, and each planning phase's Claude API budget against actual |
| `falcon-portal.infra_cost.csv` | Infrastructure OPEX after go-live per cloud line, environment and in total: daily, weekly, monthly, annual |
| `falcon-portal.estimate.json` | The model the workbook is built from; each line cites its source |
| `_bmad-output/` | The sample spine and spec the model cites |
| `token_usage/` | The sample Claude usage ledger (per work item and per day) and project timeline for the close-out |
| `claude-prices.json` | Claude API list prices used for the Claude sheets |

In a real project the rate card lives in `_bmad/memory/agent-scrooge/rate-card.json` and the close-out files come from `token_report.py` and `project_timeline.py`. Here they sit inline or in plain folders so the sample is self-contained.

## Rebuild it

From the kit's root:

```bash
uv run skills/agent-scrooge/scripts/build_estimate.py skills/agent-scrooge/assets/examples/falcon-portal falcon-portal.estimate.json --force
```

`--force` is needed because a git checkout changes the workbook's file date, which the builder would otherwise read as a hand edit. Change a number in the model (for example `delivery_days`, `horizon_years` or a team member's `employees`) and rebuild to see it flow through; in Excel, change the blue input cells, such as Years of run (3 to 5).
