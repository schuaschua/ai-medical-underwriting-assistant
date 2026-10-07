---
title: 'Story 1.10: Customer keeps or discards a non-medical page'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '24d24e99e54379ef2811afd5c15b7a0c7c5d619f'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A page the gate found non-medical waits at `awaiting_customer` for ever: the customer is not asked, and nothing can record an answer.

**Approach:** Build the one decision operation in `workflow` (stored decision, audit event and status change together, then the orchestration is told), expose it through `web`, and show the customer a prompt on each waiting page that names the predicted type and confidence and offers discard or keep. The operation is built for all four decisions; this story's screen uses keep and discard, and story 1.11 adds the underwriter's queue for accept and deny.

## Boundaries & Constraints

**Always:**
- Spine AD-10, AD-8, AD-5, AD-9, AD-2. `POST /cases/{case_id}/pages/{page_id}/decisions` on `workflow`, reachable only from `web`, is the only way a page is kept, discarded, accepted or denied. The rule is enforced in `workflow`'s domain code.
- Who may decide what is one mapping in the contracts package: customer keeps or discards a page that is `awaiting_customer`; underwriter accepts or denies a page that is `awaiting_triage`. An actor that is not a demo role is refused with `actor_not_human` (403); a role deciding what is not its to decide with `role_not_allowed` (403); a page that awaits no such decision, or whose case is `failed` or `completed`, with `not_awaiting_decision` (409); an unknown case or page with 404.
- Results: discard -> `discarded`; keep -> `awaiting_triage`; accept -> `extracting`; deny -> `denied`. Audit actions `page.discarded`, `page.kept`, `page.accepted`, `page.denied`, actor kind `human`, actor the demo role, ref the decision's id. The decision row (table `human_decision`, owned by `workflow`), the status change and the audit event are written in one transaction.
- The case status follows the pages, in the same transaction as every change that can move it: any page awaiting a human -> `awaiting_human`; else any page still in work -> `running`; else, every page final -> `completed`. The settle after the gate uses the same rule, so a late retry cannot undo a decision (deferred item of story 1.9). A case started with `stop_after: gate` still ends `completed` at the gate and takes no decision.
- The orchestration waits for human decisions as external events, never by polling or timers, and goes on per page as decisions arrive. A decision whose event could not be raised is not lost: repeating the same decision answers with the stored one and raises the event again.
- `web` passes the demo role of the request on as the actor and adds no rule of its own beyond its route's role check. The SPA shows the prompt only for pages the server reports as `awaiting_customer`, takes the predicted type and confidence from the server, and formats the percentage itself. Text is in the strings module.
- Every non-GET call from the SPA carries the custom request header already in use.

