# Synthetic data (CAP-11)

## Underwriting manual

- About 200 pages, rendered as a PDF with prose, tables and cross-references.
- Generated from a structured rule table, which is the ground truth.
- About 40 impairments (e.g. type 2 diabetes by HbA1c, hypertension by blood pressure, BMI bands, cancer history, smoking).
- Each rule: `rule_id`, impairment, threshold, debit (e.g. +50%) or decline, and the public source its threshold is grounded in (ADA, AHA, WHO).

## Case set

- About 20 synthetic medical case PDFs, a few pages each: attending physician statements, lab reports, application forms.
- Each case records its expected facts (with pages), expected `rule_id`s and expected verdict.
- Cases cover all four verdicts.
- Each case carries planted synthetic identifiers (a name, an address, a phone number, an email address, an identity number and a policy number) and records them, so redaction can be checked.

## Classification page set

- Medical pages drawn from the case set.
- Non-medical pages: invoice, payslip, passport, recipe, utility bill.
- Edge pages: blank, rotated, handwritten doctor's note, and a mixed file of medical and non-medical pages.
- Each page records its expected label.
