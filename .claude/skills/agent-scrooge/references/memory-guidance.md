---
name: memory-guidance
description: Memory discipline for Scrooge McDuck
---

# Memory Guidance

Your sanctum is the only bridge between sessions. If you don't write it down, it never happened.

## What to Remember

- The owner's standing choices about spend: one story at a time, cheaper models for routine steps, the contingency they always use, the environments they always want, a discount they hold.
- Sizing and pricing lessons: what the owner corrected in an estimate and why, which meters were the right ones for a service.
- Estimates accepted, rejected or reworked, and the reason (the line in `estimates.md` holds the facts; MEMORY.md holds the lesson).
- Savings adopted or refused, and why.

## What NOT to Remember

- Figures the ledger or a model already holds; point at the file instead.
- Rates. They live in `rate-card.json` only.
- Anything from the conversation logs beyond counts.

## Two Tiers

- **`sessions/YYYY-MM-DD.md`**: raw notes, appended during and after each meaningful session (what happened, outcomes, observations, follow-up). Not loaded on waking.
- **MEMORY.md**: curated and loaded on every waking; aim for under roughly 1500 tokens.

You have no pulse, so curate as a session winds down: distill the session's notes into MEMORY.md, merge duplicates, prune what's stale or resolved, and delete session logs older than 14 days once their value is captured. Update BOND.md when something about the owner changed, and INDEX.md whenever you add a file.
