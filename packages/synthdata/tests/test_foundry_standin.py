"""Story 1.8: the local stand-in for the Foundry model deployments stays a dev tool.

What the stand-in answers is tested through the services that call it, in the
end-to-end tests beside this file.
"""

import re

from synthdata_stack import REPOSITORY_ROOT

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
    RUNNABLE_RETRIEVER_CONFIGS,
    SEARCH_SERVICE_RETRIEVER_CONFIGS,
)
from verdict.settings import Settings as VerdictSettings
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
    ]
    # Stories 3.2 and 3.3: no service imports another, so each names the
    # ladder rows a case may run with by itself: `retrieval` by its row
    # table and whether it was given a search service, `verdict` and
    # `workflow` by a setting. Held equal here, without a search service
    # and with one.
    plain = frozenset({RetrieverConfig.R1, RetrieverConfig.R2, RetrieverConfig.R3})
    with_search = plain | {RetrieverConfig.R5}
    assert available_rows(search_service=False) == plain
    assert DEFAULT_AVAILABLE_RETRIEVER_CONFIGS == plain
    assert VERDICT_DEFAULT_RETRIEVER_CONFIGS == plain
    assert frozenset(WorkflowSettings().available_retriever_configs) == plain
    assert frozenset(VerdictSettings().available_retriever_configs) == plain
    assert RetrievalSettings().search_service_endpoint is None
    assert available_rows(search_service=True) == with_search == BUILT_ROWS
    assert RUNNABLE_RETRIEVER_CONFIGS == with_search
    assert RUNNABLE_RETRIEVER_CONFIGS - SEARCH_SERVICE_RETRIEVER_CONFIGS == plain

    def rows_in(text: str) -> set[str]:
        return set(re.findall(r"r[1-6]", text))

    # The deploy gives `retrieval` the search service's endpoint, and both
    # `workflow` and `verdict` the one list, with `r5` in it.
    app_stack = REPOSITORY_ROOT / "infra/demo/app"
    (deployed,) = re.findall(
        r"^available_retriever_configs\s*=\s*(\[[^\]]*\])",
        (app_stack / "terraform.tfvars").read_text(),
        re.MULTILINE,
    )
    assert rows_in(deployed) == {row.value for row in with_search}
    given = (app_stack / "locals.tf").read_text() + (app_stack / "main.tf").read_text()
    assert '"RETRIEVAL_SEARCH_SERVICE_ENDPOINT"' in given
    for variable in (
        "WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS",
        "VERDICT_AVAILABLE_RETRIEVER_CONFIGS",
    ):
        assert (
            f'"{variable}", value = jsonencode(var.available_retriever_configs)'
            in given
        )
    # The local start: the same, with the stand-in as the search service.
    local = (REPOSITORY_ROOT / "dapr.yaml").read_text()
    assert "RETRIEVAL_SEARCH_SERVICE_ENDPOINT:" in local
    for variable in (
        "WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS",
        "VERDICT_AVAILABLE_RETRIEVER_CONFIGS",
    ):
        (listed,) = re.findall(rf"{variable}: '(.*)'", local)
        assert rows_in(listed) == {row.value for row in with_search}
    # The stand-in for the search service is a dev tool like the others.
    assert [mode.value for mode in SearchMode] == ["ok", "unavailable", "slow"]
