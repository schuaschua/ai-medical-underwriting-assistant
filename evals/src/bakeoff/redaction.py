"""The redaction check: did redaction leave a planted identifier behind (spine AD-17, AD-21).

The text of every page of every case is read through `web` and normalised
with the contracts' function, the one the quote check uses. A planted
identifier found in it is a leak, and so is any part of a planted name. The
report says where a leak is and of what category, never the value.

It also counts what redaction took away beside the identifiers: the quotes
of the expected facts that the text of their page no longer holds. That is a
figure of the report and no fault of the run.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from bakeoff.answer_key import PERSON_NAME, AnswerKeyEntry
from bakeoff.client import WebClient, WebError
from contracts.models.web import QuoteNotFound, RedactionLeak
from contracts.text import QuoteFinder, normalise

logger = logging.getLogger(__name__)

# A part of a name shorter than this (an initial) is not looked for on its
# own: it would be found in any text.
_MIN_NAME_PART = 2


def name_parts(entry: AnswerKeyEntry) -> set[str]:
    """Each part of each planted name of a case, normalised."""
    return {
        part
        for identifier in entry.identifiers
        if identifier.category == PERSON_NAME
        for part in normalise(identifier.value).split()
        if sum(character.isalnum() for character in part) >= _MIN_NAME_PART
    }


def leaked_categories(entry: AnswerKeyEntry, page_text: str) -> set[str]:
    """The categories of the planted identifiers of a case that a page text still holds.

    An identifier is found wherever its normalised value occurs in the
    normalised text. A part of a name is found where it stands as a word of
    its own, so that a short surname inside another word is not taken for it.
    """
    page = QuoteFinder(page_text)
    found = {
        identifier.category
        for identifier in entry.identifiers
        if normalise(identifier.value) in page.normalised
    }
    if any(page.find(part) is not None for part in name_parts(entry)):
        found.add(PERSON_NAME)
    return found


def masked_allowed_strings(entry: AnswerKeyEntry, page_texts: Sequence[str]) -> int:
    """How many of the strings that may also be redacted stand in no page text of the case.

    A string counts as still there where it stands whole, as a quote does:
    a short one inside another word is not it.
    """
    pages = [QuoteFinder(text) for text in page_texts]
    return sum(
        not any(page.find(allowed) is not None for page in pages)
        for allowed in entry.may_also_be_redacted
    )


def quotes_not_found(
    entry: AnswerKeyEntry, page_texts: dict[int, str]
) -> list[QuoteNotFound]:
    """The expected-fact quotes of a case that the stored text of their page no longer holds.

    A quote is found where the quote finder finds it, the rule the quote
    check of extracted facts uses: whole, with case, spacing and line breaks
    not counting. A quote on a page the case has no text for is not found.
    Each is named by its place, never by its words.
    """
    pages = {number: QuoteFinder(text) for number, text in page_texts.items()}
    return [
        QuoteNotFound(
            case_key=entry.case_key,
            page_number=place.page_number,
            fact_number=fact_number,
        )
        for fact_number, fact in enumerate(entry.expected_facts, start=1)
        for place in fact.places
        if place.page_number not in pages
        or pages[place.page_number].find(place.quote) is None
    ]


@dataclass
class RedactionCheck:
    """What the check found over the cases of a run."""

    cases_checked: int = 0
    pages_checked: int = 0
    identifiers_checked: int = 0
    leaks: list[RedactionLeak] = field(default_factory=list)
    may_also_be_redacted: int = 0
    may_also_be_redacted_masked: int = 0
    cases_not_checked: list[str] = field(default_factory=list)
    quotes_checked: int = 0
    quotes_not_found: list[QuoteNotFound] = field(default_factory=list)

    def add(self, entry: AnswerKeyEntry, page_texts: dict[int, str]) -> None:
        """Check the page texts of one case, by page number."""
        if not page_texts:
            # Nothing of the case was read: it is not counted as checked.
            self.cases_not_checked.append(entry.case_key)
            return
        self.cases_checked += 1
        self.pages_checked += len(page_texts)
        self.identifiers_checked += len(entry.identifiers)
        for page_number, text in sorted(page_texts.items()):
            for category in sorted(leaked_categories(entry, text)):
                # The place and the kind, never what was found.
                logger.error(
                    "planted identifier in a page text: case=%s page=%d category=%s",
                    entry.case_key,
                    page_number,
                    category,
                )
                self.leaks.append(
                    RedactionLeak(
                        case_key=entry.case_key,
                        page_number=page_number,
                        category=category,
                    )
                )
        self.may_also_be_redacted += len(entry.may_also_be_redacted)
        self.may_also_be_redacted_masked += masked_allowed_strings(
            entry, list(page_texts.values())
        )
        # Over-redaction is counted, not logged as a fault: it fails no run.
        self.quotes_checked += sum(len(fact.places) for fact in entry.expected_facts)
        self.quotes_not_found.extend(quotes_not_found(entry, page_texts))


async def read_page_texts(client: WebClient, case_id: str | None) -> dict[int, str]:
    """The stored text of every page of a case, by page number; empty when it cannot be read.

    Half a case is not checked as if it were whole: one page that cannot be
    read leaves the case unchecked.
    """
    if case_id is None:
        return {}
    try:
        listed = await client.pages(case_id)
        return {
            page.page_number: (await client.page_text(page.page_id)).text
            for page in listed.pages
        }
    except WebError as error:
        logger.warning("page texts not read: case_id=%s %s", case_id, error)
        return {}
