"""Manual content, part 2: habits, lungs, kidneys, liver and gut, blood, mind and the rest.

All wording is original and written for this project. Thresholds follow the public
guideline each rule names; every debit percentage is invented.
"""

from decimal import Decimal

from synthdata.manual_model import Category, ImpairmentSpec, Measure
from synthdata.manual_sources import (
    AASLD_HBV,
    AASLD_NAFLD,
    AASLD_TRANSPLANT,
    AASM,
    ACR_RA,
    AGA_UC,
    AJCC,
    CDC_HIV,
    CDC_OPIOIDS,
    CDC_TOBACCO,
    DECLINE,
    GINA,
    GOLD,
    HCV_GUIDANCE,
    ILAE,
    KDIGO,
    NICE_DEPRESSION,
    USPSTF_LUNG,
    WFH,
    WHO_ANAEMIA,
    WHO_AUDIT,
    WHO_BONE,
    diagnosed,
    gap,
    measured,
    q,
    rule,
)

_SMOKING = Measure(
    key="smoking_status",
    label="smoking status",
    unit="category",
    unit_printed=False,
    categories=(
        Category(value="current_smoker", label="current smoker"),
        Category(value="former_smoker", label="former smoker"),
        Category(value="never_smoker", label="never smoked"),
    ),
    meaning="Whether the applicant smokes cigarettes now, used to, or never has, as the survey definitions put it.",
)
_PACK_YEARS = Measure(
    key="pack_years",
    minimum=Decimal(0),
    conversion="Cigarettes a day divided by 20, times the years smoked: 10 a day for 30 years is 15 pack-years.",
    label="lifetime smoking",
    unit="pack-years",
    meaning="Packs of 20 cigarettes smoked a day multiplied by the number of years smoked.",
)
_AUDIT = Measure(
    key="audit_score",
    minimum=Decimal(0),
    maximum=Decimal(40),
    label="AUDIT score",
    unit="points",
    meaning="The total, from 0 to 40, of the ten-question Alcohol Use Disorders Identification Test.",
)
_MME = Measure(
    key="opioid_dose",
    minimum=Decimal(0),
    conversion="Multiply each medicine's daily milligrams by its factor and add the results: morphine 1, hydrocodone 1, oxycodone 1.5, hydromorphone 4, codeine 0.15, tramadol 0.1. A fentanyl patch counts 2.4 for each microgram an hour.",
    label="daily opioid dose",
    unit="MME a day",
    meaning="The total of all opioid medicines taken in a day, converted to morphine milligram equivalents (MME).",
)
_FEV1_ASTHMA = Measure(
    key="fev1_percent_predicted",
    minimum=Decimal(5),
    maximum=Decimal(160),
    label="FEV1",
    unit="% of predicted",
    meaning="The volume of air blown out in the first second of a forced breath, as a percentage of the value expected for age, height and sex.",
)
_FEV1_COPD = Measure(
    key="post_bronchodilator_fev1_percent_predicted",
    minimum=Decimal(5),
    maximum=Decimal(160),
    label="FEV1 after a bronchodilator",
    unit="% of predicted",
    meaning="FEV1 measured after an inhaled airway-opening medicine, so that only the fixed part of the obstruction is counted.",
)
_AHI = Measure(
    key="apnoea_hypopnoea_index",
    minimum=Decimal(0),
    maximum=Decimal(250),
    label="apnoea-hypopnoea index",
    unit="events per hour",
    meaning="The number of pauses and shallow breaths in each hour of sleep, from a sleep study done without treatment.",
)
_EGFR = Measure(
    key="egfr",
    minimum=Decimal(0),
    maximum=Decimal(200),
    label="eGFR",
    unit="mL/min/1.73 m2",
    meaning="Estimated glomerular filtration rate: how much blood the kidneys clean each minute, worked out from serum creatinine.",
)
_ACR = Measure(
    key="urine_albumin_creatinine_ratio",
    minimum=Decimal(0),
    conversion="A ratio in mg/mmol is multiplied by 8.84 to give mg/g: 3.4 mg/mmol is 30 mg/g and 34 mg/mmol is 300 mg/g.",
    label="urine albumin-to-creatinine ratio",
    unit="mg/g",
    meaning="Albumin in a urine sample relative to creatinine; a rise is the earliest sign of kidney damage.",
)
_HBV_DNA = Measure(
    key="hbv_dna",
    minimum=Decimal(0),
    conversion="A result in copies/mL is divided by 5 to give IU/mL, unless the laboratory report states its own factor.",
    label="HBV DNA",
    unit="IU/mL",
    meaning="The amount of hepatitis B virus in the blood.",
)
_SVR_WEEKS = Measure(
    key="weeks_hcv_rna_undetectable_after_treatment",
    minimum=Decimal(0),
    label="time with undetectable HCV RNA after the end of treatment",
    unit="weeks",
    meaning="Whole weeks from the last dose of antiviral treatment to the most recent test that found no hepatitis C virus.",
)
_FIB4 = Measure(
    key="fib4_index",
    minimum=Decimal("0.00"),
    conversion="The index is age in years times AST, divided by the platelet count in thousands per microlitre times the square root of ALT.",
    label="FIB-4 index",
    unit="index value",
    unit_printed=False,
    meaning="An estimate of liver scarring worked out from age, two liver enzymes and the platelet count.",
)
_MELD = Measure(
    key="meld_score",
    minimum=Decimal(6),
    maximum=Decimal(40),
    label="MELD score",
    unit="points",
    meaning="Model for End-Stage Liver Disease: a score from bilirubin, creatinine and clotting time that predicts three-month survival in cirrhosis.",
)
_CALPROTECTIN = Measure(
    key="faecal_calprotectin",
    minimum=Decimal(0),
    conversion="A result in mg/kg is the same number in mcg/g.",
    label="faecal calprotectin",
    unit="mcg/g",
    meaning="A protein from white blood cells measured in a stool sample; it rises with inflammation of the bowel wall.",
)
_HAEMOGLOBIN = Measure(
    key="haemoglobin",
    minimum=Decimal("2.0"),
    maximum=Decimal("25.0"),
    conversion="A result in g/L is divided by ten to give g/dL.",
    label="haemoglobin",
    unit="g/dL",
    meaning="The oxygen-carrying protein of red blood cells, measured in a full blood count.",
)
_FACTOR = Measure(
    key="clotting_factor_level",
    minimum=Decimal(0),
    maximum=Decimal(250),
    conversion="A level given as a percentage of normal is the same number in IU/dL.",
    label="clotting factor level",
    unit="IU/dL",
    meaning="The activity of factor VIII or factor IX in the blood without treatment; 100 IU/dL is the average of healthy people.",
)
_CD4 = Measure(
    key="cd4_count",
    minimum=Decimal(0),
    conversion="A count in cells per microlitre is the same number in cells/mm3.",
    label="CD4 count",
    unit="cells/mm3",
    meaning="The number of CD4 lymphocytes in a cubic millimetre of blood, a measure of immune strength.",
)
_PHQ9 = Measure(
    key="phq9_score",
    minimum=Decimal(0),
    maximum=Decimal(27),
    label="PHQ-9 score",
    unit="points",
    meaning="The total, from 0 to 27, of a nine-question self-report of depressive symptoms over two weeks.",
)
_SEIZURE_FREE = Measure(
    key="years_since_last_seizure",
    minimum=Decimal(0),
    label="time since the last seizure",
    unit="years",
    meaning="Whole years from the most recent seizure of any kind to the date of the application.",
)
_DAS28 = Measure(
    key="das28",
    minimum=Decimal("0.0"),
    maximum=Decimal("10.0"),
    label="DAS28",
    unit="points",
    meaning="Disease Activity Score over 28 joints, built from tender and swollen joint counts, a blood marker of inflammation and the patient's own rating.",
)
_T_SCORE = Measure(
    key="bone_density_t_score",
    minimum=Decimal("-7.0"),
    maximum=Decimal("7.0"),
    label="bone density T-score",
    unit="standard deviations",
    meaning="Bone mineral density at the hip or spine compared with the average of healthy young adults, in standard deviations.",
)
_THICKNESS = Measure(
    key="melanoma_tumour_thickness",
    minimum=Decimal("0.0"),
    label="tumour thickness",
    unit="mm",
    meaning="The depth of a melanoma from the skin surface to its deepest cell, measured by the pathologist.",
)

