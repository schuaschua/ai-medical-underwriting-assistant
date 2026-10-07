---
title: 'Story 1.9: Gate routing and per-page progress'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '59ac207f7c07820dad4c410b43023957a0aa3dcc'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every page of a case ends at `classified`: nothing decides whether it goes on to extraction, back to the customer or to the underwriter, and the customer sees only a case status, not where each page is.

**Approach:** Add the gate to `workflow`: one rule in its domain package that routes each classified page by `is_medical` and `confidence` against a threshold setting, recorded with an audit event of its own. Show each page's status as a badge on the customer's upload screen, updated by the polling that is already there.

## Boundaries & Constraints

**Always:**
- Spine AD-7, AD-2, AD-8, AD-19. The routing table and its threshold live only in `workflow`'s domain package; the threshold is the setting `WORKFLOW_GATE_THRESHOLD`, default `0.90`, and a confidence equal to the threshold counts as "or more". `classification`, `web` and the SPA never compute a route.
- Routes: medical at the threshold or more becomes `extracting`; non-medical at the threshold or more becomes `awaiting_customer`; anything under the threshold becomes `awaiting_triage`. A case is routed on the result of the contender it was started with.
- Every route is recorded through the one recording path, in one transaction with its status change, with a new audit action `page.routed`: actor kind `ai`, actor `workflow:gate`, the page, ref the `classification_id`, detail the route and the threshold used. Decision (2026-10-07, coding agent, for the owner to confirm): the spine's catalogue has no action for the gate, and without one the status change from `classified` would have no matching event, which story 1.12 requires. The contracts catalogue, its tests and the exported SPA types change in this story.
- The orchestration stays deterministic: it holds the sequencing and calls the gate rule on values an activity returned.
- After the gate: a case with a page in `awaiting_customer` or `awaiting_triage` becomes `awaiting_human`; a case started with `stop_after: gate` ends as `completed`; otherwise it stays `running`. The orchestration ends here for now: the waits for decisions come with stories 1.10 and 1.11, extraction with story 2.4.
- Progress payloads carry a failure reason: `CaseProgress` and `PageProgress` gain an optional `error_code` from the catalogue, set from the `stage.failed` event (deferred item of stories 1.1 and 1.6).
- The SPA shows what the server says: one badge per page with the page number and its status, in page order, refreshed by polling without a reload; text in the strings module; no status worked out in the browser.

