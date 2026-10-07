"""The prompts of `classification`, kept under version control (spine AD-16).

`classify_page.md` is the system message of every run of the LLM contender:
it asks the shared chat deployment for the page type of one page and a
one-line reason. The answer is parsed into the contracts model
`ClassifierOutput`; whether a page is medical is never asked of the model,
it follows from the page type (AD-13).

`azure.md` rule 27 asks for a scenario evaluation set beside each prompt, to
be run before the prompt or the model changes. For this prompt that set is
the labelled page set of the classifier bake-off in Epic 4: story 4.1 builds
the scored pages with their expected page types, and story 4.3 runs the
classifier contenders over them and reports accuracy, calibration and queue
rate. Until then a change to the prompt is checked by hand against the pages
in `data/cases/`; the unit tests use a gateway stub and say nothing about the
model's answers.
"""

from functools import lru_cache
from importlib import resources

CLASSIFY_PAGE = "classify_page.md"


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    """The text of one prompt file shipped with the package."""
    return resources.files(__name__).joinpath(name).read_text(encoding="utf-8").strip()
