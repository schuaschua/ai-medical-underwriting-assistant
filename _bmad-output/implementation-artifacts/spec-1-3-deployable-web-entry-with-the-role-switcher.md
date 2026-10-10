---
title: 'Story 1.3: Deployable web entry with the role switcher'
type: 'feature'
created: '2026-10-06'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '14712c9791f78d648d7f2466742ab364d85a09ba'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
  - '{project-root}/docs/standards/terraform.md'
  - '{project-root}/docs/standards/azure.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** There is no application to open. A customer or underwriter needs one address that serves the screens and the API, and a way to say which of the two demo roles they are acting as, since there is no sign-in.

**Approach:** Build the `web` service (FastAPI) that serves a React single-page app and `/api/*` from one origin, with a role switcher that sends `X-Demo-Role` on every call and role checks on the server. Make it runnable locally with Dapr, and deployable: container image, the `app` Terraform stack with the `web` Container App, and a manually started deploy workflow.

## Boundaries & Constraints

**Always:**
- Spine AD-9 and AD-19: one origin, CORS off; `web` holds no business rules and owns no database schema; the SPA calls `/api/*` only through one API client module; `web` rejects a missing or unknown `X-Demo-Role` with 400 in the contracts error shape and checks the role each route needs.
- Hexagonal layout from the spine: `domain/` (pure), `adapters/`, `settings.py` as the one `pydantic-settings` object with the `WEB_` prefix. Payload and error shapes come from `packages/contracts`.
- Stack versions from the spine, pinned exactly: Python 3.13, fastapi 0.142.2, uvicorn 0.54.0, httpx 0.28.1, pydantic-settings 2.15.0, azure-monitor-opentelemetry 1.8.10; React 19.3.0, Vite 8.3.3, TypeScript 6.0.3, typescript-eslint 8.71.1, Vitest 5.0.3. Any other dependency is pinned exactly at its current stable version and listed in Implementation Notes.
- `docs/standards/security.md` rules 20, 22, 25, 26, 27, 28, 31: server-side validation, no HTML from user or model text, security headers on every response (CSP self only, no inline scripts), plain error bodies, lock files committed, dependency scans in CI, no values in logs.
- SPA types for contracts payloads are generated from the contracts package, with a check that fails when they drift.
- Plain default styling. User-facing text lives in one strings module.
- Container Apps settings per the standards: HTTPS-only external ingress for `web` only, startup, readiness and liveness probes, Consumption profile, 0.5 vCPU and 1 GiB, at most 2 replicas, a `min_replicas` variable, the `web` runtime identity with only AcrPull and Monitoring Metrics Publisher.

