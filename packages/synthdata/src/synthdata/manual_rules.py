"""The rule table as data: the one definition the manual PDF and the rule table file come from.

To add or change a rule, edit the impairment modules, rerun `uv run python -m synthdata`
and commit the code with the regenerated files (see `data/README.md`).
"""

from synthdata.manual_impairments_1 import IMPAIRMENTS_1
from synthdata.manual_impairments_2 import IMPAIRMENTS_2
from synthdata.manual_model import GlossaryTerm, ManualSpec

_INTRODUCTION: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "What this manual is",
        (
            "This is a synthetic underwriting manual. It was written for a software demonstration and for nothing else. No insurer or reinsurer uses it, none wrote it, and no part of its text was taken from any insurer's or reinsurer's manual.",
            "It is laid out the way field guides to medical impairments usually are: for each impairment a short description, the questions an underwriter asks, and a table of probable ratings. That layout is common knowledge; every sentence here is original.",
            "The clinical thresholds are real in one sense only: most band edges follow a published guideline from a public body, named in the rule. A few edges are this manual's own, drawn where a guideline's stage was split in two or a waiting period was needed; every rule with such an edge says which edge it is. The ratings attached to the bands are not real in any sense. Every debit percentage and every decision to decline was invented for this document, and none of them should be used to assess a real person.",
        ),
    ),
    (
        "How a section is laid out",
        (
            "Sections 2 onwards each cover one impairment, and each has the same five numbered parts. Part 1, the impairment, says what the condition is, why it matters to mortality and when the section applies at all. Part 2, key questions, lists what to find out and why each answer matters. Part 3, evidence and readings, says which document to trust, where that evidence commonly misleads and how to convert units.",
            "Part 4, probable rating, holds the table of bands, the readings that meet no rule, the definition of each rule and what does not change the rating. Part 5 gives worked examples with invented applicants, the rule met by a reading exactly on each edge, the combinations seen most often and the rules of other sections that this section points to.",
            "A section applies in one of two ways. Some apply only when the condition, event or treatment is on file: a reading alone is not enough. Others apply to any reading of their measure, diagnosis or not. Part 1 of each section says which, and names any other impairment whose diagnosis rules the section out.",
            "Section numbers are printed with every heading, so a reference to section 10.4 always means the probable rating part of the tenth section.",
        ),
    ),
    (
        "Reading a rule",
        (
            "Every rule has an identifier made of the letters UW, a short code for the impairment and a three-digit number. The identifier belongs to one rule only and never changes.",
            "A rule is defined in exactly one place: in part 4 of its section, in a paragraph that starts with the word Rule, then the identifier, then a colon. That paragraph is complete in itself. It names the impairment and its section, states what is measured and the band, gives the rating, points to related rules, and names the public source of each edge of the band or says that the edge is this manual's own.",
            "Everywhere else a rule is only mentioned, in words such as: see rule UW-HT-002. A mention is a pointer to the definition, not a second copy of it. If a mention and a definition ever seemed to disagree, the definition would be right.",
            "A pointer always states the circumstance in which the other rule applies, in that rule's own terms: its impairment and its band. Most pointers lead to a rule that applies as well as the first. A few lead to a rule that applies in place of it, and say so.",
        ),
    ),
    (
        "Reading a rating",
        (
            "A rating is one of two things. A debit is an addition to expected mortality, written as a percentage with a plus sign: a debit of +50 % means the applicant is expected to have one and a half times the mortality of a standard life of the same age and sex. Debits are always whole numbers.",
            "Decline means that no terms are offered on the evidence in the file. Some rules that decline are postponements, and their rating reads decline as a postponement: the applicant may apply again once time has passed or a reading has improved.",
            "A few rules carry no debit, written as no debit, +0 %. They exist so that a finding is recognised and named even though it adds nothing to the rating. A case that meets only such rules is a standard case.",
        ),
    ),
    (
        "Combining debits",
        (
            "An applicant may meet several rules, in one section or in several. First find every rule that applies. Then combine them in this order.",
            "If any rule that applies says decline, the outcome is decline, whatever the other rules say.",
            "Otherwise add the debits of all the rules that apply. A debit of +50 % for raised blood pressure and a debit of +50 % for smoking give a loading of +100 %. Rules with no debit add nothing.",
            "Only rules that can apply together are added. Where one rule applies in place of another, as a diabetes rule does in place of the prediabetes rule once the diagnosis is made, the first is dropped and the second stands alone.",
            "If the total is +0 %, the case is standard. The sum is not capped in this manual, and no credits are given for favourable findings.",
            "Within one measure the bands never overlap, so an applicant meets at most one rule for each thing measured. Where a section has two measures, such as present smoking status and lifetime pack-years, a rule for each can apply and both debits count.",
        ),
    ),
    (
        "Bands and their edges",
        (
            "Each band says exactly which readings fall in it. The words from 7.0 to below 8.0 include 7.0 and exclude 8.0. The words above 40 and below 50 exclude both ends. The words of 180 or more include 180, and of 40 or less include 40.",
            "Where the public guideline draws a line with a particular edge, the band keeps it. Otherwise the lower end of a band is included and the upper end is not.",
            "Most sections rate only the abnormal range. The readings that meet no rule are listed under each rating table with what they mean, so that every reading a measure can take either falls in one band or is accounted for there.",
        ),
    ),
    (
        "Evidence and its age",
        (
            "Rate on documents, not on recollection. A laboratory report outranks an attending physician's statement for a laboratory figure; the physician's statement outranks the application form for a diagnosis, a date or a habit.",
            "Each section says how recent a reading must be. A reading older than that is not wrong, but it is not evidence of the present state, and a newer one should be asked for.",
            "When two documents disagree, do not average them and do not choose the kinder one. Follow the section's instruction if it gives one; otherwise use the more recent document of the higher rank and record the disagreement.",
            "When the reading a rule needs is missing, the rule cannot be applied. A rule is never applied on a guess, and a missing reading is never treated as a normal one.",
        ),
    ),
    (
        "When a person must decide",
        (
            "This manual gives probable ratings. It does not decide cases. Many sections name circumstances that fall outside their bands: a recent hospital admission, an operation, a complication. Those cases go to a person, as does any case in which no rule fits the evidence, two rules of one measure seem to apply, or a deciding fact cannot be traced to a page of the file.",
            "Software that reads this manual may suggest a rating and must show which rules and which facts it relied on. The suggestion is never the decision.",
        ),
    ),
    (
        "Sources",
        (
            "The last section of this manual lists every public guideline cited, with the body that issued it, its year and the sections that rely on it. The citation beside each rule says where in the guideline the threshold is found.",
            "Some bands have an edge that no guideline supplies, for example where a wide clinical stage is split in two. The definition of each rule and the note under each rating table say which edges come from the cited source and which are this manual's own.",
        ),
    ),
)

