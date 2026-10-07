"""Cases 004 to 022, as data. Every person, place, number and reading is invented.

Each case is written to show one thing (`summary`) and states the verdict it is meant
to reach (`intends`). The generator works the rules and the verdict out again from
the figures and the rule table, and refuses a case whose figures say otherwise.

Each case's pages are written to satisfy what the manual's evidence and pitfalls
parts ask of its impairments (a second sample, a result taken clear of an attack,
an echocardiogram after the event), so that the keyed verdict is the one the manual
supports. About a third of the cases word things as documents do and the manual does
not: a lay name on the form, another unit on the lab report, an old reading beside a
recent one.
"""

from datetime import date
from decimal import Decimal

from contracts.enums import Verdict
from synthdata.case_parts import (
    clinical,
    diagnosis,
    doctor,
    finding,
    panel,
    person,
)
from synthdata.model import (
    CaseDefinition,
    HandwrittenNote,
    Intent,
    Invoice,
    InvoiceLine,
    PageLayout,
    PageSpec,
    Passport,
    Payslip,
    Recipe,
    UtilityBill,
)

_FORM = PageSpec(layout=PageLayout.APPLICATION_FORM)
_STATEMENT = PageSpec(layout=PageLayout.ATTENDING_PHYSICIAN_STATEMENT)
_LAB = PageSpec(layout=PageLayout.LAB_REPORT)
_BLANK = PageSpec(layout=PageLayout.BLANK)
_NOTE = PageSpec(layout=PageLayout.HANDWRITTEN_NOTE)
_PASSPORT = PageSpec(layout=PageLayout.PASSPORT)
_RECIPE = PageSpec(layout=PageLayout.RECIPE)
_INVOICE = PageSpec(layout=PageLayout.INVOICE)
_PAYSLIP = PageSpec(layout=PageLayout.PAYSLIP)
_BILL = PageSpec(layout=PageLayout.UTILITY_BILL)
_NO_FAMILY_HISTORY = "No family history of diabetes, heart disease or cancer."


def _passport(number: str, place_of_birth: str, issued_on: date) -> Passport:
    return Passport(
        country="Republic of Exampleland",
        authority="Exampleland Passport Office",
        nationality="Examplelander",
        number=number,
        place_of_birth=place_of_birth,
        issued_on=issued_on,
        expires_on=issued_on.replace(year=issued_on.year + 10),
    )


def _invoice(
    issuer: str,
    number: str,
    issued_on: date,
    due_on: date,
    *lines: tuple[str, int, str],
) -> Invoice:
    return Invoice(
        issuer=issuer,
        invoice_number=number,
        issued_on=issued_on,
        due_on=due_on,
        lines=tuple(
            InvoiceLine(
                description=description, quantity=quantity, unit_price=Decimal(price)
            )
            for description, quantity, price in lines
        ),
    )


def _payslip(
    employer: str, month: date, paid_on: date, pay: str, tax: str, pension: str
) -> Payslip:
    """A payslip for the calendar month that starts on `month`, which has 30 days or more."""
    return Payslip(
        employer=employer,
        period_start=month,
        period_end=month.replace(day=30),
        paid_on=paid_on,
        gross_pay=Decimal(pay),
        income_tax=Decimal(tax),
        pension=Decimal(pension),
    )


def _bill(
    supplier: str,
    start: date,
    end: date,
    issued_on: date,
    kwh: int,
    rate: str,
    standing: str,
) -> UtilityBill:
    return UtilityBill(
        supplier=supplier,
        period_start=start,
        period_end=end,
        issued_on=issued_on,
        electricity_kwh=kwh,
        rate_per_kwh=Decimal(rate),
        standing_charge=Decimal(standing),
    )


_FORMER_SMOKER = CaseDefinition(
    case_id="case-004",
    summary="A former smoker with epilepsy long free of seizures: two rules met, neither with a debit; an invoice.",
    document_date=date(2026, 9, 2),
    applicant=person(
        4,
        "Casey Mockwell",
        date(1985, 2, 11),
        "male",
        "Surveyor",
        "9 Stub Row",
        "Mockingham",
    ),
    physician=doctor(
        4, "Emery Stubfield", "Mockingham Surgery", "2 Fixture Green", "Mockingham"
    ),
    clinical=clinical(
        4,
        build=(180, 76),
        smoking="Former smoker, 10 cigarettes a day for 8 years, stopped in 2012",
        alcohol=6,
        family=_NO_FAMILY_HISTORY,
        pressure=(
            (date(2026, 5, 18), 118, 76),
            (date(2026, 8, 24), 120, 78),
        ),
        collected=date(2026, 8, 24),
        labs=panel("male", "5.3 90 188 112 49 1.0 94"),
        remarks=(
            "Epilepsy since his teens. The last seizure of any kind was in June 2012, "
            "as the neurology letter confirms. Stopped smoking in 2012. Otherwise well."
        ),
        diagnoses=(
            diagnosis(
                "Epilepsy",
                date(2003, 5, 14),
                "Lamotrigine 100 mg twice daily",
                "epilepsy",
            ),
        ),
        findings=(finding("time since the last seizure", "14", "years"),),
    ),
    invoice=_invoice(
        "Mockingham Survey Instruments",
        "MSI-20718",
        date(2026, 8, 20),
        date(2026, 9, 19),
        ("Laser distance meter", 1, "149.00"),
        ("Ranging pole, 2 m", 4, "11.50"),
        ("Field book, waterproof", 6, "5.20"),
    ),
    pages=(_FORM, _STATEMENT, _LAB, _INVOICE),
    intends=Intent(verdict=Verdict.STANDARD, rule_ids=("UW-TOB-003", "UW-EPI-003")),
)

