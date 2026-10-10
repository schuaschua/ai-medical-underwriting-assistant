---
title: 'Story 1.5: Upload a document and see the case'
type: 'feature'
created: '2026-10-06'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '86d8c075f5d05b87dc41a96dc1a9cf053e7259df'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A customer has no way to hand a document to the system. Nothing accepts a PDF, stores it, or records that a case exists.

**Approach:** Build the `intake` service with its own database schema and the upload operation, which stores the original PDF and creates the case and document records. Add the `web` route and the customer's upload screen in the SPA, so an upload shows the new case. The original is stored and never served.

## Boundaries & Constraints

**Always:**
- Spine AD-2, AD-3, AD-4, AD-21: `web` calls `intake` through Dapr service invocation over HTTP with `httpx` from one client module (no Dapr SDK); `intake` owns schema `intake` and Blob containers `originals` and `cases`; the original goes to `originals` under the case's prefix; no API of any service returns the original file.
- An upload is a PDF of at most 10 MB. Size and type are checked on the server, in both `web` and `intake`, by content (the PDF header), not by file name alone.
- Payloads, ids (UUIDv7), enums and the error shape come from `packages/contracts`. A contracts change is made in the package, with its tests, the regenerated SPA types and every caller updated in this same change.
- `intake` follows the spine layout (`domain/`, `adapters/`, `settings.py` with the `INTAKE_` prefix), SQLAlchemy 2.1.3 with bound parameters, psycopg 3.3.6, one Alembic 1.20.0 environment with its version table in schema `intake`. Migrations never run at start-up; readiness fails unless the schema revision equals the head bundled with the service.
- Storage and database access use `azure-identity` in Azure. Locally the service runs against the containers in `compose.yaml`.
- Tests: unit tests use fakes; integration tests use a real PostgreSQL in a container and a local blob emulator; no test calls Azure. Tests are named for story 1.5.
- Logs carry ids, codes and timings only: never the file name, file contents or any text from the document.

**Never:**
- No redaction, page split, page text, thumbnails or workflow start: `POST /cases` stores the original only (those arrive with stories 1.6 and 1.7). No route, in any service, that returns the original PDF.
- No sign-in or owner scoping (AD-9). No business rules in `web`.
- Do not apply or plan Terraform, run the bootstrap, or run any `az` or `gh` command that changes Azure or the repository. Do not edit `infra/demo/foundation` or `infra/bootstrap`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Upload | Customer role, a valid PDF under 10 MB | 201 with `case_id` and `document_id`; original stored in `originals`; case and document rows exist | N/A |
| Upload screen | Customer picks a synthetic case PDF and submits | The screen lists the new case with status `running` | N/A |
| Too large | A PDF over 10 MB | 413, nothing stored, no rows | Error body; plain message on screen |
| Not a PDF | A text file, or a file named `.pdf` without a PDF header | 415, nothing stored, no rows | Error body; plain message on screen |
| Empty body | Zero bytes | 422, nothing stored | Error body |
| Wrong role | Underwriter calls the upload route | 403 `role_not_allowed` | Error body |
| Storage fails | Blob write fails after validation | 5xx in the error shape; no case or document row remains | Roll back |
| Database fails | Row insert fails after the blob was written | 5xx; the stored blob is removed or left unreferenced under a prefix no row points to, and the outcome is logged by id | Compensate |
| Ask for the original | Any request to any service for the uploaded file | No route exists; 404 | N/A |
| Wrong method | `PUT /api/cases` | 405 in the error shape | Error body |

</frozen-after-approval>

## Code Map

