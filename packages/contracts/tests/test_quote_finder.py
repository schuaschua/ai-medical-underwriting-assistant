"""Story 2.4: the one function that finds a quote in a page text, and the mask-token rules."""

import random
import re

import pytest

from contracts.text import (
    MASK_TOKEN_PATTERN,
    QUOTE_OFFSET_UNIT,
    QuoteFinder,
    QuoteMatch,
    find_quote,
    has_mask_token,
    is_only_mask_tokens,
    normalise,
)

# A page as `intake` stores one: lines of a form, then a table whose cells are
# lines of their own. Synthetic values only.
PAGE = (
    "Laboratory Report\n"
    "Patient name\n[Person]\n"
    "Results\nTest\nResult\nUnit\n"
    "HbA1c\n7.4\n%\n4.0 to 5.6\nH\n"
    "Fasting plasma glucose\n142\nmg/dL\n"
    "[Person] has type 2 diabetes"
)


def quoted(page: str, quote: str) -> str:
    match = find_quote(page, quote)
    assert match is not None, quote
    return page[match.start : match.end]


def test_story_2_4_the_offset_unit_is_stated_beside_the_finder() -> None:
    # What `intake`'s word boxes count too: positions in a Python string.
    assert QUOTE_OFFSET_UNIT == "unicode_code_point"


@pytest.mark.parametrize(
    "quote",
    [
        "HbA1c 7.4 %",
        "hba1c 7.4 %",
        "  HBA1C\t7.4\n% ",
        "HbA1c\n7.4\n%",
        "Fasting plasma glucose 142 mg/dL",
        "Laboratory Report",
    ],
)
def test_story_2_4_a_quote_is_found_whatever_its_case_spacing_or_line_breaks(
    quote: str,
) -> None:
    match = find_quote(PAGE, quote)

    assert match is not None
    assert 0 <= match.start < match.end <= len(PAGE)
    # The promise: the stored text between the offsets, normalised, is the quote.
    assert normalise(PAGE[match.start : match.end]) == normalise(quote)


def test_story_2_4_the_offsets_index_the_stored_text_not_the_normalised_one() -> None:
    # The page has more white space than its normalised form, so the two differ.
    page = "Smoking   status\n\n\nNever smoked\nWeight\n78 kg"

    match = find_quote(page, "weight 78 kg")

    assert match == QuoteMatch(page.index("Weight"), len(page))
    assert page[match.start : match.end] == "Weight\n78 kg"
    assert len(normalise(page)) < len(page)


def test_story_2_4_a_quote_across_table_cells_is_found_in_the_order_stored() -> None:
    assert quoted(PAGE, "HbA1c 7.4 % 4.0 to 5.6 H") == "HbA1c\n7.4\n%\n4.0 to 5.6\nH"
    # The cells in another order are not on the page: never a guess.
    assert find_quote(PAGE, "7.4 HbA1c %") is None


def test_story_2_4_a_ligature_and_invisible_characters_do_not_hide_a_quote() -> None:
    page = "Well de\ufb01ned hyper\u00adtension, \ufb02uid\u200b intake normal"

    assert quoted(page, "defined hypertension") == "de\ufb01ned hyper\u00adtension"
    assert quoted(page, "fluid intake") == "\ufb02uid\u200b intake"
    # And the other way round: the quote holds the ligature, the page does not.
    assert quoted("a defined term", "de\ufb01ned") == "defined"


def test_story_2_4_a_quote_never_begins_or_ends_inside_one_character() -> None:
    # "fi" is one character on the page; half of it is not a place in the text.
    page = "de\ufb01ned"

    assert find_quote(page, "def") is None
    assert find_quote(page, "ined") is None
    assert quoted(page, "defined") == page


def test_story_2_4_a_composed_letter_is_found_by_its_parts() -> None:
    page = "Cafe\u0301 au lait spots noted"

    assert quoted(page, "caf\u00e9 au lait") == "Cafe\u0301 au lait"


