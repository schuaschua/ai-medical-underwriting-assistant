---
title: 'Case events in the audit trail and a case list for the underwriter'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '22eafb8e8677460cf3b1bf0e91ec17a3739e2fe7'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A case's audit trail begins with the redaction and has no end: it does not say who started the case or when it was completed. And an underwriter can open a trail only from a triage row or by typing a case id, so a finished case cannot be found on screen.

**Approach:** Record two case-level events, the start (with the human role that started it) and the completion, in the same transaction as the status they report. Give the underwriter a list of cases, newest first, each opening its audit trail. Decided by the owner on 2026-10-07.

## Boundaries & Constraints

**Always:**
- Spine AD-8, AD-9, AD-2. Two new audit actions in the contracts catalogue: `case.started` (actor kind `human`, actor the demo role that asked for the start, ref the case id) and `case.completed` (actor kind `ai`, actor the lifecycle, ref the case id). Both are case-level (no page), carry no detail, and are written through `workflow`'s store in the transaction that stores the case as started or moves it to `completed`. A repeated start, or a second move to `completed`, adds no event. A failed case keeps its `stage.failed` event and gets no `case.completed`.
- `web` passes the request's demo role on with the start, as it does with a decision; `workflow` refuses a start whose actor is not a demo role. The first start's actor is the one recorded; a repeat with another role changes nothing.
- `GET /cases` on `workflow` lists cases newest first: case id, case status, when it was started, how many pages it has and how many of them wait for a person. Eval-run cases are left out. The answer is bounded by a limit setting and says when more exist. `web` exposes it to the underwriter role only; the customer is refused with `role_not_allowed`.
- The SPA shows the list in the underwriter's navigation, read by polling through the one client module, each row linking to the case's audit trail; wording in the strings module; an empty list says so. The audit trail screen words the two new actions in plain language.
- The contracts change is made in the package with its tests and exported SPA types, and every caller of the start operation is updated in this change.

