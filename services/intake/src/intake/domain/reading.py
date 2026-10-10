"""A page's stored text and word boxes, from the read model's answer (AD-14, AD-21).

The redaction service writes every page of the redacted PDF as one picture
(seen in the Azure session of 2026-10-10), so there is no text in that file
to store but the labels of its masks. The page is therefore read once more,
by Document Intelligence's read model, and what that reading says is put
together here with what the file itself says of its masks:

- the words, their order and their places come from the reading;
- where a mask is, and which entity it stands for, comes from the file's own
  text layer, which holds exactly that: a label such as `PER` and a number.
  The reading is not trusted with it: it gives `PER 1`, `PER1`, `PER®` or
  `SSN3` for the same kind of mark;
- what the label means comes from the redaction's result file.

Each mask becomes one token in square brackets, such as `[Person]`, in the
shape of the contracts' `MASK_TOKEN_PATTERN`, with one box: the mask's own.
"""

import math
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from contracts.text import MASK_TOKEN_PATTERN
from intake.domain.entities import (
    LayerWord,
    MaskNames,
    PageReading,
    PageSheet,
    ReadPage,
    Word,
)
from intake.domain.ports import RedactionJobError

_TOKEN = re.compile(MASK_TOKEN_PATTERN)
# The token of a mask whose category and label are both no usable name.
UNNAMED_MASK = "[Masked]"
# A read word lies on a mask when at least this share of its box does.
_ON_A_MASK = 0.2
# A read word that does not hold the mask's label is all mask from this share on.
_ALL_MASK = 0.5
# A read word's text beside a mask is kept when the word runs at least this
# far (PDF points) past the mask on that side.
_PAST = 1.5
# The read may have lost the end of a label under what the mask was drawn
# over; this many of its first letters still tell it.
_LABEL_START = 2


