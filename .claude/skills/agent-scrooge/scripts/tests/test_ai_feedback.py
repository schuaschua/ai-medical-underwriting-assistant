"""Tests for ai_feedback.py."""

import csv
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "ai_feedback.py"
spec = importlib.util.spec_from_file_location("ai_feedback", SCRIPT)
ai_feedback = importlib.util.module_from_spec(spec)
sys.modules["ai_feedback"] = ai_feedback
spec.loader.exec_module(ai_feedback)

STATUS = """# generated
project: demo
development_status:
  epic-1: done
  1-1-login: done
  1-2-profile: done
  epic-2: in-progress
  2-1-search: done
  2-2-filter: done
  epic-3: in-progress
  3-1-export: in-progress
"""


def project(tmp_path: Path) -> Path:
    path = tmp_path / "_bmad-output/implementation-artifacts/sprint-status.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(STATUS)
    return tmp_path


def run(capsys, *argv) -> tuple[int, dict]:
    code = ai_feedback.main([str(a) for a in argv])
    return code, json.loads(capsys.readouterr().out)


def test_pending_lists_finished_epics_until_rated(tmp_path, capsys):
    root = project(tmp_path)
    code, out = run(capsys, root, "pending")
    assert code == 0
    assert out["pending"] == ["epic-1", "epic-2"]  # epic-2 is done by its stories
    assert out["questions"]["categories"]["grounding"] == "AI answers were not grounded in company data"

    assert run(capsys, root, "record", "--epic", "1", "--rating", "4")[0] == 0
    assert run(capsys, root, "record", "--epic", "epic-2", "--skip")[0] == 0
    assert run(capsys, root, "pending")[1]["pending"] == []


def test_poor_rating_records_categories_and_other(tmp_path, capsys):
    root = project(tmp_path)
    code, out = run(capsys, root, "record", "--epic", "2", "--rating", "2", "--category", "testing",
                    "--category", "other", "--other", "ignored the API contract")
    assert code == 0 and out["replaced"] is False
    rows = list(csv.DictReader((root / ai_feedback.DATA_FILE).open()))
    assert rows == [{"epic": "epic-2", "rating": "2", "poor": "yes",
                     "categories": "AI testing standards not up to quality; Other",
                     "other": "ignored the API contract", "comment": "", "status": "rated",
                     "recorded": rows[0]["recorded"]}]

    code, out = run(capsys, root, "record", "--epic", "2", "--rating", "5")
    assert code == 0 and out["replaced"] is True
    assert run(capsys, root, "list")[1]["feedback"][0]["rating"] == "5"


def test_invalid_answers_are_refused(tmp_path, capsys):
    root = project(tmp_path)
    cases = [
        ["--rating", "6"],
        ["--rating", "1"],  # poor without a category
        ["--rating", "4", "--category", "coding"],  # categories only for poor
        ["--rating", "2", "--category", "other"],  # other without text
        ["--rating", "2", "--category", "speed"],
        ["--rating", "3", "--skip"],
    ]
    for extra in cases:
        code, out = run(capsys, root, "record", "--epic", "1", *extra)
        assert code == 1 and out["status"] == "invalid", extra
    assert not (root / ai_feedback.DATA_FILE).exists()


def test_no_sprint_status_means_nothing_pending(tmp_path, capsys):
    assert run(capsys, tmp_path, "pending")[1]["pending"] == []
