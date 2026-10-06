# AI Medical Underwriting Assistant

An AI assistant to support medical underwriting.

## Setup

This repository uses the [BMad Method](https://github.com/bmad-code-org/BMAD-METHOD) v6.12.1 with the
[Org Kit](https://github.com/schuaschua/bmad-org-kit) v1.14.0 module, configured for Claude Code.

- `_bmad/` holds the BMad configuration and the Org Kit overrides (`_bmad/custom/`).
- `.claude/skills/` holds the BMad and Org Kit skills.
- `docs/standards/` holds the org standards baselines and `docs/governance/` the blank governance questionnaires.

Open the folder in Claude Code and run the `bmad-help` skill to see what to do next.

## Code

The Python code is one [uv](https://docs.astral.sh/uv/) workspace (Python 3.13). The root `pyproject.toml`
holds the ruff, mypy and pytest settings for every member.

- `packages/contracts/` is the only shared code: the payload models for every service operation, the
  audit record, enums, the error catalogue, the `rule_id` patterns, the page type mapping, the eval
  query builder and text normalisation. It imports only the standard library and pydantic. A change to
  it is one pull request that updates every affected service.
- `services/` will hold the seven services.

### Install and check

Install uv 0.11.8, then from the repository root:

```sh
uv sync
uv run ruff format --check . && uv run ruff check .
uv run mypy packages/contracts/src packages/contracts/tests
uv run pytest --cov
```

`uv sync` creates `.venv/` and installs the exact versions in `uv.lock`. `pytest --cov` takes its test
paths, the measured packages and the 80% coverage threshold from the root `pyproject.toml`. The same
four checks run on every pull request and on every push to `main` (`.github/workflows/ci.yml`). Fix a failing check in the code; do not loosen the
settings.

### Still to do

The Org Kit's Jira sync needs this project's Jira site and project key. When they are known, run
**"setup Org Kit"** in Claude Code with those values. It adds the `bmad-create-epics-and-stories`
override (Jira sync, story structure, token budgets) and Scrooge's personal ledger hooks.
