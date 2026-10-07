---
title: 'Story 1.6: Case lifecycle and audit trail'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '11a768ab7e82c98723c6d7c786eb17e240a80124'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An uploaded case goes nowhere: nothing drives it through a fixed sequence, and nothing records who or what did each step. Later stages (redaction, classification, decisions) need one place that sequences them and one trail that proves the order.

**Approach:** Build the `workflow` service: one durable orchestration per case on Azure Durable Task Scheduler, the append-only audit table, and the case and page status it reports. `web` starts the case after an upload and reads its progress and audit trail. Stage results and human decisions are recorded through one path that later stories plug their stages into.

## Boundaries & Constraints

**Always:**
- Spine AD-2, AD-5, AD-6, AD-8: only `workflow` sequences stages; one orchestration per case with the `case_id` as its instance id, on Durable Task Scheduler through `durabletask-azuremanaged` 1.11.0; orchestrator code holds only sequencing and is deterministic; each activity is one call to a stage service.
- Starting a case is idempotent on `case_id`: a repeat changes nothing and returns the same answer.
- `workflow.audit_event` is the only audit table and is append-only: the service's database role has no UPDATE or DELETE on it, and no code path issues one. A row is unique on `case_id`, `page_id` (null counts as one value), `action` and `ref`, so a retried activity inserts nothing. Each row is written in the same transaction as the status change it reports.
- Audit records, statuses, payloads and errors come from `packages/contracts`. A contracts change is made in the package, with tests, regenerated SPA types and every caller updated in this change.
- `workflow` follows the pattern of `services/intake`: `domain/`, `adapters/`, `settings.py` with the `WORKFLOW_` prefix, SQLAlchemy with bound parameters, one Alembic environment with its version table in schema `workflow`, readiness tied to the schema revision, timeouts on database calls, tests against a real PostgreSQL and the scheduler emulator from `compose.yaml`.
- An upload is safe to retry: the browser sends an idempotency key with each upload, and a repeat with the same key returns the first case instead of creating a second (deferred item from story 1.5).
- Logs carry ids, codes and timings only.

**Never:**
- No redaction, classification, extraction or verdict stage, no gate routing and no human-decision routes: those arrive with stories 1.7 to 1.11. The orchestration built here starts the case and stops at the point where story 1.7 adds the first stage.
- No audit trail screen (story 1.12). No Dapr SDK. No business rules in `web`.
- Do not apply or plan Terraform, run the bootstrap, or run any `az` or `gh` command that changes Azure or the repository. Do not edit `infra/demo/foundation`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Start a case | `web` calls start for a case that `intake` created | One orchestration with instance id `case_id`; case status `running`; answer carries the start parameters that apply | N/A |
| Start twice | The same start again | No second orchestration; the same answer; no new rows | N/A |
| Start with options | `classifier_contender`, `retriever_configs`, `stop_after`, `eval_run_id` given | Stored with the case and returned; defaults from settings when absent | 422 for an invalid value |
| Record a stage result | A done stage result with its audit record reaches the recording path | Status change and audit row written in one transaction | N/A |
| Record it twice | The same result again (activity retry) | No second audit row; status unchanged | N/A |
| Failed stage result | A failed stage result | Case status `failed`; one `stage.failed` audit row with the error code | N/A |
| Recording fails midway | The audit insert fails | The status change is rolled back too | Error surfaced to the orchestration, which retries |
| Read progress | `GET` progress for a known case | Case status and the page list (empty until pages exist) | 404 for an unknown case |
| Read audit | `GET` audit for a known case | Events in time order | 404 for an unknown case |
| Upload then start | Customer uploads through the SPA | The case is started, and the upload screen shows its status by polling progress | If start fails, the screen says the case was received but not started, with a retry |
| Retry an upload | The same idempotency key is sent again | The first case is returned; one case, one original | N/A |
| Tamper with the trail | Any UPDATE or DELETE on `workflow.audit_event` as the service role | Refused by the database | Permission error |

