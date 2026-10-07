# Synthetic test data

Everything in this folder is invented. No person, address, telephone number, email address,
identity number, policy number, organisation or medical reading here is real, and none of the text
was copied from a real record, form or insurer's document. Real personal or health data must never
be added. The one thing taken from the real world is in the underwriting manual: its clinical
thresholds follow public guidelines, and the public bodies that issued them are named.

| Folder | Holds |
|---|---|
| `cases/` | One PDF per synthetic case (`case-001.pdf`, ...). These are what gets uploaded. Some of them are also the classifiers' scored page set. |
| `answer-key/cases/` | One JSON file per case with the known right answers. |
| `answer-key/page-set.json` | The scored page set: which case files it is, and the label of every page. |
| `answer-key/rule-table.json` | Every rule of the manual as data: the known right rules. |
| `classifier-training/` | One-page PDFs a classifier is trained on, in a folder per page type. No page of it is scored. |
| `manual/` | The synthetic underwriting manual, `underwriting-manual.pdf`. This is what retrieval indexes. |
| `scoreboards/` | The published scoreboard files of the bake-off, written by a run against the deployed environment and served read-only by `web`. The only folder here that reaches an image. See its `README.md`. |

## The cases

Twenty-two cases of three to six pages. Each has an application form, an attending physician
statement and a lab report; every case but the first also has a non-medical page or an edge
page. Together they cover all four verdicts (the generator refuses a set with fewer than three
cases of any one) and meet rules of 26 of the manual's 40 impairments.

| Case | Shows | Rules its figures meet | Verdict |
|---|---|---|---|
| `case-001` | Type 2 diabetes, HbA1c 7.4 %. Medical pages only. | `UW-DM-002` | loaded, +50 |
| `case-002` | Hypertension in a smoker; an invoice, a payslip and a utility bill. | `UW-HT-002`, `UW-TOB-001` | loaded, +100 |
| `case-003` | No impairments; a blank page and a lab report turned by the PDF's flag. | none | standard |
| `case-004` | A former smoker; epilepsy with no seizure for 14 years; an invoice. | `UW-TOB-003`, `UW-EPI-003` | standard |
| `case-005` | HbA1c in the prediabetes range, no diagnosis; fatty liver with a low FIB-4 index; a recipe. | `UW-PD-001`, `UW-FLD-001` | standard |
| `case-006` | An "underactive thyroid" with a normal TSH; osteoporosis on a bone scan (hip and spine); a passport and a handwritten note. | `UW-THY-001`, `UW-OST-001` | loaded, +25 |
| `case-007` | Mild asthma; a thin melanoma removed in 2023; a recipe. | `UW-AST-001`, `UW-MEL-001` | loaded, +50 |
| `case-008` | "Sugar diabetes" and "high blood pressure" on the form: debits of +25 and +50 add up; a payslip. | `UW-DM-001`, `UW-HT-002` | loaded, +75 |
| `case-009` | Obesity, raised LDL cholesterol, "sleep apnoea"; a handwritten note and a payslip. | `UW-BMI-003`, `UW-LDL-001`, `UW-OSA-002` | loaded, +100 |
| `case-010` | Gout with a raised urate in a smoker; a blank page and an invoice. | `UW-GOUT-002`, `UW-TOB-001` | loaded, +75 |
| `case-011` | Treated depression beside coeliac disease, which the manual has no rule for; the lab report is upside down; a utility bill. | `UW-DEP-001` | refer (`no_matching_rule`) |
| `case-012` | A "heart attack" 30 months ago; LDL reported in mmol/L; a passport. | `UW-LDL-001`, `UW-MI-002` | loaded, +125 |
| `case-013` | Reduced kidney function with albumin in the urine, each with its earlier sample; the lab report is drawn turned. | `UW-CKD-001`, `UW-ALB-001` | loaded, +75 |
| `case-014` | Type 1 diabetes ("diabetes, on insulin since childhood"), HbA1c 9.4 %; a utility bill. | `UW-DI-004` | decline |
| `case-015` | Blood pressure averaging 185 mmHg systolic; a small aortic aneurysm; an invoice. | `UW-HT-004`, `UW-AAA-001` | decline |
| `case-016` | AUDIT score of 24 and raised triglycerides; a passport and a recipe between the medical pages. | `UW-TG-001`, `UW-ALC-003` | decline |
| `case-017` | Severe "COPD" in a smoker of 40 pack-years; a handwritten note. | `UW-TOB-001`, `UW-TOB-002`, `UW-COPD-004` | decline |
| `case-018` | Type 2 diabetes with two HbA1c readings in different bands, the later in mmol/mol: the most recent is rated; a blank page. | `UW-DM-003` | loaded, +100 |
| `case-019` | Three blood pressure readings, one over a year old: the average of the last twelve months is rated; a "blood clot in the leg" 8 months ago; a payslip. | `UW-HT-002`, `UW-VTE-002` | loaded, +100 |
| `case-020` | Multiple sclerosis, which the manual has no rule for; a handwritten note and a recipe. | none | refer (`no_matching_rule`) |
| `case-021` | Rheumatoid arthritis at low activity with a moderate anaemia; a passport and a blank page. | `UW-ANA-001`, `UW-RA-002` | loaded, +75 |
| `case-022` | Two LDL results in different bands, and the manual does not say which to rate; the statement is drawn turned; a utility bill. | `UW-LDL-001`, `UW-LDL-002` | refer (`conflicting_rules`) |

