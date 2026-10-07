"""The people the classifier's training pages are drawn from (spine AD-13).

Five invented people, none of them in the case set, each with every kind of page the
generator can draw with a text layer. Between them they also have the edge kinds the
scored set has: a handwritten note, blank pages, and pages turned by the PDF's flag
and in the drawing. Their
names, places, organisations, figures and recipes differ from those of the scored
cases, and the generator refuses a training page that is also a scored page.
"""

from datetime import date
from decimal import Decimal

from synthdata.case_parts import (
    Sex,
    clinical,
    diagnosis,
    doctor,
    finding,
    months_before,
    panel,
    person,
)
from synthdata.model import (
    CaseDefinition,
    Clinical,
    HandwrittenNote,
    Invoice,
    InvoiceLine,
    PageLayout,
    PageSpec,
    Passport,
    Payslip,
    Recipe,
    UtilityBill,
)

# One page of each layout that has a text layer.
_LAYOUTS = (
    PageLayout.APPLICATION_FORM,
    PageLayout.ATTENDING_PHYSICIAN_STATEMENT,
    PageLayout.LAB_REPORT,
    PageLayout.PASSPORT,
    PageLayout.INVOICE,
    PageLayout.PAYSLIP,
    PageLayout.UTILITY_BILL,
    PageLayout.RECIPE,
)
_PAGES = tuple(PageSpec(layout=layout) for layout in _LAYOUTS)


def _line(description: str, quantity: int, unit_price: str) -> InvoiceLine:
    return InvoiceLine(
        description=description, quantity=quantity, unit_price=Decimal(unit_price)
    )


def _subject(
    number: int,
    *,
    name: str,
    born: date,
    sex: Sex,
    occupation: str,
    street: str,
    town: str,
    physician: str,
    practice: str,
    practice_street: str,
    insurer: str,
    signed: date,
    figures: Clinical,
    country: str,
    nationality: str,
    seller: str,
    lines: tuple[InvoiceLine, ...],
    employer: str,
    pay: tuple[str, str, str],
    supplier: str,
    usage: tuple[int, str, str],
    recipe: Recipe,
    note: HandwrittenNote | None = None,
    more_pages: tuple[PageSpec, ...] = (),
) -> CaseDefinition:
    """One person of the training set, with one page of each layout.

    `figures` is built with the key `50 + number`, as the person is.
    """
    key = 50 + number
    # Counted in whole months, so that a person signed up in January works too.
    month_start = months_before(signed, 1).replace(day=1)
    return CaseDefinition(
        case_id=f"train-{number:03d}",
        summary=f"Training pages, person {number}.",
        document_date=signed,
        insurer=insurer,
        applicant=person(
            key, name, born, sex, occupation, street, town, area="505", series="TRN"
        ),
        physician=doctor(key, physician, practice, practice_street, town, area="606"),
        clinical=figures,
        passport=Passport(
            country=country,
            authority=f"{country} Identity Service",
            nationality=nationality,
            number=f"T-TRN-{7000 + number * 311:07d}",
            place_of_birth=town,
            issued_on=date(2016 + number, number + 1, 10 + number),
            expires_on=date(2026 + number, number + 1, 10 + number),
        ),
        invoice=Invoice(
            issuer=seller,
            invoice_number=f"TRN-INV-{4200 + number * 17}",
            issued_on=month_start.replace(day=12),
            due_on=signed.replace(day=12),
            lines=lines,
        ),
        payslip=Payslip(
            employer=employer,
            period_start=month_start,
            period_end=month_start.replace(day=28),
            paid_on=month_start.replace(day=25),
            gross_pay=Decimal(pay[0]),
            income_tax=Decimal(pay[1]),
            pension=Decimal(pay[2]),
        ),
        utility_bill=UtilityBill(
            supplier=supplier,
            period_start=months_before(month_start, 2),
            period_end=month_start.replace(day=28),
            issued_on=signed.replace(day=3),
            electricity_kwh=usage[0],
            rate_per_kwh=Decimal(usage[1]),
            standing_charge=Decimal(usage[2]),
        ),
        recipe=recipe,
        note=note,
        pages=(*_PAGES, *more_pages),
    )


