---
name: cost-estimate
description: Price the project from its BMad artifacts into an Excel workbook, one sheet per environment, a rate card and an overall summary; or update an existing estimate
code: CE
added: 2026-09-28
type: prompt
---

# Cost estimate

The outcome is a workbook {user_name} can put in front of whoever holds the budget: `<name>.xlsx` in the cost estimates folder (org config `cost_estimates_folder`, default `{project-root}/docs/costing/`), built by `scripts/build_estimate.py` from a model, `<name>.estimate.json`, that sits beside it. Every line cites where it came from, every price says which meter and when, and everything not yet decided is listed as a gap instead of guessed.

The workbook covers the build and then 3 to 5 years of running the solution. It has:

- **Overall:** the inputs (days of delivery, working days per month, hours per month, years of run after go-live, the yearly increase in run costs, contingency, exchange rate); the delivery team as role × employees × days × allocation = mandays; the run support team (OPEX) as role × mandays per year, both priced from the rate card; and the cost summary of delivery, run per year, run over the horizon and grand totals.
- **Epics:** forecast against actual for each epic. The forecast is its deliverables, planned working days, weeks and dates, and its share of the build (team mandays and cost, infra), plus its one-off costs and Claude API budget. The actual is its dates, working days and the variance in days and %, its Claude API actual, its actual cost and the cost variance.
- **Sprints:** forecast against actual for each sprint. Sprints are fixed timeboxes of the sprint length from the delivery start date.
- **Phase Budgets:** the Claude API budget for each planning phase still to run, against its actual from the ledger once the close-out sheets are in. A control figure, never in a total.
- **Multi-Year:** the build, then Year 1 to Year 5. It shows revenue, every cost, contingency, net and cumulative net, the payback year and the return on cost. Years past the horizon show zero, so changing the horizon in Excel recalculates it.
- **Revenue:** yearly revenue per stream.
- **Run Costs:** recurring yearly costs such as support contracts and licences.
- One sheet per **environment**, with a monthly, daily and annual cost per line.
- **Rate Card**, **Other Costs** (one-off) and **Sources & Gaps**. Every total is a formula, so {user_name} can change a blue cell in Excel and the workbook recalculates. `uv run scripts/build_estimate.py --help` documents the model format in full.

## New or update

List the models already in the cost estimates folder. If {user_name} names one, or the project has one, this is an update: start with **Update** below. Several estimates can live side by side (`<project>-mvp`, `<project>-ha`), so a different scenario is a new model, usually copied from an existing one.

## What to read

The artifacts decide *what* is built; they never decide what it costs. Look before you ask: find these yourself and show {user_name} what you found and which of them you'll use.

- **Architecture spine** (`_bmad-output/planning-artifacts/architecture/<name>/ARCHITECTURE-SPINE.md`): components and the Azure service each runs on, data stores, networking, environments, regions, availability and disaster recovery.
- **Spec / PRD**: users, volumes, retention, availability targets: what sizes things.
- **Epics and stories, sprint status**: how much work there is, which kinds of work (front end, back end, infrastructure, data, testing), and how much is already done.
- **Anything else {user_name} gives you**: a file in the project, a vendor quote, figures typed in chat, a different set of environments for a what-if. A figure given in chat is cited as `owner: <who>, <date>`.

Read only inside the project.

## Build the bill of materials

For each environment (from the spine; if it names none, propose Dev, Test and Prod and ask), list each component as one line per priced meter: component, Azure service, SKU or tier, unit, quantity, usage per month and the citation. A service billed on several meters (compute and storage, say) gets a line per meter. Always-on hourly items use `"usage": "hours"`; anything that runs part-time (a dev cluster stopped at night) gets its hours as a number, cited to whoever decided it.

Then **challenge before pricing**: put the gaps and contradictions to {user_name}, most expensive first, each with roughly what it costs either way. Typical ones: the spine names a service but not its size, Prod's high availability or DR isn't stated, the environments differ from what the stories assume, or there are no data volumes. One round, in Scrooge's voice; each answer becomes an `owner:` cite or an agreed `assumption:`, and anything still open goes in `gaps`.

## Price it

For each line run `uv run scripts/azure_prices.py --service "<serviceName>" --region <region> --sku <armSkuName> --currency <currency>` (pricing region and currency from BOND.md, USD by default; `--help` has the other filters). When several meters come back, pick the one that matches the line and say which you chose when it isn't obvious. Tier 0 unless the volume clearly reaches a higher tier. Linux unless the artifacts say Windows. Copy the item's `source` into `price_source` and its `retailPrice` into `unit_price`. When the API has no currency the owner reports in, price in USD and set `cloud_currency`, `fx_rate` and `fx_source` from a rate {user_name} gives. No match in the API, or a service outside Azure (SaaS licences, a vendor): ask {user_name} for the figure, or list it as a gap.

