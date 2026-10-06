---
name: rate-card
description: Keep the employee rate card (day rate per role) that prices the delivery team in every cost estimate
code: RT
added: 2026-09-28
type: prompt
---

# Rate card

The outcome is a current rate card in `{project-root}/_bmad/memory/agent-scrooge/rate-card.json` that {user_name} has confirmed line by line. Every estimate's **Rate Card** sheet, and the day rates in its team table, come from this file.

```json
{"currency": "USD", "updated": "2026-09-28", "source": "Finance rate card FY26, given by the Delivery Manager",
 "roles": [{"role": "Developer", "day_rate": 650, "note": "blended senior/mid"}]}
```

- **Show it** as a table: role, day rate and note, with the currency, source and date above. When it's older than {user_name}'s rate cycle (ask once and remember it in BOND.md), say so.
- **Add or change roles** only with figures {user_name} gives. Record where the rates came from in `source` and today's date in `updated`. Show the change as a before-and-after and write it once {user_name} agrees. Role names must match the ones used in the estimates' team tables exactly, so offer to rename those too.
- **Currency** is the reporting currency in BOND.md (USD by default). A rate card in another currency must be converted by {user_name}, or BOND.md changed, not mixed.
- **After a change**, the estimates built on the old rates show as drifted (`build_estimate.py --drift`, since the rate card is stamped). List them from `estimates.md` and offer to rebuild them.

Rates are commercially confidential. They go in the rate card and the workbooks and nowhere else: never into a PR, a Jira comment, a commit message or a chat summary meant for someone else. If the cost estimates folder is committed to git, remind {user_name} once that the workbooks carry the rates.
