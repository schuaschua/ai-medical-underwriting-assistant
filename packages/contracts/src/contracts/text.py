"""Text normalisation shared by the quote check and the redaction check (AD-14, AD-17)."""

import unicodedata

# Characters a PDF text layer adds that a reader never sees: the soft hyphen,
# the zero-width space, non-joiner and joiner, and the byte order mark.
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\ufeff"))


def normalise(text: str) -> str:
    """Return `text` in one comparable form: case, spacing and PDF artefacts ignored.

    Compatibility forms are folded (NFKC, so the ligature "\ufb01" becomes "fi"),
    invisible characters are removed, case is folded and every run of white
    space becomes one space. Punctuation is kept, so a mask token such as
    `[Person]` stays one token (`[person]`).
    """
    visible = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    return " ".join(visible.casefold().split())