List prices are what Microsoft charges a stranger. Apply a discount or reservation only when {user_name} gives it with its source, and say so in the line's note.

## The team

The team is priced from the rate card (capability RT, `rate-card.json` in the sanctum). If it's missing, or a role the team needs isn't on it, get the day rates from {user_name} first; never invent one.

Propose the team from the work: the roles the stories call for, the number of employees in each, and the days of delivery. Base your proposal on the story count, the stories still open in the sprint status, and a pace {user_name} confirms. Show the reasoning in one or two lines and let {user_name} set the numbers. `days: null` means the whole delivery (the Overall input), a number means a shorter stint (a security specialist for ten days), and `allocation` covers part-time roles. Cite the epics file for roles the stories imply and `owner:` for the numbers {user_name} set.

Ask once about **other costs**: one-off costs of the build (licences, penetration test, training). Include only the ones {user_name} gives a figure for.

## Epics

When the epics and stories exist (`bmad-create-epics-and-stories`), add one `epics` entry per epic:
- its number, as in the story numbers (1 for stories 1.1, 1.2);
- its name;
- its deliverables: the stories, or the outputs they produce;
- its working days;
- an optional Claude API budget;
- a citation to the epics file.

Propose the days from the stories' size and the team, and let {user_name} set them. If the epic days don't add up to the days of delivery, say so and ask which is right; the sheet shares the build by epic days either way. A delivery start date (`assumptions.start_date`) dates the epics, one after another in working days. Tag a one-off cost to the epic it belongs to with `epic` on the Other Costs line.

