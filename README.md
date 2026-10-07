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
- `services/` holds the seven services. So far there are three. `services/web/` is the FastAPI service
  that serves the React app in `services/web/spa/` and every `/api` route from one origin.
  `services/intake/` owns cases, documents and the stored PDFs: database schema `intake` and the blob
  containers `originals` and `cases`. `services/workflow/` owns the case lifecycle: one orchestration
  per case on Azure Durable Task Scheduler, case and page status, and the append-only audit trail
  (database schema `workflow`). `web` calls the other two through its Dapr sidecar: it asks `intake` to
  create a case from an upload, then asks `workflow` to start it. No service imports another service's code.

### Install and check

Install uv 0.11.8 and Node.js 24.21.0, then from the repository root:

```sh
uv sync
uv run ruff format --check . && uv run ruff check .
uv run mypy packages services
docker compose up --detach --wait                    # for the integration tests
uv run pytest --cov

npm --prefix services/web/spa ci
npm --prefix services/web/spa run lint
npm --prefix services/web/spa run typecheck
npm --prefix services/web/spa test -- --run
npm --prefix services/web/spa run build
```

`uv sync` creates `.venv/` and installs the exact versions in `uv.lock`. `pytest --cov` takes its test
paths, the measured packages and the 80% coverage threshold from the root `pyproject.toml`. The tests
marked `integration` use a real PostgreSQL, the blob emulator and the Durable Task Scheduler emulator
from `compose.yaml` and fail with a message saying so if those containers are not running; each makes a
database of its own (and `workflow`'s use a task hub of their own, `aiuw-test`), so your local data is
left alone, and none calls Azure. `uv run pytest -m "not integration"` leaves them out. The SPA's
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

It starts PostgreSQL with pgvector, the Azurite blob emulator and the Durable Task Scheduler emulator in
containers (`compose.yaml`), applies the database migrations, builds the SPA, starts a stand-in for
Azure AI Language (see below), and runs the `web`, `intake` and `workflow` services, each with its
Dapr sidecar (`dapr.yaml`; each later service is added to that file).
If the Dapr runtime is missing it stops and says so. Then open <http://localhost:8000/>. The app and
its API share that one address: `/api/health` answers without a role, and every other `/api` route
needs the `X-Demo-Role` header the role switcher sends. As the customer, "Upload a document" takes a
PDF of up to 10 MB (try one from `data/cases/`), starts its case and lists it with its status, which
the screen reads again every few seconds. If the case cannot be started, it is listed as received but
not started, with a button to try again; the document is not sent a second time.

A started case is redacted first: `workflow` commands `intake`, which has Azure AI Language mask
person names, addresses, phone numbers, email addresses, and identity and policy numbers with tokens
such as `[Person]` (dates, ages and medical terms are kept), stores the redacted PDF as the document
of record and splits it into pages, each with its text, a box per word and a thumbnail. From then on
only the redacted PDF is read; no route serves the original. If redaction fails or takes longer than
180 seconds the case is shown as failed, with a message asking for the document to be uploaded again.

The Azure environment is down while the stories are built, so locally Azure AI Language is a stand-in:
`uv run python -m synthdata.language_standin` (started by `./tools/dev.sh` on port 5100). It speaks
the service's REST job routes, reads the original from the blob emulator and writes the redacted PDF
and a result file back. It finds email addresses, phone numbers, identity numbers and policy numbers
by their shape, and the names and addresses of the synthetic cases; it is not a recogniser. It is part
of the dev-only `synthdata` package, so no service image holds it, and `intake` refuses a plain-HTTP
Language endpoint that is not on this machine. Start it with `--mode fail` or `--mode hang` to see a
failed case. What the real service does is checked in the final Azure test session
(`_bmad-output/implementation-artifacts/deferred-work.md`).

The services never run migrations when they start, here or in Azure, and `intake` and `workflow` each
report "not ready" (`/ready`) until their schema is at the newest migration they ship with. Locally, one script stands in
for the pipeline's migration step. `./tools/dev.sh` runs it for you; run it yourself after pulling a
change that adds a migration:

```sh
./tools/migrate-local.sh
```

It starts the two containers if they are not running, applies `intake`'s migrations to the local
database (`uv run alembic -c services/intake/alembic.ini upgrade head`, pointed at `localhost`) and
creates the blob containers `originals` and `cases` in the emulator. For `workflow` it creates the
database role `workflow` and applies that service's migrations
(`uv run alembic -c services/workflow/alembic.ini upgrade head`, with
`WORKFLOW_DATABASE_SERVICE_ROLE=workflow`), which grant the role its rights. It can be run again safely.

`workflow` runs as that role, not as the database's own user, so the rule that the audit trail is
append-only holds on your machine as it does in Azure: the role may read `workflow.audit_event` and
add to it, and the database refuses it an `UPDATE` or a `DELETE`.

| What | Where |
| --- | --- |
| The app and its API (`web`) | <http://localhost:8000/> |
| `web`'s Dapr sidecar | `http://localhost:3500` |
| `intake` (`/health`, `/ready`, `POST /cases`, `POST /cases/<case_id>/redaction`, `GET /cases/<case_id>/pages`, `GET /pages/<page_id>/text`, `/boxes` and `/thumbnail`, `GET /documents/<document_id>/file`), and its Dapr sidecar | `http://localhost:8001`, `http://localhost:3501` |
| Stand-in for Azure AI Language (this machine only) | `http://localhost:5100` |
| `workflow` (`/health`, `/ready`, `POST /cases/<case_id>/start`, `GET /cases/<case_id>/progress`, `GET /cases/<case_id>/audit`), and its Dapr sidecar | `http://localhost:8002`, `http://localhost:3502` |
| PostgreSQL (database and user `aiuw`, and the role `workflow`; no password, this machine only) | `localhost:5432` |
| Azurite blob emulator (its built-in account `devstoreaccount1`, this machine only) | `localhost:10000` |
| Durable Task Scheduler emulator (task hubs `default` and, for tests, `aiuw-test`), and its dashboard | `localhost:8080`, <http://localhost:8082/> |

Stop with Ctrl+C, then `docker compose down` (add `-v` to delete the local database and blobs).

Locally `intake` reaches the emulator with `INTAKE_BLOB_CONNECTION_STRING=UseDevelopmentStorage=true`
(set in `dapr.yaml`), which names the emulator's built-in account and holds no secret. In Azure that
variable is never set: the service signs in to Blob Storage and PostgreSQL with its managed identity,
and to Azure AI Language as well (`INTAKE_LANGUAGE_ENDPOINT` is the account's endpoint there, with
`INTAKE_LANGUAGE_ENTRA_AUTH=true`; there is no key). Language reads the original and writes the
redacted PDF with its own identity. `workflow` commands `intake` through its own Dapr sidecar
(`WORKFLOW_DAPR_HTTP_PORT`).
`workflow` reaches the scheduler emulator without a credential; in Azure it signs in to the Durable Task
Scheduler and PostgreSQL with its managed identity. The emulator keeps its state in memory, so
orchestrations are gone after `docker compose stop`, while case status and the audit trail stay in
PostgreSQL; starting such a case again gives it a new orchestration.

To work on the SPA with hot reload, keep the above running and start `npm --prefix services/web/spa run dev`;
its dev server passes `/api` calls on to port 8000.

To run the container images instead (`web` alone serves the app; an upload needs both services and
their sidecars, which is what `./tools/dev.sh` is for):

```sh
docker build -f services/web/Dockerfile -t aiuw-web:dev .
docker run --rm -p 8000:8000 aiuw-web:dev

docker build -f services/intake/Dockerfile -t aiuw-intake:dev .
docker build -f services/workflow/Dockerfile -t aiuw-workflow:dev .
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
`infra/bootstrap/README.md`) and `infra/demo/app`, which so far holds three Container Apps: `web`, the
only one reachable from the internet, and `intake` and `workflow`, with internal ingress only.
`workflow` is held at one replica and holds Durable Task Data Contributor on the task hub. For
redaction `intake` holds Cognitive Services User on Azure AI Language, and Language's own identity may
read the `originals` container and write the `cases` container.

The `deploy` workflow (`.github/workflows/deploy.yml`) is started by hand on `main` and deploys only
the commit `main` is at. It builds the `web`, `intake` and `workflow` images in the registry, plans
`app`, refuses a plan that destroys or replaces a resource, applies it, waits until the new `web`
revision is the one serving, fails unless the latest revisions of `intake` and `workflow` run the same
commit's image, checks `/api/health` and the SPA's page, and ends by saying whether `intake` and
`workflow` are ready. It does not run database migrations yet: the database roles and migrations of
`intake` and `workflow` are a manual step (`infra/bootstrap/README.md`, sections 4 and 5). Until it is
done for `intake`, that service stays "not ready" and an upload is answered with 502; until it is done
for `workflow`, an uploaded case is shown as received but not started. It needs:

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
