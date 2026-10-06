# Synthetic test data

Everything in this folder is invented. No person, address, telephone number, email address,
identity number, policy number, organisation or medical reading here is real, and none of the text
was copied from a real record, form or insurer's document. Real personal or health data must never
be added.

| Folder | Holds |
|---|---|
| `cases/` | One PDF per synthetic case (`case-001.pdf`, ...). These are what gets uploaded. |
| `answer-key/cases/` | One JSON file per case with the known right answers. |

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

There are no expected rules or verdicts yet; they arrive with the underwriting manual.

**No service may read `answer-key/`.** It exists for tests and the eval runner only (architecture
spine AD-17). A test fails if anything under `services/` or `packages/contracts/` mentions it.

## Regenerating

From the repository root:

```sh
uv run python -m synthdata
```

The generator is the `synthdata` package (`packages/synthdata/`), a dev tool that no service
imports. Case content is data in `cases.py`; the page layouts are in `render.py`. Generation is
deterministic: the same code gives text-identical PDFs, and byte-identical ones on the same PyMuPDF
build, and always byte-identical answer keys, so a rerun on the same machine shows nothing in
`git status`.
To change or add a case, edit `cases.py`, rerun the command and commit the code and the new output
together. Never edit the files in this folder by hand; a test compares the answer keys byte for byte, and each
PDF's text, page count, page rotations and page sizes, with what the generator
writes.

How the identifiers are kept obviously fictional: telephone numbers are in the 555-0100 to 555-0199
block reserved for fiction, email addresses are at `example.com`, identity numbers start with area
`000` (which no scheme issues), and the towns and the state code `ZZ` do not exist.
