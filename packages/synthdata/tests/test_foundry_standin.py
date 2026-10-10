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
    CHAT_DEPLOYMENT_RETRIEVER_CONFIGS,
    RERANKER_RETRIEVER_CONFIGS,
    RUNNABLE_RETRIEVER_CONFIGS,
    SEARCH_SERVICE_RETRIEVER_CONFIGS,
    SERVICE_PLANNED_RETRIEVER_CONFIGS,
)
from verdict.domain.run import (
    DEFAULT_AVAILABLE_RETRIEVER_CONFIGS as VERDICT_DEFAULT_RETRIEVER_CONFIGS,
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
    # Stories 3.2, 3.3, 3.7 and 3.8: no service imports another, so each
    # names the ladder rows a case may run with by itself: `retrieval` by
    # its row table and whether it was given a search service, a reranker
    # deployment (Cohere Rerank, for `r4`) and a chat deployment (what the
    # knowledge base of `r6` plans with), `verdict` and `workflow` by a
    # setting. Held equal here, without the three, with each and with all.
    plain = frozenset({RetrieverConfig.R1, RetrieverConfig.R2, RetrieverConfig.R3})
    with_search = plain | {RetrieverConfig.R5}
    with_reranker = plain | {RetrieverConfig.R4}
    everywhere = frozenset(RetrieverConfig)

    def answered(
        search_service: bool = False, chat: bool = False, reranker: bool = False
    ) -> frozenset[RetrieverConfig]:
        """The rows `retrieval` answers, as its app builds its ports from the three."""
        return available_rows(
            search_service, reranker=reranker, knowledge_base=search_service and chat
        )

    assert answered() == plain
    assert DEFAULT_AVAILABLE_RETRIEVER_CONFIGS == plain
    assert VERDICT_DEFAULT_RETRIEVER_CONFIGS == plain
    assert frozenset(WorkflowSettings().available_retriever_configs) == plain
    assert frozenset(VerdictSettings().available_retriever_configs) == plain
    assert RetrievalSettings().search_service_endpoint is None
    assert RetrievalSettings().chat_deployment is None
    assert RetrievalSettings().rerank_deployment is None
    assert answered(search_service=True) == with_search
    # The chat deployment alone opens no row, and `r4` no longer needs it.
    assert answered(chat=True) == plain
    assert answered(reranker=True) == with_reranker
    assert answered(search_service=True, chat=True) == everywhere - {RetrieverConfig.R4}
    assert answered(search_service=True, chat=True, reranker=True) == everywhere
    assert RUNNABLE_RETRIEVER_CONFIGS == everywhere == BUILT_ROWS
    assert RUNNABLE_RETRIEVER_CONFIGS - SEARCH_SERVICE_RETRIEVER_CONFIGS == (
        with_reranker
    )
    assert RERANKER_RETRIEVER_CONFIGS == {RetrieverConfig.R4}
    assert CHAT_DEPLOYMENT_RETRIEVER_CONFIGS == {RetrieverConfig.R6}
    # On the row whose retrieval plans its own queries the agent's loop is off.
    assert SERVICE_PLANNED_RETRIEVER_CONFIGS == {RetrieverConfig.R6}
    # Every caller of a search waits longer than a search with `r4` or
    # with `r6` may take.
    slowest = max(
        RetrievalSettings().search_rerank_deadline_seconds,
        RetrievalSettings().search_agentic_deadline_seconds,
    )
    assert VerdictSettings().upstream_timeout_seconds > slowest
    assert WebSettings().service_timeout_seconds > slowest
    assert RunnerSettings().request_timeout_seconds > slowest

    def rows_in(text: str) -> set[str]:
        return set(re.findall(r"r[1-6]", text))

    # The deploy gives `retrieval` the search service's endpoint, the chat
    # deployment and the reranker deployment, whose name comes from the
    # foundation stack, and both `workflow` and `verdict` the one list,
    # with `r4`, `r5` and `r6` in it.
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
    assert (
        '"RETRIEVAL_RERANK_DEPLOYMENT", '
        'value = try(local.foundation.model_deployment_names["rerank"], "")'
    ) in given
    foundation = (
        REPOSITORY_ROOT / "infra/demo/foundation/terraform.tfvars"
    ).read_text()
    assert re.search(r"^  rerank = \{$", foundation, re.MULTILINE)
    for variable in (
        "WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS",
        "VERDICT_AVAILABLE_RETRIEVER_CONFIGS",
    ):
        assert (
            f'"{variable}", value = jsonencode(var.available_retriever_configs)'
            in given
        )
    # The local start: the same, with the stand-ins as the search service
    # and as the two deployments. `retrieval` is the one app given any.
    local = (REPOSITORY_ROOT / "dapr.yaml").read_text()
    assert "RETRIEVAL_SEARCH_SERVICE_ENDPOINT:" in local
    assert "RETRIEVAL_CHAT_DEPLOYMENT:" in local
    assert "RETRIEVAL_RERANK_DEPLOYMENT:" in local
    for variable in (
        "WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS",
        "VERDICT_AVAILABLE_RETRIEVER_CONFIGS",
    ):
        (listed,) = re.findall(rf"{variable}: '(.*)'", local)
        assert rows_in(listed) == {row.value for row in everywhere}
    # Both set the deadlines of `r4` and of `r6` as values of their own,
    # and neither sets `verdict`'s wait for one search: each stays under
    # that wait, and the deploy's own validation says the same limit.
    verdict_waits = VerdictSettings().upstream_timeout_seconds
    assert "VERDICT_UPSTREAM_TIMEOUT_SECONDS" not in local + given
    for row_deadline in ("rerank", "agentic"):
        (local_deadline,) = re.findall(
            rf'RETRIEVAL_SEARCH_{row_deadline.upper()}_DEADLINE_SECONDS: "([\d.]+)"',
            local,
        )
        (deployed_deadline,) = re.findall(
            rf"^search_{row_deadline}_deadline_seconds\s*=\s*([\d.]+)",
            (app_stack / "terraform.tfvars").read_text(),
            re.MULTILINE,
        )
        assert float(local_deadline) < verdict_waits
        assert float(deployed_deadline) < verdict_waits
        assert (
            f"var.search_{row_deadline}_deadline_seconds < {verdict_waits:g}"
            in (app_stack / "variables.tf").read_text()
        )
    # Story 3.8: the search service's own identity may call the Foundry
    # deployments it plans and embeds with, and no key is given for them.
    assert "search_foundry_user" in given
    assert "local.foundation.search_principal_id" in given
    assert '"RETRIEVAL_SEARCH_AGENTIC_API_VERSION"' in given
    assert "RETRIEVAL_SEARCH_AGENTIC_API_VERSION:" in local
    # The stand-in for the search service is a dev tool like the others.
    assert [mode.value for mode in SearchMode] == ["ok", "unavailable", "slow"]