- **What each epic carries:** its share of the delivery team and of the environments during delivery (share = its days ÷ all epic days), the one-off costs tagged to it, and its Claude API cost.
- **The Claude API cost:** the budget {user_name} gives (capability TB, from `token-budgets.json` in the reporting currency), until the ledger has actual usage for the epic's stories. Then it's the actual, which needs the close-out's Claude sheets. Never invent an API budget; leave it out if nobody sets one.
- **Forecast vs actual:** the forecast total is team + infra + one-off + the API budget. Once an epic has both an actual start and end date, the actual side fills in:
  - the actual working days (Excel's NETWORKDAYS, so like for like with the planned working days), and the variance in days and %;
  - the Claude API actual;
  - team and infra scaled by actual ÷ planned days, assuming the same team and rates, unless {user_name} gives the timesheet team cost (`actual_team_cost`);
  - the cost variance (actual − forecast).

  The actual dates come from the timeline at close-out, or from `actual_start` and `actual_end` when {user_name} gives them mid-project. Say plainly when the actual cost rests on the same-team assumption rather than timesheets.
- **In Excel:** every epic figure is a formula, and there are spare rows, so {user_name} can change an epic's days or add an epic and the costs follow.

## Sprints

Ask the sprint length in working days (default 10, two weeks); the delivery start date is the one the epics use. The sheet then lays out the sprints by itself, one timebox after another, until the days of delivery run out. If {user_name} has a sprint plan, record the stories planned for each sprint in `sprints` (number, stories), with an optional Claude API budget per sprint.

- **Forecast:** the sprint's planned working days at the delivery team's daily cost (team cost ÷ days of delivery) and the environments' daily cost, plus any budget. The last sprint may have fewer planned days than its timebox.
- **Actual:** this runs up to the last actual activity (the latest completed story, or the latest day of Claude usage). It shows:
  - the days worked in the sprint (NETWORKDAYS);
  - the stories completed within the sprint's dates (from the timeline);
  - the Claude API cost on its days (from the daily ledger);
  - the team at the days worked, unless {user_name} gives the sprint's timesheet cost (`actual_team_cost`);
  - the cost variance.
- **Work that ran past the plan** shows as extra sprints with no planned days. Their whole cost is the overrun.
- BMad's sprint status records stories, not sprint numbers, so stories are counted by the dates they were completed. Say so when {user_name} compares against their own sprint board.

## Token budgets

Once the epics exist, and again once the sprint plan does, ask {user_name} for the Claude API budgets if the sanctum has no `token-budgets.json`. Follow capability TB (`references/token-budgets.md`). It sets one budget per epic, per sprint if wanted, and per remaining planning phase, and records them in the ledger's budget file and in this model (`api_budget` on the epics and sprints, and `phase_budgets`).

## Running it: the horizon and OPEX

Ask how many years after go-live the estimate should cover: 3, 4 or 5, default 5. Ask too whether run costs should rise each year (for example 3% for price increases); the default is 0.

- **Run support (OPEX):** the people who keep the solution running after go-live, such as bug fixes, patching, upgrades, on-call and IT support. Record them as role × mandays per year in `support`, priced from the rate card. Propose the roles from the architecture (a cluster needs patching, a database upgrades) and from how the build went. {user_name} sets the mandays.
- **Recurring run costs:** yearly costs outside the environments and the support team, such as a support contract, licences or SaaS subscriptions. Record them in `run_costs`, only with figures {user_name} gives.
- An environment that doesn't run all year after go-live (a Test environment used six months a year) gets `run_months_per_year`.

## Revenue

Ask what the solution earns, year by year after go-live, and on what basis (fees, subscriptions, savings the business will count as revenue). Revenue comes from {user_name} or from a business case in the artifacts, cited like any other line. Never estimate it yourself, and never borrow a growth rate. Record each stream in `revenue` with one amount per year of the horizon and its basis in words. If there's no revenue, or none is known yet, leave `revenue` out. The workbook then shows costs only and lists the missing revenue as a gap.

## Build and report

Every build also writes `<name>.infra_cost.csv` beside the workbook: the infrastructure OPEX after go-live, as plain values. It has one row per priced cloud line, then each environment's total, then the grand total. Each row gives the daily, weekly, monthly and annual cost:

- daily = monthly × 12 ÷ 365, as on the environment sheets;
- weekly = daily × 7;
- annual = monthly × the environment's run months per year.

These are year-one list prices, before any yearly increase or contingency. The file is written as soon as the environments are priced from the architecture, so it's there before the team, epics or revenue are known.


Write the model to `<cost estimates folder>/<name>.estimate.json`, run `uv run scripts/build_estimate.py {project-root} <name>.estimate.json --check`, fix what it flags (a cite that doesn't resolve, a role not on the rate card, an unpriced line), then build without `--check`.

Report from the script's `figures`, figure first: the grand total over the build and the horizon, split into delivery and run, with the contingency. When there is revenue, add the total revenue, the net, the payback year and the return on cost. When there are token budgets, add the total Claude API budget and, once actuals exist, the spend against it. Then give the infrastructure OPEX from `figures.infra_opex` (daily, weekly, monthly and annual) with the path to `infra_cost.csv`, each environment's monthly and daily cost, the team's mandays and cost, the run support's mandays and cost per year, the gaps, and the workbook's path. Tell {user_name} that the blue cells are theirs to change in Excel, and that the model is the source: a rebuild replaces the workbook. For a dashboard, every data range is a named Excel table and `<name>.facts.csv` holds the cost lines as plain values (see `references/project-close-out.md`, For the dashboard). Finish the way every Scrooge report finishes, with where the money leaks: the most expensive lines, any over-sized or always-on item that needn't be, and what a reservation would save if Prod runs a year or more. Price each saving, and never touch what makes the system sound.

Add or update the estimate's line in `estimates.md` in the sanctum.

## Actual cloud spend (Azure Cost Management)

The estimate prices the cloud at list price, month by month, with a daily rate derived from it (monthly × 12 / 365). What the subscription actually spends day by day lives in Azure Cost Management. After each estimate, until BOND.md records the answer, ask {user_name} once whether they have Azure Cost Management, or its daily cost exports, for this solution's subscription, and whether they'd like to bring the actuals in later to compare against the estimate. Record the answer in BOND.md (Money): the subscription or resource group, where the exports land, and how often. Never go looking for the data yourself. Scrooge reads cost exports only once {user_name} puts them in the project.

## Update

1. `uv run scripts/build_estimate.py {project-root} <name>.estimate.json --drift` names the cited files, and the rate card, that changed since the build. Re-read those ADs and sections and propose the line changes they cause, as a before-and-after with the monthly difference.
2. Prices move: if the model's `price_date` is more than a month old, or {user_name} asks, re-run the price lookups and report what changed.
3. If the build refuses because the workbook was changed after the last build, {user_name} edited it in Excel. Read the workbook's blue input cells (openpyxl), show the differences from the model, and carry the ones {user_name} wants to keep into the model before rebuilding. Use `--force` only when {user_name} says to discard the hand edits.
4. Rebuild, report the old and new grand totals and the lines that moved them, and update `estimates.md`.