IMPAIRMENTS_2: tuple[ImpairmentSpec, ...] = (
    ImpairmentSpec(
        impairment_id="tobacco_use",
        code="TOB",
        name="Tobacco use",
        phrase="a history of smoking",
        applies=measured(
            "Every applicant: smoking status is recorded for all of them."
        ),
        measures=(_SMOKING, _PACK_YEARS),
        overview=(
            "Cigarette smoking is the largest avoidable cause of early death. It damages the arteries and the lungs and causes cancer in many organs, and the harm grows with the amount smoked and the years of smoking.",
            "This section has a rule for present status and a second, separate rule for the lifetime total. A long-term heavy smoker meets both, and the two debits are added.",
            "Stopping helps at any age. The former smoker's rule carries no debit, but a large lifetime total still counts after stopping.",
        ),
        questions=(
            q(
                "Does the applicant smoke now, and how many cigarettes a day?",
                "Status is the first rule of this section, and the daily number is needed for the lifetime total.",
            ),
            q(
                "At what age did smoking start, and were there years without it?",
                "Pack-years are packs a day multiplied by years actually smoked.",
            ),
            q(
                "If stopped, on what date, and has there been any relapse?",
                "A former smoker is one who has stopped; an applicant who stopped last month and one who stopped ten years ago are the same in status but not in notes.",
            ),
            q(
                "Does the medical record agree with the application form?",
                "Smoking is under-declared more often than any other habit, and the physician's record is the better witness.",
            ),
        ),
        evidence=(
            "Take status from the attending physician's statement where it differs from the application form. A current smoker is someone who has smoked at least 100 cigarettes in their life and smokes now, every day or on some days; a former smoker has smoked that many and has stopped.",
            "Work out pack-years from the record: 10 cigarettes a day for 30 years is half a pack for 30 years, or 15 pack-years. Round down to a whole number.",
            "Someone who has smoked fewer than 100 cigarettes in total has never smoked for the purposes of this section and meets no rule.",
        ),
        pitfalls=(
            "An applicant who stopped within the last few weeks is a former smoker by the survey definition this section uses. Record the date of stopping so that a person can weigh a very recent change.",
            "Cotinine in a urine or saliva test shows nicotine from any source, including patches and gum. A positive test with a credible account of nicotine replacement goes to a person.",
            "Cigars, pipes and electronic cigarettes are not counted in pack-years.",
        ),
        rating_note="The status definitions and the 20 pack-year level are those of the cited survey glossary and screening recommendation.",
        gaps=(
            gap(
                _SMOKING.equals("never_smoker"),
                "no rule; fewer than 100 cigarettes in a lifetime",
            ),
            gap(
                _PACK_YEARS.below("20"),
                "no rule for the lifetime total; the status rules still apply",
            ),
        ),
        unchanged=(
            "The brand, the tar content and whether the smoke is inhaled do not change either rule. Smoking fewer than 20 cigarettes a day does not lower the status debit.",
        ),
        combinations=(
            "Smoking makes every arterial and lung impairment worse, and its debit is always added to theirs: raised blood pressure, a past heart attack, an aneurysm, leg artery disease and chronic obstructive pulmonary disease above all.",
            "A smoker with diabetes or with raised LDL cholesterol carries all the debits together.",
        ),
        rules=(
            rule(
                "UW-TOB-001",
                _SMOKING.equals("current_smoker"),
                50,
                CDC_TOBACCO,
                "UW-TOB-002",
            ),
            rule(
                "UW-TOB-002",
                _PACK_YEARS.at_least("20"),
                25,
                USPSTF_LUNG,
                "UW-COPD-002",
                note="This debit is added to the one for present status, whether the applicant smokes now or has stopped.",
            ),
            rule(
                "UW-TOB-003",
                _SMOKING.equals("former_smoker"),
                0,
                CDC_TOBACCO,
                "UW-TOB-002",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="alcohol_use",
        code="ALC",
        name="Alcohol use",
        phrase="a record of heavy drinking",
        applies=measured("An AUDIT score recorded by a clinician is on file."),
        measures=(_AUDIT,),
        overview=(
            "Heavy drinking shortens life through liver disease, accidents, several cancers, heart muscle damage and suicide. The amount an applicant states is an unreliable guide, since most heavy drinkers understate it.",
            "A structured screening questionnaire gives a steadier picture than a single figure for weekly units, because it also asks about loss of control and harm.",
        ),
        questions=(
            q(
                "Has a doctor ever advised the applicant to cut down?",
                "Advice recorded in the notes means the drinking was enough to worry a clinician.",
            ),
            q(
                "Has there been treatment for alcohol dependence, or an admission for detoxification?",
                "Either takes the case out of the lower bands, whatever today's score.",
            ),
            q(
                "Are the liver enzymes or the red cell size raised?",
                "Blood tests are indirect witnesses that do not depend on what the applicant says.",
            ),
            q(
                "Has there been a drink-driving offence?",
                "It is one of the few events that reliably marks drinking beyond control.",
            ),
        ),
        evidence=(
            "Use an AUDIT total recorded by a clinician in the last twelve months. Do not derive a score from weekly units on the application form: the questionnaire measures more than quantity.",
            "With no score on file and nothing in the record suggesting harm, no rule of this section applies. Where the record suggests harm and there is no score, send the case to a person.",
        ),
        pitfalls=(
            "A score taken during treatment for dependence reflects abstinence, not the drinking that led to treatment. The history decides such a case, and it goes to a person.",
            "Some clinics record only the first three questions, which score from 0 to 12. That short form is not the score this section uses.",
        ),
        rating_note="8, 16 and 20 are the lower limits of the hazardous, harmful and possible-dependence zones in the cited manual.",
        gaps=(gap(_AUDIT.below("8"), "no rule; the low-risk zone"),),
        unchanged=(
            "The type of drink and whether drinking is daily or at weekends do not change the band. Normal liver enzymes do not lower it.",
        ),
        combinations=(
            "Alcohol and the liver: where fatty liver disease has been diagnosed in a drinker, its FIB-4 band is rated in that section and added, and cirrhosis is rated in its own section.",
            "Alcohol and mood travel together, each making the other worse; the depression debit is added. Raised triglycerides and raised blood pressure caused by drinking are still rated in their own sections.",
        ),
        rules=(
            rule(
                "UW-ALC-001",
                _AUDIT.between("8", "16"),
                25,
                WHO_AUDIT,
                "UW-FLD-002",
            ),
            rule(
                "UW-ALC-002",
                _AUDIT.between("16", "20"),
                75,
                WHO_AUDIT,
                "UW-DEP-001",
            ),
            rule(
                "UW-ALC-003",
                _AUDIT.at_least("20"),
                DECLINE,
                WHO_AUDIT,
                "UW-CIR-001",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="long_term_opioid_therapy",
        code="OPI",
        name="Long-term opioid therapy",
        phrase="long-term opioid treatment for pain",
        applies=diagnosed(
            "An opioid medicine has been prescribed for pain, other than cancer pain, for three months or longer."
        ),
        measures=(_MME,),
        overview=(
            "Opioid medicines prescribed for months or years against chronic pain carry a risk of fatal overdose that rises with the daily dose. The risk is higher still when sedatives or alcohol are taken as well.",
            "This section covers prescribed treatment for pain that is not due to cancer. Illicit use and treatment for opioid dependence are not rated here and go to a person.",
        ),
        questions=(
            q(
                "Which opioids are prescribed, at what dose, and for how long?",
                "The daily total across all products is the figure the table needs.",
            ),
            q(
                "Is a benzodiazepine or another sedative prescribed too?",
                "The combination multiplies the risk of breathing stopping during sleep.",
            ),
            q(
                "What is the painful condition?",
                "The underlying disease may be an impairment in its own right.",
            ),
            q(
                "Has the dose risen over the last year?",
                "A climbing dose suggests tolerance and predicts further rises.",
            ),
        ),
        evidence=(
            "Add up the daily dose of every opioid on the current prescription list and convert it to morphine milligram equivalents with the factors shown below, which are those of the cited guideline. Record the working.",
            "Treatment for less than three months after surgery or injury meets no rule of this section.",
        ),
        pitfalls=(
            "Prescription lists often show an old dose alongside the present one after a change. Use the dose in the most recent clinic letter.",
            "Medicines taken as needed are counted at the most the prescription allows in a day, unless the record shows what is actually taken.",
        ),
        rating_note="50 and 90 morphine milligram equivalents a day are the cited guideline's levels for added caution and for avoidance.",
        unchanged=(
            "A signed treatment agreement, urine screening and the form of the medicine, tablet or patch, do not change the band.",
        ),
        combinations=(
            "Sedatives, sleeping tablets and alcohol each add to the danger of breathing stopping during sleep. Severe sleep apnoea does the same from another direction. Add the debits from those sections, and say plainly in the notes that the combination is present.",
            "Depression is common in chronic pain and raises the risk of overdose; its debit is added.",
        ),
        rules=(
            rule("UW-OPI-001", _MME.below("50"), 25, CDC_OPIOIDS),
            rule(
                "UW-OPI-002",
                _MME.between("50", "90"),
                75,
                CDC_OPIOIDS,
                "UW-DEP-002",
            ),
            rule(
                "UW-OPI-003",
                _MME.at_least("90"),
                150,
                CDC_OPIOIDS,
                "UW-OSA-003",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="asthma",
        code="AST",
        name="Asthma",
        phrase="asthma",
        applies=diagnosed("Asthma has been diagnosed."),
        measures=(_FEV1_ASTHMA,),
        overview=(
            "Asthma is long-term inflammation of the airways that makes them narrow in bouts, with wheeze, cough and breathlessness. Between bouts most people with asthma breathe normally.",
            "Deaths from asthma are uncommon and nearly all follow severe attacks. Low lung function between attacks is one of the recognised warnings that such an attack is more likely.",
        ),
        questions=(
            q(
                "How many courses of steroid tablets were needed in the last twelve months?",
                "Each course marks an attack that inhalers could not control.",
            ),
            q(
                "Has there ever been an admission to intensive care for asthma?",
                "A previous near-fatal attack is the strongest predictor of a fatal one.",
            ),
            q(
                "Is a preventer inhaler used every day?",
                "Reliance on the reliever alone is a known risk factor.",
            ),
            q(
                "Does the applicant smoke?",
                "Smoking blunts the effect of preventer inhalers and may mean the diagnosis is partly something else.",
            ),
        ),
        evidence=(
            "Use the FEV1 as a percentage of predicted from spirometry done when the applicant was well, in the last two years.",
            "An admission to hospital for asthma in the last twelve months, or any past admission to intensive care, takes the case to a person.",
        ),
        pitfalls=(
            "Spirometry during or just after an attack reads low and does not show the usual state. Check the date against the dates of steroid courses.",
            "Peak flow is not FEV1 and cannot be read on this table.",
        ),
        rating_note="60 % of predicted is the level the cited strategy lists as a risk factor for exacerbations.",
        unchanged=(
            "Allergies, hay fever, eczema and the brand of inhaler do not change the band. Childhood asthma that ended before the age of 16 with no treatment since meets no rule and is simply noted.",
        ),
        combinations=(
            "In a smoker over 40, asthma and chronic obstructive pulmonary disease overlap. If narrowing persists after a bronchodilator, the other section's rule is read as well and the debits are added.",
            "Frequent courses of steroid tablets thin the bones and raise glucose; those effects are rated in their own sections.",
        ),
        rules=(
            rule("UW-AST-001", _FEV1_ASTHMA.at_least("60"), 0, GINA),
            rule(
                "UW-AST-002",
                _FEV1_ASTHMA.below("60"),
                75,
                GINA,
                "UW-COPD-002",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="copd",
        code="COPD",
        name="Chronic obstructive pulmonary disease",
        phrase="chronic obstructive pulmonary disease",
        applies=diagnosed(
            "Chronic obstructive pulmonary disease, chronic bronchitis with obstruction or emphysema has been diagnosed."
        ),
        measures=(_FEV1_COPD,),
        overview=(
            "Chronic obstructive pulmonary disease is permanent narrowing of the airways and destruction of the air sacs, nearly always from smoking. Breathlessness worsens over the years, with flare-ups that may need hospital care.",
            "Lung function after a bronchodilator grades the fixed obstruction and is the basis of the rating. Unlike in asthma, the figure does not return to normal.",
        ),
        questions=(
            q(
                "How many flare-ups needed antibiotics or steroids last year, and did any need hospital?",
                "Two flare-ups, or one admission, in a year marks a faster decline.",
            ),
            q(
                "Does the applicant still smoke?",
                "Stopping is the only measure that slows the loss of lung function.",
            ),
            q(
                "Is oxygen used at home?",
                "Home oxygen means the disease is at its last stage.",
            ),
            q(
                "Has weight been lost without trying?",
                "Weight loss in this disease is a sign of advanced illness.",
            ),
        ),
        evidence=(
            "Use the FEV1 after a bronchodilator from spirometry in the last two years. A figure taken without a bronchodilator overstates the obstruction; ask whether one was given.",
            "Home oxygen takes the case to a person whatever the spirometry shows.",
        ),
        pitfalls=(
            "The diagnosis itself needs a ratio of FEV1 to total exhaled volume below 0.7 after a bronchodilator. A report with a normal ratio does not support the diagnosis, whatever the FEV1.",
            "Spirometry within six weeks of a flare-up understates lung function.",
        ),
        rating_note="80, 50 and 30 % of predicted are the limits of grades 1 to 4 in the cited strategy.",
        unchanged=(
            "The inhalers prescribed and the applicant's account of breathlessness do not change the band. Having stopped smoking does not lower it, though it is the best sign for the future.",
        ),
        combinations=(
            "Continued smoking is added from the tobacco section, as is the lifetime total. Lung cancer and heart disease, not breathing failure, cause many of the deaths in this group, so a past heart attack is added in full.",
            "Unintended weight loss into the underweight range is a marker of advanced disease and is added from the build section.",
        ),
        rules=(
            rule("UW-COPD-001", _FEV1_COPD.at_least("80"), 25, GOLD),
            rule(
                "UW-COPD-002",
                _FEV1_COPD.between("50", "80"),
                50,
                GOLD,
                "UW-TOB-001",
            ),
            rule(
                "UW-COPD-003",
                _FEV1_COPD.between("30", "50"),
                150,
                GOLD,
                "UW-BMI-002",
            ),
            rule("UW-COPD-004", _FEV1_COPD.below("30"), DECLINE, GOLD),
        ),
    ),
    ImpairmentSpec(
        impairment_id="obstructive_sleep_apnoea",
        code="OSA",
        name="Obstructive sleep apnoea",
        phrase="obstructive sleep apnoea",
        applies=diagnosed(
            "Obstructive sleep apnoea has been diagnosed by a sleep study."
        ),
        measures=(_AHI,),
        overview=(
            "In obstructive sleep apnoea the throat closes repeatedly during sleep. Each closure drops the blood oxygen and briefly wakes the sleeper, who is then sleepy by day.",
            "Untreated severe disease raises blood pressure and the risk of heart rhythm problems and road accidents. Treatment with a pressure mask at night works well when it is used.",
        ),
        questions=(
            q(
                "What index did the sleep study report before treatment?",
                "The table is read on the untreated figure, since that is the severity of the disease.",
            ),
            q(
                "Is a pressure mask used, for how many hours a night?",
                "Use for four hours or more on most nights is the usual test of adequate treatment, and the machine records it.",
            ),
            q(
                "Has the applicant fallen asleep while driving?",
                "Sleepiness at the wheel is the most immediate danger.",
            ),
        ),
        evidence=(
            "Use the apnoea-hypopnoea index from the diagnostic sleep study, not from the treatment machine's nightly report.",
            "Good recorded use of a pressure mask does not move the applicant to a lower band, but it should be noted, and a person may take it into account.",
        ),
        pitfalls=(
            "Home studies that do not record sleep itself tend to give a lower index than a laboratory study. The manual does not adjust for this.",
            "The oxygen desaturation index is a different number. Do not read it on this table.",
        ),
        rating_note="5, 15 and 30 events per hour are the limits of mild, moderate and severe disease in the cited guideline.",
        gaps=(gap(_AHI.below("5"), "no rule; not sleep apnoea"),),
        unchanged=(
            "Use of a pressure mask, a jaw splint or surgery to the throat does not change the band, which is read on the untreated index. Snoring without a sleep study meets no rule.",
        ),
        combinations=(
            "Sleep apnoea and obesity come as a pair in most cases: both debits are added. Blood pressure that resists three medicines is typical and is rated in its section.",
            "Opioid medicines and sedatives deepen the pauses in breathing; the opioid section's debit is added.",
        ),
        rules=(
            rule("UW-OSA-001", _AHI.between("5", "15"), 0, AASM),
            rule(
                "UW-OSA-002",
                _AHI.between("15", "30", upper_inclusive=True),
                25,
                AASM,
                "UW-HT-002",
            ),
            rule(
                "UW-OSA-003",
                _AHI.above("30"),
                75,
                AASM,
                "UW-BMI-004",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="chronic_kidney_disease",
        code="CKD",
        name="Chronic kidney disease",
        phrase="chronic kidney disease",
        applies=measured("Two eGFR results at least three months apart are on file."),
        measures=(_EGFR,),
        overview=(
            "Chronic kidney disease is a loss of kidney function, or a sign of kidney damage, that has lasted three months or more. Diabetes and high blood pressure cause most of it.",
            "Few people with reduced kidney function reach dialysis; far more die early of heart disease, and that risk rises step by step as filtration falls. The rating follows the standard filtration categories.",
        ),
        questions=(
            q(
                "What were the last three eGFR results, and over what period?",
                "A stable figure over years is a different risk from the same figure reached by a fall of 10 in twelve months.",
            ),
            q(
                "What is the cause of the kidney disease?",
                "Inherited cysts, inflammation of the filters and diabetes each run a different course.",
            ),
            q(
                "Is there albumin in the urine?",
                "Albumin and filtration together predict the outcome better than either alone; albumin has its own section.",
            ),
            q(
                "Is the applicant under a kidney clinic?",
                "Referral usually happens once the eGFR is under 30 or falling quickly.",
            ),
        ),
        evidence=(
            "Use the most recent eGFR, provided a second result at least three months earlier was also below 60; one low reading does not establish chronic disease.",
            "An eGFR of 60 or more meets no rule of this section, even when a diagnosis of early kidney disease is recorded on the strength of albumin alone.",
        ),
        pitfalls=(
            "eGFR is worked out from creatinine, which depends on muscle. It reads low in a very muscular person or after a large meat meal and high in a frail one. A result that does not fit the person should be repeated.",
            "A single low result during an acute illness, dehydration or a course of anti-inflammatory tablets is not chronic disease.",
        ),
        rating_note="60, 45, 30 and 15 are the limits of categories G3a, G3b, G4 and G5 in the cited guideline.",
        gaps=(
            gap(
                _EGFR.at_least("60"),
                "no rule of this section; albumin in the urine is read in its own section",
            ),
        ),
        unchanged=(
            "The cause of the kidney disease does not change the band, although inherited cystic disease and inflammation of the filters go to a person. A single kidney with an eGFR of 60 or more meets no rule.",
        ),
        combinations=(
            "Albumin in the urine is rated in its own section and always added: a low eGFR with heavy albumin loss is far worse than either alone.",
            "Diabetes and raised blood pressure cause most kidney disease and are added. Anaemia appears as filtration falls below 30 and is rated in its section.",
        ),
        rules=(
            rule(
                "UW-CKD-001",
                _EGFR.between("45", "60"),
                50,
                KDIGO,
                "UW-ALB-001",
            ),
            rule(
                "UW-CKD-002",
                _EGFR.between("30", "45"),
                100,
                KDIGO,
                "UW-HT-002",
            ),
            rule(
                "UW-CKD-003",
                _EGFR.between("15", "30"),
                200,
                KDIGO,
                "UW-ANA-001",
            ),
            rule("UW-CKD-004", _EGFR.below("15"), DECLINE, KDIGO),
        ),
    ),
    ImpairmentSpec(
        impairment_id="albuminuria",
        code="ALB",
        name="Albuminuria",
        phrase="albumin in the urine",
        applies=measured("A laboratory urine albumin-to-creatinine ratio is on file."),
        measures=(_ACR,),
        overview=(
            "Healthy kidneys keep albumin in the blood. Albumin in the urine means the filters are leaking, and it is often the first evidence of damage from diabetes or high blood pressure.",
            "Even with normal filtration, albumin in the urine marks a higher risk of heart disease and of later kidney failure. It is rated here and combined with any rule the filtration rate meets.",
        ),
        questions=(
            q(
                "Was the finding confirmed on a second sample?",
                "Exercise, fever and infection can all cause a passing rise.",
            ),
            q(
                "Is a medicine that protects the kidney taken?",
                "Treatment with one of the blood pressure medicines that lower albumin loss shows the finding is being acted on.",
            ),
            q(
                "Does the applicant have diabetes or high blood pressure?",
                "Without either, the cause needs to be found before terms are offered.",
            ),
        ),
        evidence=(
            "Use a urine albumin-to-creatinine ratio from the last twelve months that was confirmed by a second sample within three months of it.",
            "A dipstick result is not a ratio. Where only a dipstick is on file, ask for a laboratory ratio.",
        ),
        pitfalls=(
            "A sample taken during a urine infection, a fever or a period, or within a day of hard exercise, can read high. That is why a second sample is required.",
            "A protein-to-creatinine ratio is a different test with different limits.",
        ),
        rating_note="30 and 300 mg/g are the limits of categories A2 and A3 in the cited guideline.",
        gaps=(gap(_ACR.below("30"), "no rule; a normal ratio"),),
        unchanged=(
            "Taking a kidney-protecting blood pressure medicine does not change the band. A normal eGFR does not lower it.",
        ),
        combinations=(
            "With diabetes, albumin in the urine marks the start of diabetic kidney disease; both debits are added. With raised blood pressure it shows the pressure has done damage.",
            "With a reduced eGFR, both kidney sections apply and are added.",
        ),
        rules=(
            rule(
                "UW-ALB-001",
                _ACR.between("30", "300", upper_inclusive=True),
                25,
                KDIGO,
                "UW-CKD-001",
            ),
            rule(
                "UW-ALB-002",
                _ACR.above("300"),
                75,
                KDIGO,
                "UW-DM-002",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="chronic_hepatitis_b",
        code="HBV",
        name="Chronic hepatitis B",
        phrase="chronic hepatitis B",
        applies=diagnosed(
            "Hepatitis B surface antigen has been positive for more than six months."
        ),
        measures=(_HBV_DNA,),
        overview=(
            "Chronic hepatitis B is infection with the hepatitis B virus that has lasted more than six months. Many carriers stay well for life; in others the virus slowly scars the liver and can cause liver cancer.",
            "The amount of virus in the blood is the main guide to who is at risk and who needs antiviral treatment.",
        ),
        questions=(
            q(
                "What are the latest HBV DNA level and liver enzymes?",
                "A high viral level with raised enzymes means active liver inflammation.",
            ),
            q(
                "Is antiviral treatment taken?",
                "The table is read with the level on treatment; a suppressed level on long-term treatment is a good sign.",
            ),
            q(
                "Has scarring been assessed by a scan or a blood index?",
                "Scarring already present outweighs the viral level.",
            ),
            q(
                "Is the liver scanned every six months?",
                "Regular scans are how liver cancer is caught early in carriers.",
            ),
        ),
        evidence=(
            "Use an HBV DNA result from the last twelve months, reported in IU/mL. Convert a result in copies/mL as shown below and record both figures.",
            "Where cirrhosis has been diagnosed, the cirrhosis section governs and this section's rule is combined with it.",
        ),
        pitfalls=(
            "Antibody to the core of the virus with a negative surface antigen means past, cleared infection. It is not chronic hepatitis B and meets no rule.",
            "Viral levels swing. One low result in an untreated carrier does not show lasting control; look for two results six months apart.",
        ),
        rating_note="2,000 and 20,000 IU/mL are the treatment levels in the cited guidance.",
        unchanged=(
            "How the infection was acquired, and whether the e antigen is positive, do not change the band in this manual.",
        ),
        combinations=(
            "Cirrhosis, once present, governs the outlook; its rule is added to the rule here. Infection with HIV or hepatitis C as well speeds liver damage; each is rated in its section.",
            "Alcohol and hepatitis B together are far more harmful than either alone: add the alcohol debit.",
        ),
        rules=(
            rule(
                "UW-HBV-001",
                _HBV_DNA.at_most("2000"),
                25,
                AASLD_HBV,
                "UW-HIV-001",
            ),
            rule(
                "UW-HBV-002",
                _HBV_DNA.between(
                    "2000", "20000", lower_inclusive=False, upper_inclusive=True
                ),
                75,
                AASLD_HBV,
            ),
            rule(
                "UW-HBV-003",
                _HBV_DNA.above("20000"),
                150,
                AASLD_HBV,
                "UW-CIR-001",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="chronic_hepatitis_c",
        code="HCV",
        name="Chronic hepatitis C",
        phrase="hepatitis C",
        applies=diagnosed("Hepatitis C virus RNA has been detected at any time."),
        measures=(_SVR_WEEKS,),
        overview=(
            "Hepatitis C is a blood-borne virus that, untreated, inflames the liver for decades and can end in cirrhosis. Short courses of antiviral tablets now clear it in nearly everyone treated.",
            "Cure is confirmed when no virus is found twelve weeks after the end of treatment. The rating turns on whether that point has been reached.",
        ),
        questions=(
            q(
                "Has antiviral treatment been completed, and on what date?",
                "The weeks in the table are counted from the last dose.",
            ),
            q(
                "What did the test twelve weeks or more after treatment show?",
                "It is the test that confirms cure.",
            ),
            q(
                "How much scarring was there before treatment?",
                "Clearing the virus stops further damage but does not remove scarring already present.",
            ),
        ),
        evidence=(
            "Count whole weeks from the end of treatment to the latest test that found no HCV RNA. An untreated applicant, or one in whom virus was found after treatment, counts as zero weeks.",
            "Antibody to hepatitis C stays positive for life after cure and is not evidence of infection.",
        ),
        pitfalls=(
            "A positive antibody test with RNA never detected means the infection cleared by itself. That is not chronic hepatitis C and meets no rule.",
            "A test at the end of treatment is not the test of cure. Only a result twelve weeks or more after the last dose counts.",
        ),
        rating_note="Twelve weeks is the point at which the cited guidance defines sustained virologic response.",
        unchanged=(
            "The genotype of the virus, the medicines used and the route of infection do not change the band.",
        ),
        combinations=(
            "Scarring that was present before cure remains. Where fatty liver disease has been diagnosed as well, its FIB-4 band is rated in that section and added; cirrhosis is rated in its own section.",
            "A history of injecting drugs goes to a person. Alcohol is added as usual.",
        ),
        rules=(
            rule(
                "UW-HCV-001",
                _SVR_WEEKS.below("12"),
                100,
                HCV_GUIDANCE,
                "UW-CIR-001",
            ),
            rule(
                "UW-HCV-002",
                _SVR_WEEKS.at_least("12"),
                25,
                HCV_GUIDANCE,
                "UW-FLD-002",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="fatty_liver_disease",
        code="FLD",
        name="Fatty liver disease",
        phrase="fatty liver disease",
        applies=diagnosed(
            "Fatty liver has been reported on imaging or diagnosed by a physician."
        ),
        measures=(_FIB4,),
        overview=(
            "Fat builds up in the liver of many people who are overweight or have diabetes. In most it stays harmless; in a minority it inflames and scars the liver over many years.",
            "Scarring, not fat, decides the outlook. A simple index from routine blood tests sorts those at low risk of advanced scarring from those who need a scan.",
        ),
        questions=(
            q(
                "How was the fatty liver found?",
                "Most are chance findings on ultrasound; one found while investigating abnormal blood tests deserves a closer look.",
            ),
            q(
                "How much alcohol is taken?",
                "Alcohol produces the same picture and has its own section.",
            ),
            q(
                "Has a scan of liver stiffness been done?",
                "It is the usual next step when the index is not clearly low.",
            ),
        ),
        evidence=(
            "Work out the FIB-4 index from age, AST, ALT and platelet count taken on the same day within the last twelve months, or use the figure the clinic recorded. Round to two decimal places.",
            "The index is unreliable under the age of 35; for a younger applicant with a raised index, send the case to a person.",
        ),
        pitfalls=(
            "The index rises with age by its construction and overstates risk over 65, where the lower cut-off in practice is often taken as 2.0. The manual keeps one set of bands and sends applicants over 65 with an index from 1.3 to below 2.0 to a person.",
            "AST and ALT taken after heavy exercise or during an acute illness distort the index.",
        ),
        rating_note="1.3 and 2.67 are the cut-offs for low and high risk of advanced fibrosis in the cited guidance.",
        unchanged=(
            "The amount of fat seen on the scan and mildly raised liver enzymes do not change the band.",
        ),
        combinations=(
            "Fatty liver disease is the liver's share of the metabolic cluster. Obesity, diabetes and raised triglycerides are each rated and added.",
            "Where alcohol is also taken at a hazardous level, the alcohol section applies as well. A liver stiffness scan that suggests cirrhosis moves the case to that section.",
        ),
        rules=(
            rule("UW-FLD-001", _FIB4.below("1.3"), 0, AASLD_NAFLD),
            rule(
                "UW-FLD-002",
                _FIB4.between("1.3", "2.67", upper_inclusive=True),
                50,
                AASLD_NAFLD,
                "UW-BMI-003",
            ),
            rule(
                "UW-FLD-003",
                _FIB4.above("2.67"),
                150,
                AASLD_NAFLD,
                "UW-CIR-001",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="cirrhosis",
        code="CIR",
        name="Cirrhosis of the liver",
        phrase="cirrhosis",
        applies=diagnosed(
            "Cirrhosis has been diagnosed by biopsy, by a stiffness scan or by a liver specialist."
        ),
        measures=(_MELD,),
        overview=(
            "Cirrhosis is widespread scarring that distorts the liver and blocks the flow of blood through it. Alcohol, viral hepatitis and fatty liver disease are the usual causes.",
            "While the liver still does its work the condition is called compensated, and years of stable life are possible. Once fluid, bleeding or confusion appear, survival is short without a transplant.",
        ),
        questions=(
            q(
                "What caused the cirrhosis, and has the cause been removed?",
                "Abstinence from alcohol or cure of hepatitis C can halt the disease.",
            ),
            q(
                "Has there ever been fluid in the abdomen, bleeding from the gullet or confusion?",
                "Any one of these means decompensation, which takes the case out of the rated band.",
            ),
            q(
                "What are the latest bilirubin, creatinine and clotting results?",
                "They are the parts of the score this section rates on.",
            ),
        ),
        evidence=(
            "Use the MELD score recorded by the liver clinic in the last six months, or work it out from blood tests taken on one day.",
            "Any past decompensation is rated as the declining rule of this section, whatever the present score.",
        ),
        pitfalls=(
            "Warfarin raises the clotting time and kidney disease raises creatinine, so either can inflate the score for reasons outside the liver.",
            "A good score does not rule out past decompensation. Read the history for fluid, bleeding and confusion.",
        ),
        rating_note="A score of 15 is the level at which the cited guideline advises evaluation for a transplant.",
        unchanged=(
            "The cause of the cirrhosis does not change the band. A normal ultrasound does not lower it.",
        ),
        combinations=(
            "The cause is rated in its own section and added: alcohol by the AUDIT score, hepatitis B by viral level, hepatitis C by whether cure is confirmed.",
            "Continued drinking with cirrhosis is the worst combination in this manual short of a decline; a liver cancer found on surveillance goes to a person.",
        ),
        rules=(
            rule(
                "UW-CIR-001",
                _MELD.below("15"),
                200,
                AASLD_TRANSPLANT,
                "UW-ALC-002",
            ),
            rule("UW-CIR-002", _MELD.at_least("15"), DECLINE, AASLD_TRANSPLANT),
        ),
    ),
    ImpairmentSpec(
        impairment_id="ulcerative_colitis",
        code="UC",
        name="Ulcerative colitis",
        phrase="ulcerative colitis",
        applies=diagnosed("Ulcerative colitis has been diagnosed at colonoscopy."),
        measures=(_CALPROTECTIN,),
        overview=(
            "Ulcerative colitis is long-term inflammation of the lining of the large bowel, with bloody diarrhoea in flares and quiet spells between. Medicines keep most people well; some need the bowel removed.",
            "Life expectancy is close to normal when the disease is quiet. Continuing inflammation brings flares, steroid courses and, over decades, a higher risk of bowel cancer.",
        ),
        questions=(
            q(
                "How much of the bowel is affected?",
                "Disease of the whole colon carries more cancer risk than disease of the rectum alone.",
            ),
            q(
                "How many flares needed steroids in the last two years?",
                "Repeated steroid courses mean maintenance treatment is not holding.",
            ),
            q(
                "When was the last colonoscopy, and what did it show?",
                "Surveillance looks for the early changes that come before cancer.",
            ),
        ),
        evidence=(
            "Use a faecal calprotectin from the last twelve months, taken when the applicant was not known to have a bowel infection.",
            "An applicant whose colon has been removed is outside these bands and goes to a person.",
        ),
        pitfalls=(
            "Anti-inflammatory painkillers and a bowel infection both raise calprotectin without a flare of colitis.",
            "A single stool sample varies from day to day; a result near 150 mcg/g is best repeated.",
        ),
        rating_note="150 mcg/g is the cut-off below which the cited guideline treats active inflammation as unlikely.",
        unchanged=(
            "The medicines used, including biological medicines, and the length of bowel affected do not change the band.",
        ),
        combinations=(
            "Bleeding leads to anaemia, which is rated in its section and added. Repeated steroid courses thin the bones.",
            "Inflammation of the bile ducts occurs in a small minority, greatly raises the risk of cancer and goes to a person.",
        ),
        rules=(
            rule("UW-UC-001", _CALPROTECTIN.below("150"), 25, AGA_UC),
            rule(
                "UW-UC-002",
                _CALPROTECTIN.at_least("150"),
                75,
                AGA_UC,
                "UW-ANA-001",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="anaemia",
        code="ANA",
        name="Anaemia",
        phrase="anaemia",
        applies=measured("A full blood count from the last six months is on file."),
        measures=(_HAEMOGLOBIN,),
        overview=(
            "Anaemia is a low level of haemoglobin. It is a sign rather than a disease, and its causes run from heavy periods and poor diet to kidney failure, bowel cancer and disorders of the bone marrow.",
            "A mild, explained anaemia is of no consequence and meets no rule. Moderate anaemia is rated while its cause is confirmed; severe anaemia is postponed until it has been treated and explained.",
        ),
        questions=(
            q(
                "What is the cause, and how was it established?",
                "An unexplained anaemia in an adult must be assumed to hide something until shown otherwise.",
            ),
            q(
                "Has the bowel been investigated?",
                "Iron deficiency in a man, or in a woman past the menopause, calls for a search for bleeding.",
            ),
            q(
                "Has the level recovered on treatment?",
                "A normal level after iron confirms the diagnosis and ends the rating.",
            ),
        ),
        evidence=(
            "Use the haemoglobin from a full blood count in the last six months. Convert a result in g/L as shown below.",
            "A haemoglobin of 11.0 g/dL or more meets no rule of this section.",
        ),
        pitfalls=(
            "Haemoglobin runs lower at the end of pregnancy and for some weeks after heavy blood donation. Check for either before rating.",
            "A carrier of thalassaemia has small red cells and a haemoglobin that sits just below the normal range for life; it usually stays at 11.0 g/dL or more and then meets no rule.",
        ),
        rating_note="8.0 and 11.0 g/dL are the limits of severe and moderate anaemia in the cited table for adults who are not pregnant.",
        gaps=(gap(_HAEMOGLOBIN.at_least("11.0"), "no rule; mild anaemia or none"),),
        unchanged=(
            "The size of the red cells and the level of iron stores do not change the band, though they help explain it.",
        ),
        combinations=(
            "Anaemia is a consequence of other impairments more often than a disease in itself. With kidney disease, ulcerative colitis or rheumatoid arthritis, the debit here is added to the debit for the cause.",
            "Anaemia with weight loss and no explanation is not rated at all until it has been investigated.",
        ),
        rules=(
            rule(
                "UW-ANA-001",
                _HAEMOGLOBIN.between("8.0", "11.0"),
                50,
                WHO_ANAEMIA,
                "UW-CKD-002",
            ),
            rule(
                "UW-ANA-002",
                _HAEMOGLOBIN.below("8.0"),
                DECLINE,
                WHO_ANAEMIA,
                note="This is a postponement until the anaemia has been treated and its cause is known.",
                postponement=True,
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="haemophilia",
        code="HEM",
        name="Haemophilia",
        phrase="haemophilia",
        applies=diagnosed("Haemophilia A or B has been diagnosed."),
        measures=(_FACTOR,),
        overview=(
            "Haemophilia is an inherited shortage of clotting factor VIII or IX, almost always in men. Bleeding into joints and muscles is the hallmark, and without treatment the joints are destroyed.",
            "Regular replacement of the missing factor has brought life expectancy close to normal. How little factor a person makes still decides how often bleeds happen and how much treatment is needed.",
        ),
        questions=(
            q(
                "What is the baseline factor level without treatment?",
                "It is the figure the table is read on and it does not change through life.",
            ),
            q(
                "Is factor given regularly to prevent bleeds, or only when one occurs?",
                "Regular prevention marks good care in severe disease.",
            ),
            q(
                "Has an inhibitor ever developed?",
                "An antibody against the replacement factor makes bleeds much harder to treat.",
            ),
            q(
                "Was the applicant treated with blood products before 1992?",
                "Older products carried hepatitis C and HIV.",
            ),
        ),
        evidence=(
            "Take the baseline factor level from the haemophilia centre's letter. A level measured shortly after an infusion is not a baseline.",
            "A current inhibitor, or a bleed inside the skull at any time, takes the case to a person. A level of 40 IU/dL or more meets no rule of this section.",
        ),
        pitfalls=(
            "Women who carry the gene can have a low factor level and are read on the same table.",
            "A level measured during an illness or in pregnancy may be raised above baseline.",
        ),
        rating_note="1, 5 and 40 IU/dL are the limits of severe, moderate and mild haemophilia in the cited guidelines.",
        gaps=(gap(_FACTOR.at_least("40"), "no rule; not haemophilia by factor level"),),
        unchanged=(
            "Whether factor VIII or factor IX is missing does not change the band. Neither does the type of replacement product.",
        ),
        combinations=(
            "People treated before blood products were made safe may carry hepatitis C or HIV. Each is rated in its own section and added.",
            "Joint damage from past bleeds limits activity but is not rated separately.",
        ),
        rules=(
            rule(
                "UW-HEM-001",
                _FACTOR.below("1"),
                150,
                WFH,
                "UW-HCV-001",
            ),
            rule(
                "UW-HEM-002",
                _FACTOR.between("1", "5", upper_inclusive=True),
                75,
                WFH,
            ),
            rule(
                "UW-HEM-003",
                _FACTOR.between("5", "40", lower_inclusive=False),
                25,
                WFH,
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="hiv_infection",
        code="HIV",
        name="HIV infection",
        phrase="HIV infection",
        applies=diagnosed("HIV infection has been diagnosed."),
        measures=(_CD4,),
        overview=(
            "The human immunodeficiency virus attacks the CD4 cells that direct the immune system. Daily antiviral treatment stops the virus multiplying and lets the cell count recover.",
            "People who start treatment early and take it reliably can expect a long life. The cell count shows how much immune reserve there is, and a low count means either late diagnosis or treatment that is not working.",
        ),
        questions=(
            q(
                "When was HIV diagnosed, and when did treatment start?",
                "A long gap between the two means years of unchecked damage.",
            ),
            q(
                "What are the latest CD4 count and viral load?",
                "A viral load that cannot be detected confirms that treatment is taken and working.",
            ),
            q(
                "What was the lowest CD4 count ever recorded?",
                "A very low past count predicts slower and less complete recovery.",
            ),
            q(
                "Is there hepatitis B or C as well?",
                "Infection with two of these viruses damages the liver faster than either alone.",
            ),
        ),
        evidence=(
            "Use the CD4 count from the last twelve months. Where the latest viral load in that period can be detected, send the case to a person, whatever the count.",
            "Fewer than twelve months of treatment is also outside these bands.",
        ),
        pitfalls=(
            "The CD4 count dips for weeks after an infection or a vaccination. One low figure among steady ones is noted, not rated; use the next result.",
            "The CD4 percentage is a different figure from the count.",
        ),
        rating_note="500 and 200 cells/mm3 are the limits of stages 1, 2 and 3 in the cited case definition.",
        unchanged=(
            "The combination of medicines, the route of infection and the years since diagnosis do not change the band.",
        ),
        combinations=(
            "Hepatitis B and hepatitis C share routes of infection with HIV and are each rated and added. Smoking is more common in this group and weighs more heavily; it is added.",
            "A past AIDS-defining illness goes to a person even when the count has recovered.",
        ),
        rules=(
            rule("UW-HIV-001", _CD4.at_least("500"), 50, CDC_HIV),
            rule(
                "UW-HIV-002",
                _CD4.between("200", "500"),
                100,
                CDC_HIV,
                "UW-HBV-001",
            ),
            rule("UW-HIV-003", _CD4.below("200"), DECLINE, CDC_HIV),
        ),
    ),
    ImpairmentSpec(
        impairment_id="depression",
        code="DEP",
        name="Depression",
        phrase="depression",
        applies=diagnosed(
            "Depression has been diagnosed or treated within the last five years."
        ),
        measures=(_PHQ9,),
        overview=(
            "Depression is a lasting low mood with loss of interest, poor sleep, tiredness and, when severe, thoughts of death. It is common, and most episodes lift with talking treatment, medicine or time.",
            "The extra mortality comes from suicide and from the neglect of physical health. Severity at the latest assessment separates the large group at small risk from the smaller group at real risk.",
        ),
        questions=(
            q(
                "How many episodes have there been, and when was the last?",
                "Each further episode makes another more likely.",
            ),
            q(
                "Has there ever been a suicide attempt or an admission to a psychiatric unit?",
                "Either one takes the case beyond these bands to a person.",
            ),
            q(
                "What treatment is taken now, and who supervises it?",
                "Care from a specialist team, rather than a family doctor, usually means a harder course.",
            ),
            q(
                "Is the applicant working?",
                "Time off work is a practical measure of how disabling the episode is.",
            ),
        ),
        evidence=(
            "Use a PHQ-9 total recorded by a clinician in the last twelve months. Where several are on file, use the latest and note the highest.",
            "With no score on file, a single past episode that ended more than five years ago with no treatment since meets no rule of this section. Anything else without a score goes to a person.",
        ),
        pitfalls=(
            "The questionnaire measures the last two weeks. A low score during treatment shows the treatment is working, not that the tendency has gone.",
            "Low mood after a bereavement, without a diagnosis or treatment, is not rated here.",
        ),
        rating_note="A score of 16 divides less severe from more severe depression in the cited guideline.",
        unchanged=(
            "The antidepressant chosen and its dose do not change the band. Talking treatment alone is rated on the same table.",
        ),
        combinations=(
            "Alcohol and depression feed each other; add the alcohol debit. Long-term opioid treatment with depression is a recognised risk for overdose and is added.",
            "A diagnosis of bipolar disorder or psychosis is not depression for this section and goes to a person.",
        ),
        rules=(
            rule(
                "UW-DEP-001",
                _PHQ9.below("16"),
                25,
                NICE_DEPRESSION,
                "UW-ALC-001",
            ),
            rule(
                "UW-DEP-002",
                _PHQ9.at_least("16"),
                100,
                NICE_DEPRESSION,
                "UW-OPI-001",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="epilepsy",
        code="EPI",
        name="Epilepsy",
        phrase="epilepsy",
        applies=diagnosed("Epilepsy has been diagnosed."),
        measures=(_SEIZURE_FREE,),
        overview=(
            "Epilepsy is a tendency to repeated seizures that have no immediate cause. Medicine stops the seizures completely in about two people in three.",
            "The risks to life are accidents during a seizure, a prolonged seizure and, rarely, sudden unexpected death. All of them fall away the longer a person stays free of seizures.",
        ),
        questions=(
            q(
                "On what date was the most recent seizure of any type?",
                "The years in the table run from that date.",
            ),
            q(
                "What type of seizures occur, and do they happen in sleep?",
                "Convulsive seizures at night carry the highest risk of sudden death.",
            ),
            q(
                "How many medicines are taken, and has any been withdrawn?",
                "Needing several medicines points to seizures that are hard to control.",
            ),
            q(
                "Was a cause found on brain imaging?",
                "A tumour or an old injury is an impairment to be assessed in its own right.",
            ),
        ),
        evidence=(
            "Take the date of the last seizure from the neurology letter or the attending physician's statement, and count whole years to the application.",
            "A seizure provoked by a clear, removed cause, such as a head injury in the previous week, is not epilepsy and meets no rule here.",
        ),
        pitfalls=(
            "Brief absences, jerks on waking and auras are seizures. Applicants often count only convulsions, so the date in the neurology letter is preferred to the date on the form.",
            "A seizure during a planned withdrawal of medicine still restarts the count.",
        ),
        rating_note="Ten seizure-free years is part of the cited definition of resolved epilepsy. The edge at two years is this manual's own.",
        own_edges=(Decimal(2),),
        unchanged=(
            "The medicine taken, the result of the brain wave recording and whether a driving licence is held do not change the band.",
        ),
        combinations=(
            "Seizures brought on by alcohol or its withdrawal are rated here and in the alcohol section, and the debits are added.",
            "Epilepsy after a stroke is rated in both sections. A learning disability with epilepsy goes to a person.",
        ),
        rules=(
            rule(
                "UW-EPI-001",
                _SEIZURE_FREE.below("2"),
                100,
                ILAE,
                "UW-ALC-001",
            ),
            rule("UW-EPI-002", _SEIZURE_FREE.between("2", "10"), 50, ILAE),
            rule("UW-EPI-003", _SEIZURE_FREE.at_least("10"), 0, ILAE),
        ),
    ),
    ImpairmentSpec(
        impairment_id="rheumatoid_arthritis",
        code="RA",
        name="Rheumatoid arthritis",
        phrase="rheumatoid arthritis",
        applies=diagnosed(
            "Rheumatoid arthritis has been diagnosed by a rheumatologist."
        ),
        measures=(_DAS28,),
        overview=(
            "Rheumatoid arthritis is an autoimmune inflammation of the joint linings, chiefly of the hands and feet, that erodes the joints if it is not suppressed.",
            "The inflammation is not confined to joints. Active disease over years speeds up artery disease and can involve the lungs, which is why disease activity, rather than joint damage, drives the rating.",
        ),
        questions=(
            q(
                "What was the disease activity score at the last two clinic visits?",
                "Sustained low activity is the aim of treatment and the best sign.",
            ),
            q(
                "Which disease-modifying medicines are taken?",
                "A biological medicine is not penalised, but it shows that standard treatment was not enough.",
            ),
            q(
                "Are steroid tablets taken regularly, and at what dose?",
                "Long-term steroids bring bone thinning, diabetes and infections.",
            ),
            q(
                "Are the lungs involved?",
                "Scarring of the lungs is the complication that most shortens life.",
            ),
        ),
        evidence=(
            "Use the DAS28 recorded by the rheumatology clinic in the last twelve months, whichever blood marker it was calculated with.",
            "Lung involvement takes the case to a person.",
        ),
        pitfalls=(
            "The score can be worked out with either of two blood markers, and the version using C-reactive protein runs a little lower. The manual reads both on the one table.",
            "Some clinics record a different index with different cut-offs. Only DAS28 can be read here.",
        ),
        rating_note="2.6, 3.2 and 5.1 are the cut-offs for remission, low, moderate and high activity among the cited measures.",
        unchanged=(
            "Deformity of the hands, the presence of rheumatoid factor and joint replacements do not change the band.",
        ),
        combinations=(
            "Years of inflammation speed artery disease: a past heart attack, raised blood pressure and smoking are added from their sections.",
            "Steroid tablets taken for long periods thin the bones, and the osteoporosis debit is added. Anaemia of long-standing inflammation is rated in its section.",
        ),
        rules=(
            rule("UW-RA-001", _DAS28.below("2.6"), 0, ACR_RA),
            rule(
                "UW-RA-002",
                _DAS28.between("2.6", "3.2", upper_inclusive=True),
                25,
                ACR_RA,
            ),
            rule(
                "UW-RA-003",
                _DAS28.between(
                    "3.2", "5.1", lower_inclusive=False, upper_inclusive=True
                ),
                50,
                ACR_RA,
                "UW-OST-001",
            ),
            rule("UW-RA-004", _DAS28.above("5.1"), 100, ACR_RA),
        ),
    ),
    ImpairmentSpec(
        impairment_id="osteoporosis",
        code="OST",
        name="Osteoporosis",
        phrase="osteoporosis",
        applies=measured("A bone density scan from the last three years is on file."),
        measures=(_T_SCORE,),
        overview=(
            "Osteoporosis is loss of bone density that leaves bones liable to break after a minor fall. It is silent until a wrist, a vertebra or a hip fractures.",
            "A hip fracture in later life carries a real risk of death in the following year. In applicants of working age the added mortality is small, and the single rule here reflects that.",
        ),
        questions=(
            q(
                "Has there been a fracture after a minor fall?",
                "A fracture already sustained doubles the chance of another.",
            ),
            q(
                "Why is the bone thin?",
                "Steroid treatment, early menopause, low weight and bowel disease are causes that have their own bearing on risk.",
            ),
            q(
                "Is bone-protecting treatment taken?",
                "Treatment roughly halves the risk of spinal fracture.",
            ),
        ),
        evidence=(
            "Use the lower of the hip and spine T-scores from a bone density scan in the last three years.",
            "A T-score above -2.5 meets no rule of this section. Two or more fractures of the spine take the case to a person.",
        ),
        pitfalls=(
            "Arthritis of the spine and a hardened aorta lying in front of it make the spine T-score read falsely high. Where hip and spine differ by more than one unit, the hip figure is the more reliable.",
            "A Z-score compares with people of the same age and is a different figure.",
        ),
        rating_note="A T-score of -2.5 or lower is the cited definition of osteoporosis.",
        gaps=(
            gap(
                _T_SCORE.above("-2.5"),
                "no rule; bone density that is normal or only reduced",
            ),
        ),
        unchanged=(
            "The medicine prescribed, calcium and vitamin D supplements and height lost do not change the single rule.",
        ),
        combinations=(
            "Thin bones are often the mark of something else. A body mass index below 18.5 kg/m2, long steroid treatment for rheumatoid arthritis or lung disease and heavy drinking are each rated in their own sections and added.",
        ),
        rules=(
            rule(
                "UW-OST-001",
                _T_SCORE.at_most("-2.5"),
                25,
                WHO_BONE,
                "UW-BMI-002",
            ),
        ),
    ),
    ImpairmentSpec(
        impairment_id="melanoma",
        code="MEL",
        name="Melanoma of the skin",
        phrase="a melanoma that has been removed",
        applies=diagnosed(
            "An invasive melanoma of the skin has been removed, with no spread to lymph nodes or beyond."
        ),
        measures=(_THICKNESS,),
        overview=(
            "Melanoma is a cancer of the pigment cells of the skin. Caught while thin it is cured by removal; once it has grown deep it can spread through lymph and blood years later.",
            "The pathologist's measurement of thickness is the strongest single guide to outcome for a tumour confined to the skin. This section applies only where there was no spread to lymph nodes or beyond.",
        ),
        questions=(
            q(
                "What thickness did the pathology report give, and was the surface ulcerated?",
                "Ulceration worsens the outlook at any thickness.",
            ),
            q(
                "Was a sentinel lymph node sampled, and what did it show?",
                "A positive node takes the case out of this section altogether.",
            ),
            q(
                "On what date was the melanoma removed, and has there been a second one?",
                "Most recurrences come within five years, and a second primary restarts the clock.",
            ),
            q(
                "Is the applicant in regular skin follow-up?",
                "Follow-up finds both recurrences and new melanomas early.",
            ),
        ),
        evidence=(
            "Take the tumour thickness from the pathology report, to one decimal place. A report that does not state it is not enough to rate on.",
            "Spread to a lymph node or a distant site, or removal less than twelve months ago, takes the case to a person. Melanoma in situ, which has no thickness, meets no rule of this section.",
        ),
        pitfalls=(
            "Thickness is measured in millimetres to one decimal place. The older level system, written in Roman numerals, is a different scale and cannot be read on this table.",
            "A second, wider operation after the first removal is routine. It is not a recurrence.",
        ),
        rating_note="1.0, 2.0 and 4.0 mm are the limits of categories T1 to T4 in the cited staging manual.",
        unchanged=(
            "The part of the body affected and the applicant's skin type do not change the band. A clear follow-up scan does not lower it.",
        ),
        combinations=(
            "A weakened immune system makes recurrence more likely. HIV infection with a CD4 count below 500 cells/mm3 is rated in its own section and added, and an organ transplant goes to a person.",
            "A second melanoma is rated on the thicker of the two.",
        ),
        rules=(
            rule("UW-MEL-001", _THICKNESS.at_most("1.0"), 50, AJCC),
            rule(
                "UW-MEL-002",
                _THICKNESS.between(
                    "1.0", "2.0", lower_inclusive=False, upper_inclusive=True
                ),
                100,
                AJCC,
            ),
            rule(
                "UW-MEL-003",
                _THICKNESS.between(
                    "2.0", "4.0", lower_inclusive=False, upper_inclusive=True
                ),
                200,
                AJCC,
                "UW-HIV-002",
            ),
            rule("UW-MEL-004", _THICKNESS.above("4.0"), DECLINE, AJCC),
        ),
    ),
)
