---
title: 'Story 1.8: Page classification with a confidence and a reason'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '7ab21ba00951e1519a2558da31c2baf1e0b4124b'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
  - '{project-root}/docs/standards/azure.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** After redaction a case's pages sit at `uploaded`: nothing says what each page is, so non-medical and doubtful pages cannot be caught before extraction.

**Approach:** Build the `classification` service with the `llm` contender, and have `workflow` command it for every page once redaction is done. Each page gets a page type, medical or not (from the one mapping), a confidence that is the agreement rate across repeated model runs, and a one-line reason. Because the Azure environment stays down while coding, the model is reached through one gateway that is proven against a gateway stub in tests and a local stand-in in the local run.

## Boundaries & Constraints

**Always:**
- Spine AD-13, AD-16, AD-6, AD-7, AD-8, AD-3, AD-4. `classification` returns `page_type`, `is_medical`, `confidence`, `reason` and `contender`, and never routes. `is_medical` comes only from `contracts.rules.is_medical`.
- `POST /classifications` is keyed on `case_id` + `page_id` + `contender`: a key row is inserted as `running` before any work; a repeat while running answers 409 `in_progress`; a repeat after the end answers the stored result and makes no model call. The stage enforces its own 180-second deadline and then stores a `failed` result. A stale `running` row is settled the way `intake` settles one.
- The page is read from `intake` through Dapr: its text and its image (the thumbnail), one page at a time. Nothing but the redacted reading is used.
- The model is called through one gateway module: the shared chat deployment on the Foundry account, managed identity, deployment name and endpoint as settings; a 429 or 5xx is retried up to three times honouring `Retry-After`, then `model_unavailable`; each call has its own span. The prompt lives in `services/classification/src/classification/prompts/` (or the package's equivalent) under version control.
- Every model response is parsed into `ClassifierOutput`. A response that fails validation is never passed on: the stage stores a `failed` result with `invalid_model_output`.
- Confidence is the share of runs that agree with the most frequent `page_type`; the number of runs is a setting (default 5). The reason is one of the agreeing runs' reasons. A tie is settled by a stated, deterministic rule.
- The audit record of a done result is `page.classified`, actor `classification:<chat deployment name>`, ref the `classification_id`, detail null.
- `workflow` classifies with the contender the case was started with, records each result through the one recording path, and a failed classification fails the case (the rule recorded in story 1.6).
- Logs carry ids, codes, counts and timings only: never page text, the reason, or the model's output.
- The service follows the pattern of `services/intake`: `domain/`, `adapters/`, `settings.py` with the `CLASSIFICATION_` prefix, its own schema and Alembic environment, readiness tied to the schema revision, tests named for the story.

**Never:**
- No gate routing, no `awaiting_*` statuses, no decisions (stories 1.9 to 1.11). After classification the orchestration ends for now with the pages `classified` and the case `running`.
- No `doc-intelligence` contender (story 4.2): a command naming it is refused as not available, and the case fails.
- No `web` route or SPA change for classifications: each arrives with the story whose screen needs it.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something. Do not edit `infra/demo/foundation`. No stand-in ships in a service image.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Classify a page | Command for a redacted page, contender `llm` | Stored classification with `page_type`, `is_medical`, `confidence` in 0..1, `reason`, `contender`; result `done`; `page.classified` event; page status `classified` | N/A |
| Runs agree | 5 runs, 5 say `lab_report` | `confidence` 1.0 | N/A |
| Runs differ | 5 runs, 3 `invoice`, 2 `other` | `page_type` `invoice`, `confidence` 0.6, `is_medical` false | N/A |
| Repeat after the end | Same command again | The stored result; no model call | N/A |
| Repeat while running | Same command in flight | 409 `in_progress`; `workflow` retries | N/A |
| Invalid model output | A run returns an unknown page type, extra text or no JSON | Result `failed` with `invalid_model_output`; no classification stored or passed on | Case fails with a `stage.failed` event for the page |
| Model unavailable | 429 or 5xx on every retry | Result `failed` with `model_unavailable` | As above |
| Deadline passes | No result within 180 s | Result `failed` with `stage_timeout` | As above |
| Unknown page, or page of another case | Command names a page `intake` does not hold for that case | 404 `not_found`; not retried; the case fails | N/A |
| Contender not built | Command with `doc-intelligence` | 422 `validation_failed`; not retried; the case fails | N/A |
| Whole case | A case of several pages after redaction | Every page classified, one command per page; one `page.classified` event per page naming service and model deployment | N/A |
| Read classifications | `GET /cases/{case_id}/classifications` | The case's stored classifications | Empty list for a case with none |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/models/classification.py` -- `ClassifierOutput`, `ClassifyCommand`, `Classification`, `ClassificationResult`, `ClassificationList` exist; reuse. `operations.py` -- `classify_page`, `list_classifications`, and the `intake` reads `read_page_text`, `read_page_thumbnail`, `list_pages`. `rules.py` -- `is_medical`. `audit.py` -- `ai_actor`. `errors.py` -- `INVALID_MODEL_OUTPUT`, `MODEL_UNAVAILABLE`, `STAGE_TIMEOUT`, `IN_PROGRESS`
- `services/intake/` -- the service pattern to copy in shape, sharing nothing by import: `settings.py`, `domain/redaction.py` (key row, deadline, stale row, stored result with its audit record, shielded failure path), `adapters/redaction_db.py`, `adapters/db.py` (Entra token, spans), `adapters/http/` (app factory, error handlers, middleware, probes), `migrations/`, `Dockerfile`, `tests/support`
- `services/workflow/src/workflow/adapters/dapr.py` -- `StageClient`: add the classify command (and `_STAGE_ANSWERS` already passes `in_progress`, `not_found`, `validation_failed`); the shape `classification` copies for its own client to `intake`
- `services/workflow/src/workflow/adapters/orchestration.py` -- extension point after redaction; `adapters/scheduler.py` -- `redact_document` is the model for a stage activity (`_run` with the stage timeout, `Activities.record`, refused against failed); the redaction answer must hand the page ids on to the orchestration (ids only, AD-6)
- `services/workflow/src/workflow/domain/recording.py`, `transitions.py` -- a done classification moves its page to `classified`; a failed page stage fails the page and the case. Already built; confirm against the real stage
- `packages/synthdata/src/synthdata/language_standin.py`, its tests and `tests/conftest.py` -- the model of a dev-only stand-in and of tests that run a real service against one (story 1.4's guard: nothing under `services/` names `synthdata` or the answer key)
- `infra/demo/app/` -- how `intake` and `workflow` are declared; foundation outputs `foundry_endpoint`, `foundry_project_id`, `foundry_project_endpoints`, `model_deployment_names["chat"]`, `runtime_identities["classification"]`. Roles from `docs/standards/azure.md`: AcrPull, Monitoring Metrics Publisher, Foundry User on the Foundry project
- `infra/bootstrap/README.md` sections 4 and 5 -- the database role steps to mirror in a new section
- `compose.yaml`, `dapr.yaml`, `tools/dev.sh`, `tools/migrate-local.sh`, `README.md`, `pyproject.toml` (workspace member, mypy paths, pytest pythonpath, coverage), `.github/workflows/ci.yml`, `.github/workflows/deploy.yml` (image list)

## Tasks & Acceptance

**Execution:**
- [x] `services/classification/` -- the service: settings; domain (classify operation with key row, deadline and stale-row rule; agreement rule; result with its audit record; the read); ports for the page reader, the model and the repository; adapters (Dapr client to `intake`, model gateway with `openai` 3.24.0 and an Entra token, repository, HTTP routes, telemetry); prompt file; migration `0001`; probes; `Dockerfile`; tests with a gateway stub
- [x] `services/workflow/` -- classify activity and client call; orchestration: after a done redaction, one classify command per page (in parallel), each recorded; a failed, refused or unanswered one ends the case as failed; tests
- [x] `packages/synthdata/` -- a stand-in for the model endpoint for the local run and the cross-service tests: answers the gateway's request shape with a contract-shaped classification derived from the page text, with modes for disagreeing runs, invalid output and 429; tests
- [x] `dapr.yaml`, `tools/dev.sh`, `tools/migrate-local.sh`, `README.md`, `pyproject.toml`, `.github/workflows/ci.yml`, `.github/workflows/deploy.yml` -- the fourth service in the local run, the checks and the image build
- [x] `infra/demo/app/` -- the `classification` Container App (internal ingress, Dapr app id `classification`, one replica, its identity and the three roles, settings for the Foundry endpoint and the chat deployment name) and a new README section for its database role, leaving existing text untouched
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- append the Azure checks this story cannot run (see Design Notes)
- [x] Tests for every matrix row

**Acceptance Criteria:**
- Given the local stack with both stand-ins, when a case from `data/cases/` is uploaded and started, then every page ends `classified`, the audit trail holds one `page.classified` event per page whose actor names `classification` and the model deployment, and `GET /cases/{case_id}/classifications` on the service lists one classification per page.
- Given a stand-in told to return invalid output, when the case is started, then no classification of that page is stored, and the case is `failed` with a `stage.failed` event for the page.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

**Decisions made while building**

- **Agreement rule** (`classification/domain/agreement.py`): the page type most runs named; confidence is that share of the runs. A tie goes to the type that comes first in the contracts' `PageType` order, and the reason is that of the first agreeing run.
- **One bad run fails the page.** An answer that does not parse into `ClassifierOutput`, or a model that is unavailable on any one run, is not out-voted by the other runs: the stage stores `invalid_model_output` or `model_unavailable`.
- **What is checked before the key row.** The contender (anything but `llm` is 422) and that `intake` lists the page for the case (else 404). Neither leaves a key row. A repeat whose key row exists is answered without asking `intake`.
- **Model call:** chat completions on `<model endpoint>/openai/v1/`, the prompt as the system message, the page text and thumbnail in one user message, a strict JSON schema built from `PageType`, and `max_completion_tokens` from a setting (2000). The gateway returns the answer text as it is; the domain parses it. A 4xx other than 408, 409 and 429 is not retried and is stored as `stage_failed`.
- **Table:** one table `classification.classification` holds the key row and, once done, `page_type`, `is_medical` and `confidence`; the reason is kept only inside the stored result JSON. The read lists done rows oldest first.
- **Container App:** held at exactly one replica, so the service's own limit on concurrent model calls is the limit for the environment.
- **The note on `azure.md` rule 27** is in `classification/prompts/__init__.py`, beside `classify_page.md`.

**Earlier tests that changed, and why**

- The lifecycle now runs on from redaction to classification, so tests of stories 1.6 and 1.7 that waited for the lifecycle's end see pages as `classified` and `page.classified` events in the trail: `test_workflow_integration.py`, `test_workflow_redaction_integration.py`, `test_redaction_end_to_end.py`.
- The redaction activity's answer now carries `page_ids`, and a done redaction with no page marks the case failed, so the orchestrator unit tests answer with one page and its classification: `test_workflow_redaction.py`, `test_workflow_orchestration.py`. Their fake context returns the engine's own task type, because the orchestrator waits for several tasks at once.
- `workflow_fakes.py`: `FakeStages` and `SidecarStandIn` answer the classify command as well.

**The `httpx2` dependency**

`openai` 3.24.0 is built on `httpx2`, and starlette's `TestClient` uses `httpx2` whenever it is installed. In the one workspace environment that changed the type of every test client's response. Three existing tests were adjusted for the type checker, with no change to what they test: `services/web/tests/test_lifecycle.py` and `services/intake/tests/test_intake_upload_retry.py` (annotations), `services/web/tests/test_upload.py` (one header passed as bytes pairs). `httpx2==2.13.1` is a declared dependency of `classification` and of the root dev group. Service images are not affected: each holds only its own service's dependencies.

**Local proof (2026-10-07)**

`./tools/dev.sh` with the real Dapr runtime and both stand-ins: `case-002.pdf` uploaded and started through `web` ended with six pages `classified`, six `page.classified` events by `classification:local-stand-in` in one trace with the redaction, and six classifications listed by the service. With the model stand-in restarted in `--mode invalid`, `case-001.pdf` ended `failed` with one page-level `stage.failed` (`invalid_model_output`) and no classification stored. The same two paths run in `packages/synthdata/tests/test_classification_end_to_end.py`, with a transport where the sidecars would be. This run was before the review round below; that round was checked by tests only.

**Changed in the review round**

- **Spans** (all three services with adapters): every adapter span is opened through `adapter_span` in the service's `telemetry.py`, which records no exception message and marks an error by type only; each database engine has `hide_parameters=True`. One test per service.
- **Faults that pass are not stored as failures** (`classify.py`): when `intake` answers `upstream_unavailable` for the page, or the request is cancelled, the key row is released (new repository operation `release`) and the command sent again classifies the page. Storing a done result is tried a second time before the classification ends as failed. A `DomainError` is logged by its code.
- **Gateway** (`adapters/model.py`): a failed Entra token fetch counts as not answered; 408 and 409 are retried; without a `Retry-After` the waits double from `model_retry_seconds` to `model_max_retry_seconds` with a random part; a `Retry-After` that is not a finite number is ignored; one child span per HTTP call; token counts on the run's span and in one log line per run; a process-wide limit on concurrent calls (`model_max_concurrent_calls`, 10).
- **Settings:** a model endpoint with a path, query or fragment is refused; new `model_max_completion_tokens` and `model_max_concurrent_calls`. The `app` stack passes `classifier_max_concurrent_runs`, `model_max_concurrent_calls` and `model_max_retries`.
- **Orchestration:** a done redaction that names no page marks the case failed.
- **Tests added** for the classify activity's stage timeout, a result for another contender, each tuning setting reaching what it configures, and a throttled page with `model_max_retries=1` on the real service.

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Spans record exceptions by default, so a database error puts SQL and its parameters (the model's reason among them) on exported spans (blind) | medium | `start_as_current_span` without `record_exception=False` in `classification/adapters/db.py`, `dapr.py`, `model.py`; no engine sets `hide_parameters`. The same holds in `intake` and `workflow` from earlier stories; fixed together, as one cause | patch |
| 2 | A transient failure after the key row is stored for good: a sidecar blip on the page read, a failed token fetch, a database blip while storing a done result (blind, edge x2) | medium | `classify.py` stores every exception as `failed`; a repeat only returns the stored result, and a failed page fails the case | patch |
| 3 | A cancelled request (a restart or deploy with pages in flight) is stored as `stage_failed` and fails the case (blind) | medium | `except asyncio.CancelledError` settles the row as failed; `workflow` would have repeated the command | patch |
| 4 | The failure log says `reason=DomainError` and loses the code (blind) | low | `_failure_of` falls through to the type name; direct correction | patch |
| 5 | The gateway waits a constant second between retries and gives up after about 3 s of a 180 s deadline; 408 and 409 are terminal; `Retry-After: nan` is accepted (blind, edge) | medium | `ModelGateway.classify` and `_retry_after_seconds`; the chat deployment's capacity is low on purpose, so 429 is likely | patch |
| 6 | No `max_completion_tokens` on the call (blind) | low | `_call` sets none; one setting | patch |
| 7 | Token usage is thrown away: nothing for the rate-limit check or the classifier bake-off's cost per page (blind) | medium | `completion.usage` is not read; FR15 reports cost per page | patch |
| 8 | No service-wide cap on concurrent model calls; a Terraform comment says the replica count bounds them (blind) | medium | The semaphore in `_run_model` is per request; six pages times five runs go out at once | patch |
| 9 | The settings the deferred list says to tune in Azure cannot be set from Terraform (blind) | low | `main.tf` passes only `CLASSIFICATION_CLASSIFIER_RUNS`; direct addition | patch |
| 10 | A model endpoint with a path or query gives a wrong base URL (edge) | low | `model_base_url` appends to whatever is set; direct validation | patch |
| 11 | "Every call has a span of its own" is not true of retries, and a test asserts the looser thing (blind) | low | One span per run in `ModelGateway.classify`; the spec asks for a span per call | patch |
| 12 | A done redaction with no page ids leaves the case `running` for ever (edge) | low | `intake` never answers so (`no_pages` fails the redaction), but the contract allows it; direct guard | patch |
| 13 | Nothing proves the classify activity waits the stage timeout (gap) | medium | Filed with evidence | patch |
| 14 | The client's refusal of a result for another contender is untested (gap) | medium | Filed with evidence | patch |
| 15 | Four settings are not proven to reach what they configure (gap) | medium | Filed with evidence | patch |
| 16 | Five calls resend the same prompt, text and image; one request with `n` would rate-limit the input once (blind) | maybe-false | Depends on whether the deployment takes `n` with structured output and an image; medium if the limit is hit. Settled in the Azure session | defer |
| 17 | `classifier_runs` of 1, or an even number, is accepted (blind) | low | A setting the operator chooses; the default is 5 | reject |
| 18 | Settings gaps: the hang budget against the deadline, concurrency against runs, the deadline against `workflow`'s timeout (blind, edge) | low | A hang ends as `stage_timeout`, which is true; the other two harm nothing named | reject |
| 19 | The page-membership check reads the case's page list once per page (blind) | low | At most 200 ids per read; the fix is a contracts change | reject |
| 20 | Table loose ends: columns written and not read, no CHECKs, two clocks, equality after a round trip, where `EntraToken` lives (blind) | low | No caller diverges today; the `DELETE` grant is needed once finding 3 releases a key row | reject |
| 21 | Stand-in gaps: shared counter for equal pages, kept request bodies, no "429 then succeed" mode (blind) | low | Dev tool; the paths are covered against the gateway stub | reject |
| 22 | Page text is sent whole with no cap; a cut-off answer and a refusal both read as `invalid_model_output`; `Retry-After` as a date is dropped (blind) | low | One PDF page of text; both are failures either way; the fallback wait applies | reject |

## Design Notes

- **Azure checks that go to `deferred-work.md` (final session):** the model call against the real Foundry endpoint with the service identity (endpoint form, API shape, Foundry User on the project being enough); that the deployment accepts the page image and structured output as sent; whether repeated runs really vary (a confidence that is always 1.0 makes the gate's threshold meaningless) and whether the thumbnail is large enough for the model to read; the token rate limit with several pages times the number of runs in parallel; the database role step for `classification`.
- `azure.md` rule 27 asks for a scenario evaluation set beside the prompt: that is the classifier bake-off of Epic 4 (stories 4.1 and 4.3); say so beside the prompt.
- The stage's deadline is 180 s for all runs of one page together; run them concurrently, bounded by a setting.
- The Azure environment is torn down (owner's rule in `CLAUDE.md`). Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation, and it is over the 1,600-token target because the story adds a whole service.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run` -- expected: all pass
- `docker build -f services/classification/Dockerfile -t aiuw-classification:dev . && docker build -f services/workflow/Dockerfile -t aiuw-workflow:dev .` -- expected: both build
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