**Never:**
- No sign-in, cookies, user records or session storage on the server. No Entra authentication on the Container App.
- No upload, case, triage or audit functionality: those screens arrive with their own stories. A role's navigation lists only screens that exist.
- Do not run `terraform apply`, the bootstrap script, `az` commands that change Azure, or any `gh` command that changes the repository. The Azure foundation is not applied yet; the `app` stack and deploy workflow are written and validated only.
- No Dapr SDK package (AD-3). No changes to `packages/contracts` models; a generated-types exporter may be added beside them.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Open the app | `GET /` | The SPA's HTML, with security headers | N/A |
| Deep link | `GET /triage` (a client route) | The SPA's HTML | N/A |
| Unknown API path | `GET /api/nope` | 404 in the error shape, not the SPA | Error body |
| Health | `GET /api/health` with no role header | 200 | N/A |
| Role echo | `GET /api/me` with `X-Demo-Role: underwriter` | 200 with the role | N/A |
| Missing role | `GET /api/me` with no header | 400, code `invalid_role` | Error body with trace id |
| Unknown role | `X-Demo-Role: admin` | 400, code `invalid_role` | Error body |
| Wrong role for a route | Customer calls an underwriter-only route | 403, code `role_not_allowed` | Error body |
| Switch role in the SPA | User picks Underwriter | Every later API call carries `X-Demo-Role: underwriter`; navigation shows only underwriter screens; the choice survives a reload | N/A |
| First visit | No role chosen yet | The SPA asks for a role before showing any role screen | N/A |
| Cross-origin call | A request with an `Origin` from elsewhere | No CORS headers are returned | N/A |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/enums.py` (`DemoRole`), `errors.py` (`ErrorCode`, `DomainError`, `HTTP_STATUS`, `NO_TRACE_ID`), `operations.py` -- reuse; do not redefine roles or error codes
- `pyproject.toml` -- workspace root: `services/*` are members; `testpaths` covers `services`; add `web` to coverage `source_pkgs`
- `.github/workflows/ci.yml` -- existing Python checks; extend for the SPA (lint, type check, Vitest, build) and add dependency scans
- `infra/demo/foundation/outputs.tf`, `infra/bootstrap/README.md`, `.github/workflows/infra-pr.yml` -- what the `app` stack reads by remote state and how plans run; being patched by another task, so read but do not edit
- `_bmad-output/implementation-artifacts/deferred-work.md` -- two items assigned to this story: export the contract to the SPA with a drift check; dependency vulnerability scans in CI
- Spine: AD-3, AD-9, AD-18, AD-19, Consistency Conventions (errors, configuration, tracing), Source tree (`services/web/`, `services/web/spa/`)

## Tasks & Acceptance

**Execution:**
- [x] `services/web/pyproject.toml`, `services/web/src/web/` -- FastAPI app: `settings.py`, `domain/roles.py` (which role may use which route group), `adapters/http/` (app factory, role dependency, error handlers, security headers middleware, `/api/health`, `/api/me`, static SPA with client-route fallback), OpenTelemetry setup switched on only when a connection string is configured
- [x] `services/web/tests/` -- tests for every matrix row that concerns the server, named for story 1.3
- [x] `services/web/spa/` -- Vite React TypeScript app: `src/api/client.ts` (the one client, adds the role header), `src/api/contracts.gen.ts` (generated), `src/strings.ts`, role switcher, role-aware navigation, a home screen per role, tests with Vitest and Testing Library, ESLint, Prettier, committed `package-lock.json`
- [x] `packages/contracts` or `tools/` -- a small exporter that writes the JSON Schema of the contracts models, and the SPA script that turns it into TypeScript; a check that fails on drift
- [x] `services/web/Dockerfile` -- multi-stage: build the SPA, then a slim Python image running as a non-root user
- [x] `dapr.yaml`, `compose.yaml`, `README.md` -- local run: Dapr multi-app run file with `web`, PostgreSQL with pgvector and the Durable Task Scheduler emulator in containers, one documented start command
- [x] `infra/demo/app/` -- the `app` stack: remote state from `foundation`, the `web` Container App with Dapr app id `web`, ingress, probes, identity and its two role assignments, `min_replicas` variable, image tag variable
- [x] `.github/workflows/deploy.yml` -- `workflow_dispatch` only, `main` only, GitHub Environment `demo`, OIDC: build and push the image with `az acr build`, plan and apply `app`, then a read-only smoke check of `/api/health`
- [x] `.github/workflows/ci.yml` -- add SPA lint, type check, tests and build; add `pip-audit` and `npm audit --omit=dev`; include `infra/demo/app` in the infrastructure pull-request plan only if that needs no change to `infra-pr.yml` beyond a matrix entry (otherwise leave a note)

**Acceptance Criteria:**
- Given a developer machine with Docker and the Dapr CLI, when the documented start command is run, then `http://localhost:<port>/` serves the SPA and `/api/me` answers through the same origin.
- Given the built container image, when it is run locally, then `/api/health` returns 200 and the process is not root.
- Given `infra/demo/app`, when `terraform fmt -check` and `terraform validate` run, then both pass.
- Given the contracts package changes a model, when the drift check runs, then it fails until the generated types are regenerated.

## Implementation Notes

**Dependencies beyond the pinned list** (each pinned exactly, current stable on 2026-10-06):

- Python, `services/web`: `azure-identity` 1.26.0 (the spine's pin). Application Insights has local authentication off, so telemetry needs an Entra token; `pydantic` 2.13.5 (same pin as contracts). `azure-monitor-opentelemetry` 1.8.10 brings pre-release packages of its own (`azure-monitor-opentelemetry-exporter` 1.0.0b57, `azure-core-tracing-opentelemetry` 1.0.0b13 and the OpenTelemetry instrumentation packages); they are locked in `uv.lock`.
- SPA runtime: `react-dom` 19.3.0, `react-router` 8.4.0.
- SPA dev: `@vitejs/plugin-react` 6.1.2, `@vitest/coverage-v8` 5.0.3, `eslint` 10.12.0, `@eslint/js` 10.0.1, `eslint-plugin-react-hooks` 7.1.1, `globals` 17.13.0, `prettier` 3.9.9, `@testing-library/react` 16.3.3, `@testing-library/jest-dom` 7.0.1, `@testing-library/user-event` 14.6.7, `jsdom` 30.1.2, `json-schema-to-typescript` 16.0.0, `@types/react` 19.3.0, `@types/react-dom` 19.3.0, `@types/node` 24.19.1.
- CI: `pip-audit` 2.10.1 (run with `uvx`, not installed in the workspace), `actions/setup-node` v7.0.0, `Azure/login` v3.1.0, both pinned by commit.
- Images: `node:24.21.0-bookworm-slim`, `python:3.13.16-slim-bookworm`, `ghcr.io/astral-sh/uv:0.11.8`, `pgvector/pgvector:0.8.2-pg17`, and the Durable Task Scheduler emulator pinned by digest (its newest version tag, `v0.0.10`, stops at start-up; newer builds are published only as `latest`).
- Terraform: `Azure/avm-res-app-containerapp/azurerm` 0.9.0; providers as in `foundation` (`azurerm` 4.81.0, `azapi` 2.13.0; `modtm` 0.4.0 and `random` 3.9.1 through the module), plus `hashicorp/time` 0.14.2 for the wait after the AcrPull assignment.

**Decisions made while building:**

- `/api/me` and `/api/health` have no model in `packages/contracts`, and that package may not change, so their two small response models live in `web/adapters/http/api.py` (built on `ContractModel` and `DemoRole`). The SPA's `Me` type is written by hand in `src/api/client.ts` from the generated `DemoRole`.
- Every `/api` route is added to the `role_checked` router, which refuses a call without a valid role; health is the one exemption, and a test walks the real app's routes to hold that. No underwriter-only or customer-only route exists yet, so the 403 row is tested through the dependency such a route will add (`role_for(RouteGroup.UNDERWRITER)`) on a route defined in the test.
- The error catalogue fixes one status per code and has no code for "method not allowed". Under `/api` a wrong method is answered as `not_found` (404), elsewhere as `validation_failed` (422); a 405 is never returned.
- Only paths under `assets/` are files that must exist. Any other path that is not a file gets the SPA's page, so a client route may contain a dot.
- An unhandled error is logged and put on the active span as its type and code locations only. `span.record_exception` is not used, because it stores the error's message, which can hold input values (security rule 31).
- Telemetry's service name and the health-route exclusion are set in code (`web/adapters/telemetry.py`), so they hold wherever the service runs; `infra/demo/app/main.tf` sets no OpenTelemetry variable.
- Without telemetry there is no span, so an error's `trace_id` is the caller's `traceparent` trace id if one was sent, otherwise the all-zero "no trace" id.
- The drift check has two halves: `uv run pytest` fails when `services/web/spa/src/api/contracts.schema.json` differs from the models, and `npm run contracts:check` fails when `contracts.gen.ts` differs from that schema. CI runs both.
- The Application Insights connection string reaches the container as a Container Apps secret, not a plain environment value.
- `deploy.yml` reads the registry from the `foundation` stack's state output. Its destroy-or-replace guard lets one entry through: the module's `azapi_resource_action`, which the plan shows as replaced on every new image and which deletes nothing in Azure.

**Left for others:**

- `.github/workflows/infra-pr.yml` checks `infra/demo/app` (format, init without a backend, validate) but does not plan it: its plan needs the `foundation` state.
- `docs/standards/azure.md` still calls its runtime-roles table a placeholder "until the `app` stack assigns them (story 1.3)". The `app` stack now assigns the two `web` roles; the file was not edited because another task had it open.

**Not verified:**

- `./tools/dev.sh` end to end. The Dapr runtime is not installed on the build machine (`dapr init` has not been run, and it installs outside the project), so the script stops with its "run `dapr init`" message. The run file was accepted by the CLI's validation; the containers, the SPA build and `python -m web` were each run on their own.
- Anything in Azure: no plan of `infra/demo/app` (it needs the `foundation` state) and no run of `deploy.yml`. Both files pass their linters (`terraform validate`, `actionlint`) only; in particular the 60-second wait, the revision check (`az resource show` on the app and its revision) and the plan guard have never met a real plan.
- The checks ran on Node.js 22.19.0, not the 24.21.0 that CI and the image use; npm warned that `jsdom` and `react-router` ask for 22.22 or later.


### Proven locally with the real Dapr runtime (2026-10-07, main session, after the owner ran `dapr init`)

- `./tools/dev.sh` started the containers, ran the migrations, built the SPA and started `web`, `intake` and `workflow` with their sidecars (Dapr runtime 1.18.4). `/api/health` 200, `intake` and `workflow` `/ready` 200, `/` served the SPA as HTML, `/api/me` answered through the same origin.
- Through the sidecars: an upload of `data/cases/case-002.pdf` answered 201; a retry with the same idempotency key returned the same case; a 6.3 MB PDF answered 201 (so the 16 MB sidecar limit holds above Dapr's 4 MB default); an 11 MB file answered 413 `file_too_large`; start answered 200 `running` and a repeat gave the same answer; a customer start with options answered 403; progress answered 200 with no pages; audit answered 403 for the customer and 200 for the underwriter; an underwriter upload answered 403.
- Still unproven: the same path in the deployed Azure environment.


### Proven in the deployed Azure environment (2026-10-07, test session 3, main session as operator)

- Foundation brought up in one apply (1 imported, 103 added, 11 minutes 21 seconds).
- The database setup in `infra/bootstrap/README.md` sections 4 and 5 was extracted and run unchanged: both exited 0, the temporary firewall rule was gone after each, and the service role's rights on the `workflow` tables were `audit_event` INSERT and SELECT, `alembic_version` SELECT, `case_status` and `page_status` INSERT, SELECT and UPDATE.
- The three images were built in the registry with `az acr build` at commit `d55b526`; the `app` stack applied in 2 minutes 18 seconds (33 added). The role assignments were accepted. All three revisions became ready; only `web` has external ingress.
- Against the public address: `/api/health` 200; `/` 200 as HTML with the security headers; `/api/me` 200 with a role and 400 `invalid_role` without one, carrying a real trace id; an upload of a synthetic case PDF 201 in 2.3 s; the same idempotency key returned the same case; a 6 MB PDF 201 in 3.5 s; start 200 `running`, twice; progress 200; audit 200 for the underwriter and 403 for the customer.
- After 8 idle minutes `web` had scaled to zero and `intake` still had one replica. The first `/api/health` then took 30 s and an upload 32 s. So a cold start costs about half a minute, and whether a Dapr call wakes a service that has scaled to zero is still unanswered, because `intake` did not scale down in that time.
- Not done in this session: the deploy workflow itself (it runs only from `main`); the same steps were run by hand from the branch. The blob count in `originals` could not be read, because the operator has no data-plane role on the storage account.
- The tear-down of both stacks was started at the end of the session and was still running when this was written (the `app` stack was already destroyed); the next session confirms the resource group is empty.

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | The `app` stack gets no pull-request check (blind, gap) | medium | `infra-pr.yml` matrix lists only `demo/foundation`. A plan needs the foundation's remote state, which is absent while the environment is torn down, so the fix is format and validate only | patch |
| 2 | `deploy.yml` has no destroy or replace guard, does not check it is deploying current `main`, and its smoke check passes on the old revision or an image with no SPA (all three) | medium | `terraform.md` rule 33; health answers 200 without the SPA (`test_spa_host.py`) | patch |
| 3 | CI never builds the container image (blind, gap) | medium | A broken Dockerfile is first seen at deploy | patch |
| 4 | Nothing enforces that every `/api` route checks the role (blind) | medium | Each route opts in by hand; a later route without the dependency is open | patch |
| 5 | SPA: a 200 with a non-JSON body unmounts the app; no request timeout; no error boundary; choosing a role on a deep link, or a role change from another tab, lands on "Page not found"; an error code equal to an object prototype key renders a function (blind, edge) | medium | Read from `client.ts`, `ChooseRole.tsx`, `roleStore.ts`, `ErrorMessage.tsx` | patch |
| 6 | SPA host: encoded null byte or over-long path gives 500; HEAD requests fail; assets have no cache header and `/api` no `no-store` (blind, edge) | medium | `resolve()` and `is_file()` raise; `router.get` adds no HEAD route | patch |
| 7 | Unhandled errors log only the exception type, are not recorded on the span, and logging is never configured (blind) | medium | Nothing to debug with; `security.md` rule 31 forbids values, not locations | patch |
| 8 | Telemetry has no service name and traces every health probe against a 0.5 GB daily cap (blind) | medium | Seven services would share one unknown role name | patch |
| 9 | Empty connection string or client id is treated as set (edge) | low | Direct correction | patch |
| 10 | Test gaps: Terraform's variable names against `Settings`; middleware wiring on the real app; trace id from an active span; per-code error text and hidden all-zero reference; bare `/api` and `/index.html` cache header; `X-Frame-Options` and three CSP directives; a vacuous assertion (gap, blind) | medium | Filed with evidence | patch |
| 11 | `app` stack: AcrPull may not have propagated at first pull; request-size variable unvalidated; registry name hard-coded in `deploy.yml` (blind, edge) | medium | Read from `main.tf`, `variables.tf`, `deploy.yml` | patch |
| 12 | Local run: `dev.sh` does not check `dapr init`; scheduler has no healthcheck; README overstates and omits the deploy prerequisites (blind) | low | Direct corrections | patch |
| 13 | `Me` and `Health` are hand-written on both sides, outside contracts (all three) | medium | True; adding them to contracts adds public surface, which this spec forbade | defer to story 1.5 |
| 14 | Framework 405, 413, 415 and 429 are all answered as 422 `validation_failed` (blind, edge) | medium | True; the error catalogue has no fitting codes | defer to story 1.5 (contracts change) |
| 15 | `deploy.yml` applies a plan nobody reviewed (blind) | medium | True; covered for the first build by the recorded exception to `terraform.md` rules 26 and 33 | defer |
| 16 | Base images pinned by tag, not digest; no image vulnerability scan (blind) | low | True; not required by the standards | reject |
| 17 | Node `engines` allows 22 while CI uses 24 (blind) | low | The owner's machine has Node 22.19, so tightening would break local work | reject |
| 18 | `setItem` failing while `getItem` returns the old role; starlette test-client deprecation warning; schema script without `$defs` (edge, gap) | low | Not met in normal use | reject |

## Design Notes

- The Azure foundation (story 1.2) is written but not applied: its bootstrap needs the owner. This story therefore proves the service locally and in a local container, and delivers the `app` stack and deploy workflow unapplied. The first real deploy is a later step.
- The owner's rule (2026-10-06): the Azure environment is alive only while it is being tested. Nothing in this story may leave anything running in Azure.
- There are no cookies, so cross-site request forgery has nothing to ride on; the required `X-Demo-Role` header is the custom header `security.md` rule 24 asks for.
- Use the Terraform binary at `.work/bin/terraform` (1.16.5, the version the foundation stack pins) if the system one is older.
- Other work is patching `infra/demo/foundation`, `infra/bootstrap` and `.github/workflows/infra-pr.yml` in the same working tree: do not edit those, and do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice.
- Approval: Darrel authorised unattended work on stories 1.1 to 1.6 on 2026-10-06; this spec was not reviewed by him before implementation, and it is over the 1,600-token target because the story bundles the service, the SPA and its deployment.

## Verification

**Commands:**
- `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa ci && npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa test -- --run && npm --prefix services/web/spa run build` -- expected: all pass
- `docker build -f services/web/Dockerfile -t aiuw-web:dev . && docker run --rm -d -p 18080:8000 --name aiuw-web-dev aiuw-web:dev`, then `curl -sf localhost:18080/api/health`, then `docker rm -f aiuw-web-dev` -- expected: 200
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean (after `init -backend=false`)