_PREDIABETES = CaseDefinition(
    case_id="case-005",
    summary="HbA1c in the prediabetes range with no diagnosis, and a fatty liver with a low FIB-4 index; a recipe.",
    document_date=date(2026, 9, 9),
    applicant=person(
        5,
        "Finley Demoworth",
        date(1979, 6, 30),
        "female",
        "Librarian",
        "31 Sample Terrace",
        "Dummyford",
    ),
    physician=doctor(
        5, "Hayden Placeholt", "Dummyford Health Centre", "6 Mock Parade", "Dummyford"
    ),
    clinical=clinical(
        5,
        build=(162, 64),
        alcohol=5,
        family="Father had type 2 diabetes, diagnosed at age 66.",
        pressure=((date(2026, 9, 1), 122, 78),),
        collected=date(2026, 9, 1),
        labs=panel("female", "5.9 104 194 121 57 0.8 91"),
        remarks=(
            "HbA1c a little above the reference range on a routine test; she has not "
            "been told she has diabetes. Fatty liver seen on ultrasound in 2025; the "
            "FIB-4 index was worked out from blood taken on 1 September, when she was well."
        ),
        diagnoses=(
            diagnosis(
                "Fatty liver",
                date(2025, 3, 10),
                "Diet and exercise advice",
                "fatty_liver_disease",
            ),
        ),
        findings=(finding("FIB-4 index", "0.92", None, date(2026, 9, 1)),),
    ),
    recipe=Recipe(
        source="The Placeholder Kitchen",
        title="Lentil and Tomato Soup",
        serves=4,
        minutes=45,
        ingredients=(
            "200 g red lentils, rinsed",
            "1 onion, finely chopped",
            "2 carrots, diced",
            "400 g tin of chopped tomatoes",
            "1 litre vegetable stock",
            "1 teaspoon ground cumin",
        ),
        steps=(
            "Soften the onion and the carrots in a little oil over a low heat for ten minutes.",
            "Stir in the cumin, then add the lentils, the tomatoes and the stock.",
            "Simmer for twenty-five minutes until the lentils are soft, then season and serve.",
        ),
    ),
    pages=(_FORM, _STATEMENT, _LAB, _RECIPE),
    intends=Intent(verdict=Verdict.STANDARD, rule_ids=("UW-PD-001", "UW-FLD-001")),
)

_THYROID = CaseDefinition(
    case_id="case-006",
    summary="An underactive thyroid with a normal TSH, and osteoporosis on a bone scan; a passport and a handwritten note.",
    document_date=date(2026, 9, 11),
    applicant=person(
        6,
        "Kendall Specimendale",
        date(1968, 9, 14),
        "female",
        "Graphic designer",
        "5 Template Close",
        "Samplestead",
    ),
    physician=doctor(
        6,
        "Logan Testgrove",
        "Samplestead Family Doctors",
        "40 Dummy Hill",
        "Samplestead",
    ),
    clinical=clinical(
        6,
        build=(170, 68),
        alcohol=4,
        family="Mother had an underactive thyroid and a broken hip at 70.",
        pressure=((date(2026, 9, 3), 114, 72),),
        collected=date(2026, 9, 3),
        labs=panel("female", "5.2 84 178 101 62 0.7 104", ("TSH", "2.4")),
        remarks=(
            "Thyroid well controlled; the dose of levothyroxine has not changed for "
            "three years. Bone density scan in June 2026 after an early menopause. "
            "No fractures at any time."
        ),
        diagnoses=(
            diagnosis(
                "Hypothyroidism",
                date(2018, 4, 9),
                "Levothyroxine 75 micrograms daily",
                "hypothyroidism",
                declared_as="Underactive thyroid",
            ),
            diagnosis(
                "Osteoporosis",
                date(2026, 6, 10),
                "Alendronate 70 mg once weekly",
                "osteoporosis",
                declared_as="Thin bones",
            ),
        ),
        findings=(
            finding(
                "bone density T-score",
                "-2.8",
                "standard deviations",
                date(2026, 6, 10),
                "hip",
            ),
            finding(
                "bone density T-score",
                "-2.1",
                "standard deviations",
                date(2026, 6, 10),
                "spine",
            ),
        ),
    ),
    passport=_passport("P-SYN-0000612", "Samplestead", date(2021, 3, 15)),
    note=HandwrittenNote(
        lines=(
            "Note for the insurer, 3 September 2026",
            "Thyroid: feels well, weight steady.",
            "Tablets taken every morning.",
            "Bone scan done in the summer.",
            "Started the weekly tablet for bones.",
            "No falls. No fractures.",
            "Blood results are attached.",
            "(signed) family doctor",
        )
    ),
    pages=(_FORM, _LAB, _STATEMENT, _PASSPORT, _NOTE),
    intends=Intent(
        verdict=Verdict.LOADED, loading_pct=25, rule_ids=("UW-THY-001", "UW-OST-001")
    ),
)

