---
name: comment-watch
description: Pulse task that finds new reviewer comments in the governance folder and drafts replies, never applying
code: CW
added: 2026-09-26
type: prompt
---

# Comment Watch

Runs on a pulse, every two hours, with no one at the keyboard. Your owner can also ask for it.

## What success looks like

Every reviewer comment added since the last watch, on any questionnaire .docx in the governance folder recorded in BOND.md, has a `pending` `C-<key>` item with a drafted reply in that questionnaire's review file, owned by the right lead. Nothing else in the project changed. When your owner next wakes you, you can tell them in a line or two what arrived and where the drafts are.

## What you must hold

- `uv run scripts/docx-comments.py <docx> --review <review file>` lists each document's comments; the new ones are those with `in_review: false` that are not resolved. The script needs no Word tooling, which suits a headless wake.
- Each reply answers what the reviewer actually asked, from the same sources and under the same rules as any draft, ends with the reasoning note, and is owned by the lead of the question the comment sits on. Create the review file with its legend if the document has none yet.
- Draft only. Never apply, never set any status but `pending`, never draft or refresh answers to questionnaire items, never notify, email or message anyone. A comment that asks for a new answer gets a reply draft that says the answer is with its lead.
- A document you cannot read (locked, corrupt, mid-save) is skipped and noted; try again next pulse.
- Run `uv run scripts/review-status.py <review file>` on every file you wrote.
- Record the watch in today's session log and the timestamp under State in PULSE.md. When anything new arrived, add a line under `## Pending Sparks` in MEMORY.md (documents, item ids, owning lead) so you hand it over on the next waking. When nothing arrived, write nothing but the timestamp.
