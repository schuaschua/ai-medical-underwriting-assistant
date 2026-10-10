"""Cut the parsed manual into chunks (spine AD-12).

Two cuts of the same parsed manual. The `smart` cut gives exactly one rule
per chunk. The `fixed` cut is the plain baseline of the retrieval ladder
(AD-11, row `r1`): the body text in reading order, in runs of a fixed number
of words with an overlap, wherever the rules happen to fall.

The chunker works from what the layout model read, never from what the
generator of the manual knows. It finds a definition by the contracts'
marker (`Rule <rule_id>:`), a section by the number printed with its
heading, and page furniture by the layout model's roles, by its repeating
on most pages, or by its being the text the roles name on other pages. So it
learns the rules from the manual alone, and the layout stand-in can be
replaced by the real service.

The real service does not keep to the manual's paragraphs: it gives some
footer lines no role, and it ends some definitions early and gives the rest
as the next paragraph. A definition is therefore read to its own end: where
the manual prints its definitions with labelled parts (a threshold, a
rating, a source), a paragraph that lacks one of them is joined with what
follows it.

Both cuts walk the manual the same way (`_body`), so both leave out the
same page furniture and stand under the same headings.

It checks its own result and fails loudly: a partial index, or one whose
rules stand under the wrong section, is worse than none. The checks need
nothing but the manual: headings are numbered in order and none twice, the
rules of one section share one id code, every rule that is referred to is
defined, and a definition ends where a sentence ends and has every part the
manual's definitions have.
"""

import re
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace

from contracts.enums import ChunkSet
from contracts.rules import RULE_DEFINITION_PATTERN, RULE_ID_PATTERN
from retrieval.domain.entities import Chunk, LayoutParagraph, ParsedLayout

# What the layout model calls the parts of a page that are not its content.
FURNITURE_ROLES = frozenset({"pageHeader", "pageFooter", "pageNumber"})
# What it calls a heading. When any paragraph of the manual has such a role,
# a numbered line without one is no heading.
HEADING_ROLES = frozenset({"sectionHeading", "title"})
# Without roles, a text is furniture when it is on more than this share of
# the pages (digits aside, so that `Page 7` and `Page 8` are one text), and
# on at least this many.
_FURNITURE_SHARE = 0.5
_FURNITURE_MIN_PAGES = 3
# A furniture text shorter than this is not looked for inside a chunk: a
# page number alone would be found in any threshold.
_FURNITURE_MIN_CHARS = 12
# A heading is one short paragraph.
_HEADING_MAX_CHARS = 160

_DEFINITION = re.compile(RULE_DEFINITION_PATTERN)
_RULE_ID = re.compile(rf"\b{RULE_ID_PATTERN}\b")
# `12 Hypertension`: a numbered section. `12.4 Probable rating`: a part of it.
# A heading's words begin with a capital and do not end like a sentence or a
# list, so `3 months after diagnosis` and `1 to 2 episodes.` are no headings.
_SECTION = re.compile(r"^(\d{1,3})\s+([A-Z].*[^.:;,])$")
_PART = re.compile(r"^(\d{1,3})\.(\d{1,2})\s+([A-Z].*[^.:;,])$")
_DIGITS = re.compile(r"\d+")
# Where a definition may end: at the end of a sentence, a bracket or a quote after it.
_SENTENCE_END = re.compile(r"[.!?][)\]\"'”’]*$")
# The labelled parts of a definition, in the order the manual prints them:
# its threshold, its rating and where the threshold is from (`Source of the
# threshold:`, `Source of the edge at 140 mmHg:`, `The source for the
# measure and the other edges of this section:`). A manual in which any
# definition has all three prints every definition so: see `_whole_test`.
_LABELLED_PARTS = re.compile(
    r"\bThreshold:.*\bProbable rating:.*\b[Ss]ource (?:of|for) the\b[^:]*:", re.DOTALL
)


