---
title: 'Story 1.7: PII redaction before anything else reads the document'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'aa1df16e0eac2a15158076da2f3db78a602cb122'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A started case stops after its first step: the uploaded original is never redacted, no pages exist, and nothing later could read the document without seeing personal identifiers.

**Approach:** Make redaction the first stage `workflow` commands. `intake` sends the original to Azure AI Language's document PII redaction (entity mask), stores the redacted PDF as the document of record, splits it into pages with text, word boxes and thumbnails, and serves those. A failure or a passed deadline ends the case as `failed` and the customer is asked to upload again. Because the Azure environment stays down while coding, the Language call is proven against a local stand-in that speaks the same REST shape.

## Boundaries & Constraints

**Always:**
- Spine AD-21, AD-14, AD-6, AD-8, AD-3. Only the redacted PDF is ever read after redaction: page split, text, boxes, thumbnails and the file route all use it. No route serves an original, and nothing falls back to it.
- `POST /cases/{case_id}/redaction` is keyed on `case_id`: a key row is inserted as `running` before any work; a repeat while running answers 409 `in_progress`; a repeat after the end answers the stored result and does no work. The stage enforces its own 180-second deadline, then cancels the Language job, creates no pages and stores a `failed` result. `workflow`'s activity timeout is 200 seconds and it retries `in_progress` with backoff.
- A result carries ids and counts only. The `document.redacted` audit record's detail is a count per category taken from the service's result by category name, never a value; actor `intake:azure-ai-language`.
- Redacted PDF, the service's result file and thumbnails sit in container `cases` under `<case_id>/`. Page text and word boxes are stored by `intake` in its own schema. Redacted categories are a setting (default: person names, addresses, phone numbers, email addresses, identity and policy numbers); dates, ages and medical terms are kept.
- Language is reached over REST with `httpx` and the service identity (no key); the API version and endpoint are settings. `workflow` calls `intake` through the Dapr sidecar from one client module, with the trace context.
- Logs carry ids, codes, counts and timings only: never page text, identifier values or blob URLs with tokens.
- Tests: every acceptance criterion has a test named for the story; unit tests use fakes; integration tests use the containers of `compose.yaml` plus the stand-in.