The rules and the verdict in this table are not typed in: the generator works them out (see
"How the expected rules and verdict are worked out") and the table only repeats the answer key.

Documents do not talk like a manual, and about a third of the cases show it: the form gives a
lay name where the statement gives the clinical one (`case-006`, `008`, `009`, `012`, `014`,
`017`, `019`), the lab reports in another unit that the manual converts (`case-012`, `018`),
and an old reading stands beside recent ones (`case-018`, `019`). Each case's pages also say
what the manual's evidence and pitfalls parts ask about its impairments (the second sample for
kidney function, the echocardiogram after a heart attack, a urate taken clear of an attack, no
oxygen at home), so that the keyed verdict is the only one the manual supports.

The pages:

- Every page with text has a real text layer and a small footer saying it is synthetic.
- A blank page has nothing on it.
- A page is turned in one of two ways. By the PDF's rotation flag (`rotation` 90 in `case-003`,
  180 in `case-011`): the content is drawn upright and shown turned. Or truly (`drawn_rotation`
  90 in `case-013` and `case-022`): no flag, the sheet lies on its side and every line of text
  runs up the page, as on a sheet scanned sideways.
- A handwritten doctor's note (`case-006`, `009`, `017`, `020`) is a picture and has no text
  layer. It is drawn in an italic face with uneven lines, not in real handwriting. It names
  nobody, because redaction cannot read a picture, and it states no reading, so the case's
  facts do not depend on it. Its expected label is `attending_physician_statement` (medical).
- A passport is `id_document`; a recipe, like a payslip, a utility bill and a blank page, is
  `other`. The passport prints the applicant's identity number as its personal number. The
  passport number itself is not a planted identifier (the local redaction stand-in does not
  know its shape); it is listed among the strings that may also be redacted.

## The answer key

Each entry (`AnswerKeyEntry` in `packages/synthdata/src/synthdata/model.py`) lists:

