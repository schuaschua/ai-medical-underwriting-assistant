"""Draw a case definition as a PDF with a real text layer, one layout per kind of page."""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pymupdf

from synthdata.model import (
    CaseDefinition,
    Diagnosis,
    DrawnRotation,
    Finding,
    HandwrittenNote,
    PageLayout,
)

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
    # The SHA-256 of every picture drawn on each page; a page of text has none.
    page_pictures: tuple[tuple[str, ...], ...] = ()


class _Sheet:
    """One page being written top to bottom; it remembers every string it draws."""

    def __init__(self, page: pymupdf.Page, turn: DrawnRotation = 0) -> None:
        self._page = page
        # The sheet is laid out upright and, when turned, drawn a quarter turn
        # anticlockwise on a page that is as wide as the sheet is tall.
        self._turn = turn
        self._y = _TOP
        self._floor = _FOOTER_Y - _FOOTER_CLEARANCE
        self.written: list[str] = []

    def _at(self, x: float, y: float) -> tuple[float, float]:
        """Where a point of the upright sheet lies on the page."""
        return (y, _WIDTH - x) if self._turn else (x, y)

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
        self._page.insert_text(
            self._at(x, self._y),
            text,
            fontsize=size,
            fontname=font,
            rotate=self._turn,
        )
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
        self._page.draw_line(
            self._at(_LEFT, self._y), self._at(_RIGHT, self._y), width=0.7
        )
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


def printed_label(label: str) -> str:
    """A measure's label as a page prints it at the start of a line: `Serum urate`."""
    return label[:1].upper() + label[1:]


def finding_label(finding: Finding) -> str:
    """A finding's label as the physician's statement prints it: `Bone density T-score (hip)`."""
    label = printed_label(finding.label)
    return f"{label} ({finding.site})" if finding.site else label


def finding_value(finding: Finding) -> str:
    """A finding's value as the physician's statement prints it: `24 points`."""
    value = f"{finding.value} {finding.unit}" if finding.unit else finding.value
    if finding.taken_on is None:
        return value
    return f"{value} on {finding.taken_on.isoformat()}"


def declared_condition(diagnosis: Diagnosis) -> str:
    """A condition as the applicant declares it on the form, in their own words if they differ."""
    name = diagnosis.declared_as or diagnosis.condition
    return f"{name}, diagnosed {diagnosis.diagnosed_on.isoformat()}"


def _application_form(sheet: _Sheet, case: CaseDefinition) -> None:
    applicant, clinical = case.applicant, case.clinical
    sheet.title(case.insurer, "Life Insurance Application Form")
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
        sheet.field("  Condition", declared_condition(diagnosis))
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
    if clinical.findings:
        sheet.section("Other findings")
        for finding in clinical.findings:
            sheet.field(finding_label(finding), finding_value(finding))
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


def _passport(sheet: _Sheet, case: CaseDefinition) -> None:
    passport, applicant = case.passport, case.applicant
    if (
        passport is None
    ):  # CaseDefinition already rejects this; the check narrows the type.
        raise ValueError("a passport page needs `passport`")
    sheet.title(passport.country, "Passport")
    sheet.field("Type", "P")
    sheet.field("Passport number", passport.number)
    sheet.field("Name", applicant.name)
    sheet.field("Nationality", passport.nationality)
    sheet.field("Date of birth", applicant.date_of_birth.isoformat())
    sheet.field("Sex", applicant.sex.capitalize())
    sheet.field("Place of birth", passport.place_of_birth)
    sheet.field("Personal number", applicant.identity_number)
    sheet.field("Date of issue", passport.issued_on.isoformat())
    sheet.field("Date of expiry", passport.expires_on.isoformat())
    sheet.field("Issuing authority", passport.authority)
    sheet.down(10)
    sheet.paragraph(
        f"This passport remains the property of the {passport.country}. The holder "
        "should sign it on receipt and keep it in a safe place."
    )
    sheet.down(6)
    sheet.field("Holder's signature", applicant.name)


def _recipe(sheet: _Sheet, case: CaseDefinition) -> None:
    recipe = case.recipe
    if (
        recipe is None
    ):  # CaseDefinition already rejects this; the check narrows the type.
        raise ValueError("a recipe page needs `recipe`")
    sheet.title(recipe.source, recipe.title)
    sheet.field("Serves", str(recipe.serves))
    sheet.field("Time", f"{recipe.minutes} minutes")
    sheet.section("Ingredients")
    for ingredient in recipe.ingredients:
        sheet.line(ingredient)
    sheet.section("Method")
    for number, step in enumerate(recipe.steps, start=1):
        sheet.paragraph(f"{number}. {step}")
        sheet.down(4)