_ASTHMA = CaseDefinition(
    case_id="case-007",
    summary="Mild asthma with normal lung function, and a thin melanoma removed three years ago; a recipe.",
    document_date=date(2026, 9, 15),
    applicant=person(
        7,
        "Marlow Examplehurst",
        date(1994, 12, 3),
        "male",
        "Software tester",
        "17 Fixture Mews",
        "Fixtureham",
    ),
    physician=doctor(
        7,
        "Oakley Mockridge",
        "Fixtureham Medical Practice",
        "1 Sample Square",
        "Fixtureham",
    ),
    clinical=clinical(
        7,
        build=(175, 80),
        alcohol=8,
        family="Sister has asthma.",
        pressure=((date(2026, 9, 7), 120, 76),),
        collected=date(2026, 9, 7),
        labs=panel("male", "5.1 88 176 104 55 0.9 108"),
        remarks=(
            "Asthma since childhood; spirometry done when well. Never admitted to "
            "hospital for it. Melanoma removed from the shoulder in April 2023: the "
            "pathology report gives the thickness below, with no spread to lymph "
            "nodes, and there has been no recurrence."
        ),
        diagnoses=(
            diagnosis(
                "Asthma", date(2006, 10, 20), "Low-dose steroid inhaler daily", "asthma"
            ),
            diagnosis(
                "Melanoma of the skin",
                date(2023, 4, 18),
                "Removed; yearly skin checks",
                "melanoma",
                declared_as="Skin cancer, removed",
            ),
        ),
        findings=(
            finding("FEV1", "92", "% of predicted", date(2026, 9, 7)),
            finding("tumour thickness", "0.8", "mm", date(2023, 4, 18)),
        ),
    ),
    recipe=Recipe(
        source="Cut out of a made-up Sunday paper",
        title="Spiced Carrot Flatbreads",
        serves=4,
        minutes=30,
        ingredients=(
            "300 g self-raising flour",
            "2 large carrots, grated",
            "150 g plain yoghurt",
            "1 teaspoon cumin seeds",
            "A pinch of chilli flakes",
            "Oil, for the pan",
        ),
        steps=(
            "Mix the flour, the carrots, the yoghurt and the spices to a soft dough and rest it for ten minutes.",
            "Divide into eight, roll each piece thin and cook in a hot oiled pan for two minutes a side.",
            "Keep them warm in a cloth and eat the same day.",
        ),
    ),
    pages=(_FORM, _STATEMENT, _LAB, _RECIPE),
    intends=Intent(
        verdict=Verdict.LOADED, loading_pct=50, rule_ids=("UW-AST-001", "UW-MEL-001")
    ),
)

_DIABETES_AND_PRESSURE = CaseDefinition(
    case_id="case-008",
    summary="Two impairments whose debits add up: type 2 diabetes and raised blood pressure, in the applicant's own words on the form; a payslip.",
    document_date=date(2026, 9, 16),
    applicant=person(
        8,
        "Parker Dummyfield",
        date(1966, 4, 22),
        "male",
        "Bus driver",
        "63 Placeholder Road",
        "Specimenby",
    ),
    physician=doctor(
        8,
        "Reagan Sampleford",
        "Specimenby Group Practice",
        "12 Test Lane",
        "Specimenby",
    ),
    clinical=clinical(
        8,
        build=(172, 88),
        alcohol=10,
        family="Mother had a stroke at age 74.",
        pressure=(
            (date(2026, 7, 6), 144, 88),
            (date(2026, 9, 8), 146, 90),
        ),
        collected=date(2026, 9, 8),
        labs=panel("male", "6.6 128 192 110 44 1.1 78"),
        remarks=(
            "Type 2 diabetes with good glycaemic control. Blood pressure above target "
            "on ramipril 5 mg; the dose is to be increased. No complications found."
        ),
        diagnoses=(
            diagnosis(
                "Type 2 diabetes mellitus",
                date(2020, 1, 13),
                "Metformin 500 mg twice daily",
                "type_2_diabetes",
                declared_as="Sugar diabetes, on tablets",
            ),
            diagnosis(
                "Essential hypertension",
                date(2018, 7, 2),
                "Ramipril 5 mg once daily",
                "hypertension",
                declared_as="High blood pressure",
            ),
        ),
    ),
    payslip=_payslip(
        "Specimenby Omnibus Company",
        date(2026, 8, 1),
        date(2026, 8, 28),
        "2840.00",
        "381.60",
        "142.00",
    ),
    pages=(_FORM, _STATEMENT, _LAB, _PAYSLIP),
    intends=Intent(
        verdict=Verdict.LOADED, loading_pct=75, rule_ids=("UW-DM-001", "UW-HT-002")
    ),
)

_BUILD_AND_LIPIDS = CaseDefinition(
    case_id="case-009",
    summary="Obesity, raised LDL cholesterol and sleep apnoea; a handwritten note and a payslip.",
    document_date=date(2026, 9, 17),
    applicant=person(
        9,
        "Skyler Testleigh",
        date(1981, 8, 5),
        "female",
        "Call centre adviser",
        "24 Demo Gardens",
        "Placeholderton",
    ),
    physician=doctor(
        9,
        "Tatum Demohurst",
        "Placeholderton Clinic",
        "8 Specimen Walk",
        "Placeholderton",
    ),
    clinical=clinical(
        9,
        build=(160, 95),
        alcohol=3,
        family="Father had a heart attack at age 62.",
        pressure=((date(2026, 9, 9), 126, 82),),
        collected=date(2026, 9, 9),
        labs=panel("female", "5.5 95 252 172 51 0.7 98"),
        remarks=(
            "Weight has risen steadily over ten years. Raised LDL cholesterol; a "
            "statin was offered and declined. Sleep apnoea found on a sleep study in "
            "November 2025, before any treatment; the index below is from that study."
        ),
        diagnoses=(
            diagnosis(
                "Obstructive sleep apnoea",
                date(2025, 11, 4),
                "Pressure mask at night",
                "obstructive_sleep_apnoea",
                declared_as="Sleep apnoea",
            ),
        ),
        findings=(
            finding(
                "apnoea-hypopnoea index", "22", "events per hour", date(2025, 11, 4)
            ),
        ),
    ),
    payslip=_payslip(
        "Sample Contact Services",
        date(2026, 8, 1),
        date(2026, 8, 27),
        "2460.00",
        "318.20",
        "123.00",
    ),
    note=HandwrittenNote(
        lines=(
            "Sleep clinic, follow-up",
            "Using the mask most nights.",
            "Less sleepy in the day, she says.",
            "Partner reports the snoring has gone.",
            "Weight unchanged since last visit.",
            "Carry on. See again in a year.",
            "(signed) clinic doctor",
        )
    ),
    pages=(_FORM, _STATEMENT, _NOTE, _LAB, _PAYSLIP),
    intends=Intent(
        verdict=Verdict.LOADED,
        loading_pct=100,
        rule_ids=("UW-BMI-003", "UW-LDL-001", "UW-OSA-002"),
    ),
)

