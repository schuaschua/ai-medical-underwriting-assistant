"""Text normalisation and the quote finder, shared by the quote check and the redaction check (AD-14, AD-17)."""

import re
import unicodedata
from dataclasses import dataclass

# Characters a PDF text layer adds that a reader never sees: the soft hyphen,
# the zero-width space, non-joiner and joiner, and the byte order mark.
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\ufeff"))

# The unit of `quote_start`, `quote_end` and of `intake`'s `char_start` and
# `char_end`: Unicode code points of the page text as `intake` stores it,
# which is what indexing a Python `str` counts. Not bytes, and not the UTF-16
# units a browser's string counts: the SPA hands offsets back to `intake`
# and never indexes the text itself. A range is half-open:
# `text[quote_start:quote_end]` is the quoted text.
QUOTE_OFFSET_UNIT = "unicode_code_point"

# AD-21: redaction puts the category of what it found in its place, in square
# brackets, such as `[Person]`, `[PhoneNumber]`, `[US_SSN]` or `[PERSON_1]`.
# A token is a name of two or more characters that begins with a letter, in
# either case, and goes on in letters, digits, underscores and hyphens. A
# one-letter flag such as `[H]` in a table of results is not one. The real
# service draws a label and a number on a picture of the page (`PER` and `1`,
# seen in Azure on 2026-10-10); `intake` writes the token for it into the page
# text it stores, named by the category: `[Person]`.
MASK_TOKEN_PATTERN = r"\[[A-Za-z][A-Za-z0-9_-]+\]"  # noqa: S105 - the shape of a mask token, not a credential
_MASK_TOKEN = re.compile(MASK_TOKEN_PATTERN)

# Between two digits these join them into one number: `7.4`, `1,200`.
_DIGIT_JOINERS = ".,"


def normalise(text: str) -> str:
    """Return `text` in one comparable form: case, spacing and PDF artefacts ignored.

    Compatibility forms are folded (NFKC, so the ligature "\ufb01" becomes "fi"),
    invisible characters are removed, case is folded and every run of white
    space becomes one space. Punctuation is kept, so a mask token such as
    `[Person]` stays one token (`[person]`).
    """
    visible = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    return " ".join(visible.casefold().split())


def has_mask_token(text: str) -> bool:
    """Whether the text holds a mask token of redaction, such as `[Person]`."""
    return _MASK_TOKEN.search(text) is not None


def is_only_mask_tokens(text: str) -> bool:
    """Whether the text is nothing but mask tokens and punctuation.

    True when it holds at least one mask token and, with the tokens taken
    out, no letter or digit is left: `[Person]`, or `[Person], [Address].`.
    Text with a mask token beside real words is not.
    """
    if not has_mask_token(text):
        return False
    return not any(character.isalnum() for character in _MASK_TOKEN.sub("", text))


@dataclass(frozen=True, slots=True)
class QuoteMatch:
    """Where a quote sits in a page text, in `QUOTE_OFFSET_UNIT`: `text[start:end]`."""

    start: int
    end: int


def _pieces(text: str) -> list[tuple[int, int]]:
    """Cut `text` into the smallest pieces that normalise on their own.

    NFKC of the whole text is then the NFKC of each piece, one after the
    other. A piece never begins with a combining mark: a letter keeps every
    mark that follows it, whatever their classes, because normalisation may
    reorder them. And it keeps a following character that composes with it.
    Each piece is `text[start:end]`.
    """
    pieces: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(text) + 1):
        if index < len(text):
            character = text[index]
            if not (character.isascii() and text[index - 1].isascii()):
                # Two ASCII characters never compose: the common case, cheaply.
                if unicodedata.combining(character):
                    continue
                piece = text[start:index]
                together = unicodedata.normalize("NFKC", piece + character)
                apart = unicodedata.normalize("NFKC", piece) + unicodedata.normalize(
                    "NFKC", character
                )
                if together != apart:
                    continue
        pieces.append((start, index))
        start = index
    return pieces


