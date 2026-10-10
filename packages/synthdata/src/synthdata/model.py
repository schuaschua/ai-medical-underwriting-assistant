"""The case definition (what the generator renders) and the answer-key entry (what it records)."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from contracts.base import ContractModel, NonEmptyStr, OneLine, PageNumber, Percent
from contracts.enums import PageType, SystemReason, Verdict
from contracts.rules import RuleId, is_medical

CaseKey = Annotated[str, StringConstraints(pattern=r"^case-[0-9]{3}$")]
# A case, or one person of the classifier's training set (`train-001`).
DocumentKey = Annotated[str, StringConstraints(pattern=r"^(case|train)-[0-9]{3}$")]
Rotation = Literal[0, 90, 180, 270]
# How far a page's content is really turned on its sheet: upright, or a quarter turn.
DrawnRotation = Literal[0, 90]
UPSIDE_DOWN = 180
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
    PASSPORT = "passport"
    RECIPE = "recipe"
    HANDWRITTEN_NOTE = "handwritten_note"


# The contracts enum has no payslip, utility bill, recipe or blank value, so those are
# `other`. A doctor's handwritten note is a statement by the attending physician.
PAGE_TYPE_OF_LAYOUT: dict[PageLayout, PageType] = {
    PageLayout.APPLICATION_FORM: PageType.APPLICATION_FORM,
    PageLayout.ATTENDING_PHYSICIAN_STATEMENT: PageType.ATTENDING_PHYSICIAN_STATEMENT,
    PageLayout.LAB_REPORT: PageType.LAB_REPORT,
    PageLayout.INVOICE: PageType.INVOICE,
    PageLayout.PAYSLIP: PageType.OTHER,
    PageLayout.UTILITY_BILL: PageType.OTHER,
    PageLayout.BLANK: PageType.OTHER,
    PageLayout.PASSPORT: PageType.ID_DOCUMENT,
    PageLayout.RECIPE: PageType.OTHER,
    PageLayout.HANDWRITTEN_NOTE: PageType.ATTENDING_PHYSICIAN_STATEMENT,
}
# The layouts that are drawn as a picture, or not at all: their pages have no text layer.
LAYOUTS_WITHOUT_TEXT = frozenset({PageLayout.BLANK, PageLayout.HANDWRITTEN_NOTE})


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
    # The condition as the physician writes it, which need not be the manual's name.
    condition: OneLine
    diagnosed_on: date
    treatment: OneLine
    # The `impairment_id` of the manual's impairment this is, said outright and never
    # guessed from the wording; None for a condition the manual has no impairment for.
    impairment: OneLine | None
    # The applicant's own words on the application form, where they differ.
    declared_as: OneLine | None = None


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


class Finding(ContractModel):
    """One more reading on the physician's statement, named as the manual names its measure."""

    label: OneLine
    value: OneLine
    # None for a ratio or an index, which the manual writes without a unit.
    unit: OneLine | None = None
    taken_on: date | None = None
    # Where on the body it was measured, when a report gives one figure per site.
    site: OneLine | None = None


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
    findings: tuple[Finding, ...] = ()

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


class Passport(ContractModel):
    country: OneLine
    authority: OneLine
    nationality: OneLine
    # Not a planted identifier: the identity number is printed as the personal number.
    number: OneLine
    place_of_birth: OneLine
    issued_on: date
    expires_on: date


class Recipe(ContractModel):
    source: OneLine
    title: OneLine
    serves: int = Field(gt=0)
    minutes: int = Field(gt=0)
    ingredients: tuple[OneLine, ...] = Field(min_length=1)
    steps: tuple[OneLine, ...] = Field(min_length=1)


class HandwrittenNote(ContractModel):
    """A doctor's note, drawn as a picture. It names nobody: redaction cannot read a picture."""

    lines: tuple[OneLine, ...] = Field(min_length=1)


class PageSpec(ContractModel):
    layout: PageLayout
    # The PDF's rotation flag: the content is drawn upright and shown turned.
    rotation: Rotation = 0
    # The content itself is drawn turned, as on a sheet scanned sideways; no flag is set.
    drawn_rotation: DrawnRotation = 0

    @model_validator(mode="after")
    def _turned_one_way(self) -> Self:
        if self.rotation and self.drawn_rotation:
            raise ValueError("a page is turned by its flag or in its drawing, not both")
        if self.drawn_rotation and self.layout in LAYOUTS_WITHOUT_TEXT:
            raise ValueError(f"a {self.layout} page is not drawn turned")
        return self


class Intent(ContractModel):
    """What a case is written to show. The generator checks it against the rule table."""

    verdict: Verdict
    loading_pct: Percent | None = None
    rule_ids: tuple[RuleId, ...] = ()