_GOUT_AND_SMOKING = CaseDefinition(
    case_id="case-010",
    summary="Gout with a raised urate in a current smoker; a blank page and an invoice.",
    document_date=date(2026, 9, 18),
    applicant=person(
        10,
        "Shiloh Fixturewood",
        date(1973, 1, 27),
        "male",
        "Plasterer",
        "2 Mock Orchard",
        "Testmere",
    ),
    physician=doctor(
        10, "Blair Exampleworth", "Testmere Surgery", "19 Dummy Quay", "Testmere"
    ),
    clinical=clinical(
        10,
        build=(183, 92),
        smoking="Current smoker, 15 cigarettes a day for 20 years",
        alcohol=18,
        family="Father had gout.",
        pressure=((date(2026, 9, 10), 128, 80),),
        collected=date(2026, 9, 10),
        labs=panel("male", "5.4 92 204 128 42 1.1 81", ("Serum urate", "7.8")),
        remarks=(
            "Two attacks of gout in the last year, the latest in March 2026; the "
            "urate on the attached report was taken in September, months clear of "
            "any attack. Still above target on allopurinol. Advised to stop smoking."
        ),
        diagnoses=(
            diagnosis(
                "Gout", date(2022, 11, 8), "Allopurinol 100 mg once daily", "gout"
            ),
        ),
    ),
    invoice=_invoice(
        "Testmere Builders Merchants",
        "INV-2026-1187",
        date(2026, 9, 1),
        date(2026, 9, 30),
        ("Multi-finish plaster, 25 kg", 6, "9.80"),
        ("Plasterboard sheet, 12.5 mm", 10, "8.45"),
        ("Jointing tape, 90 m roll", 2, "3.60"),
    ),
    pages=(_FORM, _STATEMENT, _BLANK, _LAB, _INVOICE),
    intends=Intent(
        verdict=Verdict.LOADED, loading_pct=75, rule_ids=("UW-GOUT-002", "UW-TOB-001")
    ),
)

_DEPRESSION = CaseDefinition(
    case_id="case-011",
    summary="Treated depression with a low PHQ-9 score beside coeliac disease, which the manual has no rule for; the lab report is upside down; a utility bill.",
    document_date=date(2026, 9, 21),
    applicant=person(
        11,
        "Corey Mockbourne",
        date(1990, 3, 16),
        "female",
        "Dental nurse",
        "76 Test Avenue",
        "Demowick",
    ),
    physician=doctor(
        11,
        "Darcy Samplecroft",
        "Demowick Medical Centre",
        "3 Placeholder Rise",
        "Demowick",
    ),
    clinical=clinical(
        11,
        build=(166, 59),
        alcohol=2,
        family=_NO_FAMILY_HISTORY,
        pressure=((date(2026, 9, 14), 112, 70),),
        collected=date(2026, 9, 14),
        labs=panel("female", "5.0 82 168 92 64 0.7 110"),
        remarks=(
            "One episode of depression, now much improved on sertraline; the "
            "questionnaire was completed in clinic. Coeliac disease found on biopsy "
            "in 2021, well on a gluten-free diet."
        ),
        diagnoses=(
            diagnosis(
                "Depression",
                date(2024, 2, 19),
                "Sertraline 50 mg once daily",
                "depression",
            ),
            diagnosis("Coeliac disease", date(2021, 5, 6), "Gluten-free diet", None),
        ),
        findings=(finding("PHQ-9 score", "9", "points", date(2026, 9, 14)),),
    ),
    utility_bill=_bill(
        "Demowick Energy",
        date(2026, 6, 1),
        date(2026, 8, 31),
        date(2026, 9, 5),
        488,
        "0.31",
        "42.50",
    ),
    pages=(
        _FORM,
        _STATEMENT,
        PageSpec(layout=PageLayout.LAB_REPORT, rotation=180),
        _BILL,
    ),
    intends=Intent(verdict=Verdict.REFER, rule_ids=("UW-DEP-001",)),
)