def _normalised_with_spans(text: str) -> tuple[str, list[tuple[int, int, bool, bool]]]:
    """The normalised text and, for each of its characters, where it came from.

    A span is `(start, end, first, last)`: the piece `text[start:end]` the
    character was made from, and whether it is the first and the last
    character that piece gave, white space aside (a character whose
    compatibility form begins with a space gives that space and then its
    mark, and the mark is where it begins). A run of white space is one
    space, whose span runs from the start of the run to its end. Built in
    the steps of `normalise`, so the two cannot differ.
    """
    characters: list[str] = []
    spans: list[tuple[int, int, bool, bool]] = []
    # A run of white space waiting to become one space: where it began and ended.
    space: tuple[int, int] | None = None
    for start, end in _pieces(text):
        folded = (
            unicodedata.normalize("NFKC", text[start:end])
            .translate(_INVISIBLE)
            .casefold()
        )
        kept = [
            position
            for position, character in enumerate(folded)
            if not character.isspace()
        ]
        for position, character in enumerate(folded):
            if character.isspace():
                space = (space[0] if space is not None else start, end)
                continue
            if space is not None and characters:
                characters.append(" ")
                spans.append((space[0], space[1], True, True))
            space = None
            characters.append(character)
            spans.append((start, end, position == kept[0], position == kept[-1]))
    # White space at either end is dropped, as `normalise` drops it.
    return "".join(characters), spans


def _joins_a_word(normalised: str, at: int, length: int) -> bool:
    """Whether a match at `normalised[at:at + length]` cuts into a word or a number.

    It does when a letter or digit of the match has a letter or digit right
    beside it outside the match (`5.6` in `15.6`), or when a digit of the
    match is joined to a digit outside it by a point or a comma (`4 %` in
    `7.4 %`).
    """
    end = at + length
    if normalised[at].isalnum() and at > 0:
        before = normalised[at - 1]
        if before.isalnum():
            return True
        if (
            normalised[at].isdigit()
            and before in _DIGIT_JOINERS
            and at > 1
            and normalised[at - 2].isdigit()
        ):
            return True
    if normalised[end - 1].isalnum() and end < len(normalised):
        after = normalised[end]
        if after.isalnum():
            return True
        if (
            normalised[end - 1].isdigit()
            and after in _DIGIT_JOINERS
            and end + 1 < len(normalised)
            and normalised[end + 1].isdigit()
        ):
            return True
    return False


class QuoteFinder:
    """Finds quotes in one page text; the page is normalised and mapped once (AD-14)."""

    def __init__(self, page_text: str) -> None:
        self._page_text = page_text
        self._normalised, self._spans = _normalised_with_spans(page_text)

    @property
    def normalised(self) -> str:
        """The page text in its normalised form: what `normalise` gives for it."""
        return self._normalised

    def find(self, quote: str) -> QuoteMatch | None:
        """Where the quote is in the page text as stored; see `find_quote`."""
        wanted = normalise(quote)
        if not wanted:
            return None
        at = self._normalised.find(wanted)
        while at != -1:
            first, last = self._spans[at], self._spans[at + len(wanted) - 1]
            match = QuoteMatch(first[0], last[1])
            if (
                first[2]
                and last[3]
                and not _joins_a_word(self._normalised, at, len(wanted))
                # The map and `normalise` are built alike; this holds the
                # promise of `find_quote` whatever the text is.
                and normalise(self._page_text[match.start : match.end]) == wanted
            ):
                return match
            at = self._normalised.find(wanted, at + 1)
        return None


def find_quote(page_text: str, quote: str) -> QuoteMatch | None:
    """Find a quote in a page text; answer where it is in the text as stored (AD-14).

    The quote is found when its normalised form (`normalise`) occurs in the
    normalised page text: case, spacing, line breaks, ligatures and the
    invisible characters of a PDF do not count. It must stand there whole:
    an occurrence that cuts into a word or a number is passed over (`5.6`
    is not found in `15.6`, nor `4 %` in `7.4 %`), and so is one that would
    begin or end inside one character of the page (half of a ligature). The
    answer is the first place that holds, as offsets into `page_text`
    itself, not into its normalised form, in `QUOTE_OFFSET_UNIT`:
    `normalise(page_text[start:end])` is the normalised quote. None when
    the quote is not on the page, or is nothing once normalised. To check
    several quotes against one page, use `QuoteFinder`.
    """
    return QuoteFinder(page_text).find(quote)