</frozen-after-approval>

## Code Map

- `services/intake/` -- the service pattern to copy in shape (settings, domain ports, SQLAlchemy adapter with timeouts and the Entra token cache, migrations inside the package, readiness, tests with `tests/support`); share nothing by import
- `services/web/src/web/adapters/dapr.py`, `adapters/http/api.py`, `upload.py` -- the one client module and the routes; add start, progress and audit calls
- `services/web/spa/src/screens/UploadDocument.tsx`, `cases/sessionCases.ts`, `api/client.ts` -- upload screen and session list; add the idempotency key, the start call and progress polling
- `packages/contracts/src/contracts/audit.py` (`AuditRecord`, `AuditAction`, `HUMAN_ACTIONS`), `models/workflow.py` (`StartCaseRequest`, `CaseStarted`, `CaseProgress`, `AuditTrail`), `models/_stage.py` (`StageResult`), `operations.py` -- reuse; extend where the matrix needs it
- `_bmad-output/implementation-artifacts/deferred-work.md` -- assigned here: make an upload safe to retry
- `compose.yaml` (the scheduler emulator, pinned by digest), `dapr.yaml`, `tools/dev.sh`, `tools/migrate-local.sh` -- local run
- `infra/demo/app/` -- how `intake` was added; add `workflow` the same way
- Spine: AD-5, AD-6, AD-8, Operations table rows for `workflow`, the role table (`workflow`: Durable Task Data Contributor on the task hub)

## Tasks & Acceptance

**Execution:**
- [x] `packages/contracts` -- whatever start, progress, audit and the upload idempotency key need beyond what exists; tests; exported schema
- [x] `services/workflow/` -- service package: settings; domain (case lifecycle state, the recording rule for stage results, start parameters and their defaults); adapters (HTTP routes for start, progress and audit; repository; Durable Task worker and client; orchestration and its activities; telemetry); migrations (case status, page status, audit table with its unique rule; grants that leave the service role without UPDATE and DELETE on the audit table); health and readiness; `Dockerfile`; tests
- [x] `services/intake/` -- accept the idempotency key on `POST /cases` and return the first case on a repeat; migration for it; tests
- [x] `services/web/` -- start the case after an upload; `GET /api/cases/{case_id}/progress` and `/audit` (underwriter only for audit); pass the idempotency key through; tests
- [x] `services/web/spa/` -- send an idempotency key per upload attempt (the same key on retry), start the case, poll progress for the session's cases, show the received-but-not-started state with a retry; tests
- [x] `compose.yaml`, `dapr.yaml`, `tools/dev.sh`, `tools/migrate-local.sh`, `README.md` -- add `workflow` to the local run and its migration
- [x] `pyproject.toml`, `.github/workflows/ci.yml` -- add the member, coverage, the emulator for integration tests, and the image build
- [x] `infra/demo/app/` -- the `workflow` Container App (internal ingress only, Dapr app id `workflow`, 1 replica minimum, its identity with Durable Task Data Contributor on the task hub, AcrPull and Monitoring Metrics Publisher) and a new section in `infra/bootstrap/README.md` for its database role, leaving existing text untouched

**Acceptance Criteria:**
- Given the local stack with migrations applied and the emulator running, when the integration tests run, then a started case has exactly one orchestration instance whose id is the `case_id`, and starting it again adds none.
- Given a recorded stage result, when the audit trail and the status are read, then every status change has exactly one matching event and events are in time order.
- Given the service's database role, when it attempts UPDATE or DELETE on `workflow.audit_event`, then the database refuses.
- Given the repository, when the checks in Verification run, then all pass.

## Implementation Notes

