---
name: answer-library
description: Record approved answers per project with approver role and date, and retrieve them as reference only
code: AL
added: 2026-09-26
type: prompt
---

# Answer Library

The memory capability behind every other one. Record whenever you see an item reach `approved`, `changed` or `applied` (in any session or pulse); retrieve whenever you draft or coach.

## What success looks like

`answer-library.md` in your sanctum lets the next project start from reference instead of from zero: for any questionnaire question, you can find what earlier projects answered, who approved it by role, when, and on what evidence, in a few seconds. It never becomes a source of truth; its job is to make the lead's decision faster, not to make it for them.

## What you must hold

- One entry per project per question, organised by questionnaire and then question: project, questionnaire, question number and text, its content hash (from `questionnaire.py extract`), final answer text, approver role (the owning lead of the item), date the status was seen, and the evidence note. Roles only, never names.
- The hash is how answer-questionnaire (AQ) tells a re-run that nothing changed: a matching hash means skip the row entirely, a differing one means the question moved and it's due for a fresh answer.
- A later status on the same item replaces that project's entry; a `rejected` item never enters, and an entry whose item was later rejected leaves.
- Mark RTO and RPO entries "always ask fresh" so retrieval never offers them as a default.
- When retrieving, match on what the question asks, not its wording, because questionnaire versions reword questions. Present matches as "for reference: <project>, approved by the <role> on <date>", with what differs about this project where you can see it.
- Create the file on the first approval and list it in INDEX.md. Keep it lean: when a question has many projects' entries saying the same thing, merge them into one entry listing the projects.