class ManualInvalid(Exception):
    """The parsed manual cannot be cut into one chunk per rule.

    `reason` is a short code and `where` a rule id, a section number or a
    page number: never text of the manual.
    """

    def __init__(self, reason: str, where: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.where = where


def chunk_id_for(chunk_set: ChunkSet, rule_id: str) -> str:
    """AD-12: the id of the chunk that defines `rule_id` in one chunk set.

    Derived from the two and nothing else, so it is the same on every run
    and in every store. Letters, digits and dashes only: Azure AI Search
    takes it as a document key as it is (Epic 3).
    """
    return f"{chunk_set.value}-{rule_id}"


# What a `fixed` chunk that starts before the first numbered section stands
# under: the title page and the contents have no impairment, and a search
# result must name one (the contracts' `SearchItem`).
FRONT_MATTER_SECTION_ID = "0"
FRONT_MATTER = "Front matter"
# How many digits a `fixed` chunk's id has, and so how many chunks the set
# may hold: with more, the ids would no longer sort as text.
_FIXED_ID_DIGITS = 4
MAX_FIXED_CHUNKS = 10**_FIXED_ID_DIGITS - 1
# What stands between two paragraphs in a `fixed` chunk's text; the words of
# one paragraph are a space apart. The manual prints each definition as one
# paragraph, so a rule's own definition ends where its line does.
PARAGRAPH_BREAK = "\n"


def fixed_chunk_id(position: int) -> str:
    """AD-12: the id of the `fixed` chunk at a 1-based position in the manual.

    Derived from the chunk set and the position, so it is the same on every
    run of the same manual and settings. The digits are padded: the ids sort
    in the manual's order, also as text, up to `MAX_FIXED_CHUNKS`.
    """
    return f"{ChunkSet.FIXED.value}-{position:0{_FIXED_ID_DIGITS}d}"


def squash(text: str) -> str:
    """The text with every run of white space as one space, and none at its ends."""
    return " ".join(text.split())


def _is_heading(text: str) -> bool:
    """Whether a text has the form of a numbered heading."""
    return len(text) <= _HEADING_MAX_CHARS and (
        _PART.match(text) is not None or _SECTION.match(text) is not None
    )


@dataclass(slots=True)
class _Place:
    """Where the walk through the manual is: the numbered section and its part."""

    section_number: int | None = None
    section_title: str = ""
    part_id: str = ""
    part_title: str = ""
    # How often a section numbered 1 was taken: more than once when the
    # contents page's lines were read as headings before the first section.
    first_sections: int = 0

    def take_heading(self, text: str, defined_any: bool) -> None:
        """Move on if `text` is a numbered heading.

        Raises `ManualInvalid` for a heading that cannot be one of this
        manual: a number already taken, a number skipped, or a part of a
        section the walk is not in. Each means a heading was missed or
        something else was taken for one, and every rule after it would
        stand under the wrong section.
        """
        if len(text) > _HEADING_MAX_CHARS:
            return
        part = _PART.match(text)
        if part is not None:
            if self.section_number != int(part.group(1)):
                raise ManualInvalid(
                    "part_outside_section", f"{part.group(1)}.{part.group(2)}"
                )
            self.part_id = f"{part.group(1)}.{part.group(2)}"
            self.part_title = part.group(3)
            return
        section = _SECTION.match(text)
        if section is None:
            return
        number = int(section.group(1))
        expected = 1 if self.section_number is None else self.section_number + 1
        # The contents page lists every section before the first one starts,
        # so the count may begin again at 1: but only before any rule.
        if number != expected and (number != 1 or defined_any):
            raise ManualInvalid(
                "section_number_taken"
                if number < expected
                else "section_number_skipped",
                str(number),
            )
        self.section_number = number
        self.section_title = section.group(2)
        self.first_sections += number == 1
        self.part_id, self.part_title = "", ""


def _furniture(layout: ParsedLayout) -> tuple[set[int], set[str]]:
    """Which paragraphs are page furniture, by position, and the furniture texts.

    A paragraph that is a numbered heading or holds a definition is never
    furniture, however often its text repeats: a part's heading is printed
    in every section.
    """
    content = [
        _is_heading(squash(paragraph.text))
        or _DEFINITION.search(paragraph.text) is not None
        for paragraph in layout.paragraphs
    ]
    pages_of: dict[str, set[int]] = defaultdict(set)
    for paragraph, is_content in zip(layout.paragraphs, content, strict=True):
        if not is_content:
            pages_of[_DIGITS.sub("#", squash(paragraph.text))].add(
                paragraph.page_number
            )
    needed = max(_FURNITURE_MIN_PAGES, len(layout.pages) * _FURNITURE_SHARE)
    repeating = {
        text for text, pages in pages_of.items() if text and len(pages) > needed
    }
    # The real service leaves the role off a footer line on some pages, and
    # gives a footer of two lines as one paragraph or as two: a text that has
    # a furniture role on several pages is furniture on every page. Not a
    # text without a letter: a page number alone is also a cell of a table.
    pages_by_role: dict[str, set[int]] = defaultdict(set)
    for paragraph in layout.paragraphs:
        if paragraph.role in FURNITURE_ROLES:
            pages_by_role[_DIGITS.sub("#", squash(paragraph.text))].add(
                paragraph.page_number
            )
    by_roles = {
        text
        for text, pages in pages_by_role.items()
        if len(pages) >= _FURNITURE_MIN_PAGES and any(map(str.isalpha, text))
    }
    positions: set[int] = set()
    texts: set[str] = set()
    for position, paragraph in enumerate(layout.paragraphs):
        text = squash(paragraph.text)
        by_role = paragraph.role in FURNITURE_ROLES
        marker = _DEFINITION.search(text)
        if by_role and marker is not None:
            # A rule the layout model took for a header or a footer would be
            # dropped without a word.
            raise ManualInvalid("definition_in_page_furniture", marker.group(1))
        if by_role or (
            not content[position] and _DIGITS.sub("#", text) in repeating | by_roles
        ):
            positions.add(position)
            if len(text) >= _FURNITURE_MIN_CHARS:
                texts.add(text)
    return positions, texts


def _check_pages(layout: ParsedLayout) -> None:
    if not layout.pages:
        raise ManualInvalid("no_pages")
    numbers = [page.page_number for page in layout.pages]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ManualInvalid("pages_out_of_order")
    for page in layout.pages:
        # Every page of the manual prints something. A page the layout model
        # read nothing on was not read.
        if not page.has_text:
            raise ManualInvalid("page_without_text", str(page.page_number))
    last = len(numbers)
    for paragraph in layout.paragraphs:
        if not 1 <= paragraph.page_number <= last:
            raise ManualInvalid("paragraph_outside_pages", str(paragraph.page_number))


def _before_first_definition(paragraph: LayoutParagraph) -> str:
    """What a paragraph holds before its first definition marker, if anything."""
    marker = _DEFINITION.search(paragraph.text)
    return squash(paragraph.text[: marker.start()]) if marker is not None else ""


def _definitions(paragraph: LayoutParagraph) -> Iterator[tuple[str, str]]:
    """Each rule a paragraph defines, with its text: from its marker to the next one."""
    text = paragraph.text
    markers = list(_DEFINITION.finditer(text))
    starts = [marker.start() for marker in markers]
    for marker, end in zip(markers, [*starts[1:], len(text)], strict=False):
        yield marker.group(1), squash(text[marker.start() : end])


def references_in(text: str, defined: Sequence[str]) -> tuple[str, ...]:
    """The rules `text` mentions and does not define, in order of first mention."""
    mentioned = dict.fromkeys(_RULE_ID.findall(text))
    return tuple(rule_id for rule_id in mentioned if rule_id not in defined)


def _rule_code(rule_id: str) -> str:
    """The letters of a rule id: `DM` of `UW-DM-001`."""
    return rule_id.split("-")[1]


def _check_chunks(chunks: Sequence[Chunk]) -> None:
    """What must hold of the chunks of any manual, whatever its rules are."""
    defined = {chunk.rule_id for chunk in chunks}
    code_of: dict[str, str] = {}
    section_of: dict[str, str] = {}
    for chunk in chunks:
        section, code = chunk.section_id.partition(".")[0], _rule_code(chunk.rule_id)
        # A missed heading puts the next section's rules under this one; a
        # line taken for a heading splits a section's rules in two.
        if code_of.setdefault(section, code) != code:
            raise ManualInvalid("section_with_two_rule_codes", chunk.rule_id)
        if section_of.setdefault(code, section) != section:
            raise ManualInvalid("rule_code_in_two_sections", chunk.rule_id)
        for reference in chunk.reference_rule_ids:
            # A rule that is referred to and defined nowhere was lost in the cut.
            if reference not in defined:
                raise ManualInvalid("reference_not_defined", reference)


def _whole_test(layout: ParsedLayout) -> Callable[[str], bool]:
    """How to tell whether a definition of this manual is whole, learnt from the manual.

    A definition ends where a sentence ends. And where any definition of the
    manual has the labelled parts, every definition has them: one without is
    a piece of a definition, whatever it ends with.
    """
    labelled = any(
        _LABELLED_PARTS.search(text) is not None
        for paragraph in layout.paragraphs
        for _, text in _definitions(paragraph)
    )

    def whole(text: str) -> bool:
        return _SENTENCE_END.search(text) is not None and (
            not labelled or _LABELLED_PARTS.search(text) is not None
        )

    return whole


def _whole_paragraphs(
    layout: ParsedLayout, furniture: set[int], roles: bool
) -> Iterator[LayoutParagraph]:
    """The body's paragraphs in order, a definition the layout model split made one again.

    The real service ends some definitions early (after `Source of the
    threshold:`, or before `Probable rating:`) and gives the rest as the
    next paragraph, on the same page or after the page's furniture. A
    paragraph whose last definition is not whole is joined with the body
    paragraphs after it until it is. Raises `ManualInvalid` when a heading,
    the next definition or the manual's end comes first: the rest is lost.
    """
    whole = _whole_test(layout)
    unfinished: LayoutParagraph | None = None
    rule_id = ""
    for position, paragraph in enumerate(layout.paragraphs):
        if position in furniture:
            continue
        if unfinished is not None:
            if (
                _DEFINITION.search(paragraph.text) is not None
                or paragraph.role in HEADING_ROLES
                or (not roles and _is_heading(squash(paragraph.text)))
            ):
                raise ManualInvalid("definition_cut_short", rule_id)
            # It stays where it starts: its page and role are the first piece's.
            paragraph = replace(unfinished, text=f"{unfinished.text} {paragraph.text}")
            unfinished = None
        defined = list(_definitions(paragraph))
        if defined and not whole(defined[-1][1]):
            unfinished, rule_id = paragraph, defined[-1][0]
            continue
        yield paragraph
    if unfinished is not None:
        raise ManualInvalid("definition_cut_short", rule_id)


def _body(
    layout: ParsedLayout, furniture: set[int]
) -> Iterator[tuple[LayoutParagraph, list[tuple[str, str]], _Place]]:
    """Walk the manual's body: each paragraph that is no furniture, the rules it defines, and where it stands.

    The place is the walk's own and moves on with it: a caller reads what
    it needs of it before asking for the next paragraph. Raises
    `ManualInvalid` for a heading that cannot be one of this manual.
    """
    # With roles, a numbered line is a heading only where the layout model
    # says so as well; a layout without any heading role has only the numbers.
    roles = any(paragraph.role in HEADING_ROLES for paragraph in layout.paragraphs)
    place = _Place()
    defined_any = False
    for paragraph in _whole_paragraphs(layout, furniture, roles):
        defined = list(_definitions(paragraph))
        if defined:
            # A heading the layout model joined to the definition below it.
            place.take_heading(_before_first_definition(paragraph), defined_any)
        elif not roles or paragraph.role in HEADING_ROLES:
            place.take_heading(squash(paragraph.text), defined_any)
        yield paragraph, defined, place
        defined_any = defined_any or bool(defined)


def cut_chunks(layout: ParsedLayout) -> list[Chunk]:
    """The `smart` chunks of the manual, one per rule it defines, in the manual's order.

    Raises `ManualInvalid` when a page has no text; a heading's number is
    taken, skipped or of another section; a rule is defined twice; a
    definition has no text, is cut short, stands outside every numbered
    section or in a header or footer; a chunk holds page furniture; the
    rules of a section do not share one id code; a rule that is referred to
    is not defined; or the manual defines no rule at all.
    """
    _check_pages(layout)
    furniture, furniture_texts = _furniture(layout)
    whole = _whole_test(layout)
    chunks: dict[str, Chunk] = {}
    for paragraph, defined, place in _body(layout, furniture):
        for rule_id, text in defined:
            if rule_id in chunks:
                raise ManualInvalid("rule_defined_twice", rule_id)
            if _DEFINITION.sub("", text, count=1).strip() == "":
                raise ManualInvalid("definition_without_text", rule_id)
            if not whole(text):
                # The paragraph was cut before the definition's end.
                raise ManualInvalid("definition_cut_short", rule_id)
            if place.section_number is None:
                raise ManualInvalid("definition_outside_section", rule_id)
            if any(line in text for line in furniture_texts):
                # Story 2.1's review: a header or footer inside a rule's text
                # would be embedded and cited with it.
                raise ManualInvalid("page_furniture_in_chunk", rule_id)
            chunks[rule_id] = Chunk(
                chunk_id=chunk_id_for(ChunkSet.SMART, rule_id),
                chunk_set=ChunkSet.SMART,
                rule_ids=(rule_id,),
                text=text,
                reference_rule_ids=references_in(text, [rule_id]),
                section_id=place.part_id or str(place.section_number),
                section_title=place.part_title,
                impairment=place.section_title,
                manual_page=paragraph.page_number,
            )
    if not chunks:
        raise ManualInvalid("no_rules")
    _check_chunks(list(chunks.values()))
    return list(chunks.values())


@dataclass(frozen=True, slots=True)
class _Word:
    """One word of the body text, and where it is printed."""

    text: str
    # The position of its paragraph among the body's paragraphs.
    paragraph: int
    section_id: str
    section_title: str
    impairment: str
    page_number: int
    # How many sections numbered 1 the walk had taken when it read the word.
    first_sections: int


def _body_words(
    layout: ParsedLayout, furniture: set[int], furniture_texts: set[str]
) -> tuple[list[_Word], list[str]]:
    """The body text as words in reading order, and the rules it defines, in order."""
    read: list[_Word] = []
    defined: dict[str, None] = {}
    for number, (paragraph, definitions, place) in enumerate(_body(layout, furniture)):
        for rule_id, _ in definitions:
            if rule_id in defined:
                raise ManualInvalid("rule_defined_twice", rule_id)
            defined[rule_id] = None
        text = squash(paragraph.text)
        # Looked for inside one paragraph, never across two; a heading may
        # print the words of a running header and is not held to it.
        if not _is_heading(text) and any(line in text for line in furniture_texts):
            raise ManualInvalid("page_furniture_in_chunk", str(paragraph.page_number))
        read.extend(
            _Word(
                word,
                number,
                place.part_id or str(place.section_number),
                place.part_title,
                place.section_title,
                paragraph.page_number,
                place.first_sections,
            )
            for word in text.split()
        )
    # Everything before the first section's own heading is front matter:
    # without roles the walk takes the contents page's lines for headings,
    # and counts from 1 again when the first section begins.
    last = read[-1].first_sections if read else 0
    words = [
        word
        if last and word.first_sections == last
        else replace(
            word,
            section_id=FRONT_MATTER_SECTION_ID,
            section_title="",
            impairment=FRONT_MATTER,
        )
        for word in read
    ]
    return words, list(defined)


def _joined(run: Sequence[_Word]) -> str:
    """The words of a run as text: a space inside a paragraph, a line break between two."""
    parts: list[str] = []
    for before, word in zip([None, *run], run, strict=False):
        if before is not None:
            parts.append(" " if before.paragraph == word.paragraph else PARAGRAPH_BREAK)
        parts.append(word.text)
    return "".join(parts)


def definition_in(text: str, rule_id: str) -> str | None:
    """A rule's own definition inside a text that may hold more: from its marker to the end of its paragraph.

    It ends earlier where the next rule's marker stands in the same
    paragraph. None when the text does not define the rule.
    """
    for marker in _DEFINITION.finditer(text):
        if marker.group(1) != rule_id:
            continue
        rest = text[marker.end() :].partition(PARAGRAPH_BREAK)[0]
        following = _DEFINITION.search(rest)
        if following is not None:
            rest = rest[: following.start()]
        return text[marker.start() : marker.end()] + rest
    return None


def cut_fixed_chunks(
    layout: ParsedLayout, size_words: int, overlap_words: int
) -> list[Chunk]:
    """The `fixed` chunks of the manual: its body text in runs of `size_words` words.

    Each chunk begins `size_words - overlap_words` words after the one
    before it, so the last `overlap_words` words of a chunk are the first of
    the next. Page furniture is left out, as in the `smart` cut. In a
    chunk's text the words of a paragraph are a space apart and two
    paragraphs a line break. A chunk defines the rules whose definition
    marker lies inside its text and refers to the other rules it mentions;
    its section, impairment and page are those of its first word, and text
    before the first section's heading is front matter. A rule's definition
    is not kept whole: one that a cut falls in is in two chunks, part in
    each. That is the baseline's weakness and is left as it is.

    Raises `ManualInvalid` as the walk of the `smart` cut does for pages,
    headings and a definition the layout model split and the walk could not
    make whole, and when a rule is defined twice; the manual defines no rule;
    a paragraph holds page furniture; a rule that is referred to is not
    defined; a chunk's text reads as defining a rule no paragraph defines (a
    marker formed across two paragraphs); a rule the body defines has its
    marker in no chunk (an overlap too small to keep a marker that a cut
    falls in); or there are more chunks than the ids can number.
    """
    if size_words < 1 or not 0 <= overlap_words < size_words:
        raise ValueError("the overlap must be smaller than the chunk size")
    _check_pages(layout)
    furniture, furniture_texts = _furniture(layout)
    words, defined = _body_words(layout, furniture, furniture_texts)
    if not defined:
        raise ManualInvalid("no_rules")
    chunks: list[Chunk] = []
    step = size_words - overlap_words
    for start in range(0, len(words), step):
        if len(chunks) == MAX_FIXED_CHUNKS:
            raise ManualInvalid("too_many_chunks", str(MAX_FIXED_CHUNKS))
        run = words[start : start + size_words]
        first = run[0]
        text = _joined(run)
        rule_ids = tuple(dict.fromkeys(_DEFINITION.findall(text)))
        for rule_id in rule_ids:
            if rule_id not in defined:
                raise ManualInvalid("definition_across_paragraphs", rule_id)
        chunks.append(
            Chunk(
                chunk_id=fixed_chunk_id(len(chunks) + 1),
                chunk_set=ChunkSet.FIXED,
                rule_ids=rule_ids,
                text=text,
                reference_rule_ids=references_in(text, rule_ids),
                section_id=first.section_id,
                section_title=first.section_title,
                impairment=first.impairment,
                manual_page=first.page_number,
            )
        )
        if start + size_words >= len(words):
            # The run reached the end: a further one would only repeat its tail.
            break
    in_a_chunk = {rule_id for chunk in chunks for rule_id in chunk.rule_ids}
    for rule_id in defined:
        if rule_id not in in_a_chunk:
            raise ManualInvalid("definition_in_no_chunk", rule_id)
    for chunk in chunks:
        for reference in chunk.reference_rule_ids:
            if reference not in in_a_chunk:
                raise ManualInvalid("reference_not_defined", reference)
    return chunks