def test_story_2_4_a_mask_token_is_matched_like_any_other_text() -> None:
    assert quoted(PAGE, "[person] has type 2 diabetes") == (
        "[Person] has type 2 diabetes"
    )
    assert quoted(PAGE, "Patient name [Person]") == "Patient name\n[Person]"


def test_story_2_4_a_quote_that_occurs_twice_is_found_at_its_first_place() -> None:
    page = "HbA1c 7.4 %\nLatest HbA1c\n7.4 % on 2026-09-07"

    match = find_quote(page, "7.4 %")

    assert match == QuoteMatch(6, 11)
    assert page.count("7.4 %") == 2


@pytest.mark.parametrize(
    "quote", ["HbA1c 9.9 %", "Resting heart rate 61 bpm", "", "   ", "\u200b\u00ad"]
)
def test_story_2_4_a_quote_that_is_not_on_the_page_is_not_found(quote: str) -> None:
    assert find_quote(PAGE, quote) is None


def test_story_2_4_an_empty_page_holds_no_quote() -> None:
    assert find_quote("", "HbA1c") is None


@pytest.mark.parametrize(
    ("text", "holds", "only"),
    [
        ("Name: [Person]", True, False),
        ("[Person]", True, True),
        ("[Person], [Address].", True, True),
        ("  [PhoneNumber] ", True, True),
        ("[Person] has type 2 diabetes", True, False),
        # Whatever the service writes: upper case, digits, underscores,
        # hyphens, lower case.
        ("[US_SSN]", True, True),
        ("[PERSON_1]", True, True),
        ("[person]", True, True),
        ("[Phone-Number]", True, True),
        ("SSN [US_SSN] on file", True, False),
        # A one-letter flag of a results table is text, and so is a number.
        ("HbA1c 7.4 % [H]", False, False),
        ("[1]", False, False),
        ("[_x]", False, False),
        ("[two words]", False, False),
        ("HbA1c 7.4 %", False, False),
        ("...", False, False),
    ],
)
def test_story_2_4_mask_tokens_are_told_from_real_text(
    text: str, holds: bool, only: bool
) -> None:
    assert has_mask_token(text) is holds
    assert is_only_mask_tokens(text) is only
    assert re.search(MASK_TOKEN_PATTERN, text) is not None if holds else True


# --- A quote stands whole: never inside a word or a number -----------------------------


@pytest.mark.parametrize(
    ("page", "quote"),
    [
        ("HbA1c 7.4 %", "4 %"),
        ("Reference 15.6", "5.6"),
        ("Reference 5.67", "5.6"),
        ("Creatinine 17.45 mg/dL", "7.4"),
        ("Dose 1,200 mg", "200 mg"),
        ("Dose 1,200 mg", "1"),
        ("Hypertension noted", "tension"),
        ("Hypertension noted", "hyper"),
        ("hyper\u00adtension", "tension"),
        ("HbA1c7.4", "7.4"),
    ],
)
def test_story_2_4_a_quote_inside_a_word_or_a_number_is_not_found(
    page: str, quote: str
) -> None:
    assert find_quote(page, quote) is None


@pytest.mark.parametrize(
    ("page", "quote", "selected"),
    [
        ("HbA1c 7.4 %", "7.4 %", "7.4 %"),
        ("HbA1c 7.4 %", "7.4", "7.4"),
        ("HbA1c 7.4 %", "hba1c", "HbA1c"),
        ("Range (4.0 to 5.6).", "5.6", "5.6"),
        ("Range (4.0 to 5.6).", "4.0 to 5.6", "4.0 to 5.6"),
        ("Weight: 78 kg, stable", "78 kg", "78 kg"),
        ("Total 198, LDL 118.", "198", "198"),
        ("HbA1c 7.4%", "7.4", "7.4"),
        # Punctuation at the quote's own edge has no word to cut into.
        ("HbA1c: 7.4 %", ": 7.4", ": 7.4"),
    ],
)
def test_story_2_4_a_quote_that_is_a_whole_word_or_number_is_found(
    page: str, quote: str, selected: str
) -> None:
    assert quoted(page, quote) == selected


