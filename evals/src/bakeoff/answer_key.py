"""The answer key, as the runner reads it (spine AD-17).

`data/answer-key/cases/` holds one entry per synthetic case, written by the
generator (`packages/synthdata`; the fields are described in
`data/README.md`). This module reads the fields the runner scores with and
nothing else. No service reads these files, and the runner never sends
anything of them to `web` but the fixed query of a fact.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from contracts.base import NonEmptyStr, OneLine, PageNumber, Percent
from contracts.enums import Verdict
from contracts.rules import RuleId

# The category of a planted identifier that names a person: each part of
# such a name is looked for on its own too.
PERSON_NAME = "person_name"


class _KeyModel(BaseModel):
    # The entry has more fields than the runner scores with; they are left alone.
    model_config = ConfigDict(extra="ignore", frozen=True)


class ExpectedPage(_KeyModel):
    page_number: PageNumber
    is_medical: bool


class PlantedIdentifier(_KeyModel):
    category: NonEmptyStr
    value: OneLine


class FactPlace(_KeyModel):
    """One page that states a fact, with that page's own words for it."""

    page_number: PageNumber
    quote: NonEmptyStr


class ExpectedFact(_KeyModel):
    # One line in the manual's vocabulary: what the query builder is given.
    statement: OneLine
    # The rules this fact meets; most facts meet none.
    rule_ids: tuple[RuleId, ...] = ()
    # Every page that states the fact; the redaction check looks for each quote.
    places: tuple[FactPlace, ...] = ()


class ExpectedVerdict(_KeyModel):
    verdict: Verdict
    # Set when the verdict is `loaded`.
    loading_pct: Percent | None = None


class AnswerKeyEntry(_KeyModel):
    # The synthetic case's key (`case-006`), not a case id of the system.
    case_id: NonEmptyStr
    file_name: NonEmptyStr
    pages: tuple[ExpectedPage, ...] = Field(min_length=1)
    identifiers: tuple[PlantedIdentifier, ...]
    may_also_be_redacted: tuple[OneLine, ...]
    expected_facts: tuple[ExpectedFact, ...]
    expected_verdict: ExpectedVerdict

    @property
    def case_key(self) -> str:
        return self.case_id

    def is_medical(self, page_number: int) -> bool | None:
        """The expected label of a page; None when the key has no such page."""
        for page in self.pages:
            if page.page_number == page_number:
                return page.is_medical
        return None


class PageSetDocument(_KeyModel):
    """One file of the scored page set, with the expected label of each of its pages."""

    # The synthetic case's key (`case-006`): the file is that case's document.
    case_id: NonEmptyStr
    file_name: NonEmptyStr
    pages: tuple[ExpectedPage, ...] = Field(min_length=1)

    @property
    def case_key(self) -> str:
        return self.case_id


class _PageSet(_KeyModel):
    documents: tuple[PageSetDocument, ...] = Field(min_length=1)


def read_page_set(path: Path, cases: list[str] | None = None) -> list[PageSetDocument]:
    """The files of the scored page set, in their order; only the named cases when given."""
    documents = list(
        _PageSet.model_validate_json(path.read_text(encoding="utf-8")).documents
    )
    keys = [document.case_key for document in documents]
    if len(set(keys)) != len(keys):
        raise ValueError("the page set names a file twice")
    for document in documents:
        numbers = [page.page_number for page in document.pages]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"the page set names a page of {document.case_key} twice")
    if cases is None:
        return documents
    unknown = sorted(set(cases) - set(keys))
    if unknown:
        raise ValueError(f"the page set has no case {', '.join(unknown)}")
    return [document for document in documents if document.case_key in set(cases)]


def read_answer_key(
    folder: Path, cases: list[str] | None = None
) -> list[AnswerKeyEntry]:
    """Every entry of the answer key, in case order; only the named cases when given."""
    entries = [
        AnswerKeyEntry.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(folder.glob("*.json"))
    ]
    if cases is None:
        return entries
    by_key = {entry.case_key: entry for entry in entries}
    unknown = sorted(set(cases) - set(by_key))
    if unknown:
        raise ValueError(f"the answer key has no case {', '.join(unknown)}")
    return [by_key[key] for key in sorted(set(cases))]