_HEART_ATTACK = CaseDefinition(
    case_id="case-012",
    summary="A heart attack thirty months ago, called that on the form, with LDL cholesterol reported in mmol/L and still raised; a passport.",
    document_date=date(2026, 9, 22),
    applicant=person(
        12,
        "Ellis Dummybrook",
        date(1961, 10, 9),
        "male",
        "Accounts manager",
        "11 Specimen Court",
        "Examplecombe",
    ),
    physician=doctor(
        12,
        "Frankie Testholme",
        "Examplecombe Practice",
        "27 Mock Street",
        "Examplecombe",
    ),
    clinical=clinical(
        12,
        build=(177, 84),
        alcohol=7,
        family="Brother had a heart attack at age 55.",
        pressure=(
            (date(2026, 6, 15), 122, 74),
            (date(2026, 9, 15), 124, 76),
        ),
        collected=date(2026, 9, 15),
        labs=panel(
            "male",
            "5.5 96 6.2 4.3 1.1 1.0 76",
            other_unit=("Total cholesterol", "LDL cholesterol", "HDL cholesterol"),
        ),
        remarks=(
            "Heart attack on 11 March 2024, confirmed in the discharge letter and "
            "treated with one stent. An echocardiogram three months later was "
            "reported as normal, and there has been no chest pain since. LDL "
            "cholesterol not yet at target on a statin taken for two years."
        ),
        diagnoses=(
            diagnosis(
                "Myocardial infarction",
                date(2024, 3, 11),
                "Aspirin 75 mg, atorvastatin 80 mg",
                "myocardial_infarction",
                declared_as="Heart attack",
            ),
        ),
    ),
    passport=_passport("P-SYN-0001277", "Examplecombe", date(2018, 11, 26)),
    pages=(_FORM, _STATEMENT, _LAB, _PASSPORT),
    intends=Intent(
        verdict=Verdict.LOADED, loading_pct=125, rule_ids=("UW-MI-002", "UW-LDL-001")
    ),
)

_KIDNEY = CaseDefinition(
    case_id="case-013",
    summary="Reduced kidney function with albumin in the urine, each confirmed by an earlier sample; the lab report is scanned sideways.",
    document_date=date(2026, 9, 23),
    applicant=person(
        13,
        "Hollis Demofield",
        date(1970, 5, 28),
        "female",
        "School secretary",
        "58 Fixture Lane",
        "Mockbridge",
    ),
    physician=doctor(
        13,
        "Jules Fixturegate",
        "Mockbridge Health Clinic",
        "4 Example Row",
        "Mockbridge",
    ),
    clinical=clinical(
        13,
        build=(158, 66),
        alcohol=1,
        family="Mother had kidney disease.",
        pressure=((date(2026, 9, 16), 124, 78),),
        collected=date(2026, 9, 16),
        labs=panel(
            "female",
            "5.4 89 196 118 55 1.2 52",
            ("Urine albumin-to-creatinine ratio", "120"),
        ),
        remarks=(
            "Kidney function reduced and steady over two years; the earlier results "
            "above were taken when she was well, with no infection. Blood pressure "
            "at target without medication."
        ),
        diagnoses=(
            diagnosis(
                "Chronic kidney disease",
                date(2023, 6, 12),
                "Dietary advice, no medication",
                "chronic_kidney_disease",
            ),
        ),
        findings=(
            finding("eGFR", "55", "mL/min/1.73 m2", date(2026, 5, 12)),
            finding(
                "urine albumin-to-creatinine ratio", "110", "mg/g", date(2026, 7, 20)
            ),
        ),
    ),
    pages=(
        _FORM,
        _STATEMENT,
        PageSpec(layout=PageLayout.LAB_REPORT, drawn_rotation=90),
    ),
    intends=Intent(
        verdict=Verdict.LOADED, loading_pct=75, rule_ids=("UW-CKD-001", "UW-ALB-001")
    ),
)

_TYPE_1_DIABETES = CaseDefinition(
    case_id="case-014",
    summary="Type 1 diabetes, called diabetes on insulin on the form, with an HbA1c in the band the manual declines; a utility bill.",
    document_date=date(2026, 9, 24),
    applicant=person(
        14,
        "Kerry Specimenhall",
        date(1969, 12, 17),
        "male",
        "Taxi driver",
        "90 Dummy Road",
        "Sampleby",
    ),
    physician=doctor(
        14, "Lennox Placeworth", "Sampleby Surgery", "15 Template Street", "Sampleby"
    ),
    clinical=clinical(
        14,
        build=(169, 72),
        alcohol=9,
        family="No family history of diabetes.",
        pressure=((date(2026, 9, 17), 128, 82),),
        collected=date(2026, 9, 17),
        labs=panel("male", "9.4 198 186 108 52 1.0 82"),
        remarks=(
            "Type 1 diabetes since the age of 14. Control has slipped over the last "
            "two years. No severe hypoglycaemia needing another person's help and no "
            "ketoacidosis in the last five years."
        ),
        diagnoses=(
            diagnosis(
                "Type 1 diabetes mellitus",
                date(1984, 3, 12),
                "Insulin, basal and mealtime",
                "type_1_diabetes",
                declared_as="Diabetes, on insulin since childhood",
            ),
        ),
    ),
    utility_bill=_bill(
        "Sampleby Light and Power",
        date(2026, 7, 1),
        date(2026, 8, 31),
        date(2026, 9, 8),
        377,
        "0.29",
        "27.80",
    ),
    pages=(_FORM, _STATEMENT, _LAB, _BILL),
    intends=Intent(verdict=Verdict.DECLINE, rule_ids=("UW-DI-004",)),
)