# The pen of the handwritten note: an italic face in blue ink, each line a little
# off the straight. The page is then kept as a picture only.
_PEN_FONT, _PEN_SIZE, _PEN_INK = "tiit", 19.0, (0.08, 0.12, 0.45)
_PEN_LEFT, _PEN_TOP, _PEN_LINE = 70.0, 110.0, 44.0
_PICTURE_DPI = 100


def _wobble(text: str, salt: str) -> float:
    """A number from -1 to 1 that is always the same for the same line."""
    digest = hashlib.sha256(f"{salt}:{text}".encode()).digest()
    return digest[0] / 127.5 - 1


def _handwritten_picture(note: HandwrittenNote) -> bytes:
    """The note as a PNG: written on a sheet of its own, then photographed."""
    scratch = pymupdf.open()  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    sheet = scratch.new_page(width=_WIDTH, height=_HEIGHT)
    for index, text in enumerate(note.lines):
        start = pymupdf.Point(  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            _PEN_LEFT + 9 * _wobble(text, "indent"), _PEN_TOP + index * _PEN_LINE
        )
        # A slant of the letters and a slope of the line, both slight.
        hand = pymupdf.Matrix(  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            1,
            0.02 * _wobble(text, "slope"),
            -0.12 + 0.05 * _wobble(text, "slant"),
            1,
            0,
            0,
        )
        sheet.insert_text(
            start,
            text,
            fontsize=_PEN_SIZE + _wobble(text, "size"),
            fontname=_PEN_FONT,
            color=_PEN_INK,
            morph=(start, hand),
        )
    picture: bytes = sheet.get_pixmap(dpi=_PICTURE_DPI).tobytes("png")  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    scratch.close()  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    return picture


_DRAW: dict[PageLayout, Callable[[_Sheet, CaseDefinition], None]] = {
    PageLayout.APPLICATION_FORM: _application_form,
    PageLayout.ATTENDING_PHYSICIAN_STATEMENT: _attending_physician_statement,
    PageLayout.LAB_REPORT: _lab_report,
    PageLayout.INVOICE: _invoice,
    PageLayout.PAYSLIP: _payslip,
    PageLayout.UTILITY_BILL: _utility_bill,
    PageLayout.PASSPORT: _passport,
    PageLayout.RECIPE: _recipe,
}


def render_page(case: CaseDefinition, page_number: int) -> RenderedCase:
    """One page of `case` as a PDF of its own: what a classifier is trained on (AD-13)."""
    spec = case.pages[page_number - 1]
    return render_case(
        case.model_copy(update={"pages": (spec,)}),
        title=f"Synthetic training page {case.case_id}, {spec.layout}",
    )


def render_case(case: CaseDefinition, *, title: str | None = None) -> RenderedCase:
    """Render every page of `case`.

    The same case always gives the same text, and the same bytes on the same PyMuPDF build.
    """
    document = pymupdf.open()  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
    page_texts: list[str] = []
    page_pictures: list[tuple[str, ...]] = []
    for spec in case.pages:
        # A sheet drawn turned lies on its side: the page is as wide as the sheet is tall.
        width, height = (_HEIGHT, _WIDTH) if spec.drawn_rotation else (_WIDTH, _HEIGHT)
        page = document.new_page(width=width, height=height)
        sheet = _Sheet(page, spec.drawn_rotation)
        pictures: tuple[str, ...] = ()
        if spec.layout is PageLayout.HANDWRITTEN_NOTE:
            if case.note is None:  # CaseDefinition already rejects this.
                raise ValueError("a handwritten note page needs `note`")
            picture = _handwritten_picture(case.note)
            page.insert_image(page.rect, stream=picture)  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
            pictures = (hashlib.sha256(picture).hexdigest(),)
        elif spec.layout is not PageLayout.BLANK:
            _DRAW[spec.layout](sheet, case)
            sheet.footer()
        # Rotated after drawing, like an upright sheet fed into a scanner sideways.
        page.set_rotation(spec.rotation)  # type: ignore[no-untyped-call]  # PyMuPDF does not annotate this call
        page_texts.append("\n".join(sheet.written))
        page_pictures.append(pictures)
    document.set_metadata(
        {
            "title": title or f"Synthetic case {case.case_id}",
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
    return RenderedCase(
        pdf=pdf, page_texts=tuple(page_texts), page_pictures=tuple(page_pictures)
    )
