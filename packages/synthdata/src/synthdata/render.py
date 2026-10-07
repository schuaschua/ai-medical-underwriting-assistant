"""Draw a case definition as a PDF with a real text layer, one layout per kind of page."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pymupdf

from synthdata.model import CaseDefinition, PageLayout

# A4 in points.
_WIDTH, _HEIGHT = 595.0, 842.0
_LEFT, _RIGHT, _TOP = 56.0, 539.0, 64.0
_VALUE_X = 220.0
_FOOTER_Y = _HEIGHT - 40
# Body text stops this far above the footer line.
_FOOTER_CLEARANCE = 14.0
_REGULAR, _BOLD = "helv", "hebo"
FOOTER = (
    "SYNTHETIC TEST DOCUMENT. Every name, number and reading on this page is invented."
)
# A fixed timestamp, so a regenerated file does not change in git.
PDF_DATE = "D:20261006000000Z"


@dataclass(frozen=True)
class RenderedCase:
    pdf: bytes
    # What was written on each page, one string per page. The answer key is built from this.
    page_texts: tuple[str, ...]


class _Sheet:
    """One page being written top to bottom; it remembers every string it draws."""

    def __init__(self, page: pymupdf.Page) -> None:
        self._page = page
        self._y = _TOP
        self._floor = _FOOTER_Y - _FOOTER_CLEARANCE
        self.written: list[str] = []

    def put(
        self,
        x: float,
        text: str,
        *,
        size: float = 10,
        bold: bool = False,
        limit: float = _RIGHT,
    ) -> None:
        """Draw `text` on the current line without moving down.

        Content that would run into the footer, or past `limit` on the right, is an
        error: it would overlap other text and corrupt the extracted text layer.
        """
        font = _BOLD if bold else _REGULAR
        if self._y > self._floor:
            raise ValueError(f"the page is full; no room for {text!r}")
        if x + pymupdf.get_text_length(text, font, size) > limit:
            raise ValueError(f"{text!r} is too wide for its place on the page")
        self._page.insert_text((x, self._y), text, fontsize=size, fontname=font)
        self.written.append(text)

    def down(self, points: float = 16) -> None:
        self._y += points

    def line(self, text: str, *, size: float = 10, bold: bool = False) -> None:
        self.put(_LEFT, text, size=size, bold=bold)
        self.down(size + 6)

    def title(self, organisation: str, title: str) -> None:
        self.line(organisation, size=11, bold=True)
        self.down(6)
        self.line(title, size=17, bold=True)
        self.rule()

    def section(self, text: str) -> None:
        self.down(8)
        self.line(text, size=11, bold=True)

    def field(self, label: str, value: str) -> None:
        self.put(_LEFT, label, limit=_VALUE_X)
        self.put(_VALUE_X, value)
        self.down()

    def row(
        self, columns: Sequence[float], cells: Sequence[str], *, bold: bool = False
    ) -> None:
        limits = (*columns[1:], _RIGHT)
        for x, cell, limit in zip(columns, cells, limits, strict=True):
            self.put(x, cell, bold=bold, limit=limit)
        self.down()

    def table(
        self,
        columns: Sequence[float],
        header: Sequence[str],
        rows: Sequence[Sequence[str]],
    ) -> None:
        self.row(columns, header, bold=True)
        self._y -= 10
        self.rule()
        for cells in rows:
            self.row(columns, cells)

    def rule(self) -> None:
        self._page.draw_line((_LEFT, self._y), (_RIGHT, self._y), width=0.7)
        self.down(18)

    def paragraph(self, text: str) -> None:
        """Wrapped prose. Identifiers never go here: a line break would split one."""
        current = ""
        for word in text.split():
            longer = f"{current} {word}".strip()
            if (
                current
                and pymupdf.get_text_length(longer, _REGULAR, 10) > _RIGHT - _LEFT
            ):
                self.line(current)
                current = word
            else:
                current = longer
        if current:
            self.line(current)

    def footer(self) -> None:
        self._y = self._floor = _FOOTER_Y
        self.put(_LEFT, FOOTER, size=7)


def _money(amount: Decimal) -> str:
    return f"{amount:,.2f}"


def _doctor(case: CaseDefinition) -> str:
    return f"Dr {case.physician.name}"


def _application_form(sheet: _Sheet, case: CaseDefinition) -> None:
    applicant, clinical = case.applicant, case.clinical
    sheet.title("Example Mutual Life Assurance", "Life Insurance Application Form")
    sheet.section("1. Applicant")
    sheet.field("Full name", applicant.name)
    sheet.field("Date of birth", applicant.date_of_birth.isoformat())
    sheet.field("Age", f"{case.applicant_age} years")
    sheet.field("Sex", applicant.sex.capitalize())
    sheet.field("Occupation", applicant.occupation)
    sheet.field("Home address", applicant.address)
    sheet.field("Telephone", applicant.phone_number)
    sheet.field("Email", applicant.email_address)
    sheet.field("National identity number", applicant.identity_number)
    sheet.field("Policy number", applicant.policy_number)
    sheet.section("2. Health declaration")
    sheet.field("Height", f"{clinical.height_cm} cm")
    sheet.field("Weight", f"{clinical.weight_kg} kg")
    sheet.field("Body mass index (BMI)", f"{clinical.bmi} kg/m2")
    sheet.field("Smoking status", clinical.smoking_status)
    sheet.field("Alcohol", f"{clinical.alcohol_units_per_week} units a week")
    sheet.field(
        "Diagnosed medical conditions", "Yes" if clinical.diagnoses else "None declared"
    )
    for diagnosis in clinical.diagnoses:
        sheet.field(
            "  Condition",
            f"{diagnosis.condition}, diagnosed {diagnosis.diagnosed_on.isoformat()}",
        )
        sheet.field("  Current treatment", diagnosis.treatment)
    sheet.field("Family history", clinical.family_history)
    sheet.section("3. Attending physician")
    sheet.field("Physician", _doctor(case))
    sheet.field("Practice", case.physician.clinic_name)
    sheet.field("Practice telephone", case.physician.clinic_phone_number)
    sheet.section("4. Declaration")
    sheet.paragraph(
        "I declare that the answers given in this application are true and complete "
        "to the best of my knowledge, and I authorise my attending physician to "
        "release medical information to the insurer for underwriting."
    )
    sheet.down(6)
    sheet.field("Signed by", applicant.name)
    sheet.field("Date", case.document_date.isoformat())


def _attending_physician_statement(sheet: _Sheet, case: CaseDefinition) -> None:
    applicant, physician, clinical = case.applicant, case.physician, case.clinical
    sheet.line(physician.clinic_name, size=11, bold=True)
    sheet.line(physician.clinic_address)
    sheet.line(f"Telephone {physician.clinic_phone_number}")
    sheet.down(6)
    sheet.line("Attending Physician Statement", size=17, bold=True)
    sheet.rule()
    sheet.section("Patient")
    sheet.field("Patient name", applicant.name)
    sheet.field("Date of birth", applicant.date_of_birth.isoformat())
    sheet.field("Age", f"{case.applicant_age} years")
    sheet.field("Policy number", applicant.policy_number)
    sheet.section("Diagnoses and treatment")
    if clinical.diagnoses:
        sheet.table(
            (_LEFT, 240, 340),
            ("Diagnosis", "Diagnosed", "Current treatment"),
            [
                (item.condition, item.diagnosed_on.isoformat(), item.treatment)
                for item in clinical.diagnoses
            ],
        )
    else:
        sheet.line("No chronic conditions diagnosed. No regular medication.")
    sheet.section("Blood pressure readings")
    sheet.table(
        (_LEFT, 240, 340),
        ("Date", "Systolic (mmHg)", "Diastolic (mmHg)"),
        [
            (
                reading.taken_on.isoformat(),
                str(reading.systolic_mmhg),
                str(reading.diastolic_mmhg),
            )
            for reading in clinical.blood_pressure
        ],
    )
    sheet.section("Examination and history")
    sheet.field("Height", f"{clinical.height_cm} cm")
    sheet.field("Weight", f"{clinical.weight_kg} kg")
    sheet.field("Body mass index (BMI)", f"{clinical.bmi} kg/m2")
    sheet.field("Smoking status", clinical.smoking_status)
    sheet.field("Alcohol", f"{clinical.alcohol_units_per_week} units a week")
    latest = clinical.lab_results[0]
    sheet.field(
        f"Latest {latest.test}",
        f"{latest.value} {latest.unit} on {clinical.lab_collected_on.isoformat()}",
    )
    sheet.section("Physician's remarks")
    sheet.paragraph(clinical.physician_remarks)
    sheet.down(10)
    sheet.field("Completed by", _doctor(case))
    sheet.field("Date", case.document_date.isoformat())


def _lab_report(sheet: _Sheet, case: CaseDefinition) -> None:
    applicant, clinical = case.applicant, case.clinical
    reported_on = clinical.lab_collected_on + timedelta(days=1)
    sheet.title(clinical.lab_name, "Laboratory Report")
    sheet.field("Patient name", applicant.name)
    sheet.field("Date of birth", applicant.date_of_birth.isoformat())
    sheet.field("Age", f"{case.applicant_age} years")
    sheet.field("Sex", applicant.sex.capitalize())
    sheet.field("Requesting physician", _doctor(case))
    sheet.field("Specimen reference", clinical.lab_specimen_reference)
    sheet.field("Specimen collected", clinical.lab_collected_on.isoformat())
    sheet.field("Report issued", reported_on.isoformat())
    sheet.section("Results")
    sheet.table(
        (_LEFT, 220, 280, 390, 500),
        ("Test", "Result", "Unit", "Reference range", "Flag"),
        [
            (item.test, item.value, item.unit, item.reference_range, item.flag)
            for item in clinical.lab_results
        ],
    )
    sheet.down(8)
    sheet.line("H = above the reference range. L = below the reference range.")
    sheet.line("Fasting specimen. Results apply to the specimen as received.")


def _invoice(sheet: _Sheet, case: CaseDefinition) -> None:
    invoice = case.invoice
    if (
        invoice is None
    ):  # CaseDefinition already rejects this; the check narrows the type.
        raise ValueError("an invoice page needs `invoice`")
    sheet.title(invoice.issuer, "Invoice")
    sheet.field("Invoice number", invoice.invoice_number)
    sheet.field("Invoice date", invoice.issued_on.isoformat())
    sheet.field("Payment due", invoice.due_on.isoformat())
    sheet.section("Bill to")
    sheet.line(case.applicant.name)
    sheet.line(case.applicant.address)
    sheet.section("Items")
    total = Decimal(0)
    rows: list[tuple[str, str, str, str]] = []
    for item in invoice.lines:
        amount = item.unit_price * item.quantity
        total += amount
        rows.append(
            (
                item.description,
                str(item.quantity),
                _money(item.unit_price),
                _money(amount),
            )
        )
    columns = (_LEFT, 300, 370, 460)
    sheet.table(columns, ("Description", "Quantity", "Unit price", "Amount"), rows)
    sheet.down(6)
    sheet.row(columns, ("Total due", "", "", _money(total)), bold=True)
    sheet.down(10)
    sheet.line(
        f"Payment is due by {invoice.due_on.isoformat()}. Thank you for your order."
    )


def _payslip(sheet: _Sheet, case: CaseDefinition) -> None:
    payslip = case.payslip
    if (
        payslip is None
    ):  # CaseDefinition already rejects this; the check narrows the type.
        raise ValueError("a payslip page needs `payslip`")
    net_pay = payslip.gross_pay - payslip.income_tax - payslip.pension
    sheet.title(payslip.employer, "Payslip")
    sheet.field("Employee", case.applicant.name)
    sheet.field("National identity number", case.applicant.identity_number)
    sheet.field("Job title", case.applicant.occupation)
    sheet.field(
        "Pay period",
        f"{payslip.period_start.isoformat()} to {payslip.period_end.isoformat()}",
    )
    sheet.field("Pay date", payslip.paid_on.isoformat())
    sheet.section("Pay and deductions")
    columns = (_LEFT, 400)
    sheet.table(
        columns,
        ("Item", "Amount"),
        [
            ("Basic salary", _money(payslip.gross_pay)),
            ("Income tax", f"-{_money(payslip.income_tax)}"),
            ("Pension contribution", f"-{_money(payslip.pension)}"),
        ],
    )
    sheet.down(6)
    sheet.row(columns, ("Net pay", _money(net_pay)), bold=True)
    sheet.down(10)
    sheet.line("Net pay has been sent to the bank account held on file.")


def _utility_bill(sheet: _Sheet, case: CaseDefinition) -> None:
    bill = case.utility_bill
    if bill is None:  # CaseDefinition already rejects this; the check narrows the type.
        raise ValueError("a utility bill page needs `utility_bill`")
    energy = (bill.rate_per_kwh * bill.electricity_kwh).quantize(Decimal("0.01"))
    usage = (
        f"Electricity used: {bill.electricity_kwh} kWh at "
        f"{_money(bill.rate_per_kwh)} per kWh"
    )
    sheet.title(bill.supplier, "Electricity Bill")
    sheet.field("Account holder", case.applicant.name)
    sheet.field("Supply address", case.applicant.address)
    sheet.field("Bill sent to", case.applicant.email_address)
    sheet.field("Bill date", bill.issued_on.isoformat())
    sheet.field(
        "Billing period",
        f"{bill.period_start.isoformat()} to {bill.period_end.isoformat()}",
    )
    sheet.section("Charges")
    columns = (_LEFT, 400)
    sheet.table(
        columns,
        ("Item", "Amount"),
        [
            (usage, _money(energy)),
            ("Standing charge", _money(bill.standing_charge)),
        ],
    )
    sheet.down(6)
    sheet.row(columns, ("Total due", _money(energy + bill.standing_charge)), bold=True)
    sheet.down(10)
    due_on = bill.issued_on + timedelta(days=14)
    sheet.line(f"Please pay by {due_on.isoformat()}.")


_DRAW: dict[PageLayout, Callable[[_Sheet, CaseDefinition], None]] = {
    PageLayout.APPLICATION_FORM: _application_form,
    PageLayout.ATTENDING_PHYSICIAN_STATEMENT: _attending_physician_statement,
    PageLayout.LAB_REPORT: _lab_report,
    PageLayout.INVOICE: _invoice,
    PageLayout.PAYSLIP: _payslip,
    PageLayout.UTILITY_BILL: _utility_bill,
}


def render_case(case: CaseDefinition) -> RenderedCase:
    """Render every page of `case`.

    The same case always gives the same text, and the same bytes on the same PyMuPDF build.
    """
    document = pymupdf.open()  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    page_texts: list[str] = []
    for spec in case.pages:
        page = document.new_page(width=_WIDTH, height=_HEIGHT)
        sheet = _Sheet(page)
        if spec.layout is not PageLayout.BLANK:
            _DRAW[spec.layout](sheet, case)
            sheet.footer()
        # Rotated after drawing, like an upright sheet fed into a scanner sideways.
        page.set_rotation(spec.rotation)  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        page_texts.append("\n".join(sheet.written))
    document.set_metadata(
        {
            "title": f"Synthetic case {case.case_id}",
            "subject": "Synthetic test document. All details are invented.",
            "creator": "synthdata",
            "producer": "synthdata",
            "creationDate": PDF_DATE,
            "modDate": PDF_DATE,
        }
    )
    # `no_new_id` leaves out the random file id, the last thing that would differ per run.
    pdf: bytes = document.tobytes(  # type: ignore[no-untyped-call]  # as above
        deflate=True, no_new_id=True
    )
    document.close()  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    return RenderedCase(pdf=pdf, page_texts=tuple(page_texts))
