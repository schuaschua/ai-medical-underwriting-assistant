"""Story 2.2: the HTTP face of `retrieval`: its probes, and how every error leaves it.

The search and the rule read (story 2.3) are tested in `test_retrieval_search.py`.
"""

from retrieval.adapters.http.routes import Dependencies, build_router

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


def test_story_2_2_the_service_has_its_probes_and_the_two_reads_and_no_ingestion_route(
    dependencies: Dependencies,
) -> None:
    paths = {
        getattr(route, "path", None) for route in build_router(dependencies).routes
    }

    # The search and the rule read came with story 2.3; the ingestion is no route.
    assert paths == {"/health", "/ready", "/searches", "/rules/{rule_id}"}