_SEVERE_PRESSURE = CaseDefinition(
    case_id="case-015",
    summary="Blood pressure in the band the manual declines, with a small aortic aneurysm; an invoice.",
    document_date=date(2026, 9, 25),
    applicant=person(
        15,
        "Micah Examplegarth",
        date(1964, 7, 8),
        "male",
        "Site foreman",
        "33 Mock Common",
        "Dummington",
    ),
    physician=doctor(
        15,
        "Perry Mockstead",
        "Dummington Family Practice",
        "7 Sample Hill",
        "Dummington",
    ),
    clinical=clinical(
        15,
        build=(181, 97),
        alcohol=16,
        family="Father had a stroke at age 60.",
        pressure=(
            (date(2026, 8, 17), 186, 112),
            (date(2026, 9, 18), 184, 110),
        ),
        collected=date(2026, 9, 18),
        labs=panel("male", "5.6 97 222 142 43 1.2 68"),
        remarks=(
            "Long-standing hypertension, far above target; he was well and not in "
            "pain at either reading. Small aortic aneurysm, not repaired, watched by "
            "yearly ultrasound; the figure below is the outer diameter."
        ),
        diagnoses=(
            diagnosis(
                "Essential hypertension",
                date(2012, 3, 26),
                "Amlodipine 10 mg once daily",
                "hypertension",
            ),
            diagnosis(
                "Abdominal aortic aneurysm",
                date(2025, 2, 10),
                "Yearly ultrasound",
                "abdominal_aortic_aneurysm",
            ),
        ),
        findings=(
            finding("abdominal aortic diameter", "3.4", "cm", date(2026, 2, 16)),
        ),
    ),
    invoice=_invoice(
        "Dummington Plant Hire",
        "DPH-55102",
        date(2026, 9, 7),
        date(2026, 10, 7),
        ("Mini digger, hire per day", 3, "95.00"),
        ("Delivery and collection", 1, "60.00"),
        ("Safety fencing panel, hire per week", 12, "4.50"),
    ),
    pages=(_FORM, _STATEMENT, _LAB, _INVOICE),
    intends=Intent(verdict=Verdict.DECLINE, rule_ids=("UW-HT-004", "UW-AAA-001")),
)

_ALCOHOL = CaseDefinition(
    case_id="case-016",
    summary="A mixed file: an AUDIT score the manual declines, raised triglycerides, a passport and a recipe.",
    document_date=date(2026, 9, 28),
    applicant=person(
        16,
        "Remy Testcombe",
        date(1977, 11, 19),
        "male",
        "Bar manager",
        "14 Placeholder Wharf",
        "Fixturewell",
    ),
    physician=doctor(
        16,
        "Sasha Demobrook",
        "Fixturewell Medical Group",
        "22 Specimen Road",
        "Fixturewell",
    ),
    clinical=clinical(
        16,
        build=(174, 71),
        alcohol=46,
        family="Father had liver disease.",
        pressure=((date(2026, 9, 21), 126, 80),),
        collected=date(2026, 9, 21),
        labs=panel(
            "male", "5.3 91 208 124 58 0.9 96", ("Fasting triglycerides", "210")
        ),
        remarks=(
            "Drinks heavily most days and has not been able to cut down; the full "
            "ten-question AUDIT was completed in clinic. He is not in treatment: the "
            "first appointment with the alcohol service was not attended. No pancreatitis."
        ),
        findings=(finding("AUDIT score", "24", "points", date(2026, 9, 21)),),
    ),
    passport=_passport("P-SYN-0001634", "Fixturewell", date(2019, 6, 3)),
    recipe=Recipe(
        source="Recipes from the Sample Pantry",
        title="Oat and Apple Breakfast Bars",
        serves=12,
        minutes=40,
        ingredients=(
            "250 g rolled oats",
            "2 eating apples, grated",
            "100 g butter, melted",
            "3 tablespoons honey",
            "50 g raisins",
            "1 teaspoon ground cinnamon",
        ),
        steps=(
            "Heat the oven to 180 degrees and line a square baking tin with paper.",
            "Mix everything in a large bowl until the oats are coated, then press it firmly into the tin.",
            "Bake for twenty-five minutes until golden. Cool in the tin before cutting into bars.",
        ),
    ),
    pages=(_FORM, _PASSPORT, _STATEMENT, _RECIPE, _LAB),
    intends=Intent(verdict=Verdict.DECLINE, rule_ids=("UW-ALC-003", "UW-TG-001")),
)

_LUNGS = CaseDefinition(
    case_id="case-017",
    summary="Severe COPD, called that on the form, in a smoker of forty pack-years; a handwritten doctor's note.",
    document_date=date(2026, 9, 29),
    applicant=person(
        17,
        "Toby Samplethwaite",
        date(1959, 2, 2),
        "female",
        "Retired cleaner",
        "6 Demo Terrace",
        "Testhaven",
    ),
    physician=doctor(
        17, "Robin Dummyholt", "Testhaven Health Centre", "30 Fixture Road", "Testhaven"
    ),
    clinical=clinical(
        17,
        build=(163, 52),
        smoking="Current smoker, 20 cigarettes a day for 40 years",
        alcohol=4,
        family="Father had emphysema.",
        pressure=((date(2026, 9, 3), 122, 74),),
        collected=date(2026, 9, 3),
        labs=panel("female", "5.3 86 184 108 59 0.8 88"),
        remarks=(
            "Very severe fixed airflow obstruction: the ratio of FEV1 to total "
            "exhaled volume was 0.41 after a bronchodilator. Spirometry was done "
            "when stable, the last flare-up being in January 2026. She does not use "
            "oxygen at home. Still smoking."
        ),
        diagnoses=(
            diagnosis(
                "Chronic obstructive pulmonary disease",
                date(2014, 5, 19),
                "Tiotropium and salbutamol inhalers",
                "copd",
                declared_as="COPD",
            ),
        ),
        findings=(
            finding(
                "FEV1 after a bronchodilator", "26", "% of predicted", date(2026, 9, 3)
            ),
        ),
    ),
    note=HandwrittenNote(
        lines=(
            "Clinic note, 3 September 2026",
            "Seen today for a review of breathing.",
            "Short of breath on one flight of stairs.",
            "Chest quiet, with a wheeze on both sides.",
            "Still smoking. Advised again to stop.",
            "Inhalers checked: technique is good.",
            "Spirometry result is with this file.",
            "Review in three months.",
            "(signed) attending physician",
        )
    ),
    pages=(_FORM, _STATEMENT, _NOTE, _LAB),
    intends=Intent(
        verdict=Verdict.DECLINE,
        rule_ids=("UW-TOB-001", "UW-TOB-002", "UW-COPD-004"),
    ),
)