_LAYOUT_NEEDS = {
    PageLayout.INVOICE: "invoice",
    PageLayout.PAYSLIP: "payslip",
    PageLayout.UTILITY_BILL: "utility_bill",
    PageLayout.PASSPORT: "passport",
    PageLayout.RECIPE: "recipe",
    PageLayout.HANDWRITTEN_NOTE: "note",
}
DEFAULT_INSURER = "Example Mutual Life Assurance"


class CaseDefinition(ContractModel):
    """Everything one case PDF is drawn from. All of it is invented."""

    case_id: DocumentKey
    summary: OneLine
    # The date the application and the physician's statement are signed; ages count to it.
    document_date: date
    insurer: OneLine = DEFAULT_INSURER
    applicant: Applicant
    physician: Physician
    clinical: Clinical
    invoice: Invoice | None = None
    payslip: Payslip | None = None
    utility_bill: UtilityBill | None = None
    passport: Passport | None = None
    recipe: Recipe | None = None
    note: HandwrittenNote | None = None
    pages: tuple[PageSpec, ...] = Field(min_length=1)
    # Every case states it; a person of the training set has no verdict.
    intends: Intent | None = None

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


class PageCount(ContractModel):
    page_number: PageNumber
    count: int = Field(ge=1)


class PlantedIdentifier(ContractModel):
    """One identifier value, every page whose text holds it, and how often each page does."""

    category: IdentifierCategory
    # One line: an identifier is always drawn, and searched for, on a single line.
    value: OneLine
    pages: tuple[PageNumber, ...] = Field(min_length=1)
    occurrences: tuple[PageCount, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _pages_ascending(self) -> Self:
        if list(self.pages) != sorted(set(self.pages)):
            raise ValueError("pages must be ascending with no repeats")
        if self.pages != tuple(item.page_number for item in self.occurrences):
            raise ValueError("occurrences must count each listed page once, in order")
        return self


class ExpectedPage(ContractModel):
    """The right classification of one page."""

    page_number: PageNumber
    page_type: PageType
    is_medical: bool
    # What the page actually is, where `page_type` is too coarse to say (payslip, blank).
    layout: PageLayout
    rotation: Rotation
    drawn_rotation: DrawnRotation
    # False for a blank page and for a page that is a picture.
    has_text_layer: bool

    @model_validator(mode="after")
    def _labels_agree(self) -> Self:
        # AD-13: `is_medical` comes from the one mapping in the contracts package.
        if self.is_medical != is_medical(self.page_type):
            raise ValueError("is_medical must follow the page_type mapping")
        if self.page_type != PAGE_TYPE_OF_LAYOUT[self.layout]:
            raise ValueError("page_type must follow the layout")
        if self.has_text_layer == (self.layout in LAYOUTS_WITHOUT_TEXT):
            raise ValueError("has_text_layer must follow the layout")
        return self


class FactKind(StrEnum):
    # A reading or a status of one of the manual's measures, as a page states it.
    READING = "reading"
    # A condition on file. It meets no rule by itself: a rule is a band of a measure.
    DIAGNOSIS = "diagnosis"
    # A figure the manual tells the reader to work out from what the pages state:
    # pack-years, the months since an event, the average of several readings.
    DERIVED = "derived"


class FactPlace(ContractModel):
    """Where a fact is stated: the page, and the page's own words for it."""

    page_number: PageNumber
    # As the page text holds them, joined by one space; need not be the manual's words.
    quote: OneLine


class ExpectedFact(ContractModel):
    """One clinical fact a case states, listed once however many pages state it (AD-17)."""

    kind: FactKind
    # In the manual's vocabulary and units; the contracts' query builder turns it
    # into a search.
    statement: OneLine
    # The key of the manual's measure, for a reading and a derived figure.
    measure: OneLine | None = None
    # The rules this fact meets, given what else is on file. Often none.
    rule_ids: tuple[RuleId, ...] = ()
    # Every place that states it; for a derived figure, what it is worked out from.
    places: tuple[FactPlace, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _a_reading_has_its_measure(self) -> Self:
        if (self.kind is FactKind.DIAGNOSIS) != (self.measure is None):
            raise ValueError("a reading names its measure and a diagnosis does not")
        if self.kind is FactKind.DIAGNOSIS and self.rule_ids:
            raise ValueError("a diagnosis alone meets no rule")
        return self


class ExpectedVerdict(ContractModel):
    """The verdict the rule table gives a case, by the rules of stories 2.5 and 2.6."""

    verdict: Verdict
    # The sum of the debits, for `loaded` only.
    loading_pct: Percent | None = None
    # Why the case goes to a person, for `refer` only.
    system_reasons: tuple[SystemReason, ...] = ()

    @model_validator(mode="after")
    def _parts_follow_the_verdict(self) -> Self:
        if (self.verdict is Verdict.LOADED) != bool(self.loading_pct):
            raise ValueError(
                "a loading above zero goes with `loaded`, and only with it"
            )
        if (self.verdict is Verdict.REFER) != bool(self.system_reasons):
            raise ValueError("a system reason goes with `refer`, and only with it")
        return self


class AnswerKeyEntry(ContractModel):
    """The known right answers for one case PDF (spine AD-17)."""

    case_id: CaseKey
    file_name: NonEmptyStr
    summary: OneLine
    pages: tuple[ExpectedPage, ...] = Field(min_length=1)
    identifiers: tuple[PlantedIdentifier, ...]
    # Strings redaction may also mask without being wrong: parts of the names,
    # organisations, towns and reference numbers. Only those some page holds.
    may_also_be_redacted: tuple[OneLine, ...]
    expected_facts: tuple[ExpectedFact, ...]
    # Every rule the case's figures meet, in the manual's order.
    expected_rule_ids: tuple[RuleId, ...]
    expected_verdict: ExpectedVerdict

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
        medical = {page.page_number for page in self.pages if page.is_medical}
        of_facts = {rule for fact in self.expected_facts for rule in fact.rule_ids}
        for fact in self.expected_facts:
            for place in fact.places:
                if place.page_number not in medical:
                    raise ValueError(f"a fact is listed on page {place.page_number}")
        stated = [(fact.kind, fact.statement) for fact in self.expected_facts]
        if len(stated) != len(set(stated)):
            raise ValueError("a fact is listed twice")
        if of_facts != set(self.expected_rule_ids):
            raise ValueError("the expected rules must be the rules the facts meet")
        return self


# ---------------------------------------------------------------------------
# The scored page set and the training set (spine AD-13, AD-17)
# ---------------------------------------------------------------------------


class PageKind(StrEnum):
    MEDICAL = "medical"
    NON_MEDICAL = "non_medical"
    # Blank, turned, or handwritten: hard to read whatever it holds.
    EDGE = "edge"


class PageSetPage(ExpectedPage):
    kind: PageKind


class PageSetDocument(ContractModel):
    """One file of the scored page set: a case PDF, each page with its label."""

    case_id: CaseKey
    file_name: NonEmptyStr
    # Whether the file holds medical and non-medical pages together.
    mixed: bool
    pages: tuple[PageSetPage, ...] = Field(min_length=1)


# What the scored set must hold (synthetic-data.md, "Classification page set").
NON_MEDICAL_LAYOUTS = frozenset(
    {
        PageLayout.INVOICE,
        PageLayout.PAYSLIP,
        PageLayout.PASSPORT,
        PageLayout.RECIPE,
        PageLayout.UTILITY_BILL,
    }
)


# How many pages of each non-medical and each edge kind the scored set needs at least:
# one or two would make a contender's score on that kind a matter of luck.
MIN_PAGES_PER_KIND = 4


class PageSet(ContractModel):
    """The classifiers' scored pages with their labels. Part of the answer key (AD-17)."""

    documents: tuple[PageSetDocument, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _set_is_complete(self) -> Self:
        pages = [page for document in self.documents for page in document.pages]
        counts = {
            str(layout): sum(page.layout is layout for page in pages)
            for layout in sorted(NON_MEDICAL_LAYOUTS | LAYOUTS_WITHOUT_TEXT)
        }
        counts["turned"] = sum(
            bool(page.rotation or page.drawn_rotation) for page in pages
        )
        for kind, count in counts.items():
            if count < MIN_PAGES_PER_KIND:
                raise ValueError(
                    f"the page set has {count} pages of the kind {kind}, fewer than "
                    f"{MIN_PAGES_PER_KIND}"
                )
        if not any(page.drawn_rotation for page in pages):
            raise ValueError("the page set has no page that is drawn turned")
        if not any(page.rotation == UPSIDE_DOWN for page in pages):
            raise ValueError("the page set has no page that is upside down")
        upright = {
            page.page_type
            for page in pages
            if page.kind is PageKind.MEDICAL and page.has_text_layer
        }
        if upright != {page_type for page_type in PageType if is_medical(page_type)}:
            raise ValueError("the page set lacks a plain page of a medical page type")
        if not any(document.mixed for document in self.documents):
            raise ValueError("the page set has no mixed file")
        return self


MIN_TRAINING_PAGES_PER_TYPE = 5


class TrainingPage(ContractModel):
    """One one-page PDF of the training set, under the folder of its page type."""

    file: NonEmptyStr
    page_type: PageType
    layout: PageLayout


class TrainingSet(ContractModel):
    """The pages a classifier is trained on (AD-13). They are not answers: no page is scored."""

    pages: tuple[TrainingPage, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _enough_of_each_type(self) -> Self:
        for page_type in PageType:
            count = sum(page.page_type is page_type for page in self.pages)
            if count < MIN_TRAINING_PAGES_PER_TYPE:
                raise ValueError(
                    f"the training set has {count} pages of {page_type}, fewer than "
                    f"{MIN_TRAINING_PAGES_PER_TYPE}"
                )
        return self