- **Start.** `POST /cases/{case_id}/start` stores the case in `workflow.case_status` as `running` with the start parameters that apply (`INSERT ... ON CONFLICT DO NOTHING`, then the stored row is the answer), then makes sure the scheduler holds one orchestration whose instance id is the `case_id`. The create carries an id reuse policy with no replaceable status: without it the scheduler replaces a finished instance with a new run (seen on the emulator). A repeat with other options answers with the case as first started.
- **Orchestration.** `case_lifecycle` calls one activity, `confirm_case_started` (a read of the stored case, with the retry policy from the settings), and returns; story 1.7 adds redaction after it. The start route, not the activity, stores the case as `running`: the status enum has no earlier value.
- **Recording path.** `workflow.domain.recording.plan_recording` turns a stage result into one `Recording` (audit record plus status changes); `SqlCaseStore.record` writes it in one transaction, with the audit insert last, after locking the case row and checking for the same event. `Activities.record` is what a stage activity calls. Rules built now: failed result -> case `failed` (and its page, and `redaction_status` for a redaction); done redaction -> `redaction_status` `done` and its pages tracked as `uploaded`, numbered by their place in `page_ids`; done classification -> page `classified`; done extraction -> page `extracted`; done verdict -> audit event only.
- **Append-only.** `workflow` runs as its own database role, locally too (`tools/migrate-local.sh` creates role `workflow`). Migrations run as the schema owner and grant the service role its rights table by table (`WORKFLOW_DATABASE_SERVICE_ROLE`, required): `SELECT, INSERT` on `audit_event`, no `DELETE` anywhere, no default privileges.
- **Error code of a failed stage.** Stored in `workflow.audit_event.error_code`. `AuditRecord` was left as the spine defines it, so `GET .../audit` does not return the code (see deferred work).
- **Contracts.** `UploadedCase` lost `status` (the upload no longer knows one); `contracts.upload` gained `IDEMPOTENCY_KEY_HEADER` and `parse_idempotency_key`.
- **web and SPA.** The upload does not start the case; the caller does, with `POST /api/cases/{case_id}/start` (customer only), so a failed start can be retried without sending the file again and a later caller can pass start options. The SPA keeps only ids in session storage and reads each case's status from `GET /api/cases/{case_id}/progress` every 3 s; 404 there shows "Received, not started" with a retry.
- **Upload retry.** `intake.document.idempotency_key` (nullable, unique; migration `0002`). A repeat with the same key and file returns the first case; the same key with another file is 422; two uploads racing on one key are settled by the unique rule and the loser's original is removed.

