---
name: coach-lead
description: Walk the Product Lead or Tech Lead through their open questions and record their decisions in the review file
code: CL
added: 2026-09-26
type: prompt
---

# Coach a Lead

Run when your owner asks you to take the Product Lead or the Tech Lead through their open items, for one questionnaire or all of them.

## What success looks like

The lead leaves having decided each open item they were ready to decide, understanding what every question was really asking and why governance asks it, and able to defend their answer to a reviewer without you in the room. Their decisions are in the review file in their own words, so nothing depends on anyone remembering the conversation.

## What you must hold

- Work from the lead's open items across the review files (`pending` items they own, plus anything a reviewer comment reopened), plus any `OPEN — <lead> to provide` answer cells from `uv run scripts/questionnaire.py extract <docx>` (AQ writes those straight into the document, so they never reach a review file). Take the consequential ones first; stop when they want to stop.
- Explain each concept once, plainly, with this project as the example: RTO as "how long the business can live without it", RPO as "how much recent work it can afford to lose". Then ask. Theirs is the decision; yours is making it an informed one.
- Show a matching answer-library entry only as reference, labelled with its project, approver role and date, and say why this project may differ. RTO and RPO: show the reference, then ask fresh.
- Record each decision in the item: the lead's answer as the draft text, ending with a note such as `Note: I have derived this answer from the Product Lead's statement in coaching on <date> because revenue impact is a business figure only the Product Lead can give.` Leave the status for the lead to set, and tell them which line to change. Their status edit is their approval. An open answer cell with no review-file item yet gets one created now, under `Q-<id>`, so the decision has somewhere to land before AQ writes it into the document.
- If the lead's answer contradicts a BMad artifact, say so and show the artifact before you record it; they may still decide, and the note records the conflict.
- Run `uv run scripts/review-status.py <review file>` after writing and fix what it flags.
