"""The public guidelines the manual's thresholds rest on, and two helpers for writing rules.

Only the thresholds follow these guidelines. Every debit percentage is invented.
"""

from decimal import Decimal
from typing import Final

from synthdata.manual_model import (
    Applicability,
    Gap,
    KeyQuestion,
    Measure,
    RuleSpec,
    Source,
    Threshold,
)

DECLINE: Final = None


def rule(
    rule_id: str,
    threshold: Threshold,
    debit_pct: int | None,
    source: Source,
    *see: str,
    note: str | None = None,
    postponement: bool = False,
) -> RuleSpec:
    """One rule; `debit_pct` is `DECLINE` for a rule that declines.

    `see` are the rules its definition points to.
    """
    return RuleSpec(
        rule_id=rule_id,
        threshold=threshold,
        debit_pct=debit_pct,
        decline=debit_pct is None,
        postponement=postponement,
        source=source,
        see=see,
        note=note,
    )


def q(question: str, why: str) -> KeyQuestion:
    return KeyQuestion(question=question, why=why)


def gap(threshold: Threshold, meaning: str) -> Gap:
    """Readings that meet no rule, declared with what that means."""
    return Gap(threshold=threshold, meaning=meaning)


def diagnosed(words: str, *not_with: str) -> Applicability:
    """The rules apply only when the condition, event or treatment is on file."""
    return Applicability(basis="diagnosis", words=words, not_with=not_with)


def measured(words: str, *not_with: str) -> Applicability:
    """The rules apply to any reading, with or without a diagnosis."""
    return Applicability(basis="reading", words=words, not_with=not_with)


def _source(
    body: str, abbreviation: str, guideline: str, year: int, locator: str
) -> Source:
    return Source(
        body=body,
        abbreviation=abbreviation,
        guideline=guideline,
        year=year,
        locator=locator,
    )


# Used by more than one impairment.
HBA1C = Measure(
    key="hba1c",
    label="HbA1c",
    unit="%",
    minimum=Decimal("3.0"),
    maximum=Decimal("20.0"),
    conversion="A result in mmol/mol is multiplied by 0.0915 and 2.15 is added to give per cent: 53 mmol/mol is 7.0 %.",
    meaning="Glycated haemoglobin: the share of haemoglobin carrying glucose, which reflects average blood glucose over about three months.",
)

