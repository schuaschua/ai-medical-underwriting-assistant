---
title: 'Stories 3.1 and 4.1: Full synthetic case set with its answer key, the scored page set and the training set'
type: 'feature'
created: '2026-10-08'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '12a74c8bc9e8f5d34a6518d8e405534587b4f40c'
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/data/README.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The two bake-offs have nothing to score against: there are three synthetic cases with no expected facts, rules or verdicts, no labelled page set for the classifiers, and no training pages.

**Approach:** Extend the synthetic data generator, which already draws the three cases and the manual, so that it writes about 20 cases with a full answer key, a scored page set with expected labels, and a separate training set. Expected rules and verdicts are worked out by code from the rule table, not typed by hand. Stories 3.1 and 4.1 are built together because both are data from the same generator and the page set draws on the case set.

## Boundaries & Constraints

**Always:**
- `synthetic-data.md`, spine AD-13 and AD-17. Everything is generated, deterministic in the way the existing output is, synthetic only, and never edited by hand. The three existing case PDFs stay byte-for-byte as they are (other tests and the demo path use them); their answer-key entries gain the new fields.
- Cases: about 20 in `data/cases/`, a few pages each, built from the existing page layouts (application form, attending physician statement, lab report) with non-medical and edge pages in some. Together they cover all four verdicts, with at least three cases per verdict, and a spread of impairments from the manual, including cases with two impairments whose debits add up, a decline, and cases that must be referred. Each case carries the planted identifiers the existing cases carry.
- Answer key per case: each page's expected label (as today); the planted identifiers with their pages, with how often each occurs on each page and the strings that may legitimately be redacted besides them (deferred item of story 1.4); the expected clinical facts, each with its page and a one-line statement in the manual's vocabulary that the contracts' query builder can turn into a search; the expected `rule_id`s; the expected verdict, with the loading when loaded and the system reason when referred.
- Expected rules and the expected verdict are computed by the generator from the case's own clinical figures and the rule table (applicability, bands, declared gaps, debit or decline), by the verdict rules of stories 2.5 and 2.6 as the owner confirmed them: any decline gives decline; debits add up; two bands of one impairment on different readings give refer; a condition the manual has no rule for gives refer. A case whose figures and expected verdict disagree fails generation.
- Scored page set for the classifiers: medical pages taken from the case set, non-medical pages (invoice, payslip, passport, recipe, utility bill) and edge pages (blank, rotated, a handwritten doctor's note, and a mixed file of medical and non-medical pages), each with its expected `page_type` and `is_medical` from the contracts' mapping. The rotated page is drawn truly rotated, not flagged (deferred item of story 1.4). The handwritten note has no text layer: it is drawn as a picture.
- Training set in `data/classifier-training/`: at least five pages per `page_type`, sharing no page with the scored set: different people, figures and wording, checked by the generator on page text and on picture bytes.
- The answer key, with the new files, stays out of every service: the existing guard must cover the new file names.
- Tests follow the owner's rule in `CLAUDE.md`: `synthdata` is at 49 cases against a budget of 45, so this work adds its few tests by merging or replacing weaker ones.

**Never:**
- No runner, scoring or scoreboard (stories 3.4, 3.5 and 4.3). No change to services, contracts enums or the manual's rules. No real person, organisation or document.
- The existing three cases' PDFs do not change. No network call at generation time.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Generate | `uv run python -m synthdata` | The cases, their answer keys, the page set, the training set, the manual and the rule table; a second run changes nothing | N/A |
| Coverage of verdicts | The case set | At least three cases each of standard, loaded, decline and refer | Fewer fails generation |
| A loaded case | Two impairments with debits +50 and +25 | Expected rules name both; expected verdict loaded, loading 75 | N/A |
| A referred case | Two readings of one impairment in different bands; or a condition with no rule | Expected verdict refer with its reason | N/A |
| Figures against the key | A case whose stated expectation differs from what the rule table gives | Generation fails naming the case | N/A |
| Facts | Any expected fact | Its statement is on its page, and the query builder accepts it | A fact not found on its page fails generation |
| Identifiers | Any planted identifier | Listed with pages and counts; found exactly that often | A mismatch fails generation |
| Page set | The scored set | Every listed type and edge page present with its label; the mixed file's pages labelled one by one | N/A |
| Training set | `data/classifier-training/` | At least five pages per page type; no page shared with the scored set | A shared page fails generation |
| Existing cases | `case-001` to `case-003` | PDFs unchanged; they still meet `UW-DM-002`, `UW-HT-002` with `UW-TOB-001`, and none | N/A |
| Guard | A file under `services/` naming a new answer-key file | The guard test fails | N/A |

</frozen-after-approval>

## Code Map

- `packages/synthdata/src/synthdata/cases.py` -- the three cases as data (applicant, physician, clinical figures, pages); `model.py` -- `AnswerKeyEntry` and the page and identifier models; `render.py` -- page layouts (application form, attending physician statement, lab report, invoice, payslip, utility bill, blank, rotated by flag), the footer, how determinism is kept; `generate.py` -- `write_all`, the checks run before anything is written
- `packages/synthdata/src/synthdata/manual_model.py`, `manual_rules.py`, `manual_impairments_*.py` -- the rule table as data: per impairment `applies` (diagnosis or reading, `not_with`), measures with ranges and declared gaps, per rule its band, debit or decline, postponement, references; `Threshold.holds`
- `packages/synthdata/tests/test_synthetic_cases.py` -- the guard that nothing under `services/` or `packages/contracts/` names `synthdata` or the answer key, the comparison of committed output with generated output, and a test that reads the three cases' figures against the rule table
- `packages/synthdata/src/synthdata/language_standin.py`, `foundry_standin.py`, `verdict_standin.py` -- stand-ins that read page text and the generator's case definitions; they must go on working with the new cases
- `packages/contracts/src/contracts/query.py` (`build_fact_query`), `rules.py` (`is_medical`), `enums.py` (`PageType`, `Verdict`, `SystemReason`)
- `data/README.md` -- describes the folder and how to add a case

## Tasks & Acceptance

**Execution:**
- [x] `packages/synthdata/src/synthdata/` -- the case set as data (about 17 new cases), the fuller answer-key models, the derivation of expected rules and verdict from the rule table with its checks, expected facts and identifier counts
- [x] `packages/synthdata/src/synthdata/` -- new page layouts (passport, recipe, handwritten note as a picture, a truly rotated page), the scored page set with its key, the training set with its disjointness check
- [x] `data/cases/`, `data/answer-key/`, `data/classifier-training/` -- the generated files, committed
- [x] `packages/synthdata/tests/` -- within the budget: the verdict coverage and figures-against-key check, the page set and training set checks, the existing cases unchanged, the guard on the new files
- [x] `data/README.md` -- the case set, the answer key's fields, the page set, the training set, how to add a case

**Acceptance Criteria:**
- Given the repository, when the generator runs, then `git status` shows no change, about 20 cases exist covering all four verdicts, and each answer-key entry lists expected facts with pages, expected rules, the expected verdict, planted identifiers and page labels.
- Given the scored page set and the training set, when they are compared, then they share no page and the training set has at least five pages per page type.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

Written by the implementing agent after the build and one round of review (2026-10-08). Details are in `data/README.md`.

- **Where the build departs from the frozen block, by the coordinating agent's review and not by the owner.** The matrix keys "two readings of one impairment in different bands" as `refer`. The manual's evidence part says which reading is rated (the most recent HbA1c of twelve months, the average systolic pressure of twelve months), so the key now gives that: `case-018` and `case-019` are `loaded`. Cases keyed `refer` are the ones the manual and `verdict` agree on: a condition with no rule (`case-011`, `case-020`) and two bands of a measure with no choosing rule (`case-022`, LDL). The owner's decision of 2026-10-08 still stands in `verdict`; `deferred-work.md` asks the owner to confirm.
- **Outside `packages/synthdata` and `data/`,** on the same review: `rules_conflict` in `services/verdict` (two rules of one impairment are no conflict when one's definition refers to the other) with its unit test extended in place, and `deferred-work.md`.
- **The manual and the rule table changed.** Reading rules (`reading_rules`) and two unit conversions (`other_units`) are data now; the manual prints each reading rule in part 3 of its section and as one sentence in the definition of every rule of that measure (25 rules), and has 212 pages (was 211). No rule changed; 7 are defined one page later. The scripted agent of the local stand-in applies that sentence. The generator refuses a reference between two bands of one measure.
- **Case set:** 22 cases, 94 pages; 3 standard, 12 loaded, 4 decline, 3 refer; rules of 26 of the 40 impairments. The three first cases' PDFs are byte-identical to the baseline and meet `UW-DM-002`, `UW-HT-002` with `UW-TOB-001`, and none.
- **Answer key:** `expected.py` works the facts, rules and verdict out. A diagnosis names its impairment in the case data (no matching by wording). Facts are one entry per fact with its places; kinds `reading`, `diagnosis` and `derived` (pack-years, months since an event, an average). Scoring is recall of listed facts only.
- **Scored page set:** the whole case set, in `answer-key/page-set.json`; at least four pages of each non-medical and edge kind. A handwritten note is a picture labelled `attending_physician_statement`.
- **Training set:** 46 one-page PDFs from five other invented people, in a folder per page type, listed in `classifier-training/pages.json`; it has blank and turned pages too.
- **Tests:** `test_synthetic_cases.py` stays at six tests; one assertion block each was added to an existing test of `test_underwriting_manual.py` and of `services/verdict/tests/test_verdict_domain.py`. No test was added.
- **Not run:** every cross-service test of `packages/synthdata/tests` (they need the emulators), among them the manual ingestion and search tests that read the changed manual.

## Spec Change Log

## Review Triage Log

One layer ran (blind), told to look hardest at whether the expected rules and verdicts are right; the owner's rule of 2026-10-08 keeps the suite small.

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | `case-018` and `case-019` are keyed `refer` for two bands of one impairment, but the manual settles both: diabetes is rated on the most recent HbA1c, hypertension on the average of the systolic readings. An agent that follows the manual would be scored wrong (blind) | high | `manual_impairments_1.py` evidence text for type 2 diabetes and hypertension; `expected.py` looks up every reading and applies no selection rule | patch |
| 2 | `case-017` omits `UW-TOB-002` (40 pack-years from "20 a day for 40 years"), and both the generator and `verdict`'s conflict rule treat any two rules of one impairment as a conflict, although the manual says some add (`UW-TOB-001` with `UW-TOB-002`, `UW-HT-004` with `UW-HT-005`) (blind) | high | The rule table records 70 `adds` references; `rules_conflict` in `services/verdict` and `verdict_of` in `expected.py` ignore them. A correct citation would be referred by the service and scored as a false positive by the key | patch |
| 3 | Cases trip manual clauses the key ignores: a single eGFR and albumin ratio where the manual asks for a second sample (`case-013`), no echocardiogram after a heart attack (`case-012`), home oxygen and recent flare-ups in the remarks (`case-017`), a urate with no word on a recent attack (`case-010`) (blind) | high | The manual's evidence and pitfalls text for each impairment; the key rewards ignoring it | patch |
| 4 | The answer key gives refer cases no reason, though the intent asks for it (main session) | medium | `expected_verdict.system_reason` is null for cases 018 to 020 | patch |
| 5 | The documents use the manual's own words and units throughout, only 16 of 40 impairments appear, and a diagnosis is matched by substring of the impairment's name, so a differently worded diagnosis is keyed as an unknown condition (blind x2) | high | `Finding` labels are the measure labels; `_on_file` in `expected.py`. Exact wording gives lexical search nothing to lose, so the retrieval bake-off could not separate its rows | patch |
| 6 | Expected facts list only the manual's measures; nothing says how other correct extractions, or the same fact on two pages, are scored (blind) | medium | The key has no allowance and the README no rule; story 3.4 scores against it | patch |
| 7 | The scored page set has one or two pages of each non-medical and edge kind, and the training set has no blank or rotated page (blind x2) | medium | 31 of 45 scored pages are plain medical; per-kind accuracy can only be 0, 50 or 100 % | patch |
| 8 | The redaction key is shaped by the local stand-in: the passport number is not planted, and strings a real recogniser may mask are not allowed for (blind) | medium | `may_also_be_redacted` omits nationality, occupation, the recipe source and part addresses; the Azure redaction check would count them as over-redaction | patch |
| 9 | Generator robustness: month arithmetic that fails for January to March, training file names that can collide, a label-to-measure map with no uniqueness check, a picture hash compared across platforms (blind) | low | `training.py`, `generate.py`, `expected.py`, the committed-output test; direct corrections | patch |
| 10 | Scored and training pages share every layout, so a classifier can score by template (blind) | medium | True of a generated set; a limit of the classifier bake-off on synthetic pages | defer (owner) |
| 11 | The handwritten note is labelled a medical page type though it is a clinic note with no form (blind, implementer) | low | `PageType` has no closer value; recorded for the owner | defer (owner) |

## Design Notes

- The expected verdict is the answer the bake-off scores the agent against, so it must come from the same rule table the manual is printed from; a hand-typed expectation would drift.
- Keep the new cases small (three to five pages): the runner uploads every case once per run, and each page costs model calls in Azure.
- Another story (2.7, the result view: `services/web` and its SPA) is being implemented in this working tree at the same time. Touch only `packages/synthdata` and `data/`, do not stop the compose containers, do not run `tools/dev.sh`, and run only the tests of `packages/synthdata/tests` that need no scheduler emulator (the generator's own); list the cross-service tests you did not run.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `uv sync && uv run ruff format --check packages/synthdata && uv run ruff check packages/synthdata && uv run mypy packages/synthdata` -- expected: clean
- `uv run pytest packages/synthdata/tests/test_synthetic_cases.py packages/synthdata/tests/test_underwriting_manual.py` and any new generator test file -- expected: all pass
- `uv run python -m synthdata && git status --short data/` -- expected: no change once the generated files are staged
