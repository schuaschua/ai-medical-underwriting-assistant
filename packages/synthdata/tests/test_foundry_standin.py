"""Story 1.8: the local stand-in for the Foundry model deployments stays a dev tool.

What the stand-in answers is tested through the services that call it, in the
end-to-end tests beside this file.
"""

import re

from synthdata_stack import REPOSITORY_ROOT

from contracts.enums import RetrieverConfig
from retrieval.domain.rows import BUILT_ROWS
from synthdata.foundry_standin import (
    Mode,
)
from verdict.domain.run import RUNNABLE_RETRIEVER_CONFIGS
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
    # Story 3.2: no service imports another, so each names the ladder rows a
    # case may run with in its own code: `retrieval`'s row table, `verdict`'s
    # runnable rows and `workflow`'s setting. Held equal here, and with the
    # list the deploy passes to `workflow`.
    built = frozenset({RetrieverConfig.R1, RetrieverConfig.R2, RetrieverConfig.R3})
    assert BUILT_ROWS == built
    assert RUNNABLE_RETRIEVER_CONFIGS == built
    assert DEFAULT_AVAILABLE_RETRIEVER_CONFIGS == built
    assert frozenset(WorkflowSettings().available_retriever_configs) == built
    (deployed,) = re.findall(
        r"^available_retriever_configs\s*=\s*(\[[^\]]*\])",
        (REPOSITORY_ROOT / "infra/demo/app/terraform.tfvars").read_text(),
        re.MULTILINE,
    )
    assert set(re.findall(r"r[1-6]", deployed)) == {row.value for row in built}
