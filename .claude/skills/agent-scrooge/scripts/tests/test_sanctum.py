"""Tests for init-sanctum.py and wake.py against a throwaway project root."""

import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SKILL / "scripts" / script), *args], capture_output=True, text=True)


def test_first_breath_then_waking_keeps_project_data(tmp_path: Path):
    (tmp_path / "_bmad").mkdir()
    (tmp_path / "_bmad" / "config.toml").write_text('[core]\nuser_name = "Alex"\n[modules.org]\njira_project_key = "PROJ"\n')
    data = tmp_path / "_bmad" / "memory" / "agent-scrooge"
    data.mkdir(parents=True)
    (data / "stories.json").write_text('{"stories": {"PROJ-1": ["1.1", "Keep me"]}}')

    assert "MODE: FIRST_BREATH" in run("wake.py", str(tmp_path)).stdout

    out = json.loads(run("init-sanctum.py", str(tmp_path), str(SKILL)).stdout)
    assert out["status"] == "completed" and out["kept"] == ["stories.json"]
    assert set(out["written"]) == {"INDEX.md", "PERSONA.md", "CREED.md", "BOND.md", "MEMORY.md", "CAPABILITIES.md"}
    assert "Keep me" in (data / "stories.json").read_text()
    assert "Met Alex" in (data / "PERSONA.md").read_text()
    assert "PROJ" in (data / "BOND.md").read_text()

    (data / "MEMORY.md").write_text("# Memory\nmine")
    assert json.loads(run("init-sanctum.py", str(tmp_path), str(SKILL)).stdout)["status"] == "exists"
    assert (data / "MEMORY.md").read_text() == "# Memory\nmine"

    woke = run("wake.py", str(tmp_path)).stdout
    assert "MODE: WAKING" in woke and "===== CREED.md =====" in woke
    for code in ("[RF]", "[SC]", "[CD]", "[CL]", "[CE]", "[RT]", "[PC]"):
        assert code in woke
    assert "first-breath" not in woke and "Keep me" not in woke
