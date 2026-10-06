"""The case definition (what the generator renders) and the answer-key entry (what it records)."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from contracts.base import ContractModel, NonEmptyStr, OneLine, PageNumber
from contracts.enums import PageType
from contracts.rules import is_medical

CaseKey = Annotated[str, StringConstraints(pattern=r"^case-[0-9]{3}$")]
Rotation = Literal[0, 90, 180, 270]
Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]


class IdentifierCategory(StrEnum):
    """The categories redaction removes by default (spine AD-21)."""

    PERSON_NAME = "person_name"
    ADDRESS = "address"
    PHONE_NUMBER = "phone_number"
    EMAIL_ADDRESS = "email_address"
    IDENTITY_NUMBER = "identity_number"
    POLICY_NUMBER = "policy_number"


class PageLayout(StrEnum):
    """The kinds of page the generator can draw; finer than `PageType`."""

    APPLICATION_FORM = "application_form"
    ATTENDING_PHYSICIAN_STATEMENT = "attending_physician_statement"
    LAB_REPORT = "lab_report"
    INVOICE = "invoice"
    PAYSLIP = "payslip"
    UTILITY_BILL = "utility_bill"
    BLANK = "blank"


# The contracts enum has no payslip, utility bill or blank value, so those are `other`.
PAGE_TYPE_OF_LAYOUT: dict[PageLayout, PageType] = {
    PageLayout.APPLICATION_FORM: PageType.APPLICATION_FORM,
    PageLayout.ATTENDING_PHYSICIAN_STATEMENT: PageType.ATTENDING_PHYSICIAN_STATEMENT,
    PageLayout.LAB_REPORT: PageType.LAB_REPORT,
    PageLayout.INVOICE: PageType.INVOICE,
    PageLayout.PAYSLIP: PageType.OTHER,
    PageLayout.UTILITY_BILL: PageType.OTHER,
    PageLayout.BLANK: PageType.OTHER,
}


# ---------------------------------------------------------------------------
# Case definition
# ---------------------------------------------------------------------------


class Applicant(ContractModel):
    name: OneLine
    date_of_birth: date
    sex: Literal["female", "male"]
    occupation: OneLine
    address: OneLine
    phone_number: OneLine
    email_address: OneLine
    identity_number: OneLine
    policy_number: OneLine


class Physician(ContractModel):
    name: OneLine
    clinic_name: OneLine
    clinic_address: OneLine
    clinic_phone_number: OneLine


class Diagnosis(ContractModel):
    condition: OneLine
    diagnosed_on: date
    treatment: OneLine


class BloodPressureReading(ContractModel):
    taken_on: date
    systolic_mmhg: int = Field(gt=0)
    diastolic_mmhg: int = Field(gt=0)


class LabResult(ContractModel):
    test: OneLine
    value: OneLine
    unit: OneLine
    reference_range: OneLine
    flag: Literal["", "H", "L"] = ""


class Clinical(ContractModel):
    diagnoses: tuple[Diagnosis, ...]
    height_cm: int = Field(gt=0)
    weight_kg: int = Field(gt=0)
    smoking_status: OneLine
    alcohol_units_per_week: int = Field(ge=0)
    family_history: OneLine
    blood_pressure: tuple[BloodPressureReading, ...] = Field(min_length=1)
    lab_name: OneLine
    lab_specimen_reference: OneLine
    lab_collected_on: date
    lab_results: tuple[LabResult, ...] = Field(min_length=1)
    physician_remarks: OneLine

    @property
    def bmi(self) -> Decimal:
        """Body mass index, derived so it can never disagree with height and weight."""
        metres = Decimal(self.height_cm) / 100
        return (Decimal(self.weight_kg) / (metres * metres)).quantize(Decimal("0.1"))


class InvoiceLine(ContractModel):
    description: OneLine
    quantity: int = Field(gt=0)
    unit_price: Money


class Invoice(ContractModel):
    issuer: OneLine
    invoice_number: OneLine
    issued_on: date
    due_on: date
    lines: tuple[InvoiceLine, ...] = Field(min_length=1)


class Payslip(ContractModel):
    employer: OneLine
    period_start: date
    period_end: date
    paid_on: date
    gross_pay: Money
    income_tax: Money
    pension: Money


class UtilityBill(ContractModel):
    supplier: OneLine
    period_start: date
    period_end: date
    issued_on: date
    electricity_kwh: int = Field(ge=0)
    rate_per_kwh: Money
    standing_charge: Money


class PageSpec(ContractModel):
    layout: PageLayout
    rotation: Rotation = 0


_LAYOUT_NEEDS = {
    PageLayout.INVOICE: "invoice",
    PageLayout.PAYSLIP: "payslip",
    PageLayout.UTILITY_BILL: "utility_bill",
}


class CaseDefinition(ContractModel):
    """Everything one case PDF is drawn from. All of it is invented."""

    case_id: CaseKey
    summary: OneLine
    # The date the application and the physician's statement are signed; ages count to it.
    document_date: date
    applicant: Applicant
    physician: Physician
    clinical: Clinical
    invoice: Invoice | None = None
    payslip: Payslip | None = None
    utility_bill: UtilityBill | None = None
    pages: tuple[PageSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _pages_have_their_content(self) -> Self:
        for page in self.pages:
            needed = _LAYOUT_NEEDS.get(page.layout)
            if needed is not None and getattr(self, needed) is None:
                raise ValueError(f"a {page.layout} page needs `{needed}`")
        return self

    @model_validator(mode="after")
    def _identifier_values_differ(self) -> Self:
        # The answer key finds an identifier by its value, so two may not share one.
        values = [value for _, value in self.identifiers()]
        if len(values) != len(set(values)):
            raise ValueError("two identifiers share the same value")
        return self

    @property
    def file_name(self) -> str:
        return f"{self.case_id}.pdf"

    @property
    def applicant_age(self) -> int:
        """The applicant's age in whole years on the document date."""
        born, today = self.applicant.date_of_birth, self.document_date
        return (
            today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        )

    def identifiers(self) -> tuple[tuple[IdentifierCategory, str], ...]:
        """Every value in this case that redaction is expected to remove (AD-21)."""
        applicant, physician = self.applicant, self.physician
        return (
            (IdentifierCategory.PERSON_NAME, applicant.name),
            (IdentifierCategory.ADDRESS, applicant.address),
            (IdentifierCategory.PHONE_NUMBER, applicant.phone_number),
            (IdentifierCategory.EMAIL_ADDRESS, applicant.email_address),
            (IdentifierCategory.IDENTITY_NUMBER, applicant.identity_number),
            (IdentifierCategory.POLICY_NUMBER, applicant.policy_number),
            (IdentifierCategory.PERSON_NAME, physician.name),
            (IdentifierCategory.ADDRESS, physician.clinic_address),
            (IdentifierCategory.PHONE_NUMBER, physician.clinic_phone_number),
        )