- `services/web/` -- the pattern to follow for a service: `settings.py`, `domain/`, `adapters/http/` (app factory, role-checked router, error handlers, middleware), `adapters/telemetry.py`, tests, `Dockerfile`. Reuse its shape; share nothing by import (AD-20: no service imports another service's code)
- `services/web/src/web/adapters/http/api.py`, `errors.py` -- where the upload route and the new error mappings go
- `services/web/spa/src/` -- `api/client.ts` (the one client), `strings.ts`, `screens/`, role-aware navigation; `npm run contracts:generate` regenerates the types
- `packages/contracts/src/contracts/models/intake.py` (`CaseCreated` or equivalent), `operations.py` (`create_case`), `errors.py`, `enums.py` (`CaseStatus`) -- reuse; extend per the deferred items below
- `_bmad-output/implementation-artifacts/deferred-work.md` -- assigned here: move `Me` and `Health` into contracts; add error codes for method not allowed, payload too large, unsupported media type and too many requests
- `compose.yaml`, `dapr.yaml`, `tools/dev.sh` -- local run; add `intake` and a blob emulator
- `data/cases/*.pdf` -- synthetic PDFs for tests (never the answer key)
- Spine: Operations table (`POST /cases`), AD-3 (16 MB sidecar request limit for `web` and `intake`), Consistency Conventions (database, configuration, errors)

## Tasks & Acceptance

**Execution:**
- [ ] `packages/contracts` -- add `Me` and `Health`, the four error codes with their HTTP statuses, and anything `POST /cases` needs; update tests and the exported schema
- [ ] `services/intake/` -- service package: settings, domain (upload rules, case and document entities), adapters (HTTP route `POST /cases`, SQLAlchemy repository, blob store, telemetry), `migrations/` with the first revision (case and document tables only), health and readiness routes, `Dockerfile`, tests
- [ ] `services/web/` -- `POST /api/cases` for the customer role, streaming the file to `intake` through one Dapr client module; map 405, 413, 415 and 429 to their new codes; use the contracts `Me` and `Health`
- [ ] `services/web/spa/` -- upload screen for the customer (file picker, submit, progress, plain errors) and a list of cases uploaded in this browser session with their status; regenerate types; tests
- [ ] `compose.yaml`, `dapr.yaml`, `tools/dev.sh`, `README.md` -- add `intake`, the blob emulator and a documented way to run the migration locally
- [ ] `pyproject.toml`, `.github/workflows/ci.yml` -- add the `intake` member and coverage; give CI a PostgreSQL service and the blob emulator for the integration tests; build the `intake` image in the image job
- [ ] `infra/demo/app/` -- add the `intake` Container App (internal ingress only, Dapr app id `intake`, its identity with Storage Blob Data Contributor on `originals` and `cases`, AcrPull and Monitoring Metrics Publisher), raise the Dapr request size limit on `web` and `intake` to 16 MB, and document the database role bootstrap for `intake` in `infra/bootstrap/README.md` only by adding a section (do not change existing text)

**Acceptance Criteria:**
- Given the local stack from `compose.yaml` with the migration applied, when the integration tests run, then an uploaded synthetic PDF is found byte-for-byte in the `originals` emulator container and the case and document rows exist in schema `intake`.
- Given the route tables of `web` and `intake`, when they are listed, then no route returns a document file.
- Given the `intake` service started against a database whose revision is behind, when readiness is checked, then it fails.
- Given the repository, when the checks in Verification run, then all pass.

## Implementation Notes


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
| 1 | A database error after the commit reached the server deletes an original that rows reference; a cancelled request leaves an orphan with no log line (blind, edge) | high | `create_case` removes the blob on any `Exception` from the insert and does not catch cancellation | patch |
| 2 | No database connect, statement or pool timeouts; the Entra token is fetched synchronously on the event loop (blind, edge) | medium | `/ready` and uploads would hang; `/health` would miss its probe timeout | patch |
| 3 | `/ready` reports a permission error on the version table as "no migration has run" (blind, edge) | medium | Every `ProgrammingError` is treated as a missing table | patch |
| 4 | Database bootstrap instructions: grants run before the schema exists, no error stop, firewall clean-up not guaranteed, the service role may write its own version table, default-privilege statement likely refused (blind, edge) | medium | Read from `infra/bootstrap/README.md` section 4; none of it has been run | patch |
| 5 | CI's "not ready" check passes on any non-2xx, including a failed request; log step stops at the first error (blind, edge) | medium | `curl` without an exact status check | patch |
| 6 | Test doubles ship inside the `intake` package (blind) | low | `adapters/memory.py` is under `src/` and in the image | patch |
| 7 | Nothing checks the SQLAlchemy tables against the migrations; autogenerate would see other services' schemas (blind) | medium | The schema is written twice by hand | patch |
| 8 | Nothing runs the documented migration command or the Entra branch of the migration environment (gap) | medium | Filed with evidence | patch |
| 9 | `web` upload path: an over-long or malformed `Content-Length` gives 500; a non-ASCII `traceparent` gives 500 and a malformed one is forwarded; a declared length that differs from the body is forwarded; a 2xx other than 201 with a valid body is treated as a refusal; an upstream code is passed on without checking its status; upstream 5xx is logged as a refusal (supplementary) | medium | Several reproduced by the reviewer against `create_app` | patch |
| 10 | Timeouts: the browser's 120 s equals `web`'s per-phase 120 s, so the browser can give up first; `intake` has no deadline shorter than `web`'s; the upload timeout is not asserted (edge, gap, supplementary) | medium | The customer can be told to retry while the case is being created | patch |
| 11 | SPA: a 201 without the expected shape is stored and rendered; an old message stays beside a newly chosen file; the double-submit guard and storage failures are untested (supplementary) | low | Direct corrections and tests | patch |
| 12 | `intake` accepts both blob settings at once; integration tests share one emulator container; refusal-code tests cover three of five codes; "10 MB" is typed in three places (edge, supplementary) | low | Direct corrections | patch |
| 13 | Deploy checks only `web`'s revision; blob role assignments have no propagation wait (blind, gap) | medium | A broken `intake` rollout would leave the run green | patch |
| 14 | SPA strings: the global `validation_failed` text is upload-specific; `method_not_allowed` has none (blind) | low | Direct correction | patch |
| 15 | No idempotency on `POST /cases`: a retry after a timeout creates a second case (blind, supplementary) | medium | True; an idempotency key is new public surface | defer to story 1.6 |
| 16 | No clean-up of unreferenced originals, and no retention or deletion rule (blind) | medium | True; needs a sweeper or lifecycle rule and an owner decision on retention | defer |
| 17 | Deploy has no migration step; `intake` stays not ready in Azure until the operator runs the database bootstrap (implementer, edge, gap) | medium | True; `terraform.md` rule 36 wants it in the pipeline | defer to the first deploy session |
| 18 | An upload over 4 MB has never passed through real Dapr sidecars; whether a Dapr call wakes a service scaled to zero is unknown; internal ingress may not be needed (gap, blind, edge) | maybe-false | Needs a Dapr runtime locally or a deployed environment; medium if true | defer to the first deploy session |
| 19 | 10 MB is counted as 10 x 1024 x 1024 bytes, which may exceed Azure AI Language's limit (implementer) | maybe-false | Needs the redaction service's documented limit; low if true | defer to story 1.7 |
| 20 | `errors.py`, `middleware.py`, `telemetry.py` are near-identical in `web` and `intake` (blind) | low | Intended: AD-20 allows no shared code but contracts | reject |
| 21 | Only the first five bytes decide a PDF; a bare header is accepted (supplementary) | low | Intended by the spec ("by content (the PDF header)"); a corrupt file fails at redaction | reject |
| 22 | Images pinned by tag; no `HEALTHCHECK`; Azurite version-check flag; no schema `CHECK` constraints; four error codes for two outcomes (blind) | low | No named harm today | reject |
| 23 | The first review diff left out nine new files (all reviewers) | false | A mistake in preparing the diff, not in the change; those files were reviewed in a supplementary pass | reject |

## Design Notes

- Status on the upload screen: the workflow service does not exist until story 1.6, so `web` reports a newly created case as `running`, which is what the lifecycle will report once it starts the case.
- `infra/bootstrap/README.md` is otherwise owned by story 1.2; add one new section and leave the rest untouched.
- The Azure environment is alive only while it is being tested (owner's rule, 2026-10-06) and is being cycled by the main session right now: this story is proven locally. `dapr init` has not been run on this machine and installs outside the project, so do not run it; calls through Dapr are covered with a fake sidecar in tests.
- Other work may touch `infra/demo/foundation` in the same working tree: do not edit it, and do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice: plain default styling, strings in the one strings module.
- Approval: Darrel authorised unattended work on stories 1.1 to 1.6 on 2026-10-06; this spec was not reviewed by him before implementation, and it is over the 1,600-token target because the story spans two services and the SPA.

## Verification

**Commands:**
- `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov` -- expected: all pass, coverage at least 80% (integration tests need `docker compose up --wait` first)
- `npm --prefix services/web/spa ci && npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run` -- expected: all pass
- `docker build -f services/intake/Dockerfile -t aiuw-intake:dev .` -- expected: builds
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
