---
title: 'Story 1.4: First synthetic case documents'
type: 'feature'
created: '2026-10-06'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'fcd17450a23878018442247e08b53a67fd88c473'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/specs/spec-underwriting-poc/synthetic-data.md'
  - '{project-root}/docs/standards/coding-style.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Upload, redaction and the classification gate cannot be shown or tested without documents whose contents and right answers are known. No test documents exist, and real medical records may never be used.

**Approach:** Write a small deterministic generator that produces a first set of synthetic case PDFs with planted personal identifiers, medical pages, non-medical pages and edge pages, and an answer-key entry for each case. Commit both the generator and its output.

## Boundaries & Constraints

**Always:**
- Everything is invented. Names, addresses, phone numbers, email addresses, identity numbers and policy numbers are obviously fictional (for example phone numbers in a reserved fictional range, email addresses at `example.com`, identity numbers that match no real scheme's checksum).
- Generation is deterministic: the same command produces byte-identical answer keys and text-identical PDFs.
- Each case PDF is a few pages, at most 10 MB, with a real text layer (not images of text), in plain business-document layouts.
- Medical pages are of the three medical page types: attending physician statement, lab report, application form. Their clinical content is plausible and internally consistent (for example type 2 diabetes with an HbA1c value, hypertension with blood pressure readings, a BMI, smoking status), because later stories extract facts from them.
- Each case's answer-key entry lists every planted identifier with its category and the pages it appears on, and the expected `page_type` and medical or non-medical label of every page, using the enum values from `packages/contracts`.
- The answer key lives under `data/answer-key/` and is never read by any service code.

**Never:**
- No real person's data, no text copied from real medical records, forms or any insurer's documents.
- No expected rules or verdicts yet (the manual does not exist; story 3.1 adds them). No scored page set or classifier training set (story 4.1).
- No handwriting, scanned images or embedded photographs in this first set.
- No change to `packages/contracts`, `infra/`, or any workflow file.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Generate | Run the generator | At least 3 case PDFs in `data/cases/` and one answer-key file per case in `data/answer-key/cases/` | N/A |
| Regenerate | Run it again | Answer-key files byte-identical; extracted PDF text identical | N/A |
| Medical-only case | One case | Only medical pages of the three types | N/A |
| Mixed case | One case | Medical pages plus non-medical pages: invoice, payslip, utility bill | N/A |
| Edge case | One case | Includes a blank page and a page rotated 90 degrees | N/A |
| Planted identifiers | Any case | A name, address, phone number, email address, identity number and policy number each appear in the PDF text exactly where the answer key says | N/A |
| Kept content | Any medical page | Dates, ages and medical terms are present and are not listed as planted identifiers | N/A |
| Answer-key schema | A malformed entry | Rejected by the entry model | `ValidationError` |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/enums.py` -- `PageType` values and the medical mapping in `rules.py`: reuse, do not redefine
- `pyproject.toml` -- workspace root; `packages/*` are members; `testpaths` already covers `packages`; coverage `source_pkgs` needs the new package added
- `_bmad-output/specs/spec-underwriting-poc/synthetic-data.md` -- the data rules
- Spine AD-21 -- redacted categories (person names, addresses, phone numbers, email addresses, identity and policy numbers) and what is kept (dates including date of birth, ages, medical terms); AD-17 -- answer key location and who may read it

## Tasks & Acceptance

**Execution:**
- [x] `packages/synthdata/pyproject.toml` -- workspace package `synthdata`, depending on `contracts`, `pydantic==2.13.5` and `pymupdf==1.28.2` -- a dev tool, not service code
- [x] `packages/synthdata/src/synthdata/model.py` -- pydantic models for a case definition and an answer-key entry -- one schema for both
- [x] `packages/synthdata/src/synthdata/cases.py` -- the first three or more case definitions as data: people, clinical details, page list -- content separate from rendering
- [x] `packages/synthdata/src/synthdata/render.py` -- render each page type to PDF with PyMuPDF, including blank and rotated pages -- text layer required
- [x] `packages/synthdata/src/synthdata/__main__.py` -- `uv run python -m synthdata` writes `data/cases/*.pdf` and `data/answer-key/cases/*.json` -- one command
- [x] `packages/synthdata/tests/` -- tests for every matrix row, reading the generated PDFs back with PyMuPDF -- coding-style rule 20
- [x] `data/cases/`, `data/answer-key/cases/` -- commit the generated output
- [x] `data/README.md` -- what the folders hold, how to regenerate, and that everything is synthetic

**Acceptance Criteria:**
- Given a fresh clone, when `uv run python -m synthdata` is run, then `git status` shows no change to `data/answer-key/`.
- Given any generated PDF, when its text is extracted, then every planted identifier in its answer-key entry is found on the listed pages and nowhere else.
- Given the repository, when it is searched for the answer-key path, then no file under `services/` or `packages/contracts/` references it.

## Implementation Notes

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | README and docstring claim byte-identical PDFs, tests compare only text; committed PDFs' rotation and page count unchecked (all three) | medium | Filed with a demonstration; byte equality across PyMuPDF builds is not safe to assert in CI, so the claim is softened and the committed files' structure is compared | patch |
| 2 | AD-17 guard is case-sensitive, misses `ANSWER_KEY_FOLDER` and a `synthdata` import, and passes when `services/` is absent (all three) | medium | Regex `answer[-_]key` does not match the generator's own constant; the guard is the only thing enforcing AD-17 | patch |
| 3 | No rejecting test for a duplicate identifier or a repeated page (gap) | medium | Filed with evidence | patch |
| 4 | The command's default output folder is never exercised (gap) | medium | Filed with evidence | patch |
| 5 | Nothing stops content overflowing the page or a column (blind, edge) | medium | Reproduced by the reviewer: 8 rows written, 5 extractable; story 3.1 adds 17 larger cases | patch |
| 6 | Applicant and physician could share an identifier value; answer-key value is not one line (edge) | low | Direct corrections in the models | patch |
| 7 | Invoice sentence hard-codes "30 days" beside a printed due date (blind, edge) | low | Direct correction | patch |
| 8 | Committed-folder test fails on a `.DS_Store` (edge) | low | Direct correction | patch |
| 9 | CI does not type-check the new package (gap, implementer) | medium | CI mypy step names only contracts; fixed by the main session in `ci.yml` | patch |
| 10 | Rotated page only sets the `/Rotate` flag, so text extraction is unaffected (blind) | medium | True; it stresses image-based classification only. Drawing rotated content is not a trivial change | defer to story 4.1 |
| 11 | A visible "SYNTHETIC TEST DOCUMENT" footer is in every page's text layer and may cue a classifier or model (blind, implementer) | medium | True; whether to keep a visible marker is the owner's call | defer (question for Darrel) |
| 12 | Answer key has no occurrence counts, no "may redact" category and no clinical ground truth (blind) | medium | True; needed for redaction scoring and extraction scoring, both later | defer to stories 3.1 and 3.4 |
| 13 | Fictional identifiers may not be recognised by the redaction service; US and UK formats are mixed (blind, implementer) | maybe-false | Only a live redaction call settles it | defer to story 1.7 (medium if true) |
| 14 | Date-order and pay validators missing on hand-typed case data (blind, edge) | low | Data is authored here and read back by tests; guards add complexity | reject |
| 15 | Non-ASCII glyphs, layout-enum completeness, stale output on rename, wrong working directory, shared scratch folder (edge, blind) | low | None occurs with the present data or a single test run | reject |
| 16 | Case PDFs missing from the diff (blind) | false | Binary files were left out of the review diff; they are committed with the story | reject |
| 17 | PyMuPDF licence not addressed (blind) | false | Accepted for this POC by Darrel on 2026-10-06 (spec Design Notes) | reject |
| 18 | Answer-key schema lives in `synthdata`, so the eval runner depends on it (blind) | false | `evals/` is not a service; AD-17 allows the runner to read the key | reject |

## Design Notes

- PyMuPDF is already in the spine's stack (`pymupdf` 1.28.2), so no new library is introduced. Its AGPL licence was accepted for this POC by Darrel on 2026-10-06.
- PDFs embed a creation timestamp; set a fixed one so regenerated files do not churn in git.
- Other work is running in the same working tree: do not edit `infra/`, `.github/`, `docs/standards/` or `packages/contracts/`, and do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel authorised unattended work on stories 1.1 to 1.6 on 2026-10-06 before going offline; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `uv sync` -- expected: resolves with the new package
- `uv run python -m synthdata` -- expected: writes the case PDFs and answer keys
- `uv run ruff format --check . && uv run ruff check .` -- expected: clean
- `uv run mypy packages/synthdata/src` -- expected: no errors
- `uv run pytest packages/synthdata -q` -- expected: all pass
