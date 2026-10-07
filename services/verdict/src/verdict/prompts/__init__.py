"""The prompts of `verdict`, kept under version control (spine AD-16).

`suggest_verdict.md` is the instructions of the verdict agent: how to use
its three tools and what to answer. It decides nothing that is stored. The
answer is parsed into the contracts model `VerdictOutput`, and from there
domain code alone sets the verdict, the loading and every system reason, and
keeps only the reasons whose rule and facts the run saw and whose effect the
rule's text bears out (AD-15; `domain/decide.py`). Whatever the prompt says,
or a fact or a rule's text says to the model, cannot change that.

`compose_verdict.md` is the instructions for row `r6`, where the model has
no tool: it is given the facts and the rules the run's own searches
returned, and composes the same answer from them. It decides as little:
the same domain code checks its answer. Its evaluation set is the same
cases too, run with row `r6`: the cross-service tests in `packages/` and
the bake-off runner's whole-path test run it against the model stand-in
today, and the deployed bake-off run scores it against the real model and
the real search service. Until that run a change to it, or to the model, is
checked by hand in the final Azure test session, like the other prompt.

`azure.md` rule 27 asks for a scenario evaluation set beside each prompt,
that checks tool-call accuracy, to be run before the prompt or the model
changes. For this prompt that set is the synthetic cases with the rules each
of them should meet: the cross-service tests beside the model stand-in, in
`packages/`, run them against that stand-in today, and the eval runner of
story 3.4 scores them against the real model (verdict accuracy, and the step
log for the tool calls). Until then a change to the prompt is checked by hand in the
final Azure test session; the unit tests use an agent stub and say nothing
about the model's answers.
"""

from functools import lru_cache
from importlib import resources

SUGGEST_VERDICT = "suggest_verdict.md"
COMPOSE_VERDICT = "compose_verdict.md"


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    """The text of one prompt file shipped with the package."""
    return resources.files(__name__).joinpath(name).read_text(encoding="utf-8").strip()
