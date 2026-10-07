"""Story 1.8: the local stand-in for the Foundry model deployments stays a dev tool.

What the stand-in answers is tested through the services that call it, in the
end-to-end tests beside this file.
"""

import re

from synthdata_stack import REPOSITORY_ROOT

from bakeoff.settings import Settings as RunnerSettings
from contracts.enums import RetrieverConfig
from retrieval.domain.rows import BUILT_ROWS, available_rows
from retrieval.settings import Settings as RetrievalSettings
from synthdata.foundry_standin import (
    Mode,
)
from synthdata.search_standin import Mode as SearchMode
from verdict.domain.run import (
    DEFAULT_AVAILABLE_RETRIEVER_CONFIGS as VERDICT_DEFAULT_RETRIEVER_CONFIGS,
)
from verdict.domain.run import (
    RERANKER_RETRIEVER_CONFIGS,
    RUNNABLE_RETRIEVER_CONFIGS,
    SEARCH_SERVICE_RETRIEVER_CONFIGS,
)
from verdict.settings import Settings as VerdictSettings
from web.settings import Settings as WebSettings
from workflow.domain.cases import DEFAULT_AVAILABLE_RETRIEVER_CONFIGS
from workflow.settings import Settings as WorkflowSettings

# --- The stand-in never ships ------------------------------------------------------------


def test_story_1_8_the_stand_in_is_in_no_service_image_and_no_service_imports_it() -> (
    None
):
    services = sorted(path.name for path in (REPOSITORY_ROOT / "services").iterdir())
    assert "classification" in services
    assert "extraction" in services
    for service in services:
        folder = REPOSITORY_ROOT / "services" / service
        if not (folder / "pyproject.toml").exists():
            continue
        assert "synthdata" not in (folder / "pyproject.toml").read_text()
        assert "synthdata" not in (folder / "Dockerfile").read_text()
        for source in (folder / "src").rglob("*.py"):
            text = source.read_text()
            assert not re.search(r"^\s*(from|import) synthdata", text, re.MULTILINE)
            # Story 1.4's guard: nothing under `services/` reads the answer key.
            assert "answer-key" not in text
    # The mode switch exists only in the stand-in.
    assert [mode.value for mode in Mode] == [
        "ok",
        "disagree",
        "mixed",
        "invalid",
        "throttled",
        # Story 2.4: what the stand-in adds to a page's facts.
        "quote_not_on_page",
        "masked_value",
        # Stories 2.5 and 2.6: one flaw in the verdict agent's conversation.
        "endless_loop",
        "unseen_rule",
        "wrong_effect",
        "low_confidence",
        "invalid_answer",
        # Story 3.7: what goes wrong with the reranker of row `r4`.
        "rerank_incomplete",
        "rerank_slow",
    ]
    # Stories 3.2, 3.3 and 3.7: no service imports another, so each names
    # the ladder rows a case may run with by itself: `retrieval` by its row
    # table and whether it was given a search service and a chat
    # deployment (its reranker), `verdict` and `workflow` by a setting.
    # Held equal here, without the two, with each and with both.
    plain = frozenset({RetrieverConfig.R1, RetrieverConfig.R2, RetrieverConfig.R3})
    with_search = plain | {RetrieverConfig.R5}
    with_reranker = plain | {RetrieverConfig.R4}
    everywhere = with_search | with_reranker
    assert available_rows(search_service=False, reranker=False) == plain
    assert DEFAULT_AVAILABLE_RETRIEVER_CONFIGS == plain
    assert VERDICT_DEFAULT_RETRIEVER_CONFIGS == plain
    assert frozenset(WorkflowSettings().available_retriever_configs) == plain
    assert frozenset(VerdictSettings().available_retriever_configs) == plain
    assert RetrievalSettings().search_service_endpoint is None
    assert RetrievalSettings().chat_deployment is None
    assert available_rows(search_service=True, reranker=False) == with_search
    assert available_rows(search_service=False, reranker=True) == with_reranker
    assert available_rows(search_service=True, reranker=True) == everywhere
    assert RUNNABLE_RETRIEVER_CONFIGS == everywhere == BUILT_ROWS
    assert RUNNABLE_RETRIEVER_CONFIGS - SEARCH_SERVICE_RETRIEVER_CONFIGS == (
        with_reranker
    )
    assert RUNNABLE_RETRIEVER_CONFIGS - RERANKER_RETRIEVER_CONFIGS == with_search
    # Every caller of a search waits longer than a search with `r4` may take.
    slowest = RetrievalSettings().search_rerank_deadline_seconds
    assert VerdictSettings().upstream_timeout_seconds > slowest
    assert WebSettings().service_timeout_seconds > slowest
    assert RunnerSettings().request_timeout_seconds > slowest

    def rows_in(text: str) -> set[str]:
        return set(re.findall(r"r[1-6]", text))

    # The deploy gives `retrieval` the search service's endpoint and the
    # chat deployment, and both `workflow` and `verdict` the one list, with
    # `r4` and `r5` in it.
    app_stack = REPOSITORY_ROOT / "infra/demo/app"
    (deployed,) = re.findall(
        r"^available_retriever_configs\s*=\s*(\[[^\]]*\])",
        (app_stack / "terraform.tfvars").read_text(),
        re.MULTILINE,
    )
    assert rows_in(deployed) == {row.value for row in everywhere}
    given = (app_stack / "locals.tf").read_text() + (app_stack / "main.tf").read_text()
    assert '"RETRIEVAL_SEARCH_SERVICE_ENDPOINT"' in given
    assert '"RETRIEVAL_CHAT_DEPLOYMENT"' in given
    for variable in (
        "WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS",
        "VERDICT_AVAILABLE_RETRIEVER_CONFIGS",
    ):
        assert (
            f'"{variable}", value = jsonencode(var.available_retriever_configs)'
            in given
        )
    # The local start: the same, with the stand-ins as the search service
    # and as the chat deployment. `retrieval` is the one app given either.
    local = (REPOSITORY_ROOT / "dapr.yaml").read_text()
    assert "RETRIEVAL_SEARCH_SERVICE_ENDPOINT:" in local
    assert "RETRIEVAL_CHAT_DEPLOYMENT:" in local
    for variable in (
        "WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS",
        "VERDICT_AVAILABLE_RETRIEVER_CONFIGS",
    ):
        (listed,) = re.findall(rf"{variable}: '(.*)'", local)
        assert rows_in(listed) == {row.value for row in everywhere}
    # Both set `r4`'s deadline as a value of their own, and neither sets
    # `verdict`'s wait for one search: each stays under that wait, and the
    # deploy's own validation says the same limit.
    (local_deadline,) = re.findall(
        r'RETRIEVAL_SEARCH_RERANK_DEADLINE_SECONDS: "([\d.]+)"', local
    )
    (deployed_deadline,) = re.findall(
        r"^search_rerank_deadline_seconds\s*=\s*([\d.]+)",
        (app_stack / "terraform.tfvars").read_text(),
        re.MULTILINE,
    )
    verdict_waits = VerdictSettings().upstream_timeout_seconds
    assert "VERDICT_UPSTREAM_TIMEOUT_SECONDS" not in local + given
    assert float(local_deadline) < verdict_waits
    assert float(deployed_deadline) < verdict_waits
    assert (
        f"var.search_rerank_deadline_seconds < {verdict_waits:g}"
        in (app_stack / "variables.tf").read_text()
    )
    # The stand-in for the search service is a dev tool like the others.
    assert [mode.value for mode in SearchMode] == ["ok", "unavailable", "slow"]