**Never:**
- No triage queue, no underwriter screen, no thumbnails (story 1.11). No extraction: an accepted page stays `extracting` (story 2.4).
- The AI never decides: no code path records a decision with an actor that is not a human role.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Prompt | Customer opens a case with a page `awaiting_customer`, predicted `other` at 0.96 | The page shows a prompt naming the type in plain words and "96%", with Discard and Keep | N/A |
| Discard | Customer chooses discard | Page `discarded`; `page.discarded` event with actor `customer`; the prompt goes | N/A |
| Keep | Customer chooses keep | Page `awaiting_triage`; `page.kept` event | N/A |
| Accept or deny | Underwriter decides a page `awaiting_triage` (through the API) | `extracting` or `denied`; `page.accepted` or `page.denied` | N/A |
| Wrong state | Decision for a page not awaiting it, or of a `failed` or `completed` case | Nothing changes | 409 `not_awaiting_decision` |
| Wrong role | Customer sends accept; underwriter sends keep | Nothing changes | 403 `role_not_allowed` |
| Not a human | Actor is a service name or anything but a demo role | Nothing changes | 403 `actor_not_human` |
| Same decision again | The same role repeats the decision it already made on that page | The stored decision; no second row or event; the event is raised again | N/A |
| Other decision after one was made | Keep after discard | Nothing changes | 409 `not_awaiting_decision` |
| Case status | Last waiting page decided | Case leaves `awaiting_human`: `running` if a page is still in work, `completed` if every page is final | N/A |
| Decision fails on screen | The call fails | The prompt stays, with the error and a way to try again | N/A |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/models/workflow.py` -- `DecisionRequest` (its `actor` is typed `DemoRole`, so a non-human actor fails validation before the domain: loosen it so `actor_not_human` can be reached, as the deferred item of story 1.1 asks), `DecisionRecorded`; `enums.py` -- `Decision`, `DemoRole`, `PageStatus`; `audit.py` -- `HUMAN_ACTIONS`; `errors.py` -- `ACTOR_NOT_HUMAN`, `ROLE_NOT_ALLOWED`, `NOT_AWAITING_DECISION`; `operations.py` -- `record_decision`, `list_classifications`; `models/classification.py` -- `ClassificationList`
- `services/workflow/src/workflow/domain/` -- `gate.py` (`case_status_after_gate`, the rule to generalise), `recording.py` (`Recording`, `PageChange.only_from`), `transitions.py`, `cases.py` (`settle_case_after_gate`, `record_route`); `adapters/db.py` -- `SqlCaseStore.record`, `move_case`; `adapters/http/routes.py`; migrations (next is `v0003`; the service role's grants are given table by table)
- `services/workflow/src/workflow/adapters/orchestration.py` -- ends after the settle today; `adapters/scheduler.py` -- `SchedulerEngine` (the client that can raise an event), `Activities`
- `services/web/src/web/adapters/http/api.py` -- `role_checked` routes, `customer_only`, `any_role`; `adapters/dapr.py` -- `ServiceClient._call`, `_refusal` with the codes passed on
- `services/web/spa/src/screens/UploadDocument.tsx` -- `PageBadges`; `cases/caseProgress.ts`; `api/client.ts`; `strings.ts`
- `packages/synthdata/src/synthdata/foundry_standin.py` -- the mode that gives mixed routes in one case; `packages/synthdata/tests/` -- cross-service tests
- `infra/demo/app/` -- `web` now calls `classification` through Dapr; check nothing restricts it

## Tasks & Acceptance

**Execution:**
- [x] `packages/contracts/` -- the role-to-decision mapping with the page status each decision needs and leaves; the decision request's actor; tests; exported schema and SPA types
- [x] `services/workflow/` -- domain rule for a decision; `human_decision` table and migration with grants; recording of a decision with the case status that follows the pages; the route; raising the orchestration event; orchestration that waits for decisions per page and ends when no page waits; the settle after the gate on the same rule; tests for every matrix row
- [x] `services/web/` -- `POST /api/cases/{case_id}/pages/{page_id}/decisions` (the request's role is the actor; keep and discard for the customer, accept and deny for the underwriter) and `GET /api/cases/{case_id}/classifications`; client calls; tests
- [x] `services/web/spa/` -- the prompt on a page that awaits the customer, with Discard and Keep, the error state, and the refresh after an answer; strings for the six page types; tests
- [x] `packages/synthdata/tests/` -- one cross-service test: a case with a sure non-medical page, discarded in one run and kept in another, through `web`
- [x] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; mark the items this story closes; append anything that needs Azure

**Acceptance Criteria:**
- Given the local stack with the stand-ins, when the customer discards one waiting page and keeps another through `web`, then the first is `discarded` and the second `awaiting_triage`, the trail holds `page.discarded` and `page.kept` with actor `customer`, and the case status follows.
- Given any route or activity of the system, when the code is searched for a way to record a decision, then only the one operation does it and it refuses every actor that is not a demo role.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- The mapping is `packages/contracts/src/contracts/decisions.py` (`DECISION_RULES`: role, the page status a decision needs and leaves, its audit action). `DecisionRequest.actor` is any non-blank text, so `actor_not_human` is reached in `workflow`'s domain (`workflow/domain/decisions.py`, `authorise`). The browser's body is `PageDecisionRequest` (`{decision}` only); `web` adds the role as the actor.
- The one operation is `workflow.domain.decisions.record_decision`: check actor and role, then `CaseStore.decide` (one transaction: lock the case row, find a repeat, check the case and the page, move the page, insert `human_decision`, set the case status from the pages, insert the audit event), then `LifecycleEngine.decision_made` (external event `decision.<awaited status>.<page_id>`, payload the decision). A failed raise is `upstream_unavailable` (502); the same decision again is answered with the stored one and raises the event. The check order is: actor and role (403) before case and page (404) before the state (409).
- The case status rule is `workflow/domain/case_status.py` (`case_status_following`, and `case_status_after_gate` for `stop_after: gate`). The store applies it under the lock on the case's row, in `decide` and in `settle_case`. `CaseStore.move_case` is gone: the settle activity is no longer handed a status (its input is the case id), and it answers with the case status and the status of each page as that transaction read them. The orchestration makes its waits from that answer, not from the routes, so a page decided before the settle ran is not waited for.
- The orchestration waits with `wait_for_external_event` and `when_any`, one wait per waiting page; a kept page is then waited for under the `awaiting_triage` name. It ends when no page waits and answers with the case status the rule gives. A decision for a case started with `stop_after: gate` is refused whatever the case status (also between the gate and the settle).
- How the next change to the orchestration must be made (deferred item of story 1.6 and 1.9): a waiting case is replayed from its history when its decision comes, so a change to what `case_lifecycle` yields (an activity added, removed or reordered, another event name) must be registered as a new orchestration name beside `case_lifecycle`, which keeps running the cases it started; or be deployed only when no case waits. The same is written at the top of `workflow/adapters/orchestration.py`. Nothing is deployed now, and the emulator keeps its state in memory.
- Migration `0003` adds `workflow.human_decision` (unique on page and decision; CHECKs that the actor is a demo role and the decision one of the four; an index on `case_id`; SELECT and INSERT for the service role; its downgrade refuses a table with rows).
- SPA: `components/PagePrompt.tsx` (one page's prompt), `cases/classifications.ts` (reads the classifications once a page waits for the customer), `strings.ts` (`pageType`, `decision`, `percentage`). After an answer the case's progress is read again at once (`useCaseProgress` now follows a read that was out with another one). Decided by the agent, for the owner to confirm (in `deferred-work.md`): after a call that failed without an answer, with a 5xx or with a 2xx that was not the decision, only a "Try again" button for the same decision is offered, also once the server shows the page moved on, because that repeat is what raises a lost event.
- Tests changed in older stories, because the behaviour changed: the settle activity's input and answer (story 1.9 unit and integration tests), a waiting case no longer ends its orchestration (stories 1.7, 1.8 and 1.9 cross-service tests now wait for `awaiting_human` through `wait_for_case_status` and then end the waiting orchestration), the route lists of `workflow` and `web`, the migration head and the grants, and the SPA test that no confidence reaches the screen (it is shown now, never compared).
- `infra/demo/app/`: nothing changed. The apps have no Dapr access control, and the comment on `classification` already names `web` as a reader.

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | A refused decision (`actor_not_human`, `role_not_allowed`) leaves no log line (blind) | medium | `authorise()` raises before the only `logger.info` in `domain/decisions.py`; this is the attempt AD-10 exists to stop | patch |
| 2 | `decision_made` says nothing when the orchestration is missing or dead, and a raise that loses a race with the orchestration's end answers 503 (blind, edge) | medium | `_decision_made` returns silently; the raise is not guarded | patch |
| 3 | The settle's answer is read without a check: a refused or empty answer raises in the orchestrator and the case is never marked failed (edge) | medium | `orchestration.py` indexes the answer after the settle | patch |
| 4 | `PagePrompt`: renders nothing while a retry of an unsettled answer is being sent; the other button can replace a may-be-stored failure with a 409 and lose the retry; a 200 with a wrong body counts as not stored (blind, edge x3) | medium | Read in `PagePrompt.tsx`: `unsettled` is false while `sending`; both buttons stay live after a 5xx; `mayBeStored` excludes `ApiError(200)` | patch |
| 5 | `decidePage` throws a `TypeError` on a null answer; a confidence outside 0..1 is accepted; 0.995 shows as 100% (blind, edge) | low | `client.ts` and `strings.ts`; direct corrections | patch |
| 6 | `useClassifications` marks a waiting page as answered when the list had nothing for it, does not reset when the case changes, and sets state after unmount (blind, edge) | medium | `classifications.ts`: `answeredFor` takes every waiting page | patch |
| 7 | `_follow_pages` keeps the old case status silently when the transition is not allowed (blind) | low | No log line; direct addition | patch |
| 8 | `human_decision` has no CHECK on actor or decision and no index on `case_id`; the downgrade hard-codes the schema (blind) | medium | Migration `v0003` is not deployed, so it can still be changed; the check backs "the AI never decides" in the database; story 1.11's queue filters on the case | patch |
| 9 | No test sends two decisions at once against PostgreSQL (blind) | medium | The case-row lock is the whole concurrency argument and only sequential flows are tested | patch |
| 10 | The real store's "a repeat is answered before the case status is looked at" order is tested only on the fake (gap) | medium | Filed with evidence | patch |
| 11 | The read that follows a progress read in flight when the customer answers has no test (gap) | medium | Filed with evidence | patch |
| 12 | "Try again stays" is tested for a 5xx only, not for a call that got no answer (gap) | medium | Filed with evidence | patch |
| 13 | The cross-service test support drops query strings and answer headers, and leaves a waiting orchestration alive when a test fails (edge x2) | medium | `synthdata_stack.py`; story 1.11's queue is read with a query string | patch |
| 14 | README says a repeat always raises the event, and does not say the customer sees one message for "saved" and "not saved" failures (blind) | low | Read in `README.md`; `web` answers both as `upstream_unavailable` | patch |
| 15 | `test_story_1_8_the_retry_setting_reaches_the_gateway_of_the_real_service` is flaky (implementer; seen here 3 of 4 runs) | medium | `assert 9 == 10`: a run that gives up cancels its siblings, so the call count depends on timing. From story 1.8's fixes | patch |
| 16 | A stored decision whose event is never raised and never repeated leaves the orchestration waiting; the retry lives only in the browser's memory; a case that fails while its lifecycle waits keeps a live orchestration (blind, edge x2) | medium | True; harmless until something follows an accept. Needs a sweep or a wake-up in `workflow` | defer to story 2.4, as a condition of it |
| 17 | Nothing enforces "reachable only from `web`": any caller inside the environment can name a role as actor (blind) | medium | Internal ingress and Dapr are the only bounds; no Dapr access policy exists. The demo is open by decision (AD-9) | defer |
| 18 | Repeats and early decisions leave events nobody consumes in the instance's history (blind) | maybe-false | Needs the real scheduler to see whether they disturb later waits; low to medium | defer to the final Azure session |
| 19 | A case whose pages are all final, one of them failed, would be called completed (edge) | false | A failed page fails its case in the same recording, and a failed case takes no decision, so the rule is never asked about such a case | reject |
| 20 | A repeat of a stored decision on a since-failed case answers 200 while a first decision gets 409 (edge) | low | The repeat changes nothing and tells the caller what is stored | reject |
| 21 | `web` returns whole classifications to the customer, the reason among them (blind) | low | Redacted, synthetic, and the demo is open to both roles (AD-9) | reject |
| 22 | `occurred_at` is taken before the lock; `Recording` doubles as the decision's input; a flag is passed that one caller never needs; the "only one operation" test searches source text; the orchestration's output is built in memory (blind) | low | No caller is harmed today; trail order is story 1.12's deferred item | reject |
| 23 | Focus and announcements of the prompt are untested (blind) | low | No UX specification exists to set a floor (owner's choice) | reject |
| 24 | Tracking files disagree; closed deferred entries keep their old sentence (blind) | false | The sprint status is synced at the end of the workflow; entries are append-only by rule | reject |
| 25 | The settle no longer checks its input (edge, deletion) | low | It takes no status any more; the page rule decides | reject |

## Design Notes

- The decision is stored by the HTTP call, not by an activity: the human gets an answer at once, and the event only wakes the orchestration. That is why a repeat must raise the event again.
- The orchestration now stays alive while a case waits for people, so its body can no longer change freely once a deployed case is in flight (deferred item of story 1.6). Nothing is deployed; say in the notes how the next change must be made.
- Plain words for page types in the prompt: for example `other` at 0.96 reads "This looks like a page that is not a medical document (96%). Discard or keep?".
- The Azure environment is torn down (owner's rule in `CLAUDE.md`). Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice: plain default styling.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run` -- expected: all pass
- `docker build -f services/workflow/Dockerfile -t aiuw-workflow:dev . && docker build -f services/web/Dockerfile -t aiuw-web:dev .` -- expected: both build
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
