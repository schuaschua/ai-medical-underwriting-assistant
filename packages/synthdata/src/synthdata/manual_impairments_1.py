"""Manual content, part 1: metabolic and cardiovascular impairments.

All wording is original and written for this project. Thresholds follow the public
guideline each rule names; every debit percentage is invented.
"""

from decimal import Decimal

from synthdata.manual_model import ImpairmentSpec, Measure, OtherUnit, ReadingRule
from synthdata.manual_sources import (
    ACR_GOUT,
    ADA_DIAGNOSIS,
    ADA_GOALS,
    ATRIAL_FIBRILLATION,
    BLOOD_PRESSURE,
    CHEST_VTE,
    CHOLESTEROL,
    DAPT,
    DECLINE,
    HBA1C,
    HCM_GUIDELINE,
    HEART_FAILURE,
    PAD_GUIDELINE,
    STROKE_EARLY,
    STROKE_PREVENTION,
    SVS_AAA,
    THYROID,
    VALVES,
    WHO_BMI,
    diagnosed,
    gap,
    measured,
    q,
    rule,
)

_BMI = Measure(
    key="bmi",
    minimum=Decimal("10.0"),
    maximum=Decimal("80.0"),
    conversion="From pounds and inches: weight in pounds times 703, divided by the square of height in inches.",
    label="body mass index",
    unit="kg/m2",
    meaning="Weight in kilograms divided by the square of height in metres.",
)
_LDL = Measure(
    key="ldl_cholesterol",
    minimum=Decimal(0),
    maximum=Decimal(1000),
    conversion="A result in mmol/L is multiplied by 38.67 to give mg/dL: 4.14 mmol/L is 160 mg/dL.",
    other_units=(OtherUnit(unit="mmol/L", multiply=Decimal("38.67")),),
    label="LDL cholesterol",
    unit="mg/dL",
    meaning="Low-density lipoprotein cholesterol in a fasting blood sample, the fraction most closely tied to artery disease.",
)
_TRIGLYCERIDES = Measure(
    key="triglycerides",
    minimum=Decimal(0),
    maximum=Decimal(10000),
    conversion="A result in mmol/L is multiplied by 88.57 to give mg/dL: 2.0 mmol/L is about 177 mg/dL.",
    label="fasting triglycerides",
    unit="mg/dL",
    meaning="The main blood fat, measured after a fast of at least eight hours.",
)
_URATE = Measure(
    key="serum_urate",
    minimum=Decimal("0.0"),
    maximum=Decimal("25.0"),
    conversion="A result in micromol/L is divided by 59.48 to give mg/dL: 357 micromol/L is 6.0 mg/dL.",
    label="serum urate",
    unit="mg/dL",
    meaning="The level of uric acid in the blood; crystals form in joints when it stays high.",
)
_TSH = Measure(
    key="tsh",
    minimum=Decimal("0.0"),
    maximum=Decimal("500.0"),
    label="TSH",
    unit="mIU/L",
    meaning="Thyroid-stimulating hormone; it rises when the thyroid gland makes too little hormone.",
)
_SYSTOLIC = Measure(
    key="systolic_blood_pressure",
    minimum=Decimal(50),
    maximum=Decimal(300),
    conversion="A reading in kPa is multiplied by 7.5 to give mmHg.",
    label="systolic blood pressure",
    unit="mmHg",
    meaning="The higher of the two blood pressure numbers: the pressure while the heart contracts.",
)
_DIASTOLIC = Measure(
    key="diastolic_blood_pressure",
    minimum=Decimal(20),
    maximum=Decimal(200),
    conversion="A reading in kPa is multiplied by 7.5 to give mmHg.",
    label="diastolic blood pressure",
    unit="mmHg",
    meaning="The lower of the two blood pressure numbers: the pressure while the heart rests between beats.",
)
_MONTHS_SINCE_MI = Measure(
    key="months_since_myocardial_infarction",
    minimum=Decimal(0),
    label="time since the heart attack",
    unit="months",
    meaning="Whole months from the date of the most recent heart attack to the date of the application.",
)
_LVEF = Measure(
    key="left_ventricular_ejection_fraction",
    minimum=Decimal(5),
    maximum=Decimal(90),
    label="left ventricular ejection fraction",
    unit="%",
    meaning="The share of the blood in the left ventricle that is pumped out with each beat, usually from an echocardiogram.",
)
_CHADS = Measure(
    key="cha2ds2_vasc_score",
    minimum=Decimal(0),
    maximum=Decimal(9),
    label="CHA2DS2-VASc score",
    unit="points",
    meaning="A stroke risk score for atrial fibrillation built from age, sex, heart failure, hypertension, diabetes, vascular disease and earlier stroke.",
)
_JET = Measure(
    key="aortic_jet_velocity",
    minimum=Decimal("0.5"),
    maximum=Decimal("8.0"),
    label="peak aortic jet velocity",
    unit="m/s",
    meaning="The fastest blood flow through the aortic valve on echocardiography; it rises as the valve narrows.",
)
_WALL = Measure(
    key="left_ventricular_wall_thickness",
    minimum=Decimal(5),
    maximum=Decimal(60),
    conversion="A thickness in cm is multiplied by ten to give mm.",
    label="maximum left ventricular wall thickness",
    unit="mm",
    meaning="The thickest part of the wall of the heart's main pumping chamber, on echocardiogram or cardiac MRI.",
)
_AORTA = Measure(
    key="abdominal_aortic_diameter",
    minimum=Decimal("1.0"),
    maximum=Decimal("15.0"),
    conversion="A diameter in mm is divided by ten to give cm.",
    label="abdominal aortic diameter",
    unit="cm",
    meaning="The widest outer diameter of the aorta in the abdomen on ultrasound or CT.",
)
_ABI = Measure(
    key="ankle_brachial_index",
    minimum=Decimal("0.00"),
    maximum=Decimal("3.00"),
    label="resting ankle-brachial index",
    unit="ratio",
    unit_printed=False,
    meaning="Systolic pressure at the ankle divided by systolic pressure in the arm; a low value means narrowed leg arteries.",
)
_CAROTID = Measure(
    key="carotid_stenosis",
    minimum=Decimal(0),
    maximum=Decimal(100),
    label="carotid artery stenosis",
    unit="%",
    meaning="How much of the internal carotid artery's width is lost to plaque, as reported on duplex ultrasound or angiography.",
)
_NIHSS = Measure(
    key="nihss_score_at_admission",
    minimum=Decimal(0),
    maximum=Decimal(42),
    label="NIHSS score at admission",
    unit="points",
    meaning="The National Institutes of Health Stroke Scale, from 0 to 42, scored when the patient reaches hospital; a higher score is a more severe stroke.",
)
_MONTHS_SINCE_VTE = Measure(
    key="months_since_venous_thromboembolism",
    minimum=Decimal(0),
    label="time since the clot",
    unit="months",
    meaning="Whole months from the diagnosis of the most recent deep vein thrombosis or pulmonary embolism to the date of the application.",
)

