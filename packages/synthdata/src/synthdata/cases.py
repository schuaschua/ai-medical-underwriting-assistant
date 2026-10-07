"""The first three synthetic cases, as data, and the whole case set.

Every person, place, number and reading is invented. The three cases here are used by
name in other tests and in the demo path, so their PDFs are never changed; the rest of
the set is in `cases_more`.

Identifiers are built to be impossible in real life: telephone numbers use the
555-0100 to 555-0199 block that is reserved for fiction, email addresses use
`example.com`, identity numbers start with area `000`, which no scheme issues,
and the places and the state code `ZZ` do not exist.
"""

from datetime import date
from decimal import Decimal

from contracts.enums import Verdict
from synthdata.cases_more import MORE_CASES
from synthdata.model import (
    Applicant,
    BloodPressureReading,
    CaseDefinition,
    Clinical,
    Diagnosis,
    Intent,
    Invoice,
    InvoiceLine,
    LabResult,
    PageLayout,
    PageSpec,
    Payslip,
    Physician,
    UtilityBill,
)

_MEDICAL_ONLY = CaseDefinition(
    case_id="case-001",
    summary="Medical pages only: type 2 diabetes on metformin, HbA1c above range.",
    document_date=date(2026, 9, 14),
    applicant=Applicant(
        name="Avery Testwood",
        date_of_birth=date(1974, 3, 18),
        sex="female",
        occupation="Bookkeeper",
        address="42 Placeholder Lane, Exampleton, ZZ 00042",
        phone_number="(202) 555-0143",
        email_address="avery.testwood@example.com",
        identity_number="000-12-3456",
        policy_number="POL-SYN-0004417",
    ),
    physician=Physician(
        name="Riley Exampleby",
        clinic_name="Exampleton Family Practice",
        clinic_address="7 Specimen Road, Exampleton, ZZ 00043",
        clinic_phone_number="(202) 555-0118",
    ),
    clinical=Clinical(
        diagnoses=(
            Diagnosis(
                condition="Type 2 diabetes mellitus",
                diagnosed_on=date(2019, 5, 6),
                treatment="Metformin 1000 mg twice daily",
                impairment="type_2_diabetes",
            ),
        ),
        height_cm=165,
        weight_kg=78,
        smoking_status="Never smoked",
        alcohol_units_per_week=4,
        family_history="Mother had type 2 diabetes, diagnosed at age 61.",
        blood_pressure=(
            BloodPressureReading(
                taken_on=date(2026, 6, 8), systolic_mmhg=126, diastolic_mmhg=80
            ),
            BloodPressureReading(
                taken_on=date(2026, 9, 7), systolic_mmhg=128, diastolic_mmhg=82
            ),
        ),
        lab_name="Specimen Diagnostics Laboratory",
        lab_specimen_reference="SP-2026-000101",
        lab_collected_on=date(2026, 9, 7),
        lab_results=(
            LabResult(
                test="HbA1c",
                value="7.4",
                unit="%",
                reference_range="4.0 to 5.6",
                flag="H",
            ),
            LabResult(
                test="Fasting plasma glucose",
                value="142",
                unit="mg/dL",
                reference_range="70 to 99",
                flag="H",
            ),
            LabResult(
                test="Total cholesterol",
                value="198",
                unit="mg/dL",
                reference_range="below 200",
            ),
            LabResult(
                test="LDL cholesterol",
                value="118",
                unit="mg/dL",
                reference_range="below 130",
            ),
            LabResult(
                test="HDL cholesterol",
                value="52",
                unit="mg/dL",
                reference_range="50 or above",
            ),
            LabResult(
                test="Creatinine",
                value="0.9",
                unit="mg/dL",
                reference_range="0.6 to 1.1",
            ),
            LabResult(
                test="eGFR",
                value="88",
                unit="mL/min/1.73 m2",
                reference_range="60 or above",
            ),
        ),
        physician_remarks=(
            "Type 2 diabetes with fair glycaemic control on metformin alone. "
            "No retinopathy, neuropathy or nephropathy on annual review. "
            "Blood pressure within target without medication."
        ),
    ),
    pages=(
        PageSpec(layout=PageLayout.APPLICATION_FORM),
        PageSpec(layout=PageLayout.ATTENDING_PHYSICIAN_STATEMENT),
        PageSpec(layout=PageLayout.LAB_REPORT),
    ),
    intends=Intent(verdict=Verdict.LOADED, loading_pct=50, rule_ids=("UW-DM-002",)),
)

