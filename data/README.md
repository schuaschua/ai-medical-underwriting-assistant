# Synthetic test data

Everything in this folder is invented. No person, address, telephone number, email address,
identity number, policy number, organisation or medical reading here is real, and none of the text
was copied from a real record, form or insurer's document. Real personal or health data must never
be added. The one thing taken from the real world is in the underwriting manual: its clinical
thresholds follow public guidelines, and the public bodies that issued them are named.

| Folder | Holds |
|---|---|
| `cases/` | One PDF per synthetic case (`case-001.pdf`, ...). These are what gets uploaded. |
| `answer-key/cases/` | One JSON file per case with the known right answers. |
| `manual/` | The synthetic underwriting manual, `underwriting-manual.pdf`. This is what retrieval indexes. |
| `answer-key/rule-table.json` | Every rule of the manual as data: the known right rules. |

## The cases

| Case | Pages | What it is for |
|---|---|---|
| `case-001` | application form, attending physician statement, lab report | Medical pages only. Type 2 diabetes, HbA1c 7.4 %. |
| `case-002` | the three medical pages, then an invoice, a payslip and a utility bill | Medical and non-medical pages in one file. Hypertension, smoker. |
| `case-003` | application form, blank page, lab report rotated 90 degrees, attending physician statement | Edge pages. No impairments. |

Every page except the blank one has a real text layer and a small footer saying it is synthetic.
There are no images, scans or handwriting.

## The answer key

Each entry (`AnswerKeyEntry` in `packages/synthdata/src/synthdata/model.py`) lists:

- `pages`: for every page, the expected `page_type` and `is_medical` (the enum values and the
  mapping from `packages/contracts`), plus `layout` (what the page really is, since a payslip, a
  utility bill and a blank page are all `page_type` `other`) and `rotation`.
- `identifiers`: every planted identifier, its category (`person_name`, `address`, `phone_number`,
  `email_address`, `identity_number`, `policy_number`) and the 1-based pages whose text holds it.
  The applicant's six identifiers and the physician's name, practice address and practice telephone
  are all listed. Each value sits on one line of the page, exactly as written in the entry.

Dates (including the date of birth), ages and medical terms are not identifiers: redaction keeps
them. Organisation names, the invoice number and the lab's specimen reference are not listed either.

There are no expected facts, rules or verdicts per case yet. The rules the cases' own figures meet
today are `UW-DM-002` for `case-001`, `UW-HT-002` and `UW-TOB-001` for `case-002`, and none for
`case-003`; a test keeps that true.

**No service may read `answer-key/`.** It exists for tests and the eval runner only (architecture
spine AD-17). A test fails if anything under `services/` or `packages/contracts/` names the
answer key or the rule table's file (`rule-table.json`).

## The underwriting manual

`manual/underwriting-manual.pdf` is an original document of about 200 A5 pages (the generator
refuses fewer than 180 or more than 220), written for this project. It follows the usual shape of
a field guide to impairments, and nothing in it is copied or paraphrased from any insurer's or
reinsurer's manual. **Every debit percentage and every decision to decline is invented.** The band
edges follow public guidelines, except a few the manual drew itself (a stage split in two, a
waiting period); each rule says which of its edges those are. The manual says all this on its
first page and beside every rating table.

- Section 1 is the introduction (how to read a rule and a rating, how debits combine, how band
  edges are written). Then one section per impairment, the glossary and the list of public
  sources. A contents page gives each section's page. A new page starts only where a section
  starts.
- Every impairment's section has five numbered parts, the number printed with the heading:
  `N.1 The impairment` (with when the section applies), `N.2 Key questions`,
  `N.3 Evidence and readings` (with pitfalls and unit conversions), `N.4 Probable rating` (the
  table, the readings that meet no rule, the rule definitions, what does not change the rating),
  `N.5 Worked examples and related rules` (with the rule met on each band edge and common
  combinations).
- **Each rule is defined exactly once**, in part 4 of its section, in one paragraph that starts
  `Rule <rule_id>:` (the definition pattern in `packages/contracts`) followed by the impairment and
  `(section N.4)`, the threshold, the rating, its note, its cross-references and the source of each
  edge. A definition is never split across pages. Every other mention of a rule is a
  cross-reference such as `see rule UW-HT-002`, which does not match the definition pattern.
- A cross-reference states the circumstance in the terms of the rule it points to (that rule's
  impairment and band), so following one always lands on a rule that applies.
- Every page has a text layer, a page number and the same synthetic-document footer as the cases.

## The rule table

`answer-key/rule-table.json` (`RuleTable` in `packages/synthdata/src/synthdata/manual_model.py`)
is the ground truth the manual is generated from. It holds:

