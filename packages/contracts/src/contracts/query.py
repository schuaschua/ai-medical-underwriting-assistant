"""The eval query builder (AD-17)."""


def build_fact_query(statement: str) -> str:
    """Return the search query for a fact statement, the same string every time.

    AD-17: every `retriever_config` is scored on this one query per expected
    fact, so the bake-off compares retrievers and not query wording.
    """
    query = " ".join(statement.split())
    if not query:
        raise ValueError("a fact statement must not be blank")
    return query