_MIXED = CaseDefinition(
    case_id="case-002",
    summary="Medical pages plus an invoice, a payslip and a utility bill: hypertension, smoker.",
    document_date=date(2026, 9, 21),
    applicant=Applicant(
        name="Jordan Samplewick",
        date_of_birth=date(1968, 11, 2),
        sex="male",
        occupation="Warehouse supervisor",
        address="15 Fixture Street, Testbury, ZZ 00017",
        phone_number="(202) 555-0167",
        email_address="jordan.samplewick@example.com",
        identity_number="000-45-6789",
        policy_number="POL-SYN-0004582",
    ),
    physician=Physician(
        name="Quinn Demoson",
        clinic_name="Testbury Medical Centre",
        clinic_address="3 Dummy Avenue, Testbury, ZZ 00018",
        clinic_phone_number="(202) 555-0126",
    ),
    clinical=Clinical(
        diagnoses=(
            Diagnosis(
                condition="Essential hypertension",
                diagnosed_on=date(2021, 2, 15),
                treatment="Amlodipine 5 mg once daily",
                impairment="hypertension",
            ),
        ),
        height_cm=178,
        weight_kg=98,
        smoking_status="Current smoker, 10 cigarettes a day for 30 years",
        alcohol_units_per_week=14,
        family_history="Father had a heart attack at age 58.",
        blood_pressure=(
            BloodPressureReading(
                taken_on=date(2026, 8, 10), systolic_mmhg=152, diastolic_mmhg=96
            ),
            BloodPressureReading(
                taken_on=date(2026, 9, 14), systolic_mmhg=148, diastolic_mmhg=94
            ),
        ),
        lab_name="Specimen Diagnostics Laboratory",
        lab_specimen_reference="SP-2026-000102",
        lab_collected_on=date(2026, 9, 14),
        lab_results=(
            LabResult(
                test="HbA1c", value="5.5", unit="%", reference_range="4.0 to 5.6"
            ),
            LabResult(
                test="Fasting plasma glucose",
                value="94",
                unit="mg/dL",
                reference_range="70 to 99",
            ),
            LabResult(
                test="Total cholesterol",
                value="236",
                unit="mg/dL",
                reference_range="below 200",
                flag="H",
            ),
            LabResult(
                test="LDL cholesterol",
                value="158",
                unit="mg/dL",
                reference_range="below 130",
                flag="H",
            ),
            LabResult(
                test="HDL cholesterol",
                value="38",
                unit="mg/dL",
                reference_range="40 or above",
                flag="L",
            ),
            LabResult(
                test="Creatinine",
                value="1.0",
                unit="mg/dL",
                reference_range="0.7 to 1.3",
            ),
            LabResult(
                test="eGFR",
                value="84",
                unit="mL/min/1.73 m2",
                reference_range="60 or above",
            ),
        ),
        physician_remarks=(
            "Hypertension not at target on amlodipine 5 mg; dose increase planned. "
            "Raised LDL cholesterol, statin discussed. Advised to stop smoking. "
            "No history of stroke, angina or heart attack."
        ),
    ),
    invoice=Invoice(
        issuer="Testbury Home Supplies",
        invoice_number="INV-2026-0931",
        issued_on=date(2026, 9, 3),
        due_on=date(2026, 10, 3),
        lines=(
            InvoiceLine(
                description="Interior wall paint, 5 litre",
                quantity=2,
                unit_price=Decimal("34.50"),
            ),
            InvoiceLine(
                description="Paint roller set", quantity=1, unit_price=Decimal("12.99")
            ),
            InvoiceLine(
                description="Dust sheet, large", quantity=3, unit_price=Decimal("4.25")
            ),
        ),
    ),
    payslip=Payslip(
        employer="Placeholder Logistics",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        paid_on=date(2026, 8, 28),
        gross_pay=Decimal("3850.00"),
        income_tax=Decimal("612.40"),
        pension=Decimal("192.50"),
    ),
    utility_bill=UtilityBill(
        supplier="Testbury Power and Light",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 8, 31),
        issued_on=date(2026, 9, 4),
        electricity_kwh=612,
        rate_per_kwh=Decimal("0.28"),
        standing_charge=Decimal("31.00"),
    ),
    pages=(
        PageSpec(layout=PageLayout.APPLICATION_FORM),
        PageSpec(layout=PageLayout.ATTENDING_PHYSICIAN_STATEMENT),
        PageSpec(layout=PageLayout.LAB_REPORT),
        PageSpec(layout=PageLayout.INVOICE),
        PageSpec(layout=PageLayout.PAYSLIP),
        PageSpec(layout=PageLayout.UTILITY_BILL),
    ),
    intends=Intent(
        verdict=Verdict.LOADED, loading_pct=100, rule_ids=("UW-HT-002", "UW-TOB-001")
    ),
)

