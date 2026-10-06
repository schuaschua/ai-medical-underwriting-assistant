---
name: reply-comments
description: Draft replies to reviewer comments in a questionnaire and apply approved ones on request
code: RC
added: 2026-09-26
type: prompt
---

# Reply to Reviewer Comments

Run when your owner asks you to reply to the comments on a questionnaire, or to apply the replies the leads have approved.

## What success looks like

Every open reviewer comment has a `C-<key>` item in the questionnaire's review file with a reply the owning lead can approve as written: it answers what the reviewer actually asked, usually by showing the evidence the original answer left out, and it promises nothing the project has not committed to. A reply that needs the answer itself to change says so and carries the revised answer text, so approving the item approves both.

When asked to apply, every `approved` or `changed` item is in the document, as a threaded reply and, where the answer changed, as a tracked change in the answer cell, and marked `applied`.

## What you must hold

- Read comments with the safe-docx MCP server when available; otherwise `uv run scripts/docx-comments.py <docx> --review <review file>` lists them as JSON with a stable `key`, author, date, anchored text, thread parent, resolved flag and whether the review file already holds them.
- Skip resolved comments and your owner's own posted replies (`- posted:` lines in the review file). A comment that needs no reply, such as a thank-you, gets an item saying so, so the watch does not raise it again.
- The source line names the reviewer and quotes the anchored text; the owner is the lead who owns the question the comment sits on.
- Each reply ends with the reasoning note, like every draft.
- Apply only on your owner's instruction and only lead-approved items, under your CREED's apply rules. Replies need safe-docx; without it, say what is waiting and apply nothing but a changed answer cell your owner accepts untracked, via `uv run scripts/questionnaire.py write <docx> <answers.json>`.
- Run `uv run scripts/review-status.py <review file>` after every write and fix what it flags.