IMPAIRMENTS_1: tuple[ImpairmentSpec, ...] = (
    ImpairmentSpec(
        impairment_id="type_2_diabetes",
        code="DM",
        name="Type 2 diabetes mellitus",
        phrase="type 2 diabetes",
        applies=diagnosed(
            "A diagnosis of type 2 diabetes is on file. A raised HbA1c with no diagnosis is read in the prediabetes section instead.",
            "type_1_diabetes",
        ),
        measures=(HBA1C,),
        overview=(
            "Type 2 diabetes is a long-term rise in blood glucose caused by resistance to insulin and, later, by falling insulin production. It usually starts in adult life and is often found on a routine blood test rather than through symptoms.",
            "The extra mortality comes mostly from disease of the heart, the brain's blood supply and the kidneys. How high the glucose has run, and for how many years, is the best single guide to that risk, which is why this section rates on HbA1c.",
            "An applicant treated with tablets alone and an applicant on insulin are rated by the same bands. Treatment tells the underwriter how far the disease has moved on, but the reading tells them how well it is held.",
        ),
        questions=(
            q(
                "When was diabetes diagnosed, and how old was the applicant?",
                "Risk builds with the number of years of raised glucose, so a diagnosis at 35 weighs more than the same reading first found at 60.",
            ),
            q(
                "What is the most recent HbA1c, and what were the two before it?",
                "One reading can flatter or mislead. Three readings show whether control is steady, improving or slipping.",
            ),
            q(
                "Which treatment is used: diet, tablets, injections other than insulin, or insulin?",
                "A move to insulin in type 2 diabetes usually means the disease has been present long enough to exhaust the pancreas.",
            ),
            q(
                "Has an eye, kidney or foot check found any damage?",
                "Damage to small vessels shows that past control was poor, whatever the latest reading says.",
            ),
            q(
                "Are blood pressure and cholesterol treated?",
                "Diabetes with untreated blood pressure or lipids carries more risk than the sum of its parts would suggest.",
            ),
        ),
        reading_rules=(
            ReadingRule(measure="hba1c", choose="most_recent", within_months=12),
        ),
        evidence=(
            "Use the HbA1c from a laboratory report or an attending physician's statement dated within the last twelve months. A figure the applicant recalls on the application form is a prompt to obtain evidence, not a reading to rate on.",
            "Mention in the notes any earlier reading from the last twelve months that sits two bands higher than the one rated. A fasting glucose on its own does not place the applicant in a band; ask for an HbA1c.",
            "If the laboratory reports HbA1c in mmol/mol, convert it as shown below before reading the table, and record both figures.",
        ),
        pitfalls=(
            "A recent fall in HbA1c of two points or more within six months is often the first effect of a new medicine and may not last. Rate on it, but record the earlier figure so that a person can see the trend.",
            "Anaemia, recent blood loss, a transfusion and some inherited haemoglobin variants make HbA1c read falsely low or high. Where the full blood count shows a haemoglobin under 11.0 g/dL, say so beside the reading.",
            "The words well controlled in a physician's statement are an opinion. The number is the evidence.",
        ),
        rating_note="The edges at 7.0 % and 8.0 % follow the treatment goals in the cited standards. The steps at 9.0 % and 10.0 % are this manual's own, set so that each band is one percentage point wide.",
        own_edges=(
            Decimal("9.0"),
            Decimal("10.0"),
        ),
        unchanged=(
            "The type of treatment does not move an applicant between bands: diet alone, tablets, injected medicines other than insulin and insulin are all read on the same table.",
            "A family history of diabetes adds nothing once the applicant has the diagnosis. A fasting glucose, however high or low, does not replace or adjust the HbA1c band.",
        ),
        combinations=(
            "Diabetes rarely comes alone. Raised blood pressure, excess weight and raised LDL cholesterol are each rated in their own section and their debits are added to the diabetes debit; none of them is folded into it.",
            "Kidney findings matter most. Albumin in the urine or a reduced eGFR in a person with diabetes shows damage to small vessels, and the kidney sections' debits are added in full. Diabetes together with a past heart attack or stroke is common and heavy: add the debits, and expect the total to pass +200 %.",
        ),
        rules=(
            rule(
                "UW-DM-001",
                HBA1C.below("7.0"),
                25,
                ADA_GOALS,
                "UW-ALB-001",
                note="Control at the usual treatment goal.",
            ),
            rule(
                "UW-DM-002",
                HBA1C.between("7.0", "8.0"),
                50,
                ADA_GOALS,
                "UW-HT-002",
                note="Above the usual goal but inside the less stringent one.",
            ),
            rule(
                "UW-DM-003",
                HBA1C.between("8.0", "9.0"),
                100,
                ADA_GOALS,
                "UW-CKD-001",
            ),
            rule(
                "UW-DM-004",
                HBA1C.between("9.0", "10.0"),
                150,
                ADA_GOALS,
                "UW-BMI-003",
            ),
            rule(
                "UW-DM-005",
                HBA1C.at_least("10.0"),
                DECLINE,
                ADA_GOALS,
                note="The applicant may apply again once two readings three months apart are below 10.0 %.",
                postponement=True,
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="type_1_diabetes",
        code="DI",
        name="Type 1 diabetes mellitus",
        phrase="type 1 diabetes",
        applies=diagnosed(
            "A diagnosis of type 1 diabetes is on file, whatever the age at which it began.",
            "type_2_diabetes",
        ),
        measures=(HBA1C,),
        overview=(
            "Type 1 diabetes follows the loss of the insulin-producing cells of the pancreas. It usually begins in childhood or early adult life, and insulin is needed from the start and for life.",
            "Because it starts early, an applicant of 40 may already have lived with raised glucose for 30 years. The bands are therefore stricter than for type 2 diabetes at the same HbA1c, and the highest band closes one step sooner.",
        ),
        questions=(
            q(
                "At what age did insulin treatment begin?",
                "The years lived with the disease matter more here than the applicant's present age.",
            ),
            q(
                "How is insulin given, and is glucose monitored continuously?",
                "A pump or a sensor does not earn a better rating by itself, but it explains a stable record.",
            ),
            q(
                "Has there been a hospital admission for ketoacidosis or a severe low in the last two years?",
                "Either event points to unstable control that an average such as HbA1c can hide.",
            ),
            q(
                "What did the last kidney and eye screening show?",
                "Protein in the urine is the earliest sign of the complication that shortens life most in type 1 diabetes.",
            ),
        ),
        reading_rules=(
            ReadingRule(measure="hba1c", choose="most_recent", within_months=12),
        ),
        evidence=(
            "Rate on a laboratory HbA1c. A sensor's estimate of average glucose is useful background but is not a substitute for the laboratory figure.",
            "A record of severe hypoglycaemia needing another person's help, or of ketoacidosis, within two years is outside these bands: send the case to a person with the hospital letter attached.",
        ),
        pitfalls=(
            "Do not infer the type from the treatment. Many people with type 2 diabetes take insulin; the diagnosis written by the physician decides which section applies, and a file that leaves the type unclear goes to a person.",
            "A very good HbA1c reached at the cost of frequent low glucose is not good control. If the clinic letter mentions hypoglycaemia needing help, the band alone understates the risk.",
        ),
        rating_note="The edges at 7.0 % and 8.0 % follow the goals in the cited standards; the edge at 9.0 % is this manual's own.",
        own_edges=(Decimal("9.0"),),
        unchanged=(
            "An insulin pump, a continuous sensor or a closed-loop system does not change the band. The age of the applicant does not change it either, though the duration of the disease is noted for the person who reviews the case.",
            "A stable HbA1c for many years is reassuring but earns no reduction.",
        ),
        combinations=(
            "The kidney is the usual second finding. Albumin in the urine after ten or more years of type 1 diabetes is rated in the albuminuria section and added; with a reduced eGFR as well, all three debits are added.",
            "Thyroid disease and coeliac disease share an autoimmune cause with type 1 diabetes. A treated underactive thyroid with a settled TSH adds no debit. Raised blood pressure is added as it would be for anyone.",
        ),
        rules=(
            rule(
                "UW-DI-001",
                HBA1C.below("7.0"),
                75,
                ADA_GOALS,
                "UW-ALB-001",
            ),
            rule("UW-DI-002", HBA1C.between("7.0", "8.0"), 100, ADA_GOALS),
            rule(
                "UW-DI-003",
                HBA1C.between("8.0", "9.0"),
                150,
                ADA_GOALS,
                "UW-CKD-001",
            ),
            rule("UW-DI-004", HBA1C.at_least("9.0"), DECLINE, ADA_GOALS),
        ),
    ),
    ImpairmentSpec(
        impairment_id="prediabetes",
        code="PD",
        name="Prediabetes",
        phrase="prediabetes",
        applies=measured(
            "A laboratory HbA1c is on file and no diagnosis of diabetes of either type has been made.",
            "type_2_diabetes",
            "type_1_diabetes",
        ),
        measures=(HBA1C,),
        overview=(
            "Prediabetes means glucose runs above the normal range without reaching the level that defines diabetes. Many people stay in this range for years; some return to normal with weight loss, and some go on to diabetes.",
            "On its own the finding adds little mortality, so its single rule carries no debit. The section exists so that a reading in this range is recognised, named and followed to the right place if it later rises.",
        ),
        questions=(
            q(
                "Was the reading repeated, and what was the second result?",
                "A single borderline reading is often normal on repeat.",
            ),
            q(
                "Has a doctor ever used the word diabetes, or prescribed metformin?",
                "Treatment with a glucose-lowering medicine suggests the diagnosis has already moved on.",
            ),
            q(
                "What are the weight, blood pressure and lipids?",
                "Prediabetes matters mainly as part of a cluster; each of the others has its own section.",
            ),
        ),
        evidence=(
            "Use a laboratory HbA1c from the last twelve months. Where two readings from that period fall on either side of 6.5 %, treat the applicant as having diabetes until a third reading settles it.",
            "A raised fasting glucose without an HbA1c is not enough to apply this rule; ask for the HbA1c.",
        ),
        pitfalls=(
            "The label matters. Notes that say impaired glucose tolerance, borderline diabetes or glucose intolerance all mean a reading in this range; notes that say diabetes, diet controlled, mean the diagnosis has been made and this section no longer applies.",
            "Metformin prescribed for polycystic ovaries is not treatment for diabetes. Check the reason before treating a prescription as a diagnosis.",
        ),
        rating_note="Both edges are the diagnostic criteria in the cited standards. Below 5.7 % no rule of this section applies.",
        gaps=(
            gap(HBA1C.below("5.7"), "a normal reading; no rule and no debit"),
            gap(
                HBA1C.at_least("6.5"),
                "the level that defines diabetes; ask whether the diagnosis has been made, and read the diabetes section once it has",
            ),
        ),
        unchanged=(
            "Advice to lose weight, a referral to a prevention programme or a repeat test booked for next year does not change the outcome: the single rule carries no debit.",
            "A parent with diabetes does not add a debit to a reading in this range.",
        ),
        combinations=(
            "Prediabetes with a body mass index of 35 kg/m2 or more, raised blood pressure and raised triglycerides is the familiar metabolic cluster. Each member is rated in its own section; this one contributes nothing, and the others are added as usual.",
            "If a later reading reaches 6.5 %, or the physician records diabetes, the diabetes section takes over and its rule replaces this one. The two are never added together.",
        ),
        rules=(
            rule(
                "UW-PD-001",
                HBA1C.between("5.7", "6.5"),
                0,
                ADA_DIAGNOSIS,
                "UW-DM-001",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="build",
        code="BMI",
        name="Build: underweight and obesity",
        phrase="an unusual build",
        applies=measured(
            "Every applicant: height and weight are on file for all of them."
        ),
        measures=(_BMI,),
        overview=(
            "Build is the relation of weight to height. Mortality is lowest across a broad middle range and rises at both ends: at the low end through frailty, undiagnosed illness and eating disorders, and at the high end through diabetes, heart disease and sleep apnoea.",
            "This section rates the two ends only. A body mass index from 18.5 to below 35 kg/m2 meets no rule here, although the conditions that travel with weight are rated in their own sections.",
        ),
        questions=(
            q(
                "Were height and weight measured by a clinician, or stated by the applicant?",
                "Stated weight tends to be lower, and stated height taller, than measured.",
            ),
            q(
                "Has weight changed by more than 5 kg in the last twelve months, and was the change intended?",
                "Unintended loss needs an explanation before any rating is offered.",
            ),
            q(
                "For a low weight: is there a history of an eating disorder, bowel disease or cancer?",
                "A low body mass index is a finding, not a diagnosis, and the cause decides the risk.",
            ),
            q(
                "For a high weight: has there been weight-loss surgery, and when?",
                "The first year after surgery has its own risks and its own unstable weight.",
            ),
        ),
        evidence=(
            "Work out the body mass index from the measured height and weight in the attending physician's statement where there is one; otherwise from the application form. Round to one decimal place before reading the table.",
            "Where measured and stated figures put the applicant in different bands, rate on the measured figure and say so in the notes.",
        ),
        pitfalls=(
            "Weight on an application form is often rounded down and months old. A clinic weight in the attending physician's statement is preferred even when it is less flattering.",
            "Body mass index overstates fatness in a very muscular person and understates it in a frail one. The manual does not adjust for either; where the physician comments on it, pass the comment to a person.",
            "Weight during pregnancy is not rated. Use the last weight recorded before it.",
        ),
        rating_note="All four edges are those of the cited classification: severe thinness below 16, underweight below 18.5, obesity class II from 35 and class III from 40.",
        gaps=(
            gap(
                _BMI.between("18.5", "35.0"),
                "no rule; no debit for build in this range",
            ),
        ),
        unchanged=(
            "Waist measurement, body fat percentage and the applicant's own account of fitness do not move the band. Neither does weight lost within the last twelve months: the present figure is rated, and the earlier one is noted.",
            "A body mass index from 18.5 to below 35 kg/m2 carries no debit, including the whole of the overweight range and obesity class I.",
        ),
        combinations=(
            "High weight travels with diabetes, raised blood pressure, sleep apnoea and fatty liver disease. Each is rated in its own section and added to the build debit; the build debit is not reduced because the others are present.",
            "Low weight with anaemia, an inflammatory bowel disease or chronic lung disease points to illness rather than constitution: add the debits, and send to a person where the weight loss is unexplained.",
        ),
        rules=(
            rule(
                "UW-BMI-001",
                _BMI.below("16.0"),
                100,
                WHO_BMI,
                "UW-ANA-001",
            ),
            rule(
                "UW-BMI-002",
                _BMI.between("16.0", "18.5"),
                25,
                WHO_BMI,
            ),
            rule(
                "UW-BMI-003",
                _BMI.between("35.0", "40.0"),
                50,
                WHO_BMI,
                "UW-OSA-002",
            ),
            rule(
                "UW-BMI-004",
                _BMI.at_least("40.0"),
                100,
                WHO_BMI,
                "UW-DM-002",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="raised_ldl_cholesterol",
        code="LDL",
        name="Raised LDL cholesterol",
        phrase="raised LDL cholesterol",
        applies=measured(
            "An LDL cholesterol result is on file, with or without a diagnosis or treatment."
        ),
        measures=(_LDL,),
        overview=(
            "LDL cholesterol carries cholesterol into the artery wall. The higher it runs and the longer it stays high, the sooner fatty plaques narrow the coronary arteries.",
            "Very high levels from early adult life usually have an inherited cause. This section rates the level itself; a heart attack or other event that has already happened is rated in its own section and the two are combined.",
        ),
        questions=(
            q(
                "Was the sample taken fasting, and is the applicant on a statin?",
                "The table is read with the level on treatment, if treatment is taken as prescribed.",
            ),
            q(
                "What was the highest level ever recorded before treatment?",
                "An untreated level of 190 mg/dL or more suggests an inherited disorder, which matters for the family as well.",
            ),
            q(
                "Has a parent, brother or sister had a heart attack before the age of 60?",
                "Early disease in the family strengthens the case for an inherited cause.",
            ),
        ),
        evidence=(
            "Use the LDL figure from a laboratory report in the last twelve months. If only total cholesterol is given, ask for the full lipid profile; do not estimate LDL from the total.",
            "Where the report is in mmol/L, convert it as shown below and record the original figure beside the converted one.",
        ),
        pitfalls=(
            "An LDL figure worked out by the laboratory's formula is unreliable when triglycerides are above 400 mg/dL; the report usually says so. Ask for a directly measured LDL.",
            "A result taken within eight weeks of a heart attack or a major illness reads falsely low. A result taken in the first weeks of statin treatment has not yet settled.",
        ),
        rating_note="The edge at 190 mg/dL is the cited guideline's definition of severe primary hypercholesterolemia; 160 mg/dL is its risk-enhancing level.",
        gaps=(gap(_LDL.below("160"), "no rule; no debit for LDL cholesterol"),),
        unchanged=(
            "Total cholesterol, HDL cholesterol and the ratio between them do not change the band; only LDL is rated. Taking a statin is not penalised and earns no credit beyond the lower reading it produces.",
            "An LDL below 160 mg/dL meets no rule, however high it was before treatment.",
        ),
        combinations=(
            "Raised LDL with raised blood pressure, smoking or diabetes multiplies the chance of a heart attack; in this manual the debits are simply added, each from its own section.",
            "After a heart attack the LDL debit is added to the debit for the event. An untreated LDL of 190 mg/dL or more with early heart disease in a parent suggests an inherited disorder: rate by the table and tell the person reviewing the case.",
        ),
        rules=(
            rule(
                "UW-LDL-001",
                _LDL.between("160", "190"),
                25,
                CHOLESTEROL,
                "UW-HT-001",
            ),
            rule(
                "UW-LDL-002",
                _LDL.at_least("190"),
                50,
                CHOLESTEROL,
                "UW-MI-002",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="raised_triglycerides",
        code="TG",
        name="Raised triglycerides",
        phrase="raised triglycerides",
        applies=measured("A fasting triglyceride result is on file."),
        measures=(_TRIGLYCERIDES,),
        overview=(
            "Triglycerides are the fat the body stores and burns. A moderate rise usually reflects weight, alcohol, diabetes or a medicine; a severe rise brings a real risk of inflammation of the pancreas.",
            "The reading swings widely after food, so only a fasting sample is rated.",
        ),
        questions=(
            q(
                "How long had the applicant fasted before the sample?",
                "A sample taken after a meal can read twice as high as a fasting one.",
            ),
            q(
                "How much alcohol is taken in a usual week?",
                "Alcohol is the commonest reversible cause of a high reading.",
            ),
            q(
                "Has there ever been pancreatitis?",
                "A past attack with a level at or above 500 mg/dL changes the case from a laboratory finding to a disease.",
            ),
        ),
        evidence=(
            "Rate on a fasting laboratory result from the last twelve months. If the report does not say whether the sample was fasting, treat a level under 175 mg/dL as acceptable and ask for a fasting repeat for anything higher.",
            "A history of pancreatitis is outside these bands and goes to a person.",
        ),
        pitfalls=(
            "The commonest error is rating a sample taken after food. Look for the word fasting on the report or in the clinic note.",
            "Triglycerides fall quickly after a few days without alcohol, so a single good reading soon after a bad one proves little. Where two results a month apart differ by half or more, use the higher and say why.",
        ),
        rating_note="175 mg/dL and 500 mg/dL are the cited guideline's levels for a persistent rise and for severe hypertriglyceridemia.",
        gaps=(gap(_TRIGLYCERIDES.below("175"), "no rule; no debit for triglycerides"),),
        unchanged=(
            "Fish oil capsules, a fibrate or a statin on the prescription list does not change the band. A reading under 175 mg/dL meets no rule, even if earlier readings were far higher.",
        ),
        combinations=(
            "Raised triglycerides usually sit alongside excess weight, diabetes or heavy drinking, and the reading often falls when those are dealt with. Each has its own section; the debits are added.",
            "A level of 500 mg/dL or more with an AUDIT score in the hazardous range is a combination to take seriously, because both lead to pancreatitis.",
        ),
        rules=(
            rule("UW-TG-001", _TRIGLYCERIDES.between("175", "500"), 25, CHOLESTEROL),
            rule(
                "UW-TG-002",
                _TRIGLYCERIDES.at_least("500"),
                75,
                CHOLESTEROL,
                "UW-ALC-001",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="gout",
        code="GOUT",
        name="Gout",
        phrase="gout",
        applies=diagnosed(
            "Gout has been diagnosed. A raised urate in someone who has never had an attack is not gout and meets no rule here."
        ),
        measures=(_URATE,),
        overview=(
            "Gout is arthritis caused by urate crystals, classically a sudden, very painful swelling of the big toe. Attacks pass, but an untreated high urate brings more of them, joint damage and kidney stones.",
            "Gout shortens life only modestly, and mostly through the company it keeps: high blood pressure, kidney disease and excess weight. The rating is small and looks to whether urate is at target.",
        ),
        questions=(
            q(
                "How many attacks were there in the last twelve months?",
                "Two or more attacks a year means the disease is not controlled, whatever medicine is taken.",
            ),
            q(
                "Is a urate-lowering medicine taken every day?",
                "Treatment only during attacks leaves the cause untouched.",
            ),
            q(
                "Has kidney function been checked?",
                "Poor kidneys both cause a high urate and are harmed by it.",
            ),
        ),
        evidence=(
            "Use a serum urate measured at least two weeks after an attack, since the level can fall misleadingly during one.",
            "No urate on file and no attack for five years: no rule of this section applies, and the history is simply noted.",
        ),
        pitfalls=(
            "Urate falls during an acute attack, so a normal level taken that week does not show control. Check the date of the sample against the dates of attacks.",
            "Colchicine and anti-inflammatory tablets treat attacks and do nothing to urate. Only allopurinol, febuxostat or a similar medicine counts as urate-lowering treatment.",
        ),
        rating_note="6 mg/dL is the treatment target in the cited guideline.",
        unchanged=(
            "The joint affected, visible deposits under the skin and the applicant's diet do not change the band. Nor does the number of years since diagnosis.",
        ),
        combinations=(
            "Gout with reduced kidney function is the combination to look for: each worsens the other, and anti-inflammatory tablets taken for attacks harm the kidney further. Add the kidney section's debit.",
            "Gout also keeps company with raised blood pressure and excess weight. Water tablets given for blood pressure raise urate; that explains a reading but does not excuse it.",
        ),
        rules=(
            rule("UW-GOUT-001", _URATE.below("6.0"), 0, ACR_GOUT),
            rule(
                "UW-GOUT-002",
                _URATE.at_least("6.0"),
                25,
                ACR_GOUT,
                "UW-CKD-001",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="hypothyroidism",
        code="THY",
        name="Hypothyroidism",
        phrase="an underactive thyroid",
        applies=diagnosed("An underactive thyroid has been diagnosed, treated or not."),
        measures=(_TSH,),
        overview=(
            "An underactive thyroid makes too little thyroid hormone. The usual causes are autoimmune inflammation of the gland and earlier treatment for an overactive one. Replacement with a daily tablet restores normal health.",
            "A treated applicant with a settled TSH is a standard risk. A TSH that stays high shows the dose is too low or tablets are missed, and long neglect raises cholesterol and strains the heart.",
        ),
        questions=(
            q(
                "What is the dose of levothyroxine, and when did it last change?",
                "A dose changed within three months means the TSH on file may not yet reflect it.",
            ),
            q(
                "Why is the thyroid underactive?",
                "After surgery for thyroid cancer the cancer, not the hormone level, is the impairment.",
            ),
            q(
                "When was TSH last measured?",
                "Yearly testing is the sign of a followed-up patient.",
            ),
        ),
        reading_rules=(
            ReadingRule(measure="tsh", choose="most_recent", within_months=18),
        ),
        evidence=(
            "Use a TSH from the last eighteen months. If the dose changed after that test, ask for a newer one.",
            "A TSH below the laboratory's range on treatment suggests too high a dose; it meets no rule here, but mention it in the notes.",
        ),
        pitfalls=(
            "TSH takes six to eight weeks to settle after a change of dose. A result taken sooner than that reflects the old dose.",
            "Biotin supplements interfere with some laboratory methods and can make TSH read falsely low. A surprising result in someone taking supplements should be repeated.",
        ),
        rating_note="10 mIU/L is the level above which the cited guidelines advise treatment even without symptoms.",
        unchanged=(
            "The dose of levothyroxine, the cause of the underactivity and the presence of thyroid antibodies do not change the band. A TSH below the laboratory's range meets the first rule and is noted.",
        ),
        combinations=(
            "An underactive thyroid that is not replaced raises LDL cholesterol; the LDL section's debit is added, and both usually fall once the dose is right.",
            "After treatment of an overactive thyroid with radioiodine or surgery, only the present replacement is rated. After surgery for thyroid cancer, this section still applies to the TSH, and the cancer goes to a person.",
        ),
        rules=(
            rule("UW-THY-001", _TSH.below("10.0"), 0, THYROID),
            rule(
                "UW-THY-002",
                _TSH.at_least("10.0"),
                25,
                THYROID,
                "UW-LDL-001",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="hypertension",
        code="HT",
        name="Hypertension",
        phrase="hypertension",
        applies=measured(
            "Blood pressure readings from the last twelve months are on file, whether or not hypertension has been diagnosed or is treated."
        ),
        measures=(_SYSTOLIC, _DIASTOLIC),
        overview=(
            "Hypertension is blood pressure that stays above the normal range. It rarely causes symptoms, and it is the largest single treatable cause of stroke, heart failure and kidney failure.",
            "This section rates on the systolic reading, because in adults of insuring age it tracks risk more closely than the diastolic one. The diastolic reading has one rule of its own, for the very high level that marks a crisis.",
            "Treatment is not penalised. An applicant whose readings are held below 130 mmHg by medicine meets no rule in this section.",
        ),
        questions=(
            q(
                "What were the last three readings, with their dates?",
                "Blood pressure varies from visit to visit; the rating rests on the average of recent readings, not the best or the worst.",
            ),
            q(
                "Which medicines are taken, and has the number risen in the last year?",
                "A third or fourth medicine suggests pressure that resists treatment.",
            ),
            q(
                "Has an ECG, echocardiogram or kidney test shown any effect on an organ?",
                "Thickening of the heart muscle or protein in the urine shows the pressure has been high for years.",
            ),
            q(
                "Were the readings taken in a clinic, at home or over 24 hours?",
                "Home and 24-hour readings run a little lower than clinic readings, and a file should not mix them without saying so.",
            ),
        ),
        reading_rules=(
            ReadingRule(
                measure="systolic_blood_pressure", choose="average", within_months=12
            ),
            ReadingRule(
                measure="diastolic_blood_pressure", choose="highest", within_months=12
            ),
        ),
        evidence=(
            "With one systolic reading only, use it, and say in the notes that it stands alone.",
            "A reading taken during acute pain or illness is left out when the record says so.",
            "The diastolic rule is not read on an average, because it is there to catch a crisis.",
        ),
        pitfalls=(
            "A single reading taken at an insurance examination tends to run high. Where the physician's own readings are lower and there are three or more of them, the average of the physician's readings is used.",
            "Check that the two numbers have not been transposed or the systolic mistaken for a pulse rate. A reading such as 82/130 is a transcription error; ask for the original.",
            "Readings taken with a cuff too small for a large arm overstate the pressure. The manual does not adjust for this.",
        ),
        rating_note="130, 140 and 180 mmHg systolic and 120 mmHg diastolic are the cited guideline's stage 1, stage 2 and crisis levels. The edge at 160 mmHg is this manual's own, to split stage 2. Rating on the systolic reading alone departs from the cited classification, which places a person in the higher of the stages given by the systolic and the diastolic reading; here a diastolic reading from 80 to below 120 mmHg does not raise the rating.",
        own_edges=(Decimal(160),),
        gaps=(
            gap(
                _SYSTOLIC.below("130"),
                "no rule; normal or elevated pressure, or hypertension held at goal, carries no debit",
            ),
            gap(
                _DIASTOLIC.below("120"),
                "no rule of its own; the systolic rules decide the rating",
            ),
        ),
        unchanged=(
            "The number and names of the medicines do not change the band, and neither does the number of years since diagnosis. A diagnosis of hypertension with readings held below 130 mmHg meets no rule.",
            "White-coat hypertension confirmed by 24-hour monitoring is rated on the monitored average.",
        ),
        combinations=(
            "Hypertension is the commonest partner of every other cardiovascular and kidney impairment. Its debit is added to the debit for diabetes, smoking, kidney disease or a past stroke; it is never treated as already included in them.",
            "With reduced kidney function, pressure that is not controlled speeds the loss of the kidney. With sleep apnoea, pressure often resists treatment until the apnoea is treated.",
        ),
        rules=(
            rule(
                "UW-HT-001",
                _SYSTOLIC.between("130", "140"),
                25,
                BLOOD_PRESSURE,
                note="Stage 1 by the systolic reading.",
            ),
            rule(
                "UW-HT-002",
                _SYSTOLIC.between("140", "160"),
                50,
                BLOOD_PRESSURE,
                "UW-TOB-001",
                note="The lower part of stage 2.",
            ),
            rule(
                "UW-HT-003",
                _SYSTOLIC.between("160", "180"),
                100,
                BLOOD_PRESSURE,
                "UW-CKD-001",
                note="The upper part of stage 2.",
            ),
            rule(
                "UW-HT-004",
                _SYSTOLIC.at_least("180"),
                DECLINE,
                BLOOD_PRESSURE,
                "UW-HT-005",
            ),
            rule(
                "UW-HT-005",
                _DIASTOLIC.at_least("120"),
                DECLINE,
                BLOOD_PRESSURE,
                "UW-HT-004",
                note="A diastolic reading below 120 mmHg does not change the rating given by the systolic rules.",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="myocardial_infarction",
        code="MI",
        name="Myocardial infarction",
        phrase="a past heart attack",
        applies=diagnosed("A heart attack is recorded in the history."),
        measures=(_MONTHS_SINCE_MI,),
        overview=(
            "A myocardial infarction, or heart attack, is the death of heart muscle after a coronary artery is blocked. Survival has improved greatly, but the risk of another event and of sudden death is highest in the first year and never returns fully to normal.",
            "This section rates by the time that has passed. How much muscle was lost is rated separately through the ejection fraction.",
        ),
        questions=(
            q(
                "On what date did the heart attack happen, and was there more than one?",
                "The clock in the table starts at the most recent event.",
            ),
            q(
                "Was a stent placed or bypass surgery done?",
                "Restored blood flow improves the outlook, and the procedure note usually states how many arteries are diseased.",
            ),
            q(
                "What was the ejection fraction at the latest echocardiogram?",
                "It measures the damage left behind and is the strongest guide to long-term survival.",
            ),
            q(
                "Has chest pain returned, and is the applicant still smoking?",
                "Either one marks a course that is not settled.",
            ),
        ),
        evidence=(
            "Take the date of the event from the hospital discharge letter or the attending physician's statement, and count whole months to the date of the application.",
            "If there is no echocardiogram after the event, ask for one before offering terms beyond twelve months.",
        ),
        pitfalls=(
            "The words angina, acute coronary syndrome and unstable angina are not the same as a heart attack. Look for the diagnosis in the discharge letter; a raised troponin with the word infarction settles it.",
            "A stent placed for stable narrowing without any heart attack is not rated here and goes to a person.",
        ),
        rating_note="Twelve months matches the period for which the cited guideline advises two antiplatelet medicines after an acute coronary syndrome. The edge at 60 months is this manual's own.",
        own_edges=(Decimal(60),),
        unchanged=(
            "Whether the artery was opened with a stent, bypassed or treated with medicines alone does not change the band. Nor does the part of the heart affected, or a normal exercise test afterwards.",
        ),
        combinations=(
            "The ejection fraction measured after the event decides most of the long-term outlook. Below 50 %, the heart failure section's debit is added to the debit here.",
            "Continued smoking after a heart attack, an LDL cholesterol still at 160 mg/dL or more and diabetes are each added from their own sections. A second heart attack restarts the clock and goes to a person.",
        ),
        rules=(
            rule(
                "UW-MI-001",
                _MONTHS_SINCE_MI.below("12"),
                DECLINE,
                DAPT,
                note="This is a postponement: the applicant may apply again after twelve months.",
                postponement=True,
            ),
            rule(
                "UW-MI-002",
                _MONTHS_SINCE_MI.between("12", "60"),
                100,
                DAPT,
                "UW-HF-002",
            ),
            rule(
                "UW-MI-003",
                _MONTHS_SINCE_MI.at_least("60"),
                50,
                DAPT,
                "UW-LDL-001",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="heart_failure",
        code="HF",
        name="Heart failure",
        phrase="heart failure",
        applies=diagnosed("Heart failure has been diagnosed."),
        measures=(_LVEF,),
        overview=(
            "Heart failure means the heart cannot pump enough blood for the body's needs without raised filling pressures. Breathlessness, tiredness and swollen ankles are the usual complaints.",
            "It is always a serious impairment. The ejection fraction separates the forms with the worst outlook from those that can be considered at a heavy rating.",
        ),
        questions=(
            q(
                "What caused the heart failure?",
                "Failure after a heart attack, from a valve, from alcohol and from high blood pressure have different courses.",
            ),
            q(
                "What was the ejection fraction on the latest echocardiogram, and on the one before?",
                "A fraction that has recovered on treatment is better than the same figure reached on the way down.",
            ),
            q(
                "Has there been a hospital admission for heart failure in the last twelve months?",
                "An admission is the strongest short-term marker of death in this condition.",
            ),
            q(
                "How far can the applicant walk on the flat without stopping?",
                "Everyday capacity adds to what the echocardiogram shows.",
            ),
        ),
        evidence=(
            "Use the ejection fraction from an echocardiogram or cardiac MRI in the last twelve months. Where the report gives a range, such as 45 to 50 %, take the lower figure.",
            "An admission for heart failure in the last twelve months is outside these bands and goes to a person, whatever the ejection fraction.",
        ),
        pitfalls=(
            "Ejection fraction varies by a few points between scans and between methods. A change from 48 to 51 % is within that noise; where two recent scans straddle an edge, use the lower.",
            "A low ejection fraction found by chance, with no diagnosis of heart failure, is not rated here and goes to a person.",
        ),
        rating_note="The edges at 40 % and 50 % are the cited guideline's classes: reduced, mildly reduced and preserved ejection fraction.",
        unchanged=(
            "The names and doses of the medicines do not change the band. An implanted defibrillator or pacemaker does not change it either, but must be mentioned to the person reviewing the case.",
        ),
        combinations=(
            "Atrial fibrillation appears in a large share of people with heart failure and worsens the outlook: add its debit.",
            "Heart failure after a heart attack is rated in both sections. Raised blood pressure and reduced kidney function are added as usual; a falling eGFR in heart failure is a poor sign.",
        ),
        rules=(
            rule("UW-HF-001", _LVEF.at_most("40"), DECLINE, HEART_FAILURE),
            rule(
                "UW-HF-002",
                _LVEF.between("40", "50", lower_inclusive=False),
                150,
                HEART_FAILURE,
                "UW-AF-002",
            ),
            rule(
                "UW-HF-003",
                _LVEF.at_least("50"),
                75,
                HEART_FAILURE,
                "UW-HT-002",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="atrial_fibrillation",
        code="AF",
        name="Atrial fibrillation",
        phrase="atrial fibrillation",
        applies=diagnosed(
            "Atrial fibrillation or atrial flutter has been recorded on an ECG."
        ),
        measures=(_CHADS,),
        overview=(
            "In atrial fibrillation the upper chambers of the heart quiver instead of beating. The pulse is irregular, and blood pooling in the atria can clot and travel to the brain.",
            "Stroke is the main danger, so the section rates on the standard stroke risk score rather than on how often the rhythm occurs.",
        ),
        questions=(
            q(
                "Is the rhythm occasional, persistent or permanent?",
                "The pattern shapes treatment, though the stroke risk is much the same for all three.",
            ),
            q(
                "Is an anticoagulant taken, and which?",
                "An applicant with a score of 2 or more who takes none is not following usual advice, and the reason should be on file.",
            ),
            q(
                "Which items of the score apply: heart failure, hypertension, age, diabetes, stroke, vascular disease, sex?",
                "The underwriter should be able to rebuild the score from the file rather than accept a bare number.",
            ),
            q(
                "Has an ablation been done, and has the rhythm returned since?",
                "A successful ablation eases symptoms but does not remove the stroke score.",
            ),
        ),
        evidence=(
            "Use the score the cardiologist recorded if the file shows how it was built; otherwise work it out from the history. Count one point each for heart failure, hypertension, diabetes, vascular disease, age 65 to 74 and female sex, and two each for age 75 or more and for a previous stroke.",
            "A single episode with a clear, removed cause, such as an overactive thyroid since treated, and no recurrence for two years meets no rule here.",
        ),
        pitfalls=(
            "Clinic letters often quote a score without its parts, and some quote an older version of the score. Rebuild it from the history.",
            "Female sex counts one point in the score. A woman with no other item has a score of 1 and meets the lowest rule.",
        ),
        rating_note="A score of 2 is the level at which the cited guideline recommends an anticoagulant for men; the edge at 4 is this manual's own.",
        own_edges=(Decimal(4),),
        unchanged=(
            "Whether the rhythm is occasional, persistent or permanent does not change the band, and neither does the choice between rate control and rhythm control. Taking an anticoagulant does not lower the score.",
        ),
        combinations=(
            "The items of the score are themselves impairments. Hypertension, diabetes, heart failure and a past stroke each raise the score here and are also rated in their own sections; both effects stand, and the debits are added.",
            "Atrial fibrillation with a narrowed mitral valve or a mechanical heart valve is outside this section and goes to a person.",
        ),
        rules=(
            rule("UW-AF-001", _CHADS.below("2"), 25, ATRIAL_FIBRILLATION),
            rule(
                "UW-AF-002",
                _CHADS.between("2", "4"),
                50,
                ATRIAL_FIBRILLATION,
                "UW-CVA-001",
            ),
            rule(
                "UW-AF-003",
                _CHADS.at_least("4"),
                100,
                ATRIAL_FIBRILLATION,
                "UW-HF-002",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="aortic_stenosis",
        code="AS",
        name="Aortic stenosis",
        phrase="aortic stenosis",
        applies=diagnosed("Aortic stenosis has been reported on an echocardiogram."),
        measures=(_JET,),
        overview=(
            "Aortic stenosis is a narrowing of the valve through which the heart empties into the aorta. It progresses slowly for years, then quickly once chest pain, fainting or breathlessness appear.",
            "Echocardiography grades the narrowing by how fast blood must travel to get through the valve.",
        ),
        questions=(
            q(
                "What did the latest echocardiogram report as the peak velocity?",
                "It is the figure this section rates on, and it should be less than twelve months old.",
            ),
            q(
                "Does the applicant have chest pain, fainting or breathlessness on effort?",
                "Symptoms with severe narrowing mean the valve needs replacing now.",
            ),
            q(
                "Is the valve bicuspid?",
                "A two-leaflet valve narrows earlier in life and may come with a widened aorta.",
            ),
        ),
        evidence=(
            "Take the peak aortic jet velocity from the echocardiogram report. If only the valve area or mean gradient is reported, ask the cardiologist for the velocity.",
            "A replaced valve is outside these bands and goes to a person with the operation note.",
        ),
        pitfalls=(
            "Velocity understates the narrowing when the heart is pumping weakly. Where the ejection fraction is below 50 % and the valve area is reported as 1.0 cm2 or less, send the case to a person whatever the velocity.",
            "A murmur described as aortic sclerosis, with a velocity under 2.0 m/s, is not stenosis.",
        ),
        rating_note="The edges at 2.0, 3.0 and 4.0 m/s are the cited guideline's mild, moderate and severe grades.",
        gaps=(
            gap(
                _JET.below("2.0"), "no rule; thickening of the valve without narrowing"
            ),
        ),
        unchanged=(
            "The cause of the narrowing and the applicant's age do not change the band. An absence of symptoms does not lower it.",
        ),
        combinations=(
            "A narrowed valve makes the heart muscle thicken and, late on, weaken. An ejection fraction below 50 % with aortic stenosis is rated in the heart failure section as well, and the debits are added.",
            "Coronary artery disease often coexists because the two share causes. A past heart attack is added from its own section.",
        ),
        rules=(
            rule("UW-AS-001", _JET.between("2.0", "3.0"), 25, VALVES),
            rule(
                "UW-AS-002",
                _JET.between("3.0", "4.0"),
                100,
                VALVES,
                "UW-HF-002",
            ),
            rule(
                "UW-AS-003",
                _JET.at_least("4.0"),
                DECLINE,
                VALVES,
                note="The applicant may apply again twelve months after a valve replacement.",
                postponement=True,
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="hypertrophic_cardiomyopathy",
        code="HCM",
        name="Hypertrophic cardiomyopathy",
        phrase="hypertrophic cardiomyopathy",
        applies=diagnosed(
            "Hypertrophic cardiomyopathy has been diagnosed by a cardiologist."
        ),
        measures=(_WALL,),
        overview=(
            "Hypertrophic cardiomyopathy is an inherited thickening of the heart muscle that is not explained by blood pressure or a valve. Many people with it live a normal span; a few die suddenly from a rhythm disturbance.",
            "The thickness of the wall is one of the recognised markers of that sudden risk and is the measure used here.",
        ),
        questions=(
            q(
                "Has a close relative died suddenly before the age of 50?",
                "Family history of sudden death is an independent risk marker.",
            ),
            q(
                "Has the applicant fainted without explanation?",
                "An unexplained faint may have been a rhythm disturbance that stopped by itself.",
            ),
            q(
                "Has a defibrillator been implanted or advised?",
                "Advice to implant one tells the underwriter how the cardiologist judges the risk.",
            ),
        ),
        evidence=(
            "Use the maximum wall thickness from the latest echocardiogram or cardiac MRI; where both exist, the MRI figure is preferred.",
            "An implanted defibrillator, or one advised and refused, takes the case to a person.",
        ),
        pitfalls=(
            "Thickening from long-standing high blood pressure or from athletic training can look similar on a scan. Only the cardiologist's diagnosis brings a case into this section.",
            "A gene found on family screening, with a normal scan, is not the disease. It meets no rule and goes to a person.",
        ),
        rating_note="15 mm is the diagnostic thickness and 30 mm the marker of high sudden-death risk in the cited guideline.",
        gaps=(
            gap(
                _WALL.below("15"),
                "below the diagnostic thickness; no rule; send to a person if the diagnosis is stated all the same",
            ),
        ),
        unchanged=(
            "Obstruction to outflow, the gene involved and the medicines taken do not change the band.",
        ),
        combinations=(
            "Atrial fibrillation is common in this condition and carries a high stroke risk whatever the score: its debit is added.",
            "Heart failure late in the disease is rated in its own section and added.",
        ),
        rules=(
            rule(
                "UW-HCM-001",
                _WALL.between("15", "30"),
                100,
                HCM_GUIDELINE,
                "UW-AF-001",
            ),
            rule("UW-HCM-002", _WALL.at_least("30"), DECLINE, HCM_GUIDELINE),
        ),
    ),
    ImpairmentSpec(
        impairment_id="abdominal_aortic_aneurysm",
        code="AAA",
        name="Abdominal aortic aneurysm",
        phrase="an abdominal aortic aneurysm",
        applies=diagnosed("An abdominal aortic aneurysm has been found on imaging."),
        measures=(_AORTA,),
        overview=(
            "An abdominal aortic aneurysm is a ballooning of the body's main artery below the kidneys. It gives no warning. The risk of rupture, which is usually fatal, climbs steeply with diameter.",
            "Small aneurysms are watched with ultrasound and large ones are repaired. The rating follows the same sizes that set the watching interval.",
        ),
        questions=(
            q(
                "What was the diameter at the last scan, and how much had it grown in a year?",
                "Growth of more than 1 cm in a year is itself a reason for repair.",
            ),
            q(
                "Is the applicant enrolled in regular surveillance?",
                "An aneurysm found once and never rescanned is an unknown size today.",
            ),
            q(
                "Does the applicant smoke?",
                "Smoking speeds growth and raises the chance of rupture.",
            ),
        ),
        evidence=(
            "Use the diameter from an ultrasound or CT in the last twelve months, or in the last six months once the diameter has reached 5.0 cm.",
            "A repaired aneurysm is outside these bands and goes to a person with the operation note and the latest follow-up scan.",
        ),
        pitfalls=(
            "Ultrasound and CT differ by a few millimetres, and CT usually reads larger. Compare like with like when judging growth.",
            "Reports sometimes give the diameter of the channel that carries blood rather than the outer wall. The outer diameter is the one that is rated.",
        ),
        rating_note="3.0 cm defines an aneurysm and 5.5 cm is the size for planned repair in the cited guidelines; 4.0 cm is where they shorten the surveillance interval.",
        gaps=(gap(_AORTA.below("3.0"), "no rule; not an aneurysm"),),
        unchanged=(
            "The applicant's age and sex do not change the band, although the cited guidelines advise repair at a smaller size in women; such a case goes to a person.",
        ),
        combinations=(
            "Smoking is the strongest driver of growth, and its debit is added. Raised blood pressure is added too.",
            "People with an aneurysm very often have coronary or leg artery disease; each is rated in its own section and added.",
        ),
        rules=(
            rule(
                "UW-AAA-001",
                _AORTA.between("3.0", "4.0"),
                50,
                SVS_AAA,
                "UW-TOB-001",
            ),
            rule(
                "UW-AAA-002",
                _AORTA.between("4.0", "5.5"),
                150,
                SVS_AAA,
                "UW-HT-002",
            ),
            rule("UW-AAA-003", _AORTA.at_least("5.5"), DECLINE, SVS_AAA),
        ),
    ),
    ImpairmentSpec(
        impairment_id="peripheral_artery_disease",
        code="PAD",
        name="Peripheral artery disease",
        phrase="peripheral artery disease",
        applies=diagnosed(
            "Peripheral artery disease has been diagnosed or an ankle-brachial index has been measured for leg symptoms."
        ),
        measures=(_ABI,),
        overview=(
            "Peripheral artery disease is narrowing of the arteries of the legs by the same fatty plaque that affects the heart. Calf pain on walking is the typical symptom, but many people have none.",
            "Its importance for mortality lies less in the legs than in what it says about the coronary and brain arteries. A simple pressure ratio at the ankle confirms it.",
        ),
        questions=(
            q(
                "How far can the applicant walk before calf pain stops them?",
                "A shrinking walking distance shows the disease is advancing.",
            ),
            q(
                "Has there been an angioplasty, a bypass or an amputation?",
                "Each marks more severe disease than the index alone conveys.",
            ),
            q(
                "Does the applicant smoke or have diabetes?",
                "These two drive both the leg disease and the events that shorten life.",
            ),
        ),
        reading_rules=(ReadingRule(measure="ankle_brachial_index", choose="lowest"),),
        evidence=(
            "Take the resting ankle-brachial index of each leg from a vascular laboratory report.",
            "An index above 1.40 means the arteries are too stiff to compress and the test cannot show narrowing; it has its own rule. Values above 0.90 and up to 1.40 meet no rule of this section.",
        ),
        pitfalls=(
            "An index measured after exercise is lower than a resting one; only the resting figure is rated.",
            "In diabetes and in kidney failure the arteries may be too stiff to compress, giving a normal or high index in a diseased leg. A toe pressure is then the better test.",
        ),
        rating_note="0.90 and 1.40 are the cited guideline's limits for an abnormal index and for arteries that cannot be compressed.",
        gaps=(
            gap(
                _ABI.between(
                    "0.90", "1.40", lower_inclusive=False, upper_inclusive=True
                ),
                "no rule; a normal or borderline index",
            ),
        ),
        unchanged=(
            "Walking distance, the side affected and treatment with a supervised exercise programme do not change the band.",
        ),
        combinations=(
            "This disease is a marker for the whole arterial tree. A past heart attack, a past stroke and carotid narrowing are each rated in their own sections and added.",
            "Smoking and diabetes are the two great causes; both are added. An ulcer that will not heal, or any amputation, goes to a person.",
        ),
        rules=(
            rule(
                "UW-PAD-001",
                _ABI.at_most("0.90"),
                75,
                PAD_GUIDELINE,
                "UW-TOB-001",
            ),
            rule(
                "UW-PAD-002",
                _ABI.above("1.40"),
                50,
                PAD_GUIDELINE,
                "UW-DM-002",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="carotid_artery_stenosis",
        code="CAS",
        name="Carotid artery stenosis",
        phrase="carotid artery stenosis",
        applies=diagnosed(
            "Narrowing of a carotid artery has been reported on imaging."
        ),
        measures=(_CAROTID,),
        overview=(
            "The carotid arteries in the neck supply most of the brain. Plaque at the point where each one divides can shed fragments that cause a stroke or a passing loss of vision or speech.",
            "The degree of narrowing decides both the stroke risk and whether surgery is advised.",
        ),
        questions=(
            q(
                "Was the narrowing found after symptoms, or by chance?",
                "A narrowing that has already caused symptoms is far more likely to cause a stroke.",
            ),
            q(
                "What percentage did the latest scan report, and for which side?",
                "The table is read on the worse side.",
            ),
            q(
                "Has surgery or a stent been done?",
                "A treated artery is rated on its present state and on the history that led to treatment.",
            ),
        ),
        evidence=(
            "Use the percentage from a duplex ultrasound, CT or MR angiogram in the last twelve months. Where a range is given, such as 50 to 69 %, the band is clear without a single figure; record the range.",
            "Narrowing under 50 % meets no rule of this section.",
        ),
        pitfalls=(
            "Different scanning methods and different ways of measuring give different percentages for the same artery. Use the figure the report gives as its conclusion.",
            "A complete blockage is reported as occlusion, not as 100 % stenosis, and is outside this section.",
        ),
        rating_note="50 % and 70 % are the cited guideline's limits for moderate and severe stenosis.",
        gaps=(gap(_CAROTID.below("50"), "no rule; mild narrowing"),),
        unchanged=(
            "A noise heard over the artery with a stethoscope, the appearance of the plaque and which side is affected do not change the band.",
        ),
        combinations=(
            "Where the narrowing has already caused a stroke, the stroke section's debit is added.",
            "Carotid narrowing with coronary or leg artery disease is one disease in three places: each is added. Smoking and raised LDL cholesterol are added as well.",
        ),
        rules=(
            rule(
                "UW-CAS-001",
                _CAROTID.between("50", "70"),
                50,
                STROKE_PREVENTION,
                "UW-CVA-001",
            ),
            rule(
                "UW-CAS-002",
                _CAROTID.at_least("70"),
                100,
                STROKE_PREVENTION,
                "UW-PAD-001",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="stroke",
        code="CVA",
        name="Stroke",
        phrase="a past stroke",
        applies=diagnosed(
            "A stroke is recorded in the history, with damage shown on brain imaging."
        ),
        measures=(_NIHSS,),
        overview=(
            "A stroke is damage to part of the brain from a blocked or burst blood vessel. What is left afterwards ranges from nothing noticeable to dependence on others for daily life.",
            "Survivors face a raised risk of another stroke and of heart disease. How severe the stroke was when the applicant reached hospital is recorded on a standard scale in nearly every stroke unit, and that score is the measure used here.",
        ),
        questions=(
            q(
                "When did the stroke happen, and was it a blockage or a bleed?",
                "The two types have different causes and different chances of recurring.",
            ),
            q(
                "What can the applicant not do now that they could do before?",
                "Lasting disability is not scored in this section, but heavy dependence on others takes the case to a person.",
            ),
            q(
                "Was a cause found, such as atrial fibrillation or a narrowed carotid artery?",
                "A cause that has been found and treated lowers the chance of a second stroke.",
            ),
            q(
                "Has the applicant returned to work or to driving?",
                "Both are practical checks on how complete the recovery has been.",
            ),
        ),
        evidence=(
            "Take the NIHSS score at admission from the hospital discharge letter. Within six months of a stroke, send the case to a person whatever the score.",
            "If the letter gives no score, ask the stroke unit for it. Do not estimate one from a description of the symptoms. A transient ischaemic attack, with symptoms gone within a day and no damage on imaging, meets no rule of this section and goes to a person.",
        ),
        pitfalls=(
            "The score falls quickly in the first days as symptoms improve; the score at admission is the one that is rated, not the score at discharge.",
            "A bleed into the brain and a blocked artery are both strokes for this section. A bleed under the lining of the brain from a burst aneurysm is not, and goes to a person.",
        ),
        rating_note="A score of 6 or more is among the cited guidelines' criteria for removing the clot mechanically, which marks the stroke as more than minor.",
        unchanged=(
            "Clot-dissolving treatment, mechanical removal of the clot and the length of the hospital stay do not change the band. A full recovery does not lower it.",
        ),
        combinations=(
            "Atrial fibrillation is found in about a quarter of strokes caused by a blocked artery; its debit is added, and its score already counts two points for the stroke.",
            "Carotid narrowing, raised blood pressure, diabetes and smoking are added from their sections. Depression after stroke is common and is rated in its own section.",
        ),
        rules=(
            rule(
                "UW-CVA-001",
                _NIHSS.below("6"),
                75,
                STROKE_EARLY,
                "UW-AF-002",
            ),
            rule(
                "UW-CVA-002",
                _NIHSS.at_least("6"),
                200,
                STROKE_EARLY,
                "UW-CAS-002",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="venous_thromboembolism",
        code="VTE",
        name="Venous thromboembolism",
        phrase="a past blood clot in the veins",
        applies=diagnosed(
            "A deep vein thrombosis or a pulmonary embolism is recorded in the history."
        ),
        measures=(_MONTHS_SINCE_VTE,),
        overview=(
            "Venous thromboembolism covers a clot in a deep vein, usually of the leg, and a clot that has travelled to the lungs. Anticoagulant treatment for the first months prevents the clot from growing or returning.",
            "After that treatment phase the outlook depends on why the clot formed. A clot after surgery or a long-haul flight seldom returns; one with no cause often does.",
        ),
        questions=(
            q(
                "What was the date of the clot, and was it in a leg, the lungs or both?",
                "A clot in the lungs carries more early risk than one confined to the calf.",
            ),
            q(
                "Was there a trigger: surgery, a plaster cast, pregnancy, a hormone medicine, cancer?",
                "A removable trigger is the best predictor that it will not happen again.",
            ),
            q(
                "Is an anticoagulant still taken, and for how long is it planned?",
                "Lifelong treatment tells the underwriter the clinic judges the risk of recurrence to be high.",
            ),
        ),
        evidence=(
            "Take the date from the hospital letter and count whole months to the application. With more than one clot, count from the latest and send the case to a person if there have been three or more.",
            "A clot found during investigation for cancer is rated under the cancer, not here.",
        ),
        pitfalls=(
            "A clot in a surface vein, often called phlebitis, is not a deep vein thrombosis and is not rated here.",
            "The date that matters is the date of diagnosis, not the date treatment stopped.",
        ),
        rating_note="Three months is the treatment phase in the cited guideline. The edge at twelve months is this manual's own.",
        own_edges=(Decimal(12),),
        unchanged=(
            "The anticoagulant chosen, a filter placed in the main vein and an inherited clotting tendency found on testing do not change the band; the last of these is passed to a person.",
        ),
        combinations=(
            "Obesity makes a second clot more likely, and the build debit is added. A clot during treatment with an oestrogen medicine that has since been stopped is rated here like any other.",
            "A clot in someone with active cancer is rated under the cancer. Raised pressure in the lung arteries after an embolism goes to a person.",
        ),
        rules=(
            rule(
                "UW-VTE-001",
                _MONTHS_SINCE_VTE.below("3"),
                DECLINE,
                CHEST_VTE,
                note="This is a postponement until the treatment phase is complete.",
                postponement=True,
            ),
            rule(
                "UW-VTE-002",
                _MONTHS_SINCE_VTE.between("3", "12"),
                50,
                CHEST_VTE,
                "UW-BMI-003",
            ),
            rule("UW-VTE-003", _MONTHS_SINCE_VTE.at_least("12"), 0, CHEST_VTE),
        ),
    ),
)