_TWO_HBA1C_READINGS = CaseDefinition(
    case_id="case-018",
    summary="Type 2 diabetes with two HbA1c readings in different bands, the later one in mmol/mol: the manual rates the most recent; a blank page.",
    document_date=date(2026, 9, 30),
    applicant=person(
        18,
        "Jamie Fixturedean",
        date(1972, 8, 21),
        "female",
        "Pharmacy assistant",
        "45 Example Bank",
        "Demoborough",
    ),
    physician=doctor(
        18, "Devon Specimenrow", "Demoborough Surgery", "10 Test Square", "Demoborough"
    ),
    clinical=clinical(
        18,
        build=(167, 81),
        alcohol=2,
        family="Mother had type 2 diabetes.",
        pressure=((date(2026, 9, 22), 126, 78),),
        collected=date(2026, 9, 22),
        labs=panel("female", "67 168 190 112 52 0.8 86", other_unit=("HbA1c",)),
        remarks=(
            "Type 2 diabetes. Glycaemic control has worsened since the spring; a "
            "second medicine was started this week. Eyes and feet checked, nothing "
            "found. The laboratory changed its HbA1c units in the summer."
        ),
        diagnoses=(
            diagnosis(
                "Type 2 diabetes mellitus",
                date(2017, 2, 6),
                "Metformin 1000 mg twice daily",
                "type_2_diabetes",
            ),
        ),
        findings=(finding("HbA1c", "7.6", "%", date(2026, 3, 10)),),
    ),
    pages=(_FORM, _BLANK, _STATEMENT, _LAB),
    intends=Intent(verdict=Verdict.LOADED, loading_pct=100, rule_ids=("UW-DM-003",)),
)

_PRESSURE_OVER_TIME = CaseDefinition(
    case_id="case-019",
    summary="Three blood pressure readings, one of them over a year old: the manual rates the average of the last twelve months; a clot eight months ago; a payslip.",
    document_date=date(2026, 10, 1),
    applicant=person(
        19,
        "Lesley Mockhaven",
        date(1975, 4, 4),
        "male",
        "Delivery planner",
        "8 Sample Moor",
        "Specimenford",
    ),
    physician=doctor(
        19,
        "Alexis Examplefold",
        "Specimenford Practice",
        "51 Demo Street",
        "Specimenford",
    ),
    clinical=clinical(
        19,
        build=(186, 93),
        alcohol=12,
        family="Father has high blood pressure.",
        pressure=(
            (date(2025, 6, 16), 128, 80),
            (date(2026, 7, 20), 138, 86),
            (date(2026, 9, 28), 152, 94),
        ),
        collected=date(2026, 9, 28),
        labs=panel("male", "5.4 93 198 122 46 1.0 90"),
        remarks=(
            "Blood pressure has risen over the last year; he was well at each "
            "reading. One deep vein thrombosis in the calf in January 2026 after a "
            "long flight, the only clot he has had; no cancer was found."
        ),
        diagnoses=(
            diagnosis(
                "Deep vein thrombosis",
                date(2026, 1, 19),
                "Rivaroxaban for six months",
                "venous_thromboembolism",
                declared_as="Blood clot in the leg",
            ),
        ),
    ),
    payslip=_payslip(
        "Specimenford Parcels",
        date(2026, 9, 1),
        date(2026, 9, 25),
        "3010.00",
        "418.40",
        "150.50",
    ),
    pages=(_FORM, _LAB, _STATEMENT, _PAYSLIP),
    intends=Intent(
        verdict=Verdict.LOADED, loading_pct=100, rule_ids=("UW-HT-002", "UW-VTE-002")
    ),
)

_NO_RULE = CaseDefinition(
    case_id="case-020",
    summary="Multiple sclerosis, a condition the manual has no rule for: a person must decide; a handwritten note and a recipe.",
    document_date=date(2026, 10, 2),
    applicant=person(
        20,
        "Reese Dummywick",
        date(1986, 1, 15),
        "female",
        "Translator",
        "29 Template Rise",
        "Samplemere",
    ),
    physician=doctor(
        20,
        "Noel Testbrook",
        "Samplemere Medical Practice",
        "5 Fixture Parade",
        "Samplemere",
    ),
    clinical=clinical(
        20,
        build=(171, 63),
        alcohol=3,
        family=_NO_FAMILY_HISTORY,
        pressure=((date(2026, 9, 24), 116, 72),),
        collected=date(2026, 9, 24),
        labs=panel("female", "5.1 85 174 98 63 0.7 106"),
        remarks=(
            "Relapsing multiple sclerosis, two relapses since diagnosis, the last in "
            "2023. Walks without help. Under the care of the neurology clinic."
        ),
        diagnoses=(
            diagnosis(
                "Multiple sclerosis",
                date(2019, 10, 7),
                "Dimethyl fumarate twice daily",
                None,
            ),
        ),
    ),
    note=HandwrittenNote(
        lines=(
            "Neurology clinic, yearly review",
            "No new symptoms since the last visit.",
            "Walking and balance as before.",
            "Vision clear in both eyes.",
            "Tablets taken without trouble.",
            "Scan booked for the new year.",
            "(signed) clinic registrar",
        )
    ),
    recipe=Recipe(
        source="Translated from a made-up cookery book",
        title="Cold Cucumber and Dill Soup",
        serves=4,
        minutes=15,
        ingredients=(
            "2 cucumbers, peeled",
            "500 g plain yoghurt",
            "A small bunch of dill",
            "1 clove of garlic",
            "Juice of half a lemon",
            "A handful of ice cubes",
        ),
        steps=(
            "Grate one cucumber and blend the other with the yoghurt, the garlic and the lemon juice.",
            "Stir in the grated cucumber and most of the dill, then chill for an hour.",
            "Serve over the ice with the rest of the dill on top.",
        ),
    ),
    pages=(_FORM, _STATEMENT, _LAB, _NOTE, _RECIPE),
    intends=Intent(verdict=Verdict.REFER),
)

