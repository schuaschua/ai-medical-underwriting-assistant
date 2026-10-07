---
title: 'Story 2.1: Synthetic underwriting manual and rule table'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '22eafb8e8677460cf3b1bf0e91ec17a3739e2fe7'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/data/README.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Retrieval has nothing to search and a verdict nothing to cite: there is no underwriting manual, and no table of rules with known right answers to score against.

**Approach:** Extend the synthetic data generator so that one structured rule table (the ground truth) produces both the rule table file in `data/answer-key/` and an original manual PDF of about 200 pages in `data/manual/`, in which every rule is defined in exactly one place and other mentions are cross-references.

## Boundaries & Constraints

**Always:**
- Spine AD-12, AD-17; `synthetic-data.md`. The rule table is data in the `synthdata` package; the manual and the rule table file are generated from it and never edited by hand. Generation is deterministic in the way the cases are (text-identical PDF, byte-identical rule table).
- About 40 impairments. Each rule has a `rule_id` matching the contracts' pattern (`UW-[A-Z]{2,4}-[0-9]{3}`, unique), its impairment, a threshold that a program can read (what is measured, the comparison, the value or band, the unit) and as words, either a debit percentage (a whole number) or decline, and the public source its threshold is grounded in (the body and the guideline, for example ADA, AHA, WHO, with enough to find it). An impairment has one or more rules; bands of one measure do not overlap.
- The manual prints each rule's definition exactly once as `Rule <rule_id>:` (the contracts' definition pattern) followed by the rule in words; every other mention is a cross-reference written so that it does not match the definition pattern (for example "see rule UW-HT-002"). Some rules cross-refer to rules of other impairments, and the rule table records those references.
- Each impairment's section has the structure impairment (overview), key questions, probable rating (a table), with prose, at least one table and cross-references. The manual also has a contents page, an introduction on how to read ratings and combine debits, and a glossary. Sections are numbered, and the section number is printed with the heading.
- All wording is original and written for this project. Clinical thresholds follow public guidelines; the debit percentages are invented and say so. No text is copied or paraphrased from any carrier's or reinsurer's manual. Every page carries the synthetic-document footer the cases use.
- The rules cover what the existing cases hold (type 2 diabetes by HbA1c for `case-001`; hypertension and smoking for `case-002`), so that the demo path reaches a rule; `case-003` meets none.
- The rule table file is part of the answer key: nothing under `services/` or `packages/contracts/` may read or name it, and the existing guard test must cover it.

**Never:**
- No ingestion, chunking, embedding or search (stories 2.2 and 2.3). No expected facts, rules or verdicts per case (story 3.1). No change to the existing case PDFs or their answer keys.
- No real person, organisation, product or insurer named in the manual, other than the public bodies whose guidelines ground a threshold.
- No network call at generation time. No new dependency unless the layout cannot be done with what the package has.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Generate | `uv run python -m synthdata` from the repository root | The cases as before, plus `data/manual/` (the PDF) and the rule table in `data/answer-key/` | N/A |
| Size and shape | The generated manual | Between 180 and 220 pages; every impairment section has its three parts; a text layer on every page | N/A |
| Each rule defined once | Any `rule_id` of the table | The manual's text has exactly one definition of it; `contracts.rules.rule_ids_defined_in` over the whole text returns every rule once | A duplicate id or a missing definition fails generation |
| Cross-references | Any reference recorded in the table | The referred rule exists; the mention does not match the definition pattern | A reference to an unknown rule fails generation |
| Rule fields | Any rule | Id pattern, impairment, machine-readable threshold, debit or decline (never both, never neither), source | An invalid rule fails generation with the rule named |
| Bands | The rules of one measure | No value falls in two bands | Overlap fails generation |
| Rerun | Generate twice | Nothing changes in `git status` on the same machine | N/A |
| Guard | A file under `services/` naming the rule table | The existing guard test fails | N/A |

</frozen-after-approval>

## Code Map

- `packages/synthdata/src/synthdata/` -- `cases.py` (case content as data: the pattern for the rule table as data), `model.py` (`AnswerKeyEntry` and friends: pydantic models of generated files), `render.py` (PyMuPDF page layouts, the footer, how determinism is kept), `generate.py` (`write_all`), `__main__.py`
- `packages/synthdata/tests/test_synthetic_cases.py` -- how generated files are compared with the generator's output, and the guard that nothing under `services/` or `packages/contracts/` names `synthdata` or the answer key
- `packages/contracts/src/contracts/rules.py` -- `RULE_ID_PATTERN`, `RULE_DEFINITION_PATTERN`, `rule_ids_defined_in`, `is_rule_id`: use these, do not restate them
- `data/README.md` -- describes the folder; says "There are no expected rules or verdicts yet; they arrive with the underwriting manual"
- `data/answer-key/cases/*.json`, `data/cases/` -- existing output, not to change
- `.gitignore`, `pyproject.toml` -- nothing ignores `data/manual/`; the PDF is committed like the cases

