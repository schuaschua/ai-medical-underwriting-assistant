"""Short ways to write the people, the practice and the laboratory panel of a synthetic case.

The identifiers are worked out from one number per person (`key`), so no two people
of the case set or the training set share one, and all are impossible in real life:
telephone numbers in the 555-0100 to 555-0199 block, `example.com` addresses,
identity numbers with area `000`, and the state code `ZZ`.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from synthdata.model import (
    Applicant,
    BloodPressureReading,
    Clinical,
    Diagnosis,
    Finding,
    LabResult,
    Physician,
)

Sex = Literal["female", "male"]
BOTH: tuple[Sex, ...] = ("female", "male")
DEFAULT_LAB = "Specimen Diagnostics Laboratory"

# A reference range: the lowest and the highest normal value, and whether the highest
# is itself outside the range ("below 200").
type _Range = tuple[str | None, str | None, bool]
# Each test of a laboratory report: its unit, and its range for each sex.
_TESTS: dict[str, tuple[str, dict[Sex, _Range]]] = {
    "HbA1c": ("%", dict.fromkeys(BOTH, ("4.0", "5.6", False))),
    "Fasting plasma glucose": ("mg/dL", dict.fromkeys(BOTH, ("70", "99", False))),
    "Total cholesterol": ("mg/dL", dict.fromkeys(BOTH, (None, "200", True))),
    "LDL cholesterol": ("mg/dL", dict.fromkeys(BOTH, (None, "130", True))),
    "HDL cholesterol": (
        "mg/dL",
        {"female": ("50", None, False), "male": ("40", None, False)},
    ),
    "Creatinine": (
        "mg/dL",
        {"female": ("0.6", "1.1", False), "male": ("0.7", "1.3", False)},
    ),
    "eGFR": ("mL/min/1.73 m2", dict.fromkeys(BOTH, ("60", None, False))),
    "TSH": ("mIU/L", dict.fromkeys(BOTH, ("0.4", "4.0", False))),
    "Serum urate": (
        "mg/dL",
        {"female": ("2.6", "6.0", False), "male": ("3.5", "7.2", False)},
    ),
    "Fasting triglycerides": ("mg/dL", dict.fromkeys(BOTH, (None, "150", True))),
    "Urine albumin-to-creatinine ratio": (
        "mg/g",
        dict.fromkeys(BOTH, (None, "30", True)),
    ),
    "Haemoglobin": (
        "g/dL",
        {"female": ("12.0", "15.5", False), "male": ("13.5", "17.5", False)},
    ),
}
# The tests a laboratory may report in another unit, with that unit and its ranges.
_OTHER_UNIT: dict[str, tuple[str, dict[Sex, _Range]]] = {
    "HbA1c": ("mmol/mol", dict.fromkeys(BOTH, ("20", "38", False))),
    "Total cholesterol": ("mmol/L", dict.fromkeys(BOTH, (None, "5.2", True))),
    "LDL cholesterol": ("mmol/L", dict.fromkeys(BOTH, (None, "3.4", True))),
    "HDL cholesterol": (
        "mmol/L",
        {"female": ("1.3", None, False), "male": ("1.0", None, False)},
    ),
}
# The seven tests every report has, in the order it prints them.
_PANEL = tuple(_TESTS)[:7]


def lab_result(
    test: str, value: str, sex: Sex, *, other_unit: bool = False
) -> LabResult:
    """One row of a laboratory report, with its reference range and its flag."""
    unit, ranges = (_OTHER_UNIT if other_unit else _TESTS)[test]
    low, high, high_is_outside = ranges[sex]
    number = Decimal(value)
    if low is not None and high is not None:
        reference = f"{low} to {high}"
    elif high is not None:
        reference = f"below {high}"
    else:
        reference = f"{low} or above"
    flag: Literal["", "H", "L"] = ""
    if low is not None and number < Decimal(low):
        flag = "L"
    elif high is not None and (
        number > Decimal(high) or (high_is_outside and number == Decimal(high))
    ):
        flag = "H"
    return LabResult(
        test=test, value=value, unit=unit, reference_range=reference, flag=flag
    )


def panel(
    sex: Sex, values: str, *more: tuple[str, str], other_unit: tuple[str, ...] = ()
) -> tuple[LabResult, ...]:
    """A laboratory report: the seven usual results, then any others.

    `values` are HbA1c, fasting glucose, total, LDL and HDL cholesterol, creatinine
    and eGFR, separated by spaces. A further test is given with its name:
    `("Serum urate", "7.8")`. The tests named in `other_unit` are reported in the
    unit some laboratories use instead (HbA1c in mmol/mol, cholesterol in mmol/L).
    """
    given = values.split()
    if len(given) != len(_PANEL):
        raise ValueError(f"a panel has {len(_PANEL)} results, not {len(given)}")
    rows = [*zip(_PANEL, given, strict=True), *more]
    return tuple(
        lab_result(test, value, sex, other_unit=test in other_unit)
        for test, value in rows
    )


def months_before(day: date, months: int) -> date:
    """The same day of the month `months` earlier, or that month's last day."""
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    for last in (day.day, 30, 29, 28):
        try:
            return date(year, month + 1, last)
        except ValueError:
            continue
    raise ValueError(f"no day {months} months before {day}")