# ---------------------------------------------------------------------------
# Answer-key entry
# ---------------------------------------------------------------------------


class PlantedIdentifier(ContractModel):
    """One identifier value and every page whose text holds it."""

    category: IdentifierCategory
    # One line: an identifier is always drawn, and searched for, on a single line.
    value: OneLine
    pages: tuple[PageNumber, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _pages_ascending(self) -> Self:
        if list(self.pages) != sorted(set(self.pages)):
            raise ValueError("pages must be ascending with no repeats")
        return self


class ExpectedPage(ContractModel):
    """The right classification of one page."""

    page_number: PageNumber
    page_type: PageType
    is_medical: bool
    # What the page actually is, where `page_type` is too coarse to say (payslip, blank).
    layout: PageLayout
    rotation: Rotation

    @model_validator(mode="after")
    def _labels_agree(self) -> Self:
        # AD-13: `is_medical` comes from the one mapping in the contracts package.
        if self.is_medical != is_medical(self.page_type):
            raise ValueError("is_medical must follow the page_type mapping")
        if self.page_type != PAGE_TYPE_OF_LAYOUT[self.layout]:
            raise ValueError("page_type must follow the layout")
        return self


class AnswerKeyEntry(ContractModel):
    """The known right answers for one case PDF (spine AD-17)."""

    case_id: CaseKey
    file_name: NonEmptyStr
    summary: OneLine
    pages: tuple[ExpectedPage, ...] = Field(min_length=1)
    identifiers: tuple[PlantedIdentifier, ...]

    @model_validator(mode="after")
    def _entry_is_complete(self) -> Self:
        if self.file_name != f"{self.case_id}.pdf":
            raise ValueError("file_name must be `<case_id>.pdf`")
        numbers = [page.page_number for page in self.pages]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("pages must be numbered 1, 2, 3, … with none missing")
        missing = set(IdentifierCategory) - {item.category for item in self.identifiers}
        if missing:
            raise ValueError(f"no planted identifier for: {', '.join(sorted(missing))}")
        for item in self.identifiers:
            if item.pages[-1] > len(numbers):
                raise ValueError(f"an identifier is listed on page {item.pages[-1]}")
        seen = [(item.category, item.value) for item in self.identifiers]
        if len(seen) != len(set(seen)):
            raise ValueError("an identifier is listed twice")
        return self