- **After review.** Status changes follow one transition table (`domain/transitions.py`), applied in the statement that makes the change; a late, repeated or out-of-order result writes nothing and has its own outcome. The orchestration marks a case failed (case-level `stage.failed`, ref = the case id, actor `workflow:case-lifecycle`) when its first step fails on every retry or answers with an error no retry can mend; a repeat start reports a case whose orchestration is dead as failed. A trigger (migration `0002`) refuses UPDATE, DELETE and TRUNCATE on the audit table for every role, a downgrade is refused while it holds rows, and readiness fails if the connected role could change the trail. The worker starts only once the schema is at the bundled head. Start options are accepted by `web` only from the underwriter role.


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
| 1 | No allowed-transition rule: a late or repeated result can move a page backwards or change a failed case (blind, edge) | high | `plan_recording` and the SQL apply whatever a result says; only an identical (case, page, action, ref) is stopped | patch |
| 2 | A permanently failed orchestration leaves the case `running`; a repeat start on a failed or terminated instance answers `running`; permanent errors are retried five times (blind, edge, implementer) | high | No terminal-failure path in the orchestrator; `_ensure_started` returns for any existing instance | patch |
| 3 | A second redaction result for the same pages fails on every retry with an integrity error; an audit record naming another page than its result is accepted; `detail` is stored as JSON null (blind, edge) | medium | Plain INSERT of pages; no check ties `audit.page_id` to the result | patch |
| 4 | Service start and stop: shutdown is not exception-safe; a cancelled or closed loop escapes the activity bridge; the worker starts even when the schema is not at head (blind, edge) | medium | `lifespan` has no `try/finally`; `run_coroutine_threadsafe` sits outside the `try` | patch |
| 5 | The scheduler library logs full error text at warning and error (blind, implementer) | medium | Breaks `security.md` rule 31 for this adapter | patch |
| 6 | Append-only is only a grant: the owner can change history, a downgrade drops the trail, and a service role equal to the owner would not be refused (blind, edge) | medium | No trigger; `downgrade()` drops `audit_event`; nothing checks the running role's privileges | patch |
| 7 | Entra token refreshes are not serialised and can still block the loop on a stale token; worker concurrency and pool size are unset (blind) | medium | SDK default is 100 x CPU activities against a pool of 15 | patch |
| 8 | The customer role may set every start option, including `eval_run_id` and `stop_after` (rest) | medium | `POST /api/cases/{id}/start` passes the body through; the first start's options are kept for good | patch |
| 9 | `intake`: clean-up after losing the key race is not shielded; the deadline is spent twice (rest) | medium | Read from `upload.py` | patch |
| 10 | SPA: overlapping progress reads apply in arrival order; a first read that fails stays on "Checking…"; polling never backs off or pauses; the idempotency key is lost on reload; `crypto.randomUUID` assumed; only `case_status` is checked on answers (rest) | medium | Read from `caseProgress.ts`, `UploadDocument.tsx`, `client.ts` | patch |
| 11 | `web`'s timeout for start and progress equals the browser's (rest) | low | Direct correction, as on the upload path | patch |
| 12 | Test gaps: the real repository's duplicate-key mapping; the scheduler's refusal of a second create; failure branches after losing the race; the "received" banner (gap) | medium | Filed with evidence; two depend on thread timing today | patch |
| 13 | Bootstrap README: no upgrade path for an environment already migrated; section 5 step 1 cannot be rerun; ownership is handed over from a fixed table list (rest) | medium | None of it has been run | patch |
| 14 | Any well-formed case id can be started; `workflow` cannot ask `intake` whether the case exists (blind, rest, implementer) | medium | True; the first stage (story 1.7) calls `intake`, and with finding 2 fixed an unknown case then fails | defer to story 1.7 |
| 15 | The stage error code and record time are stored but not in the audit API (blind, implementer) | medium | True; the spine's audit record has no field for them | defer to story 1.12 |
| 16 | No versioning plan for changing the orchestrator body with cases in flight; audit and progress reads are unbounded (blind) | medium | True; matters when story 1.7 adds the first stage | defer to story 1.7 |
| 17 | Two workers serve the task hub during a rollout; a stored but unscheduled case has no sweep; routes have no caller check beyond Dapr (blind, rest) | maybe-false | Need a deployed environment to observe; medium if true | defer to the first deploy session |
| 18 | Readiness requires the schema revision to equal the bundled head, so migrating first takes the old replica out (blind, rest) | low | Required as written by `azure.md` rule 22 | reject |
| 19 | A tab running the previous bundle breaks when `status` leaves `UploadedCase` (rest) | low | Nothing is deployed and no tab outlives a local run | reject |
| 20 | The emulator's healthcheck proves only that its port is open; images pinned by tag (rest, blind) | low | No flake observed in three local runs | reject |
| 21 | The recording path has no production caller yet (gap) | false | Intended by this spec: story 1.7 adds the first stage that calls it | reject |

## Design Notes

- The orchestration is deliberately short in this story: it marks the case running and reaches the extension point where story 1.7 adds redaction as the first stage. The recording path for stage results is built and tested now with contract-shaped results, so later stories only add activities.
- The Azure environment is alive only while it is being tested (owner's rule) and is currently torn down: this story is proven locally. `dapr init` has not been run on this machine and installs outside the project, so do not run it; calls through Dapr are covered with a fake sidecar in tests.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice.
- Approval: Darrel authorised unattended work on stories 1.1 to 1.6 on 2026-10-06; this spec was not reviewed by him before implementation, and it is over the 1,600-token target because the story spans three services and the SPA.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa ci && npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run` -- expected: all pass
- `docker build -f services/workflow/Dockerfile -t aiuw-workflow:dev .` -- expected: builds
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
