"""Cut the parsed manual into `smart` chunks: exactly one rule each (spine AD-12).

The chunker works from what the layout model read, never from what the
generator of the manual knows. It finds a definition by the contracts'
marker (`Rule <rule_id>:`), a section by the number printed with its
heading, and page furniture by the layout model's roles or by its repeating
on most pages. So it learns the rules from the manual alone, and the layout
stand-in can be replaced by the real service.

It checks its own result and fails loudly: a partial index, or one whose
rules stand under the wrong section, is worse than none. The checks need
nothing but the manual: headings are numbered in order and none twice, the
rules of one section share one id code, every rule that is referred to is
defined, and a definition ends where a sentence ends.
"""

import re
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

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
        if by_role or (not content[position] and _DIGITS.sub("#", text) in repeating):
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
    # With roles, a numbered line is a heading only where the layout model
    # says so as well; a layout without any heading role has only the numbers.
    roles = any(paragraph.role in HEADING_ROLES for paragraph in layout.paragraphs)
    place = _Place()
    chunks: dict[str, Chunk] = {}
    for position, paragraph in enumerate(layout.paragraphs):
        if position in furniture:
            continue
        defined = list(_definitions(paragraph))
        if not defined:
            if not roles or paragraph.role in HEADING_ROLES:
                place.take_heading(squash(paragraph.text), bool(chunks))
            continue
        # A heading the layout model joined to the definition below it.
        place.take_heading(_before_first_definition(paragraph), bool(chunks))
        for rule_id, text in defined:
            if rule_id in chunks:
                raise ManualInvalid("rule_defined_twice", rule_id)
            if _DEFINITION.sub("", text, count=1).strip() == "":
                raise ManualInvalid("definition_without_text", rule_id)
            if _SENTENCE_END.search(text) is None:
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
                rule_id=rule_id,
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