**Never:**
- No event for a case being put to wait or resumed, and none for a failed case beyond what exists. No filtering, search or paging of the list beyond the limit. No customer-side list beyond the session list that exists.
- No change in meaning to the append-only audit table or to who may decide. Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Start a case | Customer uploads and the case is started | The trail's first event is `case.started` with actor `customer` | N/A |
| Start again | The same start repeated, by the same or the other role | No second event; the first actor stands | N/A |
| Start without a human actor | A start whose actor is missing or not a demo role, sent to `workflow` | Refused; no case stored | 403 `actor_not_human` |
| Case completes | The last waiting page is decided and every page is final; or a case stops after the gate | `case.completed` is the trail's last event, written with the status change | N/A |
| Completion repeated | The settle or a repeated decision runs again | No second event | N/A |
| Case fails | A stage fails | `stage.failed` as today; no `case.completed` | N/A |
| List cases | Underwriter opens the list | Cases newest first with status, start time, page count and waiting count; each row opens its trail | N/A |
| Eval-run case | A case started with an `eval_run_id` | Not listed | N/A |
| Customer calls the list | `X-Demo-Role: customer` | Refused | 403 `role_not_allowed` |
| Many cases | More cases than the limit | The newest up to the limit, and a flag that more exist | N/A |
| No cases | Nothing started yet | "No cases yet" | N/A |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/audit.py` -- `AuditAction`, `HUMAN_ACTIONS`, the validators that tie fields to actions (`error_code`, `RouteDetail`); `models/workflow.py` -- `StartCaseRequest`, `CaseStarted`, `PageQueue` (the pattern for a bounded list with `has_more`); `operations.py` -- the operations table (add the case list); `decisions.py` -- how a human role is checked
- `services/workflow/src/workflow/domain/cases.py` -- `start_case`; `domain/lifecycle.py`; `domain/recording.py` -- `Recording`, `LIFECYCLE_ACTOR`, `lifecycle_failure` (the pattern for a case-level event); `domain/decisions.py` -- `authorise`; `adapters/db.py` -- `SqlCaseStore.start`, `_follow_pages` and `settle_case` (where a case becomes `completed`), `queue` (the pattern for a bounded cross-case read), the audit insert with its sequence; `adapters/http/routes.py`; `tests/support/workflow_fakes.py`
- `services/web/src/web/adapters/http/api.py` -- `start_case` (either role may start; options are underwriter-only), `underwriter_only`; `adapters/dapr.py` -- `ServiceClient`
- `services/web/spa/src/` -- `navigation.tsx`, `screens/TriageQueue.tsx` and `triage/triageQueue.ts` (a polled underwriter list), `screens/AuditTrail.tsx`, `components/AuditEventRow.tsx`, `strings.ts`, `api/client.ts`
- `packages/synthdata/tests/` -- cross-service tests and `support/synthdata_stack.py`; several end-to-end tests assert a whole trail and will gain the two events
- `evals/` does not exist yet; the eval runner (story 3.4) will start cases through `web` as the underwriter role

## Tasks & Acceptance

**Execution:**
- [x] `packages/contracts/` -- the two actions and their rules, the start request's actor, the case list payload and operation, tests, exported schema and SPA types
- [x] `services/workflow/` -- the start with its actor and event; the completion event in the transaction that completes the case; the case list read and route with its limit setting; the fake store in step; tests against PostgreSQL for every matrix row that touches the store
- [x] `services/web/` -- the role passed on with the start; `GET /api/cases` for the underwriter; tests
- [x] `services/web/spa/` -- the case list screen and its navigation entry; the two actions' wording in the trail; tests
- [x] `packages/synthdata/tests/`, earlier tests -- trails that now begin with `case.started` and end with `case.completed`; one cross-service test of the list through `web`
- [x] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; note that the spine's audit catalogue (AD-8) now lacks three actions the code has

**Acceptance Criteria:**
- Given the local stack, when a customer's case is run to its last decision, then its trail read by the underwriter starts with `case.started` by the customer and ends with `case.completed`, and the case is in the underwriter's list with its status.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- Contracts: `AuditAction.CASE_STARTED` and `CASE_COMPLETED`; `ACTIONS_BY_A_HUMAN` (the decisions plus the start; the four decisions of AD-10 are now named `DECISION_ACTIONS`) and `CASE_ACTIONS` with a validator (no page, ref is the case); `StartCaseOptions` (the browser's start body, no actor) and `StartCaseRequest.actor` on top of it (optional text, blank allowed, so `workflow`'s domain refuses with `actor_not_human`); `CaseSummary`, `CaseList` and the operation `list_cases` (`GET /cases`).
- `workflow`: `starting_role` and the event in `domain/cases.py` and `domain/recording.py`; `SqlCaseStore.start` inserts the event when, and only when, the case row was inserted; `_follow_pages` inserts `case.completed` with the update that completes the case, and a decision's own event is now written before it; `domain/case_list.py`, `SqlCaseStore.case_list`, `WORKFLOW_CASE_LIST_LIMIT`. Migration `0005`: one `case.started` and one `case.completed` row per case, by two partial unique indexes. A recording that would complete a case is refused in `record`. The orchestration yields what it did before.
- `web`: the start's actor is the request's role; the browser's body is the options only, and one that names an actor is 422; `GET /api/cases` for the underwriter.
- SPA: `screens/CaseList.tsx` at `/underwriter/cases`, `cases/caseList.ts`, `polling/polled.ts` and `polling/backoff.ts` (the polling of the triage queue, moved so both lists share it; it settles on a refusal no repeat can mend), `components/LocalTime.tsx`.
- Decisions made while building, and what is left, are in `deferred-work.md` under this spec.

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Nothing in the database holds "once per case" for the two events, and the trail cannot be corrected afterwards (blind) | medium | The rule rests on one insert path each; the table is append-only by trigger. A partial unique index is one small migration | patch |
| 2 | The other status-setting path would complete a case with no event, and nothing fails if it is used so (blind, implementer) | medium | `SqlCaseStore._record` applies `Recording.case_status`; story 2.4 adds a completion by stage result | patch |
| 3 | `web` validates an actor it then discards, the browser-facing type offers the field, and a blank actor is a 422 where the contract says `actor_not_human` (blind, edge x2) | medium | `api.py` reads the body as `StartCaseRequest`; `contracts.gen.ts` shows `actor` to the SPA | patch |
| 4 | `HUMAN_ACTIONS` (the four decisions) and `ACTIONS_BY_A_HUMAN` sit side by side and tests use the first to mean the second (blind) | medium | `test_workflow_domain.py:298`, `test_workflow_decisions.py:398`; the next author will pick the wrong one for an AD-10 check | patch |
| 5 | Two contract tests still claim "the spine's catalogue" while holding the owner's additions (blind) | low | `test_operations.py`, `test_validation.py`; a separate named list keeps unapproved drift visible | patch |
| 6 | The helpers for earlier tests drop every `case.started` event, so a duplicate or misplaced one would pass (blind) | medium | `after_start` and the per-file queries filter by action | patch |
| 7 | Trace ids default to none on the public functions (blind) | low | `start_case`, `settle_case_after_gate`, `CaseStore.settle_case`; direct correction | patch |
| 8 | `usePolled` retries a refusal no retry can mend and drops the server's message; a queued read is sent after unmount; shared modules import from feature modules (blind, edge) | medium | `polling/polled.ts` imports `backoffMs` from `cases/caseProgress`; `LocalTime` takes wording from `strings.audit` | patch |
| 9 | `getCaseList` reads fields of an answer that may be null; a listed id that is not a case id, or more waiting pages than pages, is shown (gap, edge) | low | `client.ts`; direct corrections | patch |
| 10 | The eval run id on a completion written by a decision, and the list's count for an unsettled stop-after-gate case, are unseen by any test of the real store (gap x2) | medium | Filed with evidence | patch |
| 11 | A trail over its limit never shows `case.completed`, and nothing says so (blind) | low | The trail lists the first events; direct note in the README | patch |
| 12 | No test shows the settle activity hands on a real trace id (gap, blind) | maybe-false | Needs telemetry on in an activity; low to medium | defer to the final Azure session |
| 13 | The list has no completion time or starter and is capped at 100 with no paging (blind) | low | Status and start time find a case at demo size; the cap is on the deferred list | reject |
| 14 | Cases stored before this change get no events (edge) | false | Nothing is deployed, and the local database is rebuilt by the tests | reject |
| 15 | The list read runs two counts per case on every poll (blind) | low | A hundred cases at most; one underwriter | reject |
| 16 | The sprint status has no line for this change (blind) | low | Added by the main session with the commit | patch |

## Design Notes

- The start's event is written where the case row is inserted, so a start that stored the case but could not reach the scheduler still has its event, once.
- This change is not in `epics.md`; it follows the owner's answer to a question from story 1.12's review, and is numbered 1.13 only to sit beside the stories it completes.
- The working tree holds uncommitted work of story 2.1 that is being reviewed in parallel: `packages/synthdata/src/synthdata/manual*.py`, `generate.py`, `render.py`, `__main__.py`, `packages/synthdata/tests/test_underwriting_manual.py`, `test_synthetic_cases.py`, `data/manual/`, `data/answer-key/rule-table.json`, `data/README.md`. Leave those files alone, and do not run the synthetic data generator.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice: plain default styling.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run` -- expected: all pass
- `docker build -f services/workflow/Dockerfile -t aiuw-workflow:dev . && docker build -f services/web/Dockerfile -t aiuw-web:dev .` -- expected: both build