def test_story_2_4_an_occurrence_inside_a_number_is_passed_over_for_a_whole_later_one() -> (
    None
):
    page = "Reference 15.6\nPotassium\n5.6\nmmol/L"

    match = find_quote(page, "5.6")

    assert match == QuoteMatch(page.index("\n5.6") + 1, page.index("\n5.6") + 4)


def test_story_2_4_a_half_character_candidate_is_passed_over_for_a_whole_later_one() -> (
    None
):
    # U+2025 is two dots in one character: a dot of it is no place in the
    # text, but the full stop further on is.
    page = "see \u2025 then stop ."

    match = find_quote(page, ".")

    assert match == QuoteMatch(len(page) - 1, len(page))
    assert find_quote("see \u2025 then stop", ".") is None
    assert quoted(page, "..") == "\u2025"


# --- The map from the normalised text back to the stored one --------------------------


def test_story_2_4_marks_of_different_classes_after_one_letter_do_not_hide_a_quote() -> (
    None
):
    # A dot below (class 220) and an acute above (230), in either order:
    # normalisation orders them one way.
    for marks in ("\u0323\u0301", "\u0301\u0323"):
        page = f"Dose a{marks} taken, then a{marks}b"
        assert QuoteFinder(page).normalised == normalise(page)
        assert quoted(page, "a\u0323\u0301 taken") == f"a{marks} taken"
        assert quoted(page, "a\u0301\u0323 taken") == f"a{marks} taken"


def test_story_2_4_a_character_whose_compatibility_form_begins_with_a_space_is_found() -> (
    None
):
    # U+00A8 is a space and a combining diaeresis once normalised; U+00B4 a
    # space and a combining acute.
    page = "na\u00a8ve \u00b4x\u00b4 end"

    assert QuoteFinder(page).normalised == normalise(page)
    assert quoted(page, "\u00a8ve") == "\u00a8ve"
    assert quoted(page, "\u00b4x\u00b4") == "\u00b4x\u00b4"
    assert quoted(page, "na \u0308ve") == "na\u00a8ve"


_ALPHABET = (
    "aAbz09 \n\t.,%[]-"
    # Combining marks of several classes, and letters that compose with them.
    "\u0301\u0323\u0308\u0327\u0345eo"
    # Ligatures and other compatibility characters, some with spaces in them.
    "\ufb01\ufb02\ufb03\u00a8\u00b4\u2025\u2026\u00bd\u2460\u3392\ufdfa\u00a0\u2003"
    # Invisible characters, case that folds to two letters, and jamo that compose.
    "\u00ad\u200b\ufeff\u00df\u0130\u1100\u1161\u11a8"
)


def test_story_2_4_the_mapped_text_is_what_normalise_gives_for_any_text() -> None:
    generator = random.Random(24)  # noqa: S311 - test data, not a secret
    for _ in range(3000):
        text = "".join(
            generator.choice(_ALPHABET) for _ in range(generator.randint(0, 24))
        )
        finder = QuoteFinder(text)
        assert finder.normalised == normalise(text), repr(text)
        # And whatever is found is where it is said to be.
        for word in normalise(text).split(" "):
            match = finder.find(word)
            if match is not None:
                assert normalise(text[match.start : match.end]) == word, repr(text)


def test_story_2_4_one_finder_checks_many_quotes_against_one_page() -> None:
    finder = QuoteFinder(PAGE)

    assert finder.find("HbA1c 7.4 %") == find_quote(PAGE, "HbA1c 7.4 %")
    assert finder.find("fasting plasma glucose 142") is not None
    assert finder.find("nowhere") is None
    assert finder.find("  ") is None