_ARTHRITIS = CaseDefinition(
    case_id="case-021",
    summary="Rheumatoid arthritis at low activity with a moderate anaemia; a passport and a blank page.",
    document_date=date(2026, 10, 5),
    applicant=person(
        21,
        "Hadley Stubmoor",
        date(1967, 3, 29),
        "female",
        "Bookshop owner",
        "12 Dummy Crescent",
        "Dummyvale",
    ),
    physician=doctor(
        21,
        "Emerson Fixturefield",
        "Dummyvale Medical Rooms",
        "48 Test Hill",
        "Dummyvale",
    ),
    clinical=clinical(
        21,
        build=(165, 60),
        alcohol=4,
        family="Mother had rheumatoid arthritis.",
        pressure=((date(2026, 9, 25), 118, 74),),
        collected=date(2026, 9, 25),
        labs=panel("female", "5.2 87 182 106 60 0.7 92", ("Haemoglobin", "10.4")),
        remarks=(
            "Rheumatoid arthritis under the rheumatology clinic, which recorded the "
            "score below; no lung involvement. Anaemia put down to the arthritis: "
            "iron studies normal, no bleeding and no blood donation."
        ),
        diagnoses=(
            diagnosis(
                "Rheumatoid arthritis",
                date(2016, 9, 5),
                "Methotrexate 15 mg once weekly",
                "rheumatoid_arthritis",
            ),
        ),
        findings=(finding("DAS28", "2.9", "points", date(2026, 8, 11)),),
    ),
    passport=_passport("P-SYN-0002145", "Dummyvale", date(2023, 1, 9)),
    pages=(_FORM, _PASSPORT, _BLANK, _STATEMENT, _LAB),
    intends=Intent(
        verdict=Verdict.LOADED, loading_pct=75, rule_ids=("UW-RA-002", "UW-ANA-001")
    ),
)

_TWO_LDL_BANDS = CaseDefinition(
    case_id="case-022",
    summary="Two LDL cholesterol results in different bands, and the manual does not say which to rate: a person must decide; the statement is scanned sideways; a utility bill.",
    document_date=date(2026, 10, 6),
    applicant=person(
        22,
        "Sutton Mockworth",
        date(1972, 6, 12),
        "male",
        "Insurance clerk",
        "3 Specimen Yard",
        "Examplestoke",
    ),
    physician=doctor(
        22, "Arlo Samplegate", "Examplestoke Surgery", "16 Fixture Bank", "Examplestoke"
    ),
    clinical=clinical(
        22,
        build=(178, 82),
        alcohol=8,
        family="Father had raised cholesterol.",
        pressure=((date(2026, 9, 29), 124, 78),),
        collected=date(2026, 9, 29),
        labs=panel(
            "male", "5.3 90 284 196 48 1.0 88", ("Fasting triglycerides", "140")
        ),
        remarks=(
            "LDL cholesterol has risen between two fasting tests this year despite "
            "a change of diet. He takes no cholesterol medicine; a statin has been "
            "advised and he is thinking it over."
        ),
        findings=(finding("LDL cholesterol", "172", "mg/dL", date(2026, 4, 14)),),
    ),
    utility_bill=_bill(
        "Examplestoke Electric",
        date(2026, 7, 1),
        date(2026, 9, 30),
        date(2026, 10, 2),
        544,
        "0.30",
        "44.10",
    ),
    pages=(
        _FORM,
        PageSpec(layout=PageLayout.ATTENDING_PHYSICIAN_STATEMENT, drawn_rotation=90),
        _LAB,
        _BILL,
    ),
    intends=Intent(verdict=Verdict.REFER, rule_ids=("UW-LDL-001", "UW-LDL-002")),
)

MORE_CASES: tuple[CaseDefinition, ...] = (
    _FORMER_SMOKER,
    _PREDIABETES,
    _THYROID,
    _ASTHMA,
    _DIABETES_AND_PRESSURE,
    _BUILD_AND_LIPIDS,
    _GOUT_AND_SMOKING,
    _DEPRESSION,
    _HEART_ATTACK,
    _KIDNEY,
    _TYPE_1_DIABETES,
    _SEVERE_PRESSURE,
    _ALCOHOL,
    _LUNGS,
    _TWO_HBA1C_READINGS,
    _PRESSURE_OVER_TIME,
    _NO_RULE,
    _ARTHRITIS,
    _TWO_LDL_BANDS,
)
