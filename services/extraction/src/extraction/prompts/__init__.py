"""The prompts of `extraction`, kept under version control (spine AD-16).

`extract_facts.md` is the system message of the one model call a page gets:
it asks the shared chat deployment for the medical facts of the page, each
with a one-line statement and a verbatim quote. The answer is parsed into the
contracts model `ExtractionOutput`. The model is never asked whether a quote
is on the page, where it is, which page it read or what a fact's id is: code
sets all of that (AD-14).

`azure.md` rule 27 asks for a scenario evaluation set beside each prompt, to
be run before the prompt or the model changes. For this prompt that set is
the expected facts of the synthetic cases, which story 3.1 records and the
eval runner of story 3.4 scores. Until then a change to the
prompt is checked by hand against the pages in `data/cases/` in the final
Azure test session (how many quotes come back verbatim); the unit tests use a
gateway stub and say nothing about the model's answers.
"""

from functools import lru_cache
from importlib import resources

EXTRACT_FACTS = "extract_facts.md"


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    """The text of one prompt file shipped with the package."""
    return resources.files(__name__).joinpath(name).read_text(encoding="utf-8").strip()