@dataclass(frozen=True, slots=True)
class _Box:
    """PDF points, origin at the top-left corner of the page as it is shown."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def area(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    def shared(self, other: "_Box") -> float:
        """The area both boxes cover."""
        width = min(self.x1, other.x1) - max(self.x0, other.x0)
        height = min(self.y1, other.y1) - max(self.y0, other.y0)
        return width * height if width > 0 and height > 0 else 0.0

    def joined(self, other: "_Box") -> "_Box":
        return _Box(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def grown(self, by: float) -> "_Box":
        return _Box(self.x0 - by, self.y0 - by, self.x1 + by, self.y1 + by)

    def along(self, horizontal: bool) -> tuple[float, float]:
        """Where the box begins and ends along the reading axis."""
        return (self.x0, self.x1) if horizontal else (self.y0, self.y1)

    def cut(self, horizontal: bool, start: float, end: float) -> "_Box":
        """The part of the box between two places on the reading axis."""
        if horizontal:
            return _Box(start, self.y0, max(start, end), self.y1)
        return _Box(self.x0, start, self.x1, max(start, end))


@dataclass(slots=True)
class _Mask:
    """One mask on a page: its label, the number it carries, and where it is."""

    label: str
    entity_id: str | None
    box: _Box
    # Whether its token is in the page text yet: a mask is written once.
    written: bool = False


def _is_number(text: str) -> bool:
    return text.isascii() and text.isdigit()


def mask_places(
    layer_words: Sequence[LayerWord], labels: Collection[str]
) -> list[_Mask]:
    """The masks of a page, from the words of the redacted PDF's own text layer.

    A mask is a label the result file knows, followed by a number: as the
    next word, where the service raises it beside the label, or joined to
    it. Any other word of the layer is not a mask and is left alone.
    """
    places: list[_Mask] = []
    longest_first = sorted(labels, key=len, reverse=True)
    for word in layer_words:
        box = _Box(word.x0, word.y0, word.x1, word.y1)
        if word.text in labels:
            places.append(_Mask(word.text, None, box))
        elif _is_number(word.text):
            last = places[-1] if places else None
            if last is None or last.entity_id is not None:
                continue
            # Beside its label: within a line's height of it.
            reach = min(last.box.x1 - last.box.x0, last.box.y1 - last.box.y0)
            if last.box.grown(reach).shared(box) > 0:
                last.entity_id = word.text
                last.box = last.box.joined(box)
        else:
            for label in longest_first:
                number = word.text.removeprefix(label)
                if number != word.text and _is_number(number):
                    places.append(_Mask(label, number, box))
                    break
    return places


def _token(mask: _Mask, names: MaskNames) -> str:
    """The mask's token: its category in brackets, in the contracts' shape."""
    for name in (
        names.category_of_entity.get((mask.label, mask.entity_id or "")),
        names.category_of_label.get(mask.label),
        mask.label,
    ):
        if name is not None and _TOKEN.fullmatch(f"[{name}]") is not None:
            return f"[{name}]"
    return UNNAMED_MASK


def _around_label(content: str, label: str) -> tuple[str, str] | None:
    """What a read word holds before and after a mask's label; None if it holds none.

    The label's first letters at the word's start count as the label: the
    read loses the rest where the mask was drawn over other text.
    """
    at = content.find(label)
    if at >= 0:
        return content[:at], content[at + len(label) :]
    for size in range(len(label) - 1, _LABEL_START - 1, -1):
        if content.startswith(label[:size]):
            return "", content[size:]
    return None


def _reading_axis(angle: float) -> tuple[bool, bool]:
    """Whether a page's text runs along x, and whether towards larger values.

    The read model gives the turn of a page's content clockwise in degrees:
    text on a page turned by 90 runs down the sheet, by 180 from right to left.
    """
    across, down = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    horizontal = abs(across) >= abs(down)
    return horizontal, (across if horizontal else down) >= 0


def _pieces(
    content: str,
    box: _Box,
    masks: Sequence[_Mask],
    names: MaskNames,
    axis: tuple[bool, bool],
) -> list[tuple[str, _Box]]:
    """One read word as what is stored for it: itself, a mask's token, or both.

    A word that lies on a mask is the read's version of the mask's label
    and number, and is replaced by the mask's token. Where the read joined
    the label to the text beside it (`DrPER®` for `Dr` and a mask), the text
    that runs past the mask is kept as a word of its own.
    """
    mask = max(masks, key=lambda place: place.box.shared(box), default=None)
    if mask is None or box.area <= 0:
        return [(content, box)]
    share = mask.box.shared(box) / box.area
    if share < _ON_A_MASK:
        return [(content, box)]
    around = _around_label(content, mask.label)
    if around is None and share < _ALL_MASK:
        return [(content, box)]
    pieces: list[tuple[str, _Box]] = []
    token = [] if mask.written else [(_token(mask, names), mask.box)]
    mask.written = True
    if around is None:
        return token
    horizontal, forward = axis
    start, end = box.along(horizontal)
    mask_start, mask_end = mask.box.along(horizontal)
    before, after = around
    # What follows the label up to the first letter is its number, as read.
    after = after[
        next(
            (at for at, character in enumerate(after) if character.isalpha()),
            len(after),
        ) :
    ]
    # Each side of the mask: where the word's part there begins and ends, and
    # how far the word runs past the mask on that side.
    low = (start, min(mask_start, end), mask_start - start)
    high = (max(mask_end, start), end, end - mask_end)
    first, last = (low, high) if forward else (high, low)
    if before and first[2] >= _PAST:
        pieces.append((before, box.cut(horizontal, first[0], first[1])))
    pieces.extend(token)
    if after and last[2] >= _PAST:
        pieces.append((after, box.cut(horizontal, last[0], last[1])))
    return pieces


def page_reading(read: ReadPage, sheet: PageSheet, names: MaskNames) -> PageReading:
    """Build a page's stored text and words from the read model's reading of it.

    Lines are taken in the order the read gives them, which is reading order
    whatever way the page is turned; the words of a line are joined by a
    space and lines by a line break, and each word keeps the offsets of its
    characters in that text. Boxes are turned from the read's unit into PDF
    points of the page as it is shown: the bounds of each word's corners, so
    a word on a turned page has the box it has on the sheet.
    """
    if read.width <= 0 or read.height <= 0 or sheet.width <= 0 or sheet.height <= 0:
        raise RedactionJobError("read_page_size")
    across, down = sheet.width / read.width, sheet.height / read.height
    masks = mask_places(sheet.layer_words, names.labels)
    axis = _reading_axis(read.angle)
    parts: list[str] = []
    words: list[Word] = []
    length = 0
    for line in read.lines:
        first_of_line = True
        for word in line:
            content = "".join(word.content.split())
            if not content:
                continue
            box = _Box(
                _clamp(word.x0 * across, sheet.width),
                _clamp(word.y0 * down, sheet.height),
                _clamp(word.x1 * across, sheet.width),
                _clamp(word.y1 * down, sheet.height),
            )
            for text, place in _pieces(content, box, masks, names, axis):
                if parts:
                    separator = "\n" if first_of_line else " "
                    parts.append(separator)
                    length += len(separator)
                first_of_line = False
                words.append(
                    Word(
                        char_start=length,
                        char_end=length + len(text),
                        x0=_clamp(place.x0, sheet.width),
                        y0=_clamp(place.y0, sheet.height),
                        x1=_clamp(place.x1, sheet.width),
                        y1=_clamp(place.y1, sheet.height),
                    )
                )
                parts.append(text)
                length += len(text)
    written = sum(mask.written for mask in masks)
    return PageReading(
        text="".join(parts),
        words=tuple(words),
        masks=written,
        masks_unread=len(masks) - written,
    )


def _clamp(value: float, limit: float) -> float:
    return min(max(value, 0.0), limit)
