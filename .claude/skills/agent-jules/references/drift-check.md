---
name: drift-check
description: Flag approved or applied answers that no longer match the current BMad artifacts
code: DR
added: 2026-09-26
type: prompt
---

# Drift Check

Run when your owner asks whether the governance answers still hold, typically after the architecture, spec or standards changed.

## What success looks like

Your owner knows which answers of record no longer match the project, why, and who has to decide, and each drifted answer has a proposed correction waiting for its lead. The answers of record are the `approved`, `changed` and `applied` items in the review files, and, for a questionnaire with no review file, the answer cells read via `uv run scripts/questionnaire.py extract <docx>` (never open the document any other way).

Drift means either the evidence an answer cites has changed, or the artifacts now commit to something the answer should mention and does not: a new architecture decision added after the Technical Architecture Assessment was answered, a standard that removed a control the answer still claims, a changed region, a new vendor in the stack, or a regulatory source whose date moved since the answer cited it.

## What you must hold

- Read the current artifacts at the locations in BOND.md and compare them against each answer's substance, not only its cited sections.
- Never edit an answer of record. For each drift, add a `pending` `D-<n>` item to the review file: the source line names the affected item by its row id (for example `drift on Q-3: AD-17 added to ARCHITECTURE-SPINE.md`) so answer-questionnaire (AQ) knows exactly which row is due for a fresh answer next time it runs, the draft carries the proposed corrected answer, the owner is the lead of the original item, and the note names the new evidence.
- For each regional source an answer cites, compare the date in `regulatory-sources.md` with the date the document shows today. If it moved, re-read the clause the answer relies on: changed, renumbered or gone is drift, with the source line naming the source id and both dates; unchanged wording just needs the new date recorded.
- Business-figure answers (revenue, users, RTO and RPO) cannot drift from artifacts you are allowed to read for them; flag them only when an artifact contradicts them outright.
- Report back as a short list: item, what changed, who decides. No drift is a valid result; say so in one line.
- Run `uv run scripts/review-status.py <review file>` after writing and fix what it flags.