_GLOSSARY: tuple[tuple[str, str], ...] = (
    (
        "Attending physician's statement",
        "A report on the applicant written for the insurer by the doctor who treats them.",
    ),
    (
        "Band",
        "A range of readings of one measure that share a rating. Bands of one measure do not overlap.",
    ),
    (
        "Cross-reference",
        "A mention of a rule outside its definition, pointing the reader to it.",
    ),
    (
        "Debit",
        "An addition to expected mortality, as a whole percentage. Debits from different rules are added.",
    ),
    (
        "Decline",
        "The outcome in which no terms are offered on the evidence in the file.",
    ),
    (
        "Impairment",
        "A medical condition, finding or habit that may change expected mortality.",
    ),
    (
        "Loading",
        "The sum of the debits of every rule that applies to a case.",
    ),
    (
        "Measure",
        "What a rule compares with its threshold: a reading, a score, a length of time or a status.",
    ),
    (
        "Postponement",
        "A decline that invites a fresh application after a stated time or change.",
    ),
    (
        "Probable rating",
        "The rating this manual suggests for a band. A person makes the decision.",
    ),
    (
        "Reading",
        "One recorded value of a measure, with its date and the document it comes from.",
    ),
    (
        "Rule",
        "One band of one measure for one impairment, with its rating and its source. Each rule has its own identifier.",
    ),
    (
        "Standard",
        "The outcome for a case whose debits add up to nothing: ordinary terms.",
    ),
    (
        "Threshold",
        "The edge or edges of a band, taken where possible from a public guideline.",
    ),
)


def _glossary() -> tuple[GlossaryTerm, ...]:
    """The general terms and every measure, each once, in alphabetical order."""
    terms = dict(_GLOSSARY)
    if len(terms) != len(_GLOSSARY):
        raise ValueError("the glossary lists a general term twice")
    measures: dict[str, str] = {}
    for impairment in (*IMPAIRMENTS_1, *IMPAIRMENTS_2):
        for measure in impairment.measures:
            # "body mass index" takes a capital; "eGFR" and "HbA1c" are left alone.
            label = measure.label
            if label.split()[0].islower():
                label = label[0].upper() + label[1:]
            unit = f" Unit: {measure.unit}." if measure.unit_printed else ""
            meaning = f"{measure.meaning}{unit}"
            # A measure shared by impairments is one entry; anything else is a clash.
            if measures.setdefault(label, meaning) != meaning or label in terms:
                raise ValueError(f"the glossary would have two meanings for {label!r}")
    terms.update(measures)
    return tuple(
        GlossaryTerm(term=term, meaning=terms[term])
        for term in sorted(terms, key=str.casefold)
    )


MANUAL = ManualSpec(
    title="Synthetic Underwriting Manual",
    introduction=_INTRODUCTION,
    impairments=(*IMPAIRMENTS_1, *IMPAIRMENTS_2),
    glossary=_glossary(),
)