_EDGE = CaseDefinition(
    case_id="case-003",
    summary="Edge pages: a blank page and a lab report rotated 90 degrees; no impairments.",
    document_date=date(2026, 9, 28),
    applicant=Applicant(
        name="Morgan Mockford",
        date_of_birth=date(1991, 7, 25),
        sex="female",
        occupation="Primary school teacher",
        address="88 Sample Crescent, Demoford, ZZ 00029",
        phone_number="(202) 555-0189",
        email_address="morgan.mockford@example.com",
        identity_number="000-78-9012",
        policy_number="POL-SYN-0004736",
    ),
    physician=Physician(
        name="Harper Fixturely",
        clinic_name="Demoford Health Clinic",
        clinic_address="21 Template Way, Demoford, ZZ 00030",
        clinic_phone_number="(202) 555-0154",
    ),
    clinical=Clinical(
        diagnoses=(),
        height_cm=168,
        weight_kg=62,
        smoking_status="Never smoked",
        alcohol_units_per_week=3,
        family_history="No family history of diabetes, heart disease or cancer.",
        blood_pressure=(
            BloodPressureReading(
                taken_on=date(2026, 9, 21), systolic_mmhg=116, diastolic_mmhg=74
            ),
        ),
        lab_name="Specimen Diagnostics Laboratory",
        lab_specimen_reference="SP-2026-000103",
        lab_collected_on=date(2026, 9, 21),
        lab_results=(
            LabResult(
                test="HbA1c", value="5.2", unit="%", reference_range="4.0 to 5.6"
            ),
            LabResult(
                test="Fasting plasma glucose",
                value="86",
                unit="mg/dL",
                reference_range="70 to 99",
            ),
            LabResult(
                test="Total cholesterol",
                value="172",
                unit="mg/dL",
                reference_range="below 200",
            ),
            LabResult(
                test="LDL cholesterol",
                value="96",
                unit="mg/dL",
                reference_range="below 130",
            ),
            LabResult(
                test="HDL cholesterol",
                value="61",
                unit="mg/dL",
                reference_range="50 or above",
            ),
            LabResult(
                test="Creatinine",
                value="0.8",
                unit="mg/dL",
                reference_range="0.6 to 1.1",
            ),
            LabResult(
                test="eGFR",
                value="102",
                unit="mL/min/1.73 m2",
                reference_range="60 or above",
            ),
        ),
        physician_remarks=(
            "Routine examination. No chronic conditions and no regular medication. "
            "Blood pressure, glucose and lipids all within the reference ranges."
        ),
    ),
    pages=(
        PageSpec(layout=PageLayout.APPLICATION_FORM),
        PageSpec(layout=PageLayout.BLANK),
        PageSpec(layout=PageLayout.LAB_REPORT, rotation=90),
        PageSpec(layout=PageLayout.ATTENDING_PHYSICIAN_STATEMENT),
    ),
    intends=Intent(verdict=Verdict.STANDARD),
)

CASES: tuple[CaseDefinition, ...] = (_MEDICAL_ONLY, _MIXED, _EDGE, *MORE_CASES)
