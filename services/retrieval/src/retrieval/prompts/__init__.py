"""The prompts of `retrieval`, kept under version control (spine AD-16).

`chunk_context.md` is the system message of the one call the ingestion job
makes to the shared chat deployment per chunk: it asks for one line that says
where a rule sits in the manual. The line is stored with the chunk and is
embedded in front of the chunk's text (AD-12), so that a rule's vector also
carries its place: the impairment and the part of the section.

`rerank.md` is the system message of the one call a search with row `r4`
makes to the same chat deployment: it asks how relevant each of the fused
candidates is to the query, as a number from 0 to 1 (AD-11). Its evaluation
set is the same scoreboard: row `r4`'s recall and accuracy beside row
`r3`'s, which the bake-off runner measures on the same cases.

`azure.md` rule 27 asks for a scenario evaluation set beside each prompt, to
be run before the prompt or the model changes. For this prompt that set is
the retrieval scoreboard of Epic 3 (story 3.5): recall of the expected rule
for a fixed query per rule of the manual. Until then a change is checked by
the test that searches the ingested manual for each rule by name (story
2.3). A changed prompt changes its digest, and the next run of the job then
writes every context line and vector again.
"""

import hashlib
from functools import lru_cache
from importlib import resources

CHUNK_CONTEXT = "chunk_context.md"
RERANK = "rerank.md"


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    """The text of one prompt file shipped with the package."""
    return resources.files(__name__).joinpath(name).read_text(encoding="utf-8").strip()


def prompt_digest(name: str) -> str:
    """A hash of one prompt's text: what a chunk's context line was asked for with."""
    return hashlib.sha256(load_prompt(name).encode()).hexdigest()
