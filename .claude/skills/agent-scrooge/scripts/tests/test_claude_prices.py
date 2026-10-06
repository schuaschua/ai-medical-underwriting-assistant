"""Tests for claude_prices.py against a saved slice of the pricing page (no network)."""

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "claude_prices.py"
spec = importlib.util.spec_from_file_location("claude_prices", SCRIPT)
claude_prices = importlib.util.module_from_spec(spec)
sys.modules["claude_prices"] = claude_prices
spec.loader.exec_module(claude_prices)

PAGE = """---
title: Pricing
---

## Model pricing

| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| :---- | :---------------- | :-------------- | :-------------- | :----------------------- | :------------ |
| Claude Opus 5.5 | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok<sup>2</sup> | $20 / MTok |
| Claude Opus 4.1 ([retired, except on Bedrock](https://example.com/x)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |
| Claude Haiku 4.5 | $1 / MTok | $1.25 / MTok | $2 / MTok | $0.10 / MTok | $5 / MTok |

*<sup>2 footnote</sup>*

## Cloud platform pricing

| Model | Something |
| --- | --- |
| Claude Opus 5.5 | $99 / MTok |
"""


def test_parses_the_model_table_only():
    models = claude_prices.parse(PAGE)
    assert list(models) == ["claude-opus-5-5", "claude-opus-4-1", "claude-haiku-4-5"]
    assert models["claude-opus-5-5"] == {"name": "Claude Opus 5.5", "input": 4.0, "cache_write_5m": 5.0,
                                         "cache_write_1h": 8.0, "cache_read": 0.2, "output": 20.0}
    assert models["claude-opus-4-1"]["name"] == "Claude Opus 4.1"


def test_log_ids_map_to_pricing_ids():
    assert claude_prices.log_model_id("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert claude_prices.log_model_id("claude-opus-5-5[1m]") == "claude-opus-5-5"


def test_save_writes_the_project_file(tmp_path, capsys):
    page = tmp_path / "pricing.md"
    page.write_text(PAGE)
    assert claude_prices.main([str(tmp_path), "--save", "--file", str(page)]) == 0
    saved = json.loads((tmp_path / "_bmad/memory/agent-scrooge/claude-prices.json").read_text())
    assert saved["currency"] == "USD" and "claude-haiku-4-5" in saved["models"]
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_changed_layout_is_an_error(tmp_path, capsys):
    page = tmp_path / "pricing.md"
    page.write_text("# Pricing\n\nNothing here.\n")
    assert claude_prices.main(["--file", str(page)]) == 2
    assert "layout may have changed" in capsys.readouterr().out
