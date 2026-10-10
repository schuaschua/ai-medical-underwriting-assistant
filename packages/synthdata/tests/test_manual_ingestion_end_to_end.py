"""Stories 2.2 and 3.2: the manual, ingested by `retrieval`'s job, against the rule table.

The job runs as it really runs, over the project's manual in a blob container
of the test's own and a real PostgreSQL, with this package's stand-ins where
Document Intelligence and the Foundry deployments would be. Only here, outside
`services/`, are its chunks compared with the answer key's rule table: the job
itself never reads it (spine AD-17).

Run `docker compose up --detach --wait` first.
"""

import json
import logging
import re
from typing import Any

import pytest
from synthdata_stack import LocalRetrieval, rule_table

from contracts.rules import rule_ids_defined_in
from synthdata.foundry_standin import (
    EMBEDDING_DIMENSIONS,
)

pytestmark = pytest.mark.integration

# The first of the two lines of the manual's footer.
FOOTER_LINE = "SYNTHETIC TEST DOCUMENT. Its ratings (debits and declines) are invented."
# The rule whose text the changed manual changes, and the one it removes.
CHANGED, REMOVED = "UW-DM-002", "UW-DM-004"


def vector_of(chunk: dict[str, Any]) -> list[float]:
    return [float(value) for value in json.loads(chunk["embedding"])]


# --- First run -----------------------------------------------------------------------


def test_story_2_2_the_manual_is_ingested_as_one_smart_chunk_per_rule_of_the_rule_table(
    ingested_manual: LocalRetrieval, caplog: pytest.LogCaptureFixture
) -> None:
    rules = rule_table()
    every_chunk = ingested_manual.chunks()
    # The `smart` set; the `fixed` set the same job writes is story 3.2's
    # (`test_manual_search_end_to_end.py`).
    chunks = {
        chunk_id: chunk
        for chunk_id, chunk in every_chunk.items()
        if chunk["chunk_set"] == "smart"
    }

    # Every rule of the table is defined by exactly one chunk, and no chunk
    # defines a rule the table lacks.
    defined = [rule_id for chunk in chunks.values() for rule_id in chunk["rule_ids"]]
    assert sorted(defined) == sorted(rules)
    assert len(chunks) == len(rules) == 111
    for chunk_id, chunk in chunks.items():
        (rule_id,) = chunk["rule_ids"]
        rule = rules[rule_id]
        # The id is derived from the chunk set and the rule (Epic 3 loads
        # the same records into another store).
        assert chunk_id == f"smart-{rule_id}"
        assert chunk["chunk_set"] == "smart"
        # Exactly one rule: its definition, and nothing of another's.
        assert rule_ids_defined_in(chunk["text"]) == [rule_id]
        assert chunk["text"].startswith(f"Rule {rule_id}: {rule['impairment']}")
        assert rule["threshold"]["words"] in chunk["text"]
        # Its parent section, its impairment and the page of its definition,
        # as the generator recorded them in the answer key.
        assert chunk["section_id"] == f"{rule['section']}.4"
        assert chunk["impairment"] == rule["impairment"]
        assert chunk["manual_page"] == rule["manual_page"]
        # The whole definition, to its source, though the stand-in gave
        # some definitions in two paragraphs, as the real service does.
        assert chunk["text"].index("Threshold:") < chunk["text"].index(
            "Probable rating:"
        )
        assert re.search(r"[Ss]ource (of|for) the [^:]*: .+\.$", chunk["text"])
        # One context line by the chat model, saying where the rule sits.
        line = chunk["context_line"]
        assert line and "\n" not in line
        assert f"section {rule['section']}, {rule['impairment']}" in line
        # A 3,072-dimension vector.
        assert len(vector_of(chunk)) == EMBEDDING_DIMENSIONS == 3072

    # The stand-in was honest about the real service: footer lines without
    # a role and definitions ended early were in what the job had to cut,
    # and no chunk of either set holds a footer line.
    (analysis,) = ingested_manual.layout.analyses.values()
    assert analysis.result is not None
    no_role = [p["content"] for p in analysis.result["paragraphs"] if "role" not in p]
    assert FOOTER_LINE in no_role
    ended_early = [text for text in no_role if text.startswith("Rule UW-")]
    assert any(text.endswith("threshold:") for text in ended_early)
    assert any("Probable rating:" not in text for text in ended_early)
    assert not [c for c in every_chunk.values() if FOOTER_LINE in c["text"]]

    # A second run changes nothing and calls no model, for either chunk set.
    calls = ingested_manual.model_calls

    with caplog.at_level(logging.INFO):
        status = ingested_manual.ingest()

    assert status == 0
    # Same ids, text and vectors; not one row was written again.
    assert ingested_manual.chunks() == every_chunk
    # No chat call and no embedding call.
    assert ingested_manual.model_calls == calls
    assert "written=0 moved=0 removed=0 unchanged=111 skipped=yes" in caplog.text
    assert caplog.text.count("skipped=yes") == 2
    # The manual is the one the index was built from: it was not sent to the
    # layout model a second time.
    assert ingested_manual.layout.submits == 1
    assert "manual unchanged since the last run" in caplog.text


# --- Failures --------------------------------------------------------------------------