- `pages`: for every page, the expected `page_type` and `is_medical` (the enum values and the
  mapping from `packages/contracts`), plus `layout` (what the page really is, since a payslip, a
  recipe and a blank page are all `page_type` `other`), `rotation` (the PDF's flag),
  `drawn_rotation` (how far the content itself is turned) and `has_text_layer`.
- `identifiers`: every planted identifier, its category (`person_name`, `address`, `phone_number`,
  `email_address`, `identity_number`, `policy_number`), the 1-based pages whose text holds it
  (`pages`) and how often each of those pages holds it (`occurrences`). The applicant's six
  identifiers and the physician's name, practice address and practice telephone are all listed.
  Each value sits on one line of the page, exactly as written in the entry.
- `may_also_be_redacted`: strings on the case's pages that a recogniser of names, addresses,
  organisations and numbers may reasonably mask without being wrong: each part of a name, the
  physician's name with "Dr", each part of an address (street, town, postcode), organisations
  and names that read like one (insurer, practice, laboratory, invoice issuer, employer,
  supplier, passport authority and country, a recipe's source), the occupation, the
  nationality, the place of birth, and reference numbers (specimen, invoice, passport). Anything
  else that is masked is over-redaction. Dates (including the date of birth), ages and medical
  terms are never listed: redaction keeps them. The list is a judgement made without the real
  service; what Azure AI Language masks is checked in the Azure session.
- `expected_facts`: the clinical facts the case states, **one entry per fact**, with every
  place that states it. A fact is a `reading` (a reading or a status of one of the manual's
  measures), a `diagnosis` (a condition on file), or `derived` (a figure the manual tells the
  reader to work out: pack-years from cigarettes a day and years smoked, whole months since a
  heart attack or a clot, the average of several blood pressure readings).
  - `statement` is one line in the manual's vocabulary and units, whatever the page says:
    `Type 2 diabetes mellitus: HbA1c 8.3 %` for a page that prints `HbA1c 67 mmol/mol`,
    `Myocardial infarction, diagnosed 2024-03-11` for a form that says `Heart attack`. The
    contracts' query builder (`build_fact_query`) takes it as it stands. A reading of an
    impairment that applies only on a diagnosis carries that impairment's name, because the
    manual has a band of HbA1c for more than one impairment.
  - `places` lists each page that states the fact with `quote`, that page's own words as its
    text holds them, joined by one space (for a blood pressure reading, its row of the table).
    For a derived fact the places are what it is worked out from.
  - `measure` is the key of the manual's measure; `rule_ids` are the rules this fact meets.
    Most facts meet none: a reading in a gap, a reading the manual does not rate because a
    later one or the average is rated, a diagnosis.
- `expected_rule_ids`: every rule the case's figures meet, in the manual's order, rules with no
  debit included.
- `expected_verdict`: `verdict`, with `loading_pct` when it is `loaded` and `system_reasons`
  (the reason `verdict` should store: `no_matching_rule` or `conflicting_rules`) when it is
  `refer`.

### How the facts are meant to be scored

The key lists the facts that bear on the manual: readings of its measures, diagnoses, and the
figures it tells the reader to work out. It does not list everything a page says: glucose, total
cholesterol, height, alcohol units, treatments and family history are left out. So the bake-off
scores **recall** of the listed facts and never penalises an extracted fact that is not listed.
A fact counts once per case, however many pages state it: it is found when it is extracted from
any one of its `places`. Rule recall uses the facts whose `rule_ids` are not empty.

### How the expected rules and verdict are worked out

By code, from what the case's pages state and the rule table (`expected.py`), never by hand:

1. An impairment applies as the rule table says (`applies`): on a diagnosis or on a reading
   alone, and never beside a diagnosis that rules it out (`not_with`). Each diagnosis in the
   case data names its impairment by `impairment_id`, or `None` when the manual has none;
   nothing is matched by wording, so "Heart attack" and "COPD" need no guessing.
2. A reading in another unit is converted with the manual's own numbers (`other_units` of the
   measure) and rounded as the bands are written. Derived figures are worked out as the manual
   says.
3. Where the impairment's evidence part says which of several readings is rated, that one is
   rated and the others meet no rule. This is data (`reading_rules` of the impairment in the
   rule table), and the manual prints it from the same data twice: in part 3 of the section,
   and as one sentence in the definition of each rule of that measure ("Where the file holds
   several readings, the most recent of the last 12 months counts."), because a definition is
   read on its own. The rules are invented with the manual, like its debits:

   | Impairment | Measure | Rated | Within |
   |---|---|---|---|
   | Type 2 and type 1 diabetes | HbA1c | the most recent | 12 months |
   | Hypothyroidism | TSH | the most recent | 18 months |
   | Hypertension | systolic blood pressure | the average | 12 months |
   | Hypertension | diastolic blood pressure | the highest | 12 months |
   | Peripheral artery disease | resting ankle-brachial index | the lowest | |
   | Chronic kidney disease | eGFR | the most recent | |
   | Depression | PHQ-9 score | the most recent | 12 months |
   | Osteoporosis | bone density T-score | the lowest | 36 months |

   For every other measure the manual states no such rule, and a time limit that only its
   prose gives is not applied by the generator.
4. Each rated reading is looked up in the bands of every impairment that applies and has its
   measure. It meets one rule, or lies in a declared gap and meets none.
5. The verdict follows the rules `verdict` applies to a run (stories 2.5 and 2.6):
   - two bands of one measure, where the manual gives no rule to choose by (two LDL results),
     give `refer` with `conflicting_rules`;
   - a diagnosis the manual has no impairment for gives `refer` with `no_matching_rule`;
   - otherwise any decline gives `decline`; otherwise the debits add up and a sum above zero
     gives `loaded`; otherwise `standard`.
   Two rules of one impairment on different measures add where one's definition refers to the
   other (a smoker's status and the lifetime total, `UW-TOB-001` with `UW-TOB-002`); `verdict`
   reads the same reference. The generator refuses a rule table in which two bands of one
   measure refer to each other, and a case that meets two rules of one impairment that neither
   are bands of one measure nor refer to each other.

Each case also states, in its definition, the verdict, loading and rules it is meant to show
(`intends`). Generation fails, naming the case, when the figures give something else, when a
diagnosed impairment has no reading its rules could be met by, when a diagnosis names an
impairment the manual does not have, when a fact's quote is not on its page, or when an
identifier is not found in the PDF's text exactly as often as listed.

The key gives what the manual gives. In `case-018` and `case-019` the file holds several
readings in different bands, and the definition of each rule says which one counts; an agent
that cites two bands all the same is referred by `verdict` and scored wrong.

**No service may read `answer-key/`.** It exists for tests and the eval runner only (architecture
spine AD-17). A test fails if anything under `services/` or `packages/contracts/` names the
answer key or one of its files (`rule-table.json`, `page-set.json`, `case-NNN.json`).

## The scored page set

`answer-key/page-set.json` (`PageSet` in `model.py`) is the whole case set: 22 files, 94
pages, every page scored. Each page is listed with the label fields of its case's entry and a
`kind`: `medical` (62 pages), `non_medical` (20) or `edge` (12: blank, turned, or without a
text layer). A document is `mixed` when it holds medical and non-medical pages together (17
of the 22; in `case-016` they alternate). The set holds, and the generator fails without: a
plain page of each medical page type; at least four pages each of invoice, payslip, passport,
recipe, utility bill, blank page, turned page and handwritten note; a page drawn turned and a
page upside down; and a mixed file.

## The training set

`classifier-training/` holds 46 one-page PDFs, in a folder per `page_type`, at least five per
type; `pages.json` lists them with their layout. They are drawn from five invented people
(`training.py`) who are in no case, with other organisations, figures and recipes. Between
them they have the edge kinds too: a handwritten note, two blank pages, a page drawn turned
and pages turned by the PDF's flag. The folder is not part of the answer key: the training job
reads it. The generator fails when a training page has the text of a page of any case (white
space aside) or the same picture byte for byte, or when two people of the cases and the
training set share a name or an identifier. A blank page has neither text nor picture, so
both sets have one. The page layouts, and so the headings and field labels, are the same as
in the cases: that is what makes a page its type.

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
    and a unit conversion where documents may use another unit. `conversion` is the sentence
    the manual prints; `other_units` gives the same conversion as numbers (multiply, then add)
    for HbA1c in mmol/mol and LDL cholesterol in mmol/L, the two the cases use, and the
    generator fails if the sentence does not hold those numbers.
  - `reading_rules`: which reading of a measure is rated when the file holds several
    (`most_recent`, `average`, `highest`, `lowest`), and within how many months of the
    application. The manual prints one sentence for each in part 3 of the section, and one
    in the definition of every rule of that measure.
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
imports. Case content is data in `cases.py` (the three first cases, whose PDFs never change:
other tests and the demo path use them) and `cases_more.py`; the training people are in
`training.py`; the page layouts are in `render.py`. The manual's content is data in
`manual_rules.py` and the two `manual_impairments` modules; its layout is in `manual.py`.
Generation is deterministic and makes no network call: the same code gives text-identical PDFs,
and byte-identical ones on the same PyMuPDF build, and always byte-identical answer keys. The
rule table records page numbers, which come from the layout, so it is byte-identical on the same
PyMuPDF build and could differ in those numbers on another. A rerun on the same machine shows
nothing in `git status`.

Never edit the files in this folder by hand; a test compares the answer keys, the page set and
the rule table byte for byte, and each PDF's text, page count, page rotations, page sizes and the
number and size of its pictures, with what the generator writes. (A picture's bytes are
compared only between two runs on one machine.)

### Adding or changing a case

1. Read the evidence and pitfalls parts of the manual for every impairment the case is to
   show, and write the pages so that they answer them (see the cases above for how).
2. Add a `CaseDefinition` to `cases_more.py` and to `MORE_CASES`, with the next `case-NNN`. The
   helpers in `case_parts.py` build the applicant and the physician (every identifier follows
   from one number, so give the case a number no other person has), the laboratory panel with
   its ranges and flags, and the clinical figures. Each diagnosis names its impairment by
   `impairment_id` (or `None`), and may give the applicant's own words for the form
   (`declared_as`). A reading the lab report does not carry goes in `findings`, under the label
   of the manual's measure (`AUDIT score`, `FEV1`), with its date; it is printed on the
   physician's statement.
3. State what the case is meant to show in `intends`: the verdict, the loading if it is
   `loaded`, and the rule ids. The generator works all three out from the figures and fails if
   they differ, so a slip in a figure cannot change a case's verdict unnoticed.
4. Run the generator and commit the code with the new output. If a case or a training page is
   removed, delete its files by hand: the generator does not, and a test fails on the leftover.

How the identifiers are kept obviously fictional: telephone numbers are in the 555-0100 to 555-0199
block reserved for fiction, email addresses are at `example.com`, identity numbers start with area
`000` (which no scheme issues), and the towns and the state code `ZZ` do not exist.
