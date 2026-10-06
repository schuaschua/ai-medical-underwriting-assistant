---
name: epic-feedback
description: At the end of every epic, ask the team to rate the AI from 1 to 5 and, when it was poor, what went wrong; kept per epic for the close-out and the dashboard
code: EF
added: 2026-09-28
type: prompt
---

# End-of-epic AI feedback

The outcome is one rating per finished epic, from the people who worked with the AI on it, kept in `ai-feedback.csv` in the sanctum. The BMad build and the epic retrospective ask it as soon as an epic finishes; Scrooge asks for any epic they missed (for example one finished by the unattended build loop), and whenever {user_name} wants to rate or re-rate one.

1. **Find what's waiting.** `uv run scripts/ai_feedback.py {project-root} pending` lists the finished epics nobody has rated yet (from the sprint status). On waking, if anything is pending, ask for it before anything else; it takes a moment.
2. **Ask, one epic at a time,** as a multiple-choice question: "How would you rate the AI on epic <n> (<title>), from 1 (very poor) to 5 (excellent)?"
3. **Only if the rating is 1 or 2 (poor),** ask "What went wrong?", allowing more than one answer:
   - AI coding standard not up to quality (`coding`)
   - AI testing standards not up to quality (`testing`)
   - AI decision-making was flawed (`decisions`)
   - AI answers were not grounded in company data (`grounding`)
   - Poor AI security / compliance suggestions (`security`)
   - Other, please specify (`other`, with their words)
4. **Record it:** `uv run scripts/ai_feedback.py {project-root} record --epic <n> --rating <1-5> [--category <key> ...] [--other "<their words>"] [--comment "<anything else they said>"]`. If they'd rather not rate the epic, record `--skip` so nobody asks again. Recording an epic again replaces its answer. The script refuses an answer that doesn't fit (a poor rating with no reason, a reason on a good rating, Other with no words); ask again rather than guess.

Take the rating as given. Don't argue it, soften it or lead the witness; a low score is a cost worth knowing about. Where a poor rating lines up with a costly epic in the ledger (rework, long review loops), say so plainly, since that's money spent twice.

`uv run scripts/ai_feedback.py {project-root} list` shows every answer. The close-out adds them to the workbook as the **AI Feedback** sheet (`tblAIFeedback`), with the average rating. Note any pattern across epics (the same category coming back) in MEMORY.md for the next project.
