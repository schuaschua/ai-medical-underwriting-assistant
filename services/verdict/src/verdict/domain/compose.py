"""Row `r6`: the run makes its own searches, and the model only composes (AD-15, AD-11).

On the other rows the agent decides what to search for and what to read, in
a loop of tool calls. On `r6` retrieval is agentic already: the search
service plans and runs queries of its own for every request. A model that
could search again on top of that would be a second agent, and the row
would measure both. So here the loop is off. Code lists the facts and makes
one search per fact, with the query the contracts' query builder makes from
the fact's statement (facts that state the same thing share one search),
and the model is then asked once, without any tool,
to compose its proposal from the facts and the rules those searches
returned.

Every step still goes through the run's `Toolbox`: the arguments are
checked and each call is one row of the step log. The model's step limit
does not bound them: it exists to stop a model that keeps calling tools,
and here no model calls any. The run has a limit of its own on how many
searches it makes.
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
    """List the case's facts, search the manual once per distinct statement, and answer all of it as one JSON object.

    The object holds `facts`, as `list_facts` answers them, and `searches`:
    for each fact in order its `fact_id` and the `rules` the search for
    its statement returned, as `search_rules` answers them.

    AD-15 says one retrieval request per fact. Identical requests are made
    once: extraction stores the same statement for every page it stands
    on, and the query builder makes the same query of each, so a second
    request would pay the search service's planning again for the same
    answer. Every fact with that statement is given the rules of the one
    search; the step in the log names the first of them.

    These searches are made by code, their number is known once the facts
    are listed, and no model can add to it. So the bound on them is the
    run's own limit on searches (the toolbox of this row is given it, with
    one step for the listing), not the limit that stops a model's loop. A
    case with more distinct statements than that is stopped there, before
    the first search is paid for: `StepLimitReached`, and the case is
    referred as at the step limit. `ToolFailed` when a search could not be
    made, as `Toolbox.call` raises it, and also when the toolbox refused
    one (`stage_failed`): nothing may be composed as if the manual held no
    rule for a fact that was never searched for.
    """
    listed = await toolbox.call(ToolName.LIST_FACTS.value, {})
    # The first fact of each distinct query, in the facts' order.
    first: dict[str, str] = {}
    for fact in toolbox.facts:
        # AD-17: the one query builder, so that the row is given the same
        # words the bake-off's recall search gives it.
        first.setdefault(build_fact_query(fact.statement), fact.fact_id)
    if len(first) > toolbox.steps_left:
        await toolbox.stop_at_the_limit(ToolName.SEARCH_RULES)
    rules: dict[str, object] = {}
    for query, fact_id in first.items():
        found = await toolbox.call(
            ToolName.SEARCH_RULES.value, {"query": query, "fact_id": fact_id}
        )
        if "rules" not in found:
            # Refused, and logged as refused: a search that was not made.
            raise toolbox.failed(ErrorCode.STAGE_FAILED)
        rules[query] = found["rules"]
    searches = [
        {"fact_id": fact.fact_id, "rules": rules[build_fact_query(fact.statement)]}
        for fact in toolbox.facts
    ]
    return json.dumps({FACTS_FIELD: listed[FACTS_FIELD], SEARCHES_FIELD: searches})
