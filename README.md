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
- `services/` holds the seven services. So far there is one: `services/web/`, the FastAPI service that
  serves the React app in `services/web/spa/` and every `/api` route from one origin.

### Install and check

Install uv 0.11.8 and Node.js 24.21.0, then from the repository root:

```sh
uv sync
uv run ruff format --check . && uv run ruff check .
uv run mypy packages services
uv run pytest --cov

npm --prefix services/web/spa ci
npm --prefix services/web/spa run lint
npm --prefix services/web/spa run typecheck
npm --prefix services/web/spa test -- --run
npm --prefix services/web/spa run build
```

`uv sync` creates `.venv/` and installs the exact versions in `uv.lock`. `pytest --cov` takes its test
paths, the measured packages and the 80% coverage threshold from the root `pyproject.toml`; the SPA's
60% threshold is in `services/web/spa/vite.config.ts`. The same checks, plus dependency scans
(`pip-audit`, `npm audit`), run on every pull request and on every push to `main`
(`.github/workflows/ci.yml`). Fix a failing check in the code; do not loosen the settings.

### Run locally

You need Docker, the [Dapr CLI](https://docs.dapr.io/getting-started/install-dapr-cli/) 1.18 with its
runtime installed once (`dapr init`), uv and Node.js.
One command starts everything:

```sh
./tools/dev.sh
```

It starts PostgreSQL with pgvector and the Durable Task Scheduler emulator in containers
(`compose.yaml`), builds the SPA, and runs the `web` service with its Dapr sidecar (`dapr.yaml`; each
later service is added to that file). If the Dapr runtime is missing it stops and says so. Then open
<http://localhost:8000/>. The app and its API share that one address: `/api/health` answers without a
role, and every other `/api` route needs the `X-Demo-Role` header the role switcher sends.

| What | Where |
| --- | --- |
| The app and its API (`web`) | <http://localhost:8000/> |
| `web`'s Dapr sidecar | `http://localhost:3500` |
| PostgreSQL (database and user `aiuw`, no password, this machine only) | `localhost:5432` |
| Durable Task Scheduler emulator, and its dashboard | `localhost:8080`, <http://localhost:8082/> |

Stop with Ctrl+C, then `docker compose down` (add `-v` to delete the local database).

To work on the SPA with hot reload, keep the above running and start `npm --prefix services/web/spa run dev`;
its dev server passes `/api` calls on to port 8000.

To run the container image instead:

```sh
docker build -f services/web/Dockerfile -t aiuw-web:dev .
docker run --rm -p 8000:8000 aiuw-web:dev
```

### Contract types

The SPA's TypeScript types for API payloads are generated from `packages/contracts`, never written by
hand. After changing a contract model, regenerate both files and commit them:

```sh
uv run python -m contracts.export_schema services/web/spa/src/api/contracts.schema.json
npm --prefix services/web/spa run contracts:generate
```

Two checks fail until that is done: a test in `uv run pytest` compares the schema file with the models,
and `npm --prefix services/web/spa run contracts:check` compares the TypeScript file with the schema.

### Deploy

The demo environment is two Terraform stacks, applied in order: `infra/demo/foundation` (see
`infra/bootstrap/README.md`) and `infra/demo/app`, which so far holds one Container App, `web`.

The `deploy` workflow (`.github/workflows/deploy.yml`) is started by hand on `main` and deploys only
the commit `main` is at. It builds the `web` image in the registry, plans `app`, refuses a plan that
destroys or replaces a resource, applies it, waits until the new revision is the one serving, and then
checks `/api/health` and the SPA's page. It needs:

| What | Set by |
| --- | --- |
| Repository variables `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID` (ids for OIDC sign-in, not secrets) | `infra/bootstrap/state-backend.sh` |
| GitHub Environment `demo`, deploying only from `main` | `infra/bootstrap/state-backend.sh` |
| The deployment identity `id-aiuw-demo-wus3-deploy`, trusted for that environment | `infra/bootstrap/state-backend.sh` |
| The `foundation` stack applied: the workflow reads the registry and the rest from its state | `infra/bootstrap/README.md`, section 2 |

No secret is stored in GitHub. Pull requests that touch `infra/` get a format check and `validate` of
`app`; it is not planned there, because its plan needs the `foundation` state.

### Still to do

The Org Kit's Jira sync needs this project's Jira site and project key. When they are known, run
**"setup Org Kit"** in Claude Code with those values. It adds the `bmad-create-epics-and-stories`
override (Jira sync, story structure, token budgets) and Scrooge's personal ledger hooks.