_ADA = ("American Diabetes Association", "ADA", "Standards of Care in Diabetes")
ADA_GOALS = _source(
    *_ADA, 2024, "section 6, Glycemic Goals and Hypoglycemia (A1C goals for adults)"
)
ADA_DIAGNOSIS = _source(
    *_ADA,
    2024,
    "section 2, Diagnosis and Classification of Diabetes (A1C criteria for prediabetes and diabetes)",
)
WHO_BMI = _source(
    "World Health Organization",
    "WHO",
    "Obesity: preventing and managing the global epidemic, WHO Technical Report Series 894",
    2000,
    "the classification of adults according to body mass index",
)
_AHA_ACC = "American Heart Association and American College of Cardiology"
CHOLESTEROL = _source(
    _AHA_ACC,
    "AHA/ACC",
    "Guideline on the Management of Blood Cholesterol",
    2018,
    "severe primary hypercholesterolemia, the risk-enhancing factors and severe hypertriglyceridemia",
)
ACR_GOUT = _source(
    "American College of Rheumatology",
    "ACR",
    "Guideline for the Management of Gout",
    2020,
    "the serum urate target for urate-lowering therapy",
)
THYROID = _source(
    "American Association of Clinical Endocrinologists and American Thyroid Association",
    "AACE/ATA",
    "Clinical Practice Guidelines for Hypothyroidism in Adults",
    2012,
    "the recommendation on treating a TSH above 10 mIU/L",
)
BLOOD_PRESSURE = _source(
    "American College of Cardiology and American Heart Association",
    "ACC/AHA",
    "Guideline for the Prevention, Detection, Evaluation, and Management of High Blood Pressure in Adults",
    2017,
    "the table of blood pressure categories and the section on hypertensive crises",
)
DAPT = _source(
    "American College of Cardiology and American Heart Association",
    "ACC/AHA",
    "Guideline Focused Update on Duration of Dual Antiplatelet Therapy in Patients With Coronary Artery Disease",
    2016,
    "the recommendations for at least 12 months of treatment after an acute coronary syndrome",
)
HEART_FAILURE = _source(
    "American Heart Association, American College of Cardiology and Heart Failure Society of America",
    "AHA/ACC/HFSA",
    "Guideline for the Management of Heart Failure",
    2022,
    "the classification of heart failure by left ventricular ejection fraction",
)
ATRIAL_FIBRILLATION = _source(
    "American College of Cardiology, American Heart Association, American College of Clinical Pharmacy and Heart Rhythm Society",
    "ACC/AHA/ACCP/HRS",
    "Guideline for the Diagnosis and Management of Atrial Fibrillation",
    2023,
    "the recommendations on stroke risk scores and anticoagulation",
)
VALVES = _source(
    "American College of Cardiology and American Heart Association",
    "ACC/AHA",
    "Guideline for the Management of Patients With Valvular Heart Disease",
    2020,
    "the stages of aortic stenosis by peak aortic jet velocity",
)
HCM_GUIDELINE = _source(
    _AHA_ACC,
    "AHA/ACC",
    "Guideline for the Diagnosis and Treatment of Patients With Hypertrophic Cardiomyopathy",
    2020,
    "the diagnostic wall thickness and the risk markers for sudden cardiac death",
)
SVS_AAA = _source(
    "Society for Vascular Surgery",
    "SVS",
    "Practice guidelines on the care of patients with an abdominal aortic aneurysm",
    2018,
    "the surveillance intervals by diameter and the threshold for elective repair",
)
PAD_GUIDELINE = _source(
    _AHA_ACC,
    "AHA/ACC",
    "Guideline on the Management of Patients With Lower Extremity Peripheral Artery Disease",
    2016,
    "the interpretation of the resting ankle-brachial index",
)
STROKE_PREVENTION = _source(
    "American Heart Association and American Stroke Association",
    "AHA/ASA",
    "Guideline for the Prevention of Stroke in Patients With Stroke and Transient Ischemic Attack",
    2021,
    "the recommendations on extracranial carotid stenosis",
)
STROKE_EARLY = _source(
    "American Heart Association and American Stroke Association",
    "AHA/ASA",
    "Guidelines for the Early Management of Patients With Acute Ischemic Stroke, 2019 update",
    2019,
    "the criteria for mechanical thrombectomy, among them an NIHSS score of 6 or more",
)
CHEST_VTE = _source(
    "American College of Chest Physicians",
    "CHEST",
    "Antithrombotic Therapy for VTE Disease, second update of the CHEST guideline and expert panel report",
    2021,
    "the three-month treatment phase of anticoagulation",
)
CDC_TOBACCO = _source(
    "Centers for Disease Control and Prevention, National Center for Health Statistics",
    "CDC",
    "National Health Interview Survey, adult tobacco use glossary",
    2017,
    "the definitions of current smoker and former smoker",
)
USPSTF_LUNG = _source(
    "US Preventive Services Task Force",
    "USPSTF",
    "Lung Cancer: Screening, recommendation statement",
    2021,
    "the 20 pack-year smoking history that defines the screened group",
)
WHO_AUDIT = _source(
    "World Health Organization",
    "WHO",
    "AUDIT, the Alcohol Use Disorders Identification Test: guidelines for use in primary health care, second edition",
    2001,
    "the risk zones by total AUDIT score",
)
CDC_OPIOIDS = _source(
    "Centers for Disease Control and Prevention",
    "CDC",
    "Guideline for Prescribing Opioids for Chronic Pain, United States",
    2016,
    "the recommendation on dosage: reassess at 50, avoid 90 morphine milligram equivalents a day or more",
)
GINA = _source(
    "Global Initiative for Asthma",
    "GINA",
    "Global Strategy for Asthma Management and Prevention",
    2023,
    "the risk factors for exacerbations, among them an FEV1 below 60 % of predicted",
)
GOLD = _source(
    "Global Initiative for Chronic Obstructive Lung Disease",
    "GOLD",
    "Global Strategy for Prevention, Diagnosis and Management of COPD",
    2024,
    "the grades of airflow obstruction by FEV1 after a bronchodilator",
)
AASM = _source(
    "American Academy of Sleep Medicine",
    "AASM",
    "Clinical Guideline for the Evaluation, Management and Long-term Care of Obstructive Sleep Apnea in Adults",
    2009,
    "the severity grades by apnoea-hypopnoea index",
)
KDIGO = _source(
    "Kidney Disease: Improving Global Outcomes",
    "KDIGO",
    "Clinical Practice Guideline for the Evaluation and Management of Chronic Kidney Disease",
    2024,
    "the GFR categories G1 to G5 and the albuminuria categories A1 to A3",
)
AASLD_HBV = _source(
    "American Association for the Study of Liver Diseases",
    "AASLD",
    "Update on Prevention, Diagnosis, and Treatment of Chronic Hepatitis B: AASLD 2018 Hepatitis B Guidance",
    2018,
    "the HBV DNA levels used to decide on treatment",
)
HCV_GUIDANCE = _source(
    "American Association for the Study of Liver Diseases and Infectious Diseases Society of America",
    "AASLD-IDSA",
    "HCV Guidance: Recommendations for Testing, Managing, and Treating Hepatitis C",
    2023,
    "the definition of sustained virologic response 12 weeks after treatment",
)
AASLD_NAFLD = _source(
    "American Association for the Study of Liver Diseases",
    "AASLD",
    "Practice Guidance on the clinical assessment and management of nonalcoholic fatty liver disease",
    2023,
    "the FIB-4 cut-offs of 1.3 and 2.67 for the risk of advanced fibrosis",
)
AASLD_TRANSPLANT = _source(
    "American Association for the Study of Liver Diseases and American Society of Transplantation",
    "AASLD/AST",
    "Evaluation for Liver Transplantation in Adults: 2013 Practice Guideline",
    2013,
    "the recommendation to evaluate at a MELD score of 15 or more",
)
AGA_UC = _source(
    "American Gastroenterological Association",
    "AGA",
    "Clinical Practice Guideline on the Role of Biomarkers for the Management of Ulcerative Colitis",
    2023,
    "the faecal calprotectin cut-off of 150 mcg/g",
)
WHO_ANAEMIA = _source(
    "World Health Organization",
    "WHO",
    "Haemoglobin concentrations for the diagnosis of anaemia and assessment of severity",
    2011,
    "the table of haemoglobin levels for mild, moderate and severe anaemia",
)
WFH = _source(
    "World Federation of Hemophilia",
    "WFH",
    "Guidelines for the Management of Hemophilia, third edition",
    2020,
    "the severity classes by clotting factor level",
)
CDC_HIV = _source(
    "Centers for Disease Control and Prevention",
    "CDC",
    "Revised Surveillance Case Definition for HIV Infection, United States",
    2014,
    "the stages of infection by CD4 lymphocyte count",
)
NICE_DEPRESSION = _source(
    "National Institute for Health and Care Excellence",
    "NICE",
    "Depression in adults: treatment and management, guideline NG222",
    2022,
    "the definition of less severe and more severe depression by PHQ-9 score",
)
ILAE = _source(
    "International League Against Epilepsy",
    "ILAE",
    "A practical clinical definition of epilepsy, official report",
    2014,
    "the definition of resolved epilepsy after ten seizure-free years",
)
ACR_RA = _source(
    "American College of Rheumatology",
    "ACR",
    "2019 Update of the Recommended Rheumatoid Arthritis Disease Activity Measures",
    2019,
    "the DAS28 cut-offs for remission, low, moderate and high disease activity",
)
WHO_BONE = _source(
    "World Health Organization",
    "WHO",
    "Assessment of fracture risk and its application to screening for postmenopausal osteoporosis, WHO Technical Report Series 843",
    1994,
    "the definition of osteoporosis as a T-score of -2.5 or lower",
)
AJCC = _source(
    "American Joint Committee on Cancer",
    "AJCC",
    "Cancer Staging Manual, eighth edition, melanoma of the skin",
    2017,
    "the T categories by tumour thickness",
)
