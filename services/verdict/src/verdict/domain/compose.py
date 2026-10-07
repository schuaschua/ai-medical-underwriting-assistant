"""Row `r6`: the run makes its own searches, and the model only composes (AD-15, AD-11).

On the other rows the agent decides what to search for and what to read, in
a loop of tool calls. On `r6` retrieval is agentic already: the search
service plans and runs queries of its own for every request. A model that
could search again on top of that would be a second agent, and the row
would measure both. So here the loop is off. Code lists the facts and makes
one search per fact, with the query the contracts' query builder makes from
the fact's statement, and the model is then asked once, without any tool,
to compose its proposal from the facts and the rules those searches
returned.

Every step still goes through the run's `Toolbox`: the arguments are
checked, the step limit holds, and each call is one row of the step log.
What the model proposes is decided on by the same code as on every row
(`decide.py`).
"""

import json

from contracts.enums import ToolName
from contracts.errors import ErrorCode
from contracts.query import build_fact_query
from verdict.domain.toolbox import Toolbox

# The two parts of what the model is given, as its one user message.
FACTS_FIELD = "facts"
SEARCHES_FIELD = "searches"


async def searched_material(toolbox: Toolbox) -> str:
    """List the case's facts, search the manual once per fact, and answer all of it as one JSON object.

    The object holds `facts`, as `list_facts` answers them, and `searches`:
    for each fact in order its `fact_id` and the `rules` its search
    returned, as `search_rules` answers them.

    A case with more facts than the run has steps left once they are
    listed is stopped there, before the first search is paid for:
    `StepLimitReached`, and the case is referred as at the step limit.
    `ToolFailed` when a search could not be made, as `Toolbox.call` raises
    it, and also when the toolbox refused one (`stage_failed`): nothing
    may be composed as if the manual held no rule for a fact that was
    never searched for.
    """
    listed = await toolbox.call(ToolName.LIST_FACTS.value, {})
    if len(toolbox.facts) > toolbox.steps_left:
        await toolbox.stop_at_the_limit(ToolName.SEARCH_RULES)
    searches: list[dict[str, object]] = []
    for fact in toolbox.facts:
        found = await toolbox.call(
            ToolName.SEARCH_RULES.value,
            {
                # AD-17: the one query builder, so that the row is given
                # the same words the bake-off's recall search gives it.
                "query": build_fact_query(fact.statement),
                "fact_id": fact.fact_id,
            },
        )
        if "rules" not in found:
            # Refused, and logged as refused: a search that was not made.
            raise toolbox.failed(ErrorCode.STAGE_FAILED)
        searches.append({"fact_id": fact.fact_id, "rules": found["rules"]})
    return json.dumps({FACTS_FIELD: listed[FACTS_FIELD], SEARCHES_FIELD: searches})
