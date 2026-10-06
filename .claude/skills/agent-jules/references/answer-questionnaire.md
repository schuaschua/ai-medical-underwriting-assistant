---
name: answer-questionnaire
description: Write cited answers straight into the governance questionnaires' answer cells (all three by default) and list the open questions per lead
code: AQ
added: 2026-09-26
type: prompt
---

# Answer the Questionnaires

Run only when your owner asks you to answer the assessments. By default that means all three in the governance folder, in this order: `TechnicalArchitectureAssessment.docx`, `DataGovernancePrivacyAssessment.docx`, `EnterpriseAIRiskAssessment.docx`. Answer a single questionnaire, or named questions, only when your owner names them. If one of the three is missing from the governance folder, say so, point to `org-setup` (it copies the blank), and answer the ones that are there.

Answer the Enterprise AI Risk Assessment even when the solution uses no AI: if the BMad artifacts show none, each answer says so and cites where the artifacts describe the solution, and the owning lead confirms it. Never leave the questionnaire blank, and never assume AI is absent because nobody mentioned it.

## Custom templates first

Before answering, list the .docx files in the governance folder. Any file other than the three standard templates is one of the organisation's own templates.

- **BOND.md doesn't record the owner's answer about custom templates yet:** ask whether the organisation has its own templates before you start. Tell them you've been trained on answering the three standard templates, and that similar questions in their own template port straight over, so a custom template costs little extra. Ask them to place the blank .docx in the governance folder (not attached in chat, not elsewhere), and whether it replaces the standard three or comes in addition. Record the answer in BOND.md (Templates) and carry on with what's there now; never wait on a file that may not come.
- **A custom template is in the folder:** it joins the run, in the place BOND.md says: instead of the standard templates it replaces, or after the three.

## Porting to a custom template

Answer the standard templates first (or reuse their current answers when they're already answered and not due for re-answering), then map each custom question to the standard question asking the same thing. Match on meaning, not wording: "Where is the data hosted?" is Technical Architecture Q1, and "Is personal data sent outside the country?" is Data Governance & Privacy Q4.

- **A full match:** carry the answer over with its evidence, adjusted to what the custom question actually asks. The reasoning note names both the evidence and the question it ported from, for example `Note: I have derived this answer from ARCHITECTURE-SPINE.md AD-4 (ported from TechnicalArchitectureAssessment Q1) because ...`.
- **A partial match:** port the part that matches and answer the rest from the artifacts, as usual.
- **No match:** answer it from scratch.

When porting, re-check the evidence against the current artifacts; never copy text you haven't verified this session. The hand-back says how many questions were ported, partly ported or new, so the owner sees the saving. If `questionnaire.py extract` can't find a question table in a custom template (no table with a Question column), say so, show what it found, and ask the owner for a version with one table of question rows, or answer it through safe-docx.

## Order

Work through them one after another, and write each questionnaire's answers before starting the next, so an interrupted session leaves finished documents behind rather than three half-done ones.

## What success looks like

Every answer cell in the questionnaire .docx holds a concise answer ending with the reasoning note, written directly (owner's standing rule: no review-file drafts for answers). An answer the files can't support is written as `OPEN — <lead> to provide.` with one line of context, so the owning lead sees exactly what you need from them. Every draft stands on evidence named in its reasoning note: a BMad artifact and its section or decision id, a dated clause of one of the region's regulatory sources, or an official Microsoft URL. Where the evidence runs out, the draft says so plainly and the item waits for its lead. The leads will sign these answers into a governance record, so a clean gap is always better than a plausible sentence.

Hand back one short list per lead across all the questionnaires you answered, the most consequential first. Mark each item with its questionnaire and question number, and phrase it so the lead can answer it in a line or two. Then name any answers that disagree across the questionnaires (for example personal data denied in one and processed in another), each with the fix you propose.

## What you must hold

- Extract the question table with `uv run scripts/questionnaire.py extract <docx>`: id, question, current answer and a content hash per row, with no need to open the document any other way. Never read a questionnaire .docx by any other means.
- Re-answer only a row that needs it: this project's answer library (AL) has no hash on file for it yet (first pass), its hash differs from the one AL recorded last time, it carries an open reviewer comment (`docx-comments.py`), or drift-check (DR) has an open `D-<n>` against it. Every other row keeps its recorded answer untouched — don't re-derive, re-cite or re-check it. Say in one line how many rows you skipped this way.
- Classify each row you do answer by owner and by source tier (your CREED's source order). Business impact, revenue, users, core processes, RTO and RPO, adjoining systems, partners and vendors belong to the Product Lead, as do the purpose, lawful basis, classification, retention, ownership and data subject rights of the data, and the AI use case, its impact on people, accountability, disclosure and opt-out. Platform, stack, security controls, resilience and monitoring belong to the Tech Lead, as do data residency and protection controls, and the AI's models, data pipelines, testing, monitoring, updates and security. A question that spans both goes to the lead who must sign it, with the other named in the source line.
- When the region has sources, check each row you answer against their `applies_to`. A question a source governs gets the clause it relies on, with the regulator's requirement kept apart from what the project's artifacts commit to (CREED source order and Regulatory source dates).
- A Technical Architecture Assessment answer may also cite the project's architecture diagram (from `agent-davinci`, in the `org` config's `diagrams_folder`) beside the AD it was drawn from, never instead of it, and only when `uv run ../agent-davinci/scripts/diagram-drift.py {project-root}` reports that diagram `current` or `outdated`; a `stale` one gets its drift named to your owner instead.
- A question about what the architecture was bound by (hosting, data residency, approved technology, security posture, AI use) may cite the architecture principles Da Vinci agreed with the owner (`architecture.md` and the per-cloud-provider files beside it, in the `org` config's `architecture_folder`), by principle number (`P-3`), beside the AD that implements it. A principle is a commitment the design was held to, not evidence that it was met.
- Consult the answer library (AL) for how past projects answered the same question, and mention a match in the note as reference only. Never lift a figure from it. RTO and RPO are always asked fresh.
- When a row due for re-answering already holds text, that's an existing answer, not a blank: verify it against the artifacts and draft either a confirmation or a correction, citing what you checked.
- Prefer safe-docx (tracked changes) when its tools are loaded; otherwise `uv run scripts/questionnaire.py write <docx> <answers.json>` writes the answer cells directly, from one JSON file mapping row id to finished answer text (reasoning note included). Either way, only the answer cell's text changes — the org's table structure, columns and formatting stay exactly as issued.
- After writing, record each answered row's question and hash alongside its answer in the answer library (AL), so the next pass can compare hashes instead of rereading the question.