def person(
    key: int,
    name: str,
    born: date,
    sex: Sex,
    occupation: str,
    street: str,
    town: str,
    *,
    area: str = "303",
    series: str = "SYN",
) -> Applicant:
    """An applicant whose identifiers all follow from `key`."""
    first, last = name.lower().split()
    return Applicant(
        name=name,
        date_of_birth=born,
        sex=sex,
        occupation=occupation,
        address=f"{street}, {town}, ZZ {key * 7 + 100:05d}",
        phone_number=f"({area}) 555-01{key:02d}",
        email_address=f"{first}.{last}@example.com",
        identity_number=f"000-{20 + key:02d}-{(key * 379) % 9000 + 1000:04d}",
        policy_number=f"POL-{series}-{5000 + key * 113:07d}",
    )


def doctor(
    key: int, name: str, practice: str, street: str, town: str, *, area: str = "404"
) -> Physician:
    """An attending physician and the practice, with a telephone number of its own."""
    return Physician(
        name=name,
        clinic_name=practice,
        clinic_address=f"{street}, {town}, ZZ {key * 7 + 103:05d}",
        clinic_phone_number=f"({area}) 555-01{key:02d}",
    )


def diagnosis(
    condition: str,
    diagnosed_on: date,
    treatment: str,
    impairment: str | None,
    declared_as: str | None = None,
) -> Diagnosis:
    """A condition on file; `impairment` is the manual's `impairment_id`, or None for none."""
    return Diagnosis(
        condition=condition,
        diagnosed_on=diagnosed_on,
        treatment=treatment,
        impairment=impairment,
        declared_as=declared_as,
    )


def finding(
    label: str,
    value: str,
    unit: str | None = None,
    taken_on: date | None = None,
    site: str | None = None,
) -> Finding:
    return Finding(label=label, value=value, unit=unit, taken_on=taken_on, site=site)


def clinical(
    key: int,
    *,
    build: tuple[int, int],
    smoking: str = "Never smoked",
    alcohol: int,
    family: str,
    pressure: tuple[tuple[date, int, int], ...],
    collected: date,
    labs: tuple[LabResult, ...],
    remarks: str,
    diagnoses: tuple[Diagnosis, ...] = (),
    findings: tuple[Finding, ...] = (),
    lab_name: str = DEFAULT_LAB,
    reference: str = "SP",
) -> Clinical:
    """The clinical figures of a case; `build` is height in cm and weight in kg."""
    height_cm, weight_kg = build
    return Clinical(
        diagnoses=diagnoses,
        height_cm=height_cm,
        weight_kg=weight_kg,
        smoking_status=smoking,
        alcohol_units_per_week=alcohol,
        family_history=family,
        blood_pressure=tuple(
            BloodPressureReading(
                taken_on=taken_on, systolic_mmhg=systolic, diastolic_mmhg=diastolic
            )
            for taken_on, systolic, diastolic in pressure
        ),
        lab_name=lab_name,
        lab_specimen_reference=f"{reference}-2026-{100 + key:06d}",
        lab_collected_on=collected,
        lab_results=labs,
        physician_remarks=remarks,
        findings=findings,
    )
