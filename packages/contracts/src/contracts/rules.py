"""The `rule_id` patterns (AD-12) and the `page_type` to medical mapping (AD-13)."""

import re
from typing import Annotated

from pydantic import StringConstraints

from contracts.enums import PageType

# AD-12: the id printed in the manual.
RULE_ID_PATTERN = r"UW-[A-Z]{2,4}-[0-9]{3}"
# AD-12: the manual prints each definition as `Rule <rule_id>:`.
RULE_DEFINITION_PATTERN = rf"\bRule\s+({RULE_ID_PATTERN}):"

_RULE_ID_RE = re.compile(RULE_ID_PATTERN)
_RULE_DEFINITION_RE = re.compile(RULE_DEFINITION_PATTERN)

RuleId = Annotated[str, StringConstraints(pattern=rf"^{RULE_ID_PATTERN}$")]

# AD-13: the one mapping from page type to medical or not.
MEDICAL_PAGE_TYPES: frozenset[PageType] = frozenset(
    {
        PageType.LAB_REPORT,
        PageType.ATTENDING_PHYSICIAN_STATEMENT,
        PageType.APPLICATION_FORM,
    }
)


def is_rule_id(value: str) -> bool:
    """Tell whether `value` is exactly one well-formed `rule_id`."""
    return _RULE_ID_RE.fullmatch(value) is not None


def rule_ids_defined_in(text: str) -> list[str]:
    """Return the rules `text` defines, in order of first appearance.

    A rule that is only mentioned is not returned (AD-12).
    """
    return list(dict.fromkeys(_RULE_DEFINITION_RE.findall(text)))


def is_medical(page_type: PageType | str) -> bool:
    """Tell whether a page type counts as medical; an unknown type raises `ValueError`."""
    return PageType(page_type) in MEDICAL_PAGE_TYPES