**Never:**
- No classification, gate, decisions, triage or audit screen (stories 1.8 to 1.12). No `web` routes for pages, text, boxes, thumbnails or the file: each arrives with the story whose screen or runner needs it.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something. Do not edit `infra/demo/foundation`.
- The stand-in never ships in a service image and is never the default in Azure.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Redact a started case | `workflow` commands redaction for an uploaded case | Redacted PDF in `cases/<case_id>/`; pages with text, boxes, thumbnail; result `done` with page ids in order and counts; `document.redacted` event; pages tracked as `uploaded` | N/A |
| Planted identifiers | A synthetic case from `data/cases/` | No planted identifier, nor any part of a planted name, in any stored page text (after contracts normalisation); dates, ages and medical terms still there | N/A |
| Repeat while running | Same command again in flight | 409 `in_progress`; `workflow` retries | N/A |
| Repeat after the end | Same command again | The stored result; no Language call, no new rows | N/A |
| Read before redaction is done | Page list or document file asked for | Page list empty; file 409 `not_redacted` | N/A |
| Read after | Pages, text, boxes (whole page or an offset range), thumbnail, file | Contract payloads; PNG thumbnail; the redacted PDF | 404 for an unknown case, page or document |
| Redaction fails | Language job fails, is rejected, or its output is unreadable | No pages; result `failed` (`redaction_failed`); case `failed` with a case-level `stage.failed` event | Language job cancelled where it still runs |
| Deadline passes | No result within 180 s | As above with `stage_timeout` | Job cancelled |
| Unknown case | Redaction commanded for a case `intake` does not hold | 404 `not_found`; `workflow` ends the case as failed, without retrying | N/A |
| Failed case on screen | Customer watches a case whose redaction failed | Message asking them to upload the document again | N/A |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/models/intake.py` -- `RedactionCommand`, `RedactionResult`, `Page`, `PageList`, `PageText`, `PageBoxesQuery`, `WordBox`, `PageBoxes` exist; reuse as they are
- `packages/contracts/src/contracts/operations.py` -- `redact_document`, `list_pages`, `read_page_text`, `read_page_boxes`, `read_page_thumbnail`, `read_document_file` are declared; routes take their paths from here
- `packages/contracts/src/contracts/errors.py` -- `IN_PROGRESS`, `NOT_REDACTED`, `REDACTION_FAILED`, `STAGE_TIMEOUT`, `NOT_FOUND`; `text.py` -- the normalisation used by the planted-identifier check
- `services/intake/src/intake/` -- `domain/upload.py` and `ports.py` (pattern for a domain operation with ports and a deadline), `adapters/blob.py` (`BlobOriginalStore` has no read on purpose: add a separate, narrowly named reader used only by redaction), `adapters/db.py` (tables, Entra token, spans), `adapters/http/routes.py` (`Dependencies`, `build_router`), `settings.py` (`cases_container` already there), `migrations/versions/` (next is `v0003`)
- `services/workflow/src/workflow/adapters/orchestration.py` -- extension point after `CONFIRM_CASE_STARTED`; `adapters/scheduler.py` -- `Activities.record` is the one path a stage activity ends with, `_run` and `ActivityRefused`/`ActivityFailed`, `build_worker`; `domain/recording.py` -- redaction rules already built; `domain/transitions.py`; `settings.py`
- `services/web/src/web/adapters/dapr.py` -- the shape of a Dapr client module (`invoke_path`, `trace_headers`, `_refusal`); `workflow` gets its own, nothing shared by import
- `services/web/spa/src/screens/UploadDocument.tsx`, `cases/caseProgress.ts`, `strings.ts` -- where the failed-case message goes
- `packages/synthdata/` -- dev-only package with `pymupdf` 1.28.2 and the planted identifiers; home of the Language stand-in. `data/answer-key/cases/*.json` -- `identifiers[].value` per case
- `infra/demo/app/main.tf`, `locals.tf` -- `intake` app and its roles; foundation outputs `language_id`, `language_endpoint`, `language_principal_id` exist. Roles to add (already in `docs/standards/azure.md`): Cognitive Services User on Language for `intake`; for Language's own identity, Storage Blob Data Reader on `originals` and Storage Blob Data Contributor on `cases`
- `compose.yaml`, `dapr.yaml`, `tools/dev.sh`, `tools/migrate-local.sh`, `README.md`, `.github/workflows/ci.yml`, `pyproject.toml` -- local run and CI

## Tasks & Acceptance

**Execution:**
- [x] `services/intake/` domain -- redaction operation (key row, deadline, cancel, result with its audit record), page entities, ports for the Language job, the redacted-file store and the page store; page reading rules (`not_redacted`, offset range to boxes)
- [x] `services/intake/` adapters -- Language REST client (`httpx`, Entra token for scope `https://cognitiveservices.azure.com/.default`, submit, poll, cancel, read the result's category counts); blob adapter for `cases` and a read of the original used only to hand it to redaction if the service needs it; page split with `pymupdf` (one text per page, a box per word with offsets into that text, PNG thumbnail); repository and migration `v0003` (redaction key row, page, page text, word boxes); routes for the six operations; settings; `Dockerfile` if a system library is needed
- [x] `services/workflow/` -- Dapr client module for `intake`; `redact_document` activity ending in `Activities.record`; orchestration: redaction after the confirm step, failed or refused redaction ends the case as failed, a done one ends the orchestration for now with the case `running`; settings for the stage timeout (200 s) and the sidecar port; `stop_after` honoured if it names redaction
- [x] `packages/synthdata/` -- Language stand-in: an HTTP app with the same job routes, reading the source blob and writing the redacted PDF and a result file to the target container in the blob emulator, masking with entity tokens; runnable for the local start and from tests
- [x] `services/web/spa/` -- upload-again message for a failed case; tests
- [x] `dapr.yaml`, `tools/dev.sh`, `README.md`, `pyproject.toml`, `.github/workflows/ci.yml` -- settings for both services, the stand-in in the local start and in CI's integration run
- [x] `infra/demo/app/` -- the three role assignments, `INTAKE_LANGUAGE_ENDPOINT` and related settings for `intake`, the sidecar setting for `workflow`
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- append the Azure checks this story cannot run (see Design Notes)
- [x] Tests for every matrix row, in `intake`, `workflow` and the SPA

**Acceptance Criteria:**
- Given the local stack with the stand-in, when a case from `data/cases/` is uploaded and started, then within the deadline progress shows redaction `done` and the pages as `uploaded`, and the audit trail holds one `document.redacted` event whose detail is counts per category.
- Given a stand-in told to fail or to hang, when the case is started, then no page exists, the case is `failed` with one `stage.failed` event, and the original was not read by anything but the redaction call.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

Built and proven locally on 2026-10-07; nothing was run against Azure.

- **Redaction in `intake`:** `domain/redaction.py` (key row, one 180-second deadline over the job, the page split and the storing, cancel, stored result with its audit record), `domain/pages.py` (reads, `not_redacted`, offset range to boxes), `adapters/language.py` (REST with `httpx`, Entra token for `https://cognitiveservices.azure.com/.default`), `adapters/pdf.py` (`pymupdf`), `adapters/redaction_db.py`, `adapters/http/page_routes.py`, migration `0003`. No system library was needed, so the `Dockerfile` is unchanged.
- **No reader of the original was added.** The service reads the original itself from the address it is handed, so `intake` never downloads one: `BlobOriginalStore` still has only `put` and `delete`, and the one download in the service is in `BlobCaseFiles`, built for the `cases` container. A test holds that in place.
- **Where the files sit:** the job is told to write under `<cases>/<case_id>`. Output outside `<case_id>/` is refused (`job_output_outside_case`). Whatever names the service used under it, `intake` copies the redacted PDF and the result file to `<case_id>/<document_id>.redacted.pdf` and `.redaction-result.json` and removes the service's own files; thumbnails are `<case_id>/pages/<page_id>.png`. On failure everything under `<case_id>/` is removed.
- **Stale `running` key row: settled as failed.** A repeat that finds a `running` row older than the deadline plus `INTAKE_REDACTION_STALE_MARGIN_SECONDS` (60) cancels the stored job id, stores a `stage_timeout` result and removes the case's files; it does not take the work over. A finish is written only while the row is still `running`, so an earlier attempt that comes back late stores no page.
- **A failed result is a 200** with `status: failed`: it is the stored result, and `workflow` records it. Only `in_progress` (409) and `not_found` (404) are errors to the caller.
- **`workflow`:** `adapters/dapr.py` (`StageClient`), the `redact_document` activity ending in `Activities.record`, and the orchestration step after the confirm step. A failed redaction fails the case in the recording of its result, so the orchestration does not also call `mark_case_failed` (one `stage.failed` event). A refused command (unknown case) or exhausted retries do call it. Stage commands have a retry policy of their own (`WORKFLOW_STAGE_MAX_ATTEMPTS` 14, waits capped at 30 s, 300 s in all) so that a command repeated while the stage still works outlasts the stage's deadline plus `intake`'s stale margin (240 s); the general activity policy (5 attempts, about 30 s) would have given up first.
- **Orchestrator versioning is not needed yet:** nothing is deployed, and the emulator keeps no state, so no case is in flight when the orchestrator body changes. It will be needed from the first change made after a deployed case can be in flight.
- **`stop_after`:** the contracts' `StopAfter` has one value, `gate`, which is later than redaction. Nothing names redaction, so there is nothing to honour here; a test states it.
- **Categories:** the default is `Person`, `Address`, `PhoneNumber`, `Email`, `USSocialSecurityNumber`, `PolicyNumber`. `PolicyNumber` is this project's own name (the stand-in knows it); the real service may not. The `app` stack sets the list from `terraform.tfvars`, so it can be changed without a code change. See `deferred-work.md`.
- **The stand-in** is `packages/synthdata/src/synthdata/language_standin.py`: the three job routes, modes `ok`, `fail`, `reject`, `hang`, `unreadable`. It finds emails, phone numbers, identity and policy numbers by shape and the names and addresses of the generator's case definitions; it is not a recogniser. `intake` refuses a plain-HTTP Language endpoint that is not on loopback, and an `https://` one without Entra sign-in, so it cannot be the endpoint in Azure. No default endpoint exists.
- **Where the tests are.** Story 1.4's guard (spine AD-17) lets nothing under `services/` name `synthdata` or the answer key, tests included. So the tests that run the stand-in or read the answer key are in `packages/synthdata/tests/`: the planted-identifier row and the failure rows against the real `intake` (`test_redaction_with_the_standin.py`), and the whole path with the real `workflow` and `intake` (`test_redaction_end_to_end.py`). `services/intake/tests` and `services/workflow/tests` cover every row with fakes, plus `workflow` against the real database and scheduler emulator with a stand-in sidecar.
- **Tests of earlier stories that changed,** because this story changes what they assert: `intake`'s route table, migration list and table list; "asking for the original is 404" (the file route now exists and answers 409 `not_redacted`); `workflow`'s "stops before the first stage" tests and the integration tests that expected a started case to stay at redaction `running` with an empty trail.
- **Local proof with the real Dapr runtime** (`./tools/dev.sh`): `case-002.pdf` uploaded and started through `web`; two seconds later progress showed redaction `done` and six pages `uploaded`, and the trail held one `document.redacted` event with counts for six categories. With the stand-in in `--mode fail`: case `failed`, no pages, one `stage.failed` event (`redaction_failed`).

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | The failure path runs after the 180 s deadline with no bound: a slow cancel can pass `workflow`'s 200 s (blind) | medium | `_fail` awaits `language.cancel` under the 30 s HTTP timeout, outside `asyncio.timeout` | patch |
| 2 | `_fail` is shielded only in the `CancelledError` handler; a request cancelled during the other two leaves the row `running` (edge) | medium | `redaction.py` handlers for `TimeoutError` and `Exception` await `_fail` bare | patch |
| 3 | A `TimeoutError` raised by a port before the deadline is stored as `stage_timeout` (edge) | low | `except TimeoutError` does not ask whether the deadline scope expired; direct correction | patch |
| 4 | Output is accepted anywhere in `cases`, so another case's blob could be read, copied and deleted; clean-up covers only `<case_id>/` (blind, edge) | medium | `LanguageRedaction._blob_name` checks the container only | patch |
| 5 | Cancel answered 401, 403 or 429 is logged as cancelled (edge) | low | `cancel` raises only at 500 and above; direct correction | patch |
| 6 | A failed delete of the service's own output files fails a good redaction (edge) | medium | The delete loop in `_redact_and_split` is not guarded | patch |
| 7 | Page text with a NUL character is refused by PostgreSQL and fails the redaction (edge) | low | Text goes to a `Text` column as read; direct correction that keeps offsets | patch |
| 8 | No limit on page count or thumbnail height: a 10 MB PDF of thousands of pages, or a very tall page, can exhaust 1 GiB (blind, edge) | medium | `pdf.py` sets zoom from width alone and holds every reading at once; the upload is open to anyone | patch |
| 9 | Category names with non-ASCII letters pass the settings check (edge) | low | `str.isalnum()`; direct correction | patch |
| 10 | A `validation_failed` answer from `intake` is retried 12 times (edge) | low | `_STAGE_ANSWERS` lacks it although `scheduler._PERMANENT` holds it; direct correction | patch |
| 11 | `tools/dev.sh` does not check that the stand-in started, and no longer passes a signal on to Dapr (blind, edge) | low | Background start with no wait; `exec` removed; direct correction | patch |
| 12 | Nothing proves the redaction activity waits the stage timeout and not the general one (gap) | medium | Filed with evidence: both timeouts are 5.0 in every test | patch |
| 13 | `SqlRedactionRepository.finish`'s "only while running" guard is tested only on the fake (gap) | medium | Filed with evidence | patch |
| 14 | `create_app` handing the categories and the stale margin to the routes is untested (gap) | medium | Filed with evidence | patch |
| 15 | The upload-again message shows for any failed case, not only a failed redaction (blind) | medium | `caseText` keys on `case_status`; story 1.8 adds other failures where a new upload does not help | patch |
| 16 | `workflow`'s retry budget (240 s) equals `intake`'s stale threshold (180 + 60 s) (blind) | medium | 2+4+8+16+30x7 = 240; the last retry may come just before the row counts as stale, leaving it `running` and the job uncancelled | patch |
| 17 | A transient refusal at submit (429, 5xx) fails the case for good (blind) | medium | `start` turns every non-202 into `RedactionJobError`; polling tolerates the same answers | patch |
| 18 | A redaction that masked nothing, or a PDF with no text layer, is recorded as done without a trace (blind) | medium | No log line says so; whether such a case should fail is the spine's open question on images, the owner's | patch (warning only); the decision stays with the spine's open question |
| 19 | Comments: AD-2 against AD-3 for the same thing, "the one service never scaled to zero", a test comment that overstates what was searched; `_NO_STORE` repeats the middleware (blind, gap) | low | Read in `infra/demo/app/main.tf`, `test_redaction_with_the_standin.py`, `page_routes.py`; direct corrections | patch |
| 20 | Work on worker threads outlives the deadline and can write a blob after the clean-up (blind, edge) | low | True; the file has no row and no route serves it, and it holds redacted content. The fix is tracking of in-flight writes | reject |
| 21 | A cancelled job, one accepted without an id, or one whose id was not stored yet may still write files after the clean-up (edge x3) | maybe-false | Depends on how the real service cancels; medium if it writes after a cancel. Settled by watching `cases` after a cancelled job | defer to the final Azure session |
| 22 | A `running` row for which no repeat ever comes stays for ever (edge) | medium | True once `workflow`'s retries are spent; needs a sweep, which is new surface. Finding 16 makes it rare | defer |
| 23 | The service's result file is kept in `cases` before anyone knows whether it holds found text (blind) | maybe-false | Already on the deferred list from this story; AD-21 puts the file in `cases`, and the service writes it there itself | reject (listed already) |
| 24 | Nothing checks what the redacted PDF carries besides its text: metadata, bookmarks, annotations, attachments, images (blind) | maybe-false | Depends on the real service's output; medium if identifiers survive there. Images are the spine's open question | defer to the final Azure session |
| 25 | A request cancelled mid-redaction fails the case at once (blind) | low | Chosen and tested; leaving the row `running` would end the same way after the stale margin, only later | reject |
| 26 | Reading order and line breaks can disagree on tables and multi-column pages (blind) | maybe-false | Needs such a page to see; medium if true, because quotes are checked against this text | defer to story 2.4 |
| 27 | Five redactions at once hold every worker thread for up to 205 s (edge) | maybe-false | `worker_max_concurrent_activities` is 5; matters when the eval runner starts 20 cases; medium if true | defer to story 3.4 |
| 28 | Mixed clocks on the key row (blind) | low | Skew between replicas is far below the 60 s margin; the fix moves the clock into SQL | reject |
| 29 | No `CHECK` on `redaction.status`, no unique index on `document_id` (blind) | low | One writer, which sets only enum values; one document per case is upheld by `POST /cases` | reject |
| 30 | `intake` cannot start without `INTAKE_LANGUAGE_ENDPOINT` (blind) | low | Set in every environment; failing at start is how the other required settings behave | reject |
| 31 | The stand-in finishes inside submit and checks no caller (blind) | low | The poll loop over a running job is covered by the adapter tests; `hang` mode exists | reject |
| 32 | Planning files disagree: sprint status against spec status, duplicate deferred entries, approval not carried (blind) | false | The sprint status is synced at the end of the workflow; deferred entries are append-only by rule; the approval note is in the spec | reject |
| 33 | `EntraToken` lives in the database adapter; defaults written twice; `Redaction.running` used only by a test; blob names not quoted in URLs; `WORKFLOW_DAPR_HTTP_PORT` is a second source (blind) | low | Names are UUIDs, so quoting changes nothing; the rest harms no caller named | reject |

## Design Notes

- **Azure checks that go to `deferred-work.md` (final session):** the Language API version and the exact request and result shape (spine open question: 2026-05-01 or a preview version); whether Language reads `originals` and writes `cases` with its own identity and the three roles; whether fictional identity numbers and `POL-SYN` policy numbers are masked, and which category names it reports; whether the result file holds the found text (if so it must be stored with the original, not in `cases`); whether the service's 10 MB limit is decimal or binary; the 180/200-second limits through real sidecars and Container Apps' own request timeout.
- **Deferred items closed here:** an unknown case now fails at redaction (story 1.6, finding 14); the recording rules and transition table are confirmed against a real stage. Orchestrator versioning is not needed yet: nothing is deployed and the emulator keeps no state, so no case is in flight when the orchestrator body changes; say so in the notes.
- **A stale `running` key row** (the process died mid-redaction) must not block the case for ever: a repeat that finds a `running` row older than the deadline plus a margin takes the work over or settles it as failed. Choose one and test it.
- The Azure environment is torn down (owner's rule in `CLAUDE.md`). Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation, and it is over the 1,600-token target because the story spans two services, a stand-in, the SPA and the `app` stack.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa ci && npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run` -- expected: all pass
- `docker build -f services/intake/Dockerfile -t aiuw-intake:dev . && docker build -f services/workflow/Dockerfile -t aiuw-workflow:dev .` -- expected: both build
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