**Never:**
- No decision routes, prompts or queue (stories 1.10 and 1.11). No extraction. No `web` route for classifications.
- The threshold is not sent to or copied into `classification`, `web`, the SPA or a prompt.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Medical, sure | `is_medical` true, confidence 0.95 | Page `extracting`; `page.routed` event | N/A |
| Non-medical, sure | `is_medical` false, confidence 1.0 | Page `awaiting_customer`; event | N/A |
| Unsure, either label | confidence 0.6 | Page `awaiting_triage`; event | N/A |
| Exactly at the threshold | confidence 0.90, medical or not | Treated as 0.90 or more | N/A |
| Another threshold | `WORKFLOW_GATE_THRESHOLD=0.5`, confidence 0.6, medical | Page `extracting` | A threshold outside 0..1 is refused at start-up |
| Route recorded twice | The route activity runs again | No second event; status unchanged | N/A |
| Case after the gate | Mixed pages | Case `awaiting_human` if any page awaits a human, else `running`; `completed` with `stop_after: gate` | N/A |
| Progress of a failed case | A stage failed | `error_code` on the case, and on the page for a page stage | Null when nothing failed |
| Customer watches | A case in progress on the upload screen | Each page shows a badge with its status; badges change as polling brings new statuses; no reload | Polling goes on while the case is `running` or `awaiting_human` |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/audit.py` -- `AuditAction` (add `page.routed`), `AuditRecord` (detail is a small JSON summary; check what its validator allows for a non-redaction action), `ai_actor`; `models/workflow.py` -- `PageProgress`, `CaseProgress` (add `error_code`); `enums.py` -- `PageStatus`, `CaseStatus`, `StopAfter.GATE`; `export_schema.py` and `services/web/spa/src/api/contracts.gen.ts`, `contracts.schema.json` (regenerate; `contracts:check` guards drift)
- `services/workflow/src/workflow/domain/` -- new gate module beside `recording.py` (`Recording`, `PageChange`, `LIFECYCLE_ACTOR` as the pattern for an actor name), `transitions.py` (`classified` may already go to the three routes; `running` to `awaiting_human` and `completed`), `cases.py`
- `services/workflow/src/workflow/adapters/orchestration.py` -- extension point after every page is classified; `adapters/scheduler.py` -- `classify_page` answers `{outcome, case_status}` today and must hand on `classification_id`, `is_medical` and `confidence` (small values, AD-6); `Activities.record`, `_run`; `adapters/db.py` -- `SqlCaseStore.record` (applies a `Recording`), `progress` (add the error codes from `audit_event.error_code`)
- `services/workflow/src/workflow/settings.py` -- add the threshold with its bounds
- `services/web/src/web/adapters/http/api.py`, `adapters/dapr.py` -- `read_progress` passes `CaseProgress` through; nothing to add but tests for the new fields
- `services/web/spa/src/cases/caseProgress.ts` -- `CaseState` keeps only the case status and `redactionFailed`; `FINISHED` stops polling. `screens/UploadDocument.tsx` -- the session's case list. `strings.ts` -- `caseStatus` map is the pattern for a `pageStatus` map
- `packages/synthdata/src/synthdata/foundry_standin.py` -- mode `disagree` gives a 3-of-5 split, so a local case can show all three routes; `packages/synthdata/tests/` -- cross-service tests live here
- `infra/demo/app/main.tf` -- `workflow`'s environment; pass the threshold from a variable

## Tasks & Acceptance

**Execution:**
- [x] `packages/contracts/` -- `page.routed`, `error_code` on the two progress payloads, tests, exported schema and SPA types
- [x] `services/workflow/src/workflow/domain/` -- the gate rule (pure, with its route type), the recording of a route, the case status after the gate; unit tests for every routing row including the boundary
- [x] `services/workflow/src/workflow/adapters/` -- the classify answer hands on what the gate needs; a route activity through the recording path; orchestration: route every page after classification, then set the case status; `progress` with error codes; the threshold setting
- [x] `services/web/` -- tests that the new progress fields pass through unchanged
- [x] `services/web/spa/` -- page badges under each case of the session, strings for every page status, polling that goes on through `awaiting_human`; tests
- [x] `packages/synthdata/tests/` -- one cross-service test: a case whose pages end in at least two different routes, with a `page.routed` event per page
- [x] `infra/demo/app/`, `dapr.yaml`, `README.md` -- the threshold setting
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark the progress-payload items done as far as this story takes them; append anything that needs Azure

**Acceptance Criteria:**
- Given the local stack with the stand-ins, when a case from `data/cases/` with medical and non-medical pages is started, then every page leaves `classified` for its route, the trail holds one `page.routed` event per page after its `page.classified` event, and progress shows the routed statuses.
- Given a search of the repository, when it looks for the threshold value or the routing table outside `services/workflow`, then it finds neither in `classification`, `web`, the SPA or a prompt.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- The gate is `services/workflow/src/workflow/domain/gate.py`: `route_page` (the rule), `Route` (its three values are the page statuses they give), `case_status_after_gate` and `route_recording`. The orchestration calls the first two on what the classify activity answered (`classification_id`, `is_medical`, `confidence`) and hands each route to the activity `route_page`, which records it through `CaseStore.record`. A page is routed from `classified` only (`PageChange.only_from`).
- `AuditRecord.detail` had to change: it took redaction counts only. It is now the counts, a `RouteDetail` (`route`, `threshold`) for `page.routed`, or null.
- The case's status after the gate is set by a second activity, `settle_case_after_gate`, through a new store method `move_case`, without an audit event (a `Recording` always carries one, and the catalogue has no case-level action for it). `stop_after: gate` wins over a waiting page: the case ends `completed`. Both are in `deferred-work.md` for the owner to confirm.
- A classify answer that lacks what the gate needs routes no page: the case is marked failed.
- After review: the threshold is not built into the orchestration. `confirm_case_started` answers with the setting in force, the engine keeps that answer in the case's history, and the orchestration routes with it; a replay or a retry under a changed setting routes as the first run did. `route_page` answers with the route the trail holds for the page, and the case is settled on those answers. `settle_case_after_gate` takes `awaiting_human` and `completed` only. The rule refuses a confidence or threshold that is not a finite number from 0 to 1, and `RouteDetail.route` takes the three gate statuses only.
- The model stand-in has a mode `mixed` (`FOUNDRY_STANDIN_MODE=mixed ./tools/dev.sh`) in which `data/cases/case-002.pdf` ends with pages on all three routes.
- Tests of stories 1.6 to 1.8 that asserted "every page ends `classified` and the case stays `running`" were changed to what the lifecycle does now.
- Not shown in the SPA: `error_code` (carried by the payload only).

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | The threshold leaves `workflow` in the `page.routed` detail and the generated SPA types (blind) | false | The intent asks for the threshold in the event's detail; AD-7 forbids copying the rule, not recording the value a route was made with. Nothing outside `workflow` computes with it. The fix asked for is a rewording of this spec | reject |
| 2 | The threshold is a closure value of the orchestration: a replay under another setting computes other routes, the route activity answers `duplicate` as ok, and the case is settled on routes that were never stored (blind, edge) | medium | `build_case_lifecycle(..., gate_threshold=)`; `case_status_after_gate` is fed the recomputed routes | patch |
| 3 | `settle_case_after_gate` takes any status, `failed` among them, with no event (edge) | medium | `Activities.settle_case_after_gate` passes the input to `move_case` unchecked | patch |
| 4 | `route_page` reads its ids outside the `try`, lets a NaN or out-of-range confidence through as "sure", and an out-of-range threshold raises a `ValidationError` that is retried (blind, edge) | medium | Read in `scheduler.py` and `gate.py`: `nan < threshold` is false | patch |
| 5 | `RouteDetail.route` accepts all nine page statuses (blind) | low | Typed `PageStatus`; direct correction | patch |
| 6 | The SPA's page check can lose any one field test unnoticed (gap) | medium | Filed with evidence | patch |
| 7 | Earlier tests were loosened: `!= "failed"`, a status overwritten before comparing, a possible timing assumption (blind) | medium | Read in `test_redaction_end_to_end.py`, `test_classification_end_to_end.py`, `test_workflow_integration.py`; the gap reviewer found the lost assertions made elsewhere, but these tests now pass for a wrong status | patch |
| 8 | On the customer's own screen a page reads "Waiting for the customer" (blind) | low | `strings.pageStatus.awaiting_customer`; direct correction | patch |
| 9 | The local stack cannot show the three routes in one case (blind) | medium | The stand-in agrees on every page or disagrees on every page; stories 1.10 and 1.11 need both a sure non-medical page and an unsure one to be proven locally | patch |
| 10 | A failed or completed case keeps pages in `awaiting_customer` or `awaiting_triage`; the queues and decisions of stories 1.10 and 1.11 must look at the case status (blind, edge) | medium | True for a refused route, exhausted retries and `stop_after: gate`; nothing reads those pages yet | defer to stories 1.10 and 1.11 |
| 11 | The settle after the gate is not tied to the case being at the gate: once a decision moves a case back to `running`, a late retry would move it again (blind) | medium | `CASE_TRANSITIONS` allows both directions; no decision exists yet | defer to story 1.10 |
| 12 | The trail is ordered by `occurred_at`, which two services' clocks set: a route can be listed before its classification (edge) | maybe-false | Needs clocks apart to see; medium if true, because story 1.12 shows the trail in time order | defer to story 1.12 |
| 13 | Nothing can tell whether `WORKFLOW_GATE_THRESHOLD` reaches the deployed service, because the Azure check runs with the default (gap) | medium | Filed with evidence and disposition | defer to the final Azure session |
| 14 | The orchestration ends at the gate while the case is `running` or `awaiting_human` and cannot be resumed (blind) | false | Nothing is deployed and the emulator keeps no state, so no such case outlives a local run; story 1.10 replaces the ending with waits | reject |
| 15 | A route is accepted for a case that is `completed` or `awaiting_human`, and the activity answers `running` (blind) | low | All routes are recorded before the settle; no caller reads that answer's status after the gate | reject |
| 16 | The SPA polls every 3 s for a case that waits for a human (blind) | low | The customer must see the page change when a decision is made; the read is one small query | reject |
| 17 | `progress` runs a third query on every read, would raise on a code no longer in the catalogue, and its three reads are not one snapshot (blind, edge) | low | The codes are written from the same catalogue; a mixed snapshot lasts one poll | reject |
| 18 | If the settle's answer is lost on every retry the orchestration's output says `failed` while the case is `completed` (edge) | low | Nothing reads the orchestration's output | reject |
| 19 | Several tests check source text and would break on harmless edits (blind) | low | True; they guard the "only in `workflow`" rule cheaply | reject |
| 20 | Tracking files disagree; the deferred item reads as done and open; the owner is not asked about `actor_kind: ai` for a rule (blind) | false | The sprint status is synced at the end of the workflow; deferred entries are append-only by rule; `ActorKind` has only `human` and `ai` | reject |

## Design Notes

- Why the gate is called from the orchestration and recorded by an activity: AD-5 puts gate routing in orchestrator code, AD-8 wants the status change and its event in one transaction. The orchestrator passes the threshold-free facts to the pure rule and hands the route to the activity; the threshold reaches the orchestration when it is built. Changing the setting while a case is in flight would change a replay: note it beside the setting.
- A page reaches `extracting` here with no extraction service yet (story 2.4). That is the state story 1.6's transition table expects before `extracted`.
- The Azure environment is torn down (owner's rule in `CLAUDE.md`). Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice: plain default styling.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run` -- expected: all pass
- `docker build -f services/workflow/Dockerfile -t aiuw-workflow:dev . && docker build -f services/web/Dockerfile -t aiuw-web:dev .` -- expected: both build
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