## Tasks & Acceptance

**Execution:**
- [x] `packages/synthdata/src/synthdata/` -- the rule table as data (about 40 impairments, each with its overview, key questions, rules and cross-references), its models and validation (ids, bands, references, debit or decline, source)
- [x] `packages/synthdata/src/synthdata/` -- the manual's layout: contents, introduction, one section per impairment with prose and a rating table, glossary; the definition and cross-reference wording; page numbers and the footer
- [x] `packages/synthdata/src/synthdata/generate.py`, `__main__.py` -- write the manual and the rule table with the cases
- [x] `data/manual/`, `data/answer-key/` -- the generated files, committed
- [x] `packages/synthdata/tests/` -- tests for every matrix row, named for the story, including the comparison of the committed files with what the generator writes
- [x] `data/README.md` -- the manual, the rule table, how to add a rule

**Acceptance Criteria:**
- Given the repository, when the generator runs, then `git status` shows no change, the manual has between 180 and 220 pages, and every rule of the table is defined exactly once in it.
- Given a reader with no other document, when they read one impairment's section, then they can tell what is measured, the bands, the rating of each band and which other rules to look at.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- **One definition, two outputs.** `manual_rules.py` assembles a `ManualSpec` from `manual_impairments_1.py`, `manual_impairments_2.py` and `manual_sources.py`. `manual.py` lays it out; `generate.py` builds the `RuleTable`, checks the text extracted from the PDF against it, and only then writes any file. Output: `data/manual/underwriting-manual.pdf` and `data/answer-key/rule-table.json`. 40 impairments, 111 rules.
- **Five-part sections.** Each impairment's section has the three parts the spec names (`N.1 The impairment`, `N.2 Key questions`, `N.4 Probable rating`) and two more: `N.3 Evidence and readings` and `N.5 Worked examples and related rules`. A new page starts only where a section starts.
- **A5 pages, 10.5 pt.** The manual is a desk handbook on A5. It has about 45,000 words of body text; on A4 the same text would be well under 180 pages. A test limits the pages with fewer than 60 words of body text to 10 (the last pages of sections).
- **Zero debits.** A debit of 0 is allowed and printed as "no debit, +0 %", so a finding can be recognised and cited with a standard outcome. Stories 2.5 and 2.6 map it to the effect `none`.
- **Postponements.** A decline that invites a fresh application is recorded with `postponement: true` and printed as "decline as a postponement". It is still a decline for the verdict.
- **Systolic-only hypertension.** Hypertension is rated on the systolic reading, with one diastolic rule (120 mmHg or more, decline), so that the sum of debits does not count one condition twice. This departs from the cited classification, and the section's band note says so.
- **Band edges.** The lower end of a band is included and the upper end is not, unless the guideline draws the line the other way (`comparison` and the inclusive flags record which). Each end of a band is recorded as following the cited source or as the manual's own, and the definition is worded accordingly.
- **Overlap and gaps.** Bands are checked per impairment and measure, because HbA1c is a measure of three impairments. Every measure has a range of possible readings; bands and declared gaps must cover it exactly, and each gap is printed with what it means.
- **Applicability.** The table records per impairment whether its rules need a diagnosis (or event, or treatment) on file or apply to any reading, and which impairments rule it out. The manual prints it in part 1.
- **Cross-references.** A rule lists only the ids of the rules it points to. The circumstance is worked out from the rule pointed to (its impairment and band) and says whether that rule applies as well (`adds`) or in place of this one (`replaces`). Worked examples add debits only for `adds`.
- **Worked examples** are generated from each rule's band with invented applicants (age, occupation, document) chosen by arithmetic, so every run writes the same ones.
- **No eponyms.** To keep to "no real person named", stroke is rated on the NIHSS, cirrhosis on MELD, and ulcerative colitis is used for inflammatory bowel disease.
- **Sources were written from memory, with no network call.** Body, guideline, year and threshold of each citation should be spot-checked before anyone relies on them.
- **Guard.** The scan of `services/` and `packages/contracts/` matches the answer key's folder name and the rule table's file name. Speaking of "a rule table" in general is not caught.
- **`render.py`.** `_FOOTER` and `_PDF_DATE` became `FOOTER` and `PDF_DATE` so that the manual uses the same footer and date; the case PDFs are unchanged.
- **Review round 1 (2026-10-07)** changed: references worded from their target; examples that no longer add rules which cannot both apply; applicability, notes, postponements, edge origins, gaps, measures and section pages in the rule table; no forced page breaks inside a section, with impairment-specific content added (pitfalls, what does not change the rating, common combinations, unit conversions); the section number in each definition; failures for text that does not fit and for a page without a text layer; the check on extracted text before any write; the narrower guard; tests that rebuild examples, edge lines and locators from the rule table; a scratch folder per test run.

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Cross-references name one band while the circumstance they state covers several: "eGFR below 60, see rule UW-CKD-001" although that rule is 45 to below 60 (blind) | high | Read on pages 11 and 12; story 2.5 follows these references and would land on the wrong rule | patch |
| 2 | Worked examples add the debits of two rules that cannot both apply, and nothing tests what the examples, edge lines and section locators say (blind, edge, gap x2) | high | `_example` always sums; UW-PD-001 with UW-DM-001 on page 23; the gap reviewer changed the arithmetic and every test passed | patch |
| 3 | The rule table cannot say when a rule applies: HbA1c 6.0 % meets rules of three impairments, and the test hand-codes the diagnosis (blind) | medium | `rule-table.json` holds the reading only; story 3.1 writes expected rules per case against it | patch |
| 4 | The table drops the definition's note and each reference's circumstance, and marks a postponement only as decline (blind) | medium | `refers_to` is a bare id list; the note of UW-DM-005 is printed and not recorded | patch |
| 5 | "Source of the threshold" is printed for band edges the manual itself calls its own, and two sentences of the introduction disagree about it (blind) | medium | UW-DM-004 and UW-DM-005 against the note on page 11; introduction 1.1 against 1.9 | patch |
| 6 | 37 of 190 pages hold under 80 words because parts 4 and 5 always start a page; the manual is about 35,000 words (blind, implementer) | medium | Counted by the reviewer; the Design Notes ask for 200 pages reached honestly, and near-empty pages are padding | patch |
| 7 | Bands may leave gaps nobody declared: BMI 18.5 to below 35 meets no rule in a section on obesity (blind) | medium | `check_rules` rejects overlap only; a reader, and the verdict rules of story 2.6, cannot tell a deliberate gap from an omission | patch |
| 8 | Hypertension is rated on systolic alone without saying it departs from the cited classification (blind) | low | The band note does not mention it; `case-002` reads 152/96; direct addition to the note | patch |
| 9 | Generation does not fail on a paragraph or table taller than a page or a contents line that overprints; `check_manual` reads the renderer's strings, not the PDF, and does not check a text layer on every page as it says (blind, edge x3) | medium | `_Book.paragraph`, `_Book.table`, `check_manual` | patch |
| 10 | The widened guard `rule[-_ ]?table` fails any service code that says "rule table" (blind) | medium | Story 2.2's `retrieval` code will say it; the guard should name the file and folder | patch |
| 11 | The definition does not carry its section number, and the test that claims so is vacuous (blind) | medium | Page 12 holds three definitions and no heading; the Design Notes ask for it for story 2.2 | patch |
| 12 | Wording breaks for category rules and zero debits; unit conversions are given in one section and not in others (blind) | low | Pages 94 and 23; direct corrections | patch |
| 13 | Model validation gaps: a measure no rule uses, a rule id against its impairment's code, pages outside the manual, a reference listed twice, two glossary meanings, a hard upper year, no value range per measure so an example can be impossible (edge x7, blind) | low | Each is one check in `manual_model.py`; direct additions | patch |
| 14 | Tests hard-code page ranges and share one scratch folder; patterns from contracts are restated; the cases are written before the manual is checked; the README says the rule table is always byte-identical though it records pages (blind, edge) | low | Read in the tests, `manual_model.py`, `generate.py`, `data/README.md`; direct corrections | patch |
| 15 | One section has no cross-reference at all (edge) | low | Melanoma; direct addition | patch |
| 16 | The footer says every number on the page is invented, which is false of the thresholds, and header and footer are in every page's text (blind, implementer) | medium | True; the intent asks for the cases' footer word for word, so the wording is the owner's to change. Story 2.2's chunker must leave page furniture out | defer |
| 17 | Citations were written from memory; one guideline (CDC 2016) is superseded; the bodies are mostly professional societies, not public bodies (blind, implementer) | maybe-false | Needs a check against the sources; medium if a threshold is wrong, since the manual claims to follow them | defer |
| 18 | No PDF outline, no schema version in the rule table (blind) | low | Nothing reads either | reject |
| 19 | The spec's notes and logs are empty (blind) | low | The implementer was not asked to write them in round one; asked for with the patches | patch |

## Design Notes

- Reaching 200 pages honestly: about five pages per impairment (overview, what to ask and why, how readings are taken and what to do with missing or conflicting evidence, the rating table with notes, worked examples with invented applicants, related rules). Varied, specific prose is better for retrieval than repeated boilerplate; identical filler across sections would make every chunk look alike.
- Later stories read this output: 2.2 splits on the definition pattern and needs the section number and impairment near each rule; 2.3 expects a query naming an impairment and threshold to find its rule; 2.5 follows cross-references between rules; 3.1 writes expected rules per case against the table. Keep each rule's definition self-contained: impairment, measure, band and rating in the same paragraph as its `Rule <rule_id>:` marker.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest packages --cov` -- expected: all pass (the service tests need the containers and are not touched by this story; the full run happens before the commit)
- `uv run python -m synthdata && git status --short data/` -- expected: no output from `git status` once the generated files are staged