- `page_count`, `contents_page` and `sections` (every numbered section with its title and first
  page).
- `impairments`: id, the code used in rule ids, name, manual section, first page, and
  - `applies`: when its rules apply at all. `basis` is `diagnosis` (the condition, event or
    treatment must be on file) or `reading` (any reading of the measure is enough); `words` is the
    sentence the manual prints; `not_with` lists impairments whose diagnosis rules this one out
    (prediabetes is not read once diabetes is diagnosed). A threshold alone does not say whether a
    rule applies: HbA1c 6.0 % is inside a band of three impairments.
  - `measures`: what is measured, its unit, the range of readings it can take (or its categories),
    and a unit conversion where documents may use another unit.
  - `gaps`: the readings of each measure that meet no rule, each with what that means. Bands and
    gaps together cover a measure's whole range; the generator fails on a gap nobody declared.
- `rules`. A rule has:
  - `rule_id` (`UW-<code>-<nnn>`, unique), `impairment_id`, `impairment`, `section` and
    `manual_page` (the 1-based PDF page that prints its definition).
  - `threshold`: `measure` and `measure_label`, `unit`, `comparison` and `words` (the threshold as
    the manual prints it). `comparison` is `below` (<), `at_most` (<=), `above` (>) or `at_least`
    (>=) with `value`; `between` with `lower`, `upper` and whether each end is included; or
    `equals` with `category` (for example `current_smoker`). Numbers are decimal strings.
  - `debit_pct` (a whole percentage; 0 means no debit) or `decline: true`; never both, never
    neither. `postponement: true` marks a decline that invites a fresh application later.
  - `note`: the extra sentence of its definition, if it has one.
  - `source`: the public body, its abbreviation, the guideline, its year and where in it the
    threshold is found.
  - `edges`: each end of the band with its `origin`, `source` (the cited guideline draws that
    line) or `manual` (this manual drew it).
  - `references`: the rules its definition points to, each with `when` (the circumstance, worded
    from the rule pointed to) and `relation`: `adds` (both rules can apply and their ratings
    combine) or `replaces` (the rule pointed to applies in place of this one). `refers_to` is the
    same rules as a plain list.

An impairment has one or more rules. Within an impairment, the bands of one measure never overlap,
so a reading meets at most one rule per measure.

### Adding or changing a rule

1. Edit the impairment in `packages/synthdata/src/synthdata/manual_impairments_1.py` or
   `manual_impairments_2.py`. A rule is one `rule(...)` call: its id, a threshold built from the
   impairment's `Measure` (`below`, `at_most`, `above`, `at_least`, `between`, `equals`), the debit
   or `DECLINE`, its `Source` from `manual_sources.py`, and the ids of the rules it points to. If
   the new band leaves readings uncovered, declare them with `gap(...)`; if an edge is not from the
   cited source, list it in `own_edges`. A new impairment also needs its applicability, overview,
   key questions, evidence notes, pitfalls, what does not change the rating and common
   combinations, in original wording.
2. Run the generator. Nothing is written unless everything builds. It fails, naming the rule
   wherever one is at fault, on a malformed or repeated id, a rule with both or neither of debit
   and decline, overlapping bands, an undeclared gap, a reference to a rule that does not exist or
   listed twice, a manual whose extracted text does not define every rule exactly once with its
   section and references, a page with no text layer, text that does not fit its place, or the
   wrong number of pages.
3. Commit the code with the regenerated manual and rule table.

## Regenerating

From the repository root:

```sh
uv run python -m synthdata
```

The generator is the `synthdata` package (`packages/synthdata/`), a dev tool that no service
imports. Case content is data in `cases.py`; the page layouts are in `render.py`. The manual's
content is data in `manual_rules.py` and the two `manual_impairments` modules; its layout is in
`manual.py`. Generation is deterministic: the same code gives text-identical PDFs, and
byte-identical ones on the same PyMuPDF build, and always byte-identical per-case answer keys. The
rule table records page numbers, which come from the layout, so it is byte-identical on the same
PyMuPDF build and could differ in those numbers on another. A rerun on the same machine shows
nothing in `git status`.
To change or add a case, edit `cases.py`, rerun the command and commit the code and the new output
together. Never edit the files in this folder by hand; a test compares the answer keys and the
rule table byte for byte, and each PDF's text, page count, page rotations and page sizes, with what
the generator writes.

How the identifiers are kept obviously fictional: telephone numbers are in the 555-0100 to 555-0199
block reserved for fiction, email addresses are at `example.com`, identity numbers start with area
`000` (which no scheme issues), and the towns and the state code `ZZ` do not exist.