TRAINING_SUBJECTS: tuple[CaseDefinition, ...] = (
    _subject(
        1,
        name="Ashton Stubworth",
        born=date(1983, 5, 9),
        sex="male",
        occupation="Electrician",
        street="18 Mock Lane",
        town="Placeham",
        physician="Bailey Mockfold",
        practice="Placeham Riverside Surgery",
        practice_street="2 Stub Street",
        insurer="Sample Provident Life",
        signed=date(2026, 8, 12),
        figures=clinical(
            51,
            reference="TR",
            build=(179, 83),
            alcohol=9,
            family="Grandfather had a stroke at age 80.",
            pressure=((date(2026, 8, 4), 121, 77),),
            collected=date(2026, 8, 4),
            labs=panel("male", "5.2 87 181 107 53 1.0 99"),
            remarks=(
                "Seen for an insurance medical. Fit and active, cycles to work. "
                "Nothing abnormal found on examination."
            ),
            lab_name="Mock Pathology Services",
        ),
        country="Commonwealth of Sampleland",
        nationality="Samplelander",
        seller="Placeham Electrical Wholesale",
        lines=(
            _line("Twin and earth cable, 100 m", 2, "64.00"),
            _line("Consumer unit, 10 way", 1, "89.50"),
            _line("Socket outlet, double", 12, "3.15"),
        ),
        employer="Stub Electrical Contractors",
        pay=("3120.00", "436.80", "156.00"),
        supplier="Placeham Gas and Electric",
        usage=(701, "0.27", "28.75"),
        recipe=Recipe(
            source="Weeknight Cooking, a made-up collection",
            title="Roast Vegetable Tray Bake",
            serves=3,
            minutes=50,
            ingredients=(
                "2 courgettes, thickly sliced",
                "1 aubergine, cubed",
                "2 red peppers, cut into strips",
                "250 g cherry tomatoes",
                "2 tablespoons olive oil",
                "1 block of halloumi, sliced",
            ),
            steps=(
                "Toss the vegetables in the oil on a large tray and roast at 200 degrees for half an hour.",
                "Lay the halloumi over the top and return the tray to the oven for ten minutes.",
                "Serve straight from the tray with warm flatbread.",
            ),
        ),
        note=HandwrittenNote(
            lines=(
                "Surgery note, 4 August 2026",
                "Medical for life cover, as requested.",
                "Heart sounds normal, pulse regular.",
                "Lungs clear. Abdomen soft.",
                "No tablets, no known allergies.",
                "Bloods taken this morning.",
                "Nothing further needed from me.",
                "(signed) family doctor",
            )
        ),
        more_pages=(PageSpec(layout=PageLayout.HANDWRITTEN_NOTE),),
    ),
    _subject(
        2,
        name="Carter Sampledge",
        born=date(1971, 9, 23),
        sex="female",
        occupation="Florist",
        street="7 Dummy Row",
        town="Dummyhurst",
        physician="Dakota Testridge",
        practice="Dummyhurst Village Practice",
        practice_street="33 Example Lane",
        insurer="Placeholder Assurance Society",
        signed=date(2026, 8, 19),
        figures=clinical(
            52,
            reference="TR",
            build=(164, 71),
            smoking="Former smoker, stopped in 2009",
            alcohol=5,
            family="Sister has rheumatoid arthritis.",
            pressure=(
                (date(2026, 5, 11), 134, 84),
                (date(2026, 8, 10), 132, 82),
            ),
            collected=date(2026, 8, 10),
            labs=panel("female", "5.6 97 216 139 54 0.9 83", ("TSH", "3.1")),
            remarks=(
                "Blood pressure a little raised on two visits; lifestyle advice "
                "given and a review booked. Cholesterol to be rechecked in a year."
            ),
            lab_name="Dummyhurst Hospital Laboratory",
        ),
        country="Kingdom of Mockland",
        nationality="Mocklander",
        seller="Dummyhurst Flower Market",
        lines=(
            _line("Cut roses, box of 50", 3, "42.00"),
            _line("Florist wire, 22 gauge", 5, "2.40"),
            _line("Kraft wrapping paper, roll", 2, "11.75"),
            _line("Ribbon, 25 m", 4, "3.90"),
        ),
        employer="Sample Blooms",
        pay=("1980.00", "214.60", "99.00"),
        supplier="Mock Valley Power",
        usage=(342, "0.33", "36.20"),
        recipe=Recipe(
            source="From a notebook of family recipes",
            title="Chickpea and Spinach Curry",
            serves=4,
            minutes=35,
            ingredients=(
                "2 tins of chickpeas, drained",
                "200 g fresh spinach",
                "1 tin of coconut milk",
                "1 onion and 3 cloves of garlic",
                "2 tablespoons mild curry paste",
                "Rice, to serve",
            ),
            steps=(
                "Fry the onion and the garlic until golden, then stir in the curry paste for a minute.",
                "Add the chickpeas and the coconut milk and simmer gently for fifteen minutes.",
                "Stir the spinach through until it wilts and serve over rice.",
            ),
        ),
        more_pages=(PageSpec(layout=PageLayout.BLANK),),
    ),
    _subject(
        3,
        name="Elliot Demogate",
        born=date(1996, 2, 14),
        sex="male",
        occupation="Chef",
        street="52 Fixture Yard",
        town="Examplewick",
        physician="Greer Fixturemoor",
        practice="Examplewick Walk-in Centre",
        practice_street="9 Test Parade",
        insurer="Specimen Life and General",
        signed=date(2026, 9, 4),
        figures=clinical(
            53,
            reference="TR",
            build=(188, 79),
            alcohol=11,
            family="No illness of note in parents or siblings.",
            pressure=((date(2026, 8, 27), 117, 69),),
            collected=date(2026, 8, 27),
            labs=panel("male", "4.9 81 162 88 60 0.8 112", ("Haemoglobin", "14.9")),
            remarks=(
                "Hay fever only. Takes an antihistamine in summer. Examination "
                "normal and no further tests are planned."
            ),
            lab_name="Examplewick Community Laboratory",
            diagnoses=(
                diagnosis(
                    "Seasonal allergic rhinitis",
                    date(2011, 6, 1),
                    "Cetirizine in summer",
                    None,
                ),
            ),
        ),
        country="Federation of Demoland",
        nationality="Demolander",
        seller="Examplewick Catering Supplies",
        lines=(
            _line("Chef's knife, 20 cm", 1, "58.00"),
            _line("Chopping board, colour coded", 6, "7.25"),
            _line("Apron, bib style", 4, "9.50"),
        ),
        employer="The Fixture Rooms Restaurant",
        pay=("2710.00", "352.30", "135.50"),
        supplier="Examplewick District Energy",
        usage=(256, "0.29", "24.10"),
        recipe=Recipe(
            source="Staff lunch sheet",
            title="Lemon and Barley Salad",
            serves=6,
            minutes=30,
            ingredients=(
                "300 g pearl barley",
                "2 lemons, juice and zest",
                "1 cucumber, diced",
                "A large bunch of parsley, chopped",
                "150 g feta, crumbled",
                "4 tablespoons olive oil",
            ),
            steps=(
                "Boil the barley for twenty-five minutes until tender, drain it and leave it to cool.",
                "Whisk the lemon juice and zest with the oil and pour it over the barley.",
                "Fold in the cucumber, the parsley and the feta just before serving.",
            ),
        ),
        more_pages=(PageSpec(layout=PageLayout.LAB_REPORT, drawn_rotation=90),),
    ),
    _subject(
        4,
        name="Harlow Specimenshaw",
        born=date(1958, 11, 30),
        sex="female",
        occupation="Retired head teacher",
        street="3 Example Close",
        town="Testhurst",
        physician="Jessie Dummyrow",
        practice="Testhurst Doctors",
        practice_street="71 Sample Road",
        insurer="Demo Friendly Society",
        signed=date(2026, 9, 10),
        figures=clinical(
            54,
            reference="TR",
            build=(159, 61),
            alcohol=6,
            family="Mother had osteoporosis.",
            pressure=(
                (date(2026, 6, 2), 136, 80),
                (date(2026, 9, 1), 134, 78),
            ),
            collected=date(2026, 9, 1),
            labs=panel("female", "5.5 93 228 148 66 0.8 71"),
            remarks=(
                "Osteoarthritis of both knees, managed with exercise and simple "
                "pain relief. Walks two miles a day. Otherwise well for her age."
            ),
            lab_name="Testhurst Pathology Partnership",
            diagnoses=(
                diagnosis(
                    "Osteoarthritis of the knees",
                    date(2020, 4, 20),
                    "Paracetamol as needed",
                    None,
                ),
            ),
            findings=(finding("PHQ-9 score", "2", "points", date(2026, 9, 1)),),
        ),
        country="Principality of Fixtureland",
        nationality="Fixturelander",
        seller="Testhurst Garden Centre",
        lines=(
            _line("Compost, peat free, 50 litre", 4, "6.99"),
            _line("Rose bush, bare root", 3, "12.50"),
            _line("Garden twine, 100 m", 1, "4.20"),
        ),
        employer="Testhurst Teachers' Pension Scheme",
        pay=("1640.00", "148.90", "0.00"),
        supplier="Fixtureland Electricity Board",
        usage=(519, "0.30", "33.40"),
        recipe=Recipe(
            source="Church hall supper club",
            title="Mushroom and Leek Pie",
            serves=5,
            minutes=70,
            ingredients=(
                "500 g chestnut mushrooms, quartered",
                "3 leeks, washed and sliced",
                "300 ml single cream",
                "1 tablespoon wholegrain mustard",
                "1 sheet of puff pastry",
                "1 egg, beaten, to glaze",
            ),
            steps=(
                "Cook the leeks and the mushrooms in butter until soft and the liquid has gone.",
                "Stir in the cream and the mustard, tip into a pie dish and cover with the pastry.",
                "Brush with the egg and bake at 200 degrees for thirty minutes until risen and brown.",
            ),
        ),
        more_pages=(
            PageSpec(layout=PageLayout.ATTENDING_PHYSICIAN_STATEMENT, rotation=180),
        ),
    ),
    _subject(
        5,
        name="Kelsey Examplemoor",
        born=date(1989, 7, 6),
        sex="female",
        occupation="Veterinary nurse",
        street="26 Template Green",
        town="Fixtureby",
        physician="Linden Sampleshaw",
        practice="Fixtureby Health Hub",
        practice_street="14 Mock Avenue",
        insurer="Fixture Mutual Insurance",
        signed=date(2026, 9, 18),
        figures=clinical(
            55,
            reference="TR",
            build=(173, 74),
            smoking="Current smoker, 5 cigarettes a day for 8 years",
            alcohol=13,
            family="Father has high cholesterol.",
            pressure=((date(2026, 9, 9), 119, 75),),
            collected=date(2026, 9, 9),
            labs=panel("female", "5.0 84 207 134 48 0.7 101", ("Serum urate", "4.4")),
            remarks=(
                "Migraine about once a month, settled by a triptan. Smokes a "
                "little and wishes to stop; referred to the stop-smoking service."
            ),
            lab_name="Fixtureby Regional Laboratory",
            diagnoses=(
                diagnosis(
                    "Migraine without aura",
                    date(2015, 1, 8),
                    "Sumatriptan as needed",
                    None,
                ),
            ),
        ),
        country="Republic of Testland",
        nationality="Testlander",
        seller="Fixtureby Pet Supplies",
        lines=(
            _line("Dog food, dry, 12 kg", 2, "38.75"),
            _line("Cat litter, 30 litre", 3, "9.20"),
            _line("Flea treatment, pack of 6", 1, "27.00"),
            _line("Lead, 2 m, nylon", 2, "6.45"),
        ),
        employer="Sample Veterinary Group",
        pay=("2290.00", "281.70", "114.50"),
        supplier="Testland Northern Electricity",
        usage=(403, "0.32", "39.90"),
        recipe=Recipe(
            source="Printed from a made-up food magazine",
            title="Baked Pears with Cinnamon",
            serves=2,
            minutes=25,
            ingredients=(
                "2 ripe pears, halved and cored",
                "1 tablespoon brown sugar",
                "Half a teaspoon of ground cinnamon",
                "A knob of butter",
                "A handful of chopped walnuts",
                "Plain yoghurt, to serve",
            ),
            steps=(
                "Set the pears cut side up in a small dish and dot them with the butter.",
                "Scatter over the sugar, the cinnamon and the walnuts.",
                "Bake at 190 degrees for twenty minutes and serve warm with the yoghurt.",
            ),
        ),
        more_pages=(
            PageSpec(layout=PageLayout.BLANK),
            PageSpec(layout=PageLayout.APPLICATION_FORM, rotation=90),
        ),
    ),
)
