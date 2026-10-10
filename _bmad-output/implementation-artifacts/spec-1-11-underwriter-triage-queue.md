---
title: 'Story 1.11: Underwriter triage queue'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'b40350bdc8926c0333b8893e07453179487a0ffd'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Pages the gate could not settle, and pages a customer kept, wait at `awaiting_triage` with no screen on which an underwriter can see them or decide.

**Approach:** Give the underwriter a triage queue: `workflow` lists the waiting pages across cases, `web` joins each with its classification and serves its thumbnail, and the SPA shows the queue with Accept and Deny, which go through the decision operation story 1.10 built.

## Boundaries & Constraints

**Always:**
- Spine Operations table, AD-10, AD-9, AD-4, AD-19. `GET /pages?status=` on `workflow` is the cross-case queue: pages in the asked status, oldest waiting first, leaving out every page of a case that belongs to an eval run and every page of a case that is `failed` or `completed` (deferred item of story 1.9). The answer is bounded by a limit setting and says when more exist.
- `web` exposes the queue to the underwriter role only, as one payload per page: case, page, page number, thumbnail address, predicted `page_type`, `is_medical`, `confidence`, `reason`, and how the page got there (the gate, or kept by the customer) if the trail says so cheaply; otherwise leave that out. `web` composes it from `workflow` (the queue) and `classification` (the case's classifications, for the contender the case was started with); it holds no rule and no state. The composed payload is a contracts model.
- `web` serves a page's thumbnail from `intake` as PNG, to either role, with the response headers every route has. It is the redacted page; no route serves anything of an original.
- Accept and deny go through `POST /api/cases/{case_id}/pages/{page_id}/decisions` with the request's role as actor; the rules stay in `workflow`. After a decision the page leaves the queue.
- The customer role is refused on the queue route with `role_not_allowed` (403), and the SPA shows the queue only in the underwriter's navigation.
- The SPA reads the queue by polling through its one client module, formats the percentage itself, shows the reason as text (never as HTML), and puts its wording in the strings module. An empty queue says so.

**Never:**
- No extraction: an accepted page stays `extracting` (story 2.4). No audit screen (story 1.12). No document viewer (story 2.7).
- No queue filtering or routing logic in `web` or the SPA beyond showing what the services answer.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Open the queue | Underwriter; pages `awaiting_triage` in two cases | Every such page listed, each with thumbnail, predicted type, confidence as a percentage and reason | N/A |
| Eval-run case | A waiting page of a case started with an `eval_run_id` | Not listed | N/A |
| Failed or completed case | A waiting page of such a case | Not listed | N/A |
| Accept | Underwriter accepts a listed page | Page `extracting`; `page.accepted` with actor `underwriter`; gone from the queue | Failure shown on the row, with a way to try again |
| Deny | Underwriter denies a listed page | Page `denied`; `page.denied`; gone from the queue | As above |
| Decided elsewhere | The page was decided in another tab | 409 `not_awaiting_decision`; the row goes at the next read, with a plain note | N/A |
| Customer calls the queue | `X-Demo-Role: customer` | Refused | 403 `role_not_allowed` |
| Status not a queue | `GET /pages?status=` with a missing or unknown status | Refused | 422 `validation_failed` |
| Thumbnail | `GET /api/pages/{page_id}/thumbnail` | The PNG `intake` holds | 404 for an unknown page |
| Classification missing | A queued page whose classification cannot be read | The page is still listed, without the prediction, and can be decided | The queue does not fail as a whole |
| Many pages | More waiting pages than the limit | The first pages up to the limit, and a flag that more exist | N/A |
| Empty | No page waits | "Nothing is waiting" | N/A |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/models/workflow.py` -- `PageQueueQuery`, `QueuedPage`, `PageQueue` (add the "more exist" flag); `models/web.py` -- home of payloads only `web` answers with (add the composed triage payload); `models/classification.py` -- `Classification`, `ClassificationList`; `operations.py` -- `list_pages_by_status`, `read_page_thumbnail`, `list_classifications`, `record_decision`
- `services/workflow/src/workflow/adapters/db.py` -- `page_status` and `case_status` tables (`eval_run_id`, `case_status`, `classifier_contender` are on the case); `adapters/http/routes.py`; `domain/ports.py` (`CaseStore`); `settings.py`
- `services/web/src/web/adapters/http/api.py` -- `role_checked`, `underwriter_only`, `any_role`; the decision and classifications routes of story 1.10; `adapters/dapr.py` -- `ServiceClient` (`_call` for JSON; the thumbnail is bytes, so it needs a call of its own with the same deadline, tracing and refusal handling); `adapters/http/middleware.py` -- the security headers
- `services/intake/src/intake/adapters/http/page_routes.py` -- the thumbnail route as it answers today
- `services/web/spa/src/navigation.tsx` -- the one table navigation and routing read; `screens/UnderwriterHome.tsx`; `screens/UploadDocument.tsx` (the customer prompt of story 1.10 is the pattern for a decision with an error state); `api/client.ts`; `strings.ts` (page type names from story 1.10)
- `packages/synthdata/tests/` -- cross-service tests; the stand-in's `mixed` mode gives one case pages on all three routes

## Tasks & Acceptance

**Execution:**
- [x] `packages/contracts/` -- the queue's "more exist" flag, the composed triage payload, tests, exported schema and SPA types
- [x] `services/workflow/` -- the queue read with its three exclusions, order and limit; the route with its query validation; tests against PostgreSQL
- [x] `services/web/` -- `GET /api/triage` (or the path the contracts name) for the underwriter, composed from `workflow` and `classification`, tolerant of a missing classification; `GET /api/pages/{page_id}/thumbnail`; tests
- [x] `services/web/spa/` -- the triage queue screen in the underwriter's navigation: rows with thumbnail, type, percentage, reason, Accept and Deny, the error and decided-elsewhere states, the empty state, polling; tests
- [x] `packages/synthdata/tests/` -- one cross-service test through `web`: a case with an unsure page and a kept page, both listed, one accepted and one denied, and an eval-run case that is not listed
- [x] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; mark the items this story closes; append anything that needs Azure

**Acceptance Criteria:**
- Given the local stack with the stand-in in `mixed` mode, when a case is run and the customer keeps a page, then the underwriter's queue lists the unsure page and the kept page with thumbnail, predicted type, confidence and reason, and accepting one and denying the other empties the queue and leaves `page.accepted` and `page.denied` events with actor `underwriter`.
- Given the customer role, when the queue route is called, then it is refused, and the customer's navigation has no queue.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- Contracts: `PageQueue.has_more`; `QueuedPage.classifier_contender` and `queued_by` (new enum `QueuedBy`: `gate`, `customer`); `TriagePage` and `TriageQueue` in `models/web.py`. `GET /api/triage` is `web`'s own path and is not in the operation registry.
- `workflow`: `domain/queue.py` holds the rule (only `awaiting_triage` and `awaiting_customer` are queues; anything else is 422), `SqlCaseStore.queue` the read. Besides eval-run, failed and completed cases it leaves out a case started with `stop_after: gate` (the rule of `case_takes_decisions`), so nothing is listed that the decision operation would refuse. Order is by the page's `updated_at`. The limit is `WORKFLOW_PAGE_QUEUE_LIMIT` (default 100).
- `web`: `adapters/http/triage.py` composes the queue, one read of `classification` per case, at most `WEB_TRIAGE_MAX_CONCURRENT_READS` (8) at once, the whole within `WEB_LIFECYCLE_TIMEOUT_SECONDS`; a reading that fails or is late leaves its page listed without it.
- Deviation from the design note on the thumbnail: an `<img>` cannot send `X-Demo-Role`, and every `/api` route but health answers 400 without it (AD-9, tested since story 1.3). The SPA reads the thumbnail through its API client and draws it on a canvas, so neither the role check nor the content security policy was loosened. Recorded for the owner in `deferred-work.md`.
- The screen does not show `is_medical`: the SPA's existing source check keeps medical-or-not out of the browser's code. The payload carries it.
- A row whose decision may be stored without the case being told stays, with "Try again", also once the server no longer lists the page (the pattern of story 1.10).

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | A poll that delists the page while the first send is still out unmounts the row, so a failure that follows shows nothing and the decision is never sent again (blind, edge) | medium | `TriageRow.tsx`: `owed` is false while a first send is out; the queue is read every 3 s and the call may take longer | patch |
| 2 | A refusal on a repeat send drops the owed state, though the earlier call may have stored the decision (blind) | medium | `mustRepeat` follows the latest error only | patch |
| 3 | A row marked "decided elsewhere" has no way out if the server keeps listing the page (blind) | low | The state is never reset; direct correction | patch |
| 4 | A failed thumbnail is never read again (blind) | low | The effect depends on the address only | patch |
| 5 | `getThumbnail` accepts any address under `/api/pages/` (blind) | low | `startsWith` check; direct correction | patch |
| 6 | `TriageReader`: readings run on when the request is cancelled, and an error that is not a `DomainError` turns the whole queue into a 500 (blind, edge x2, gap) | medium | `asyncio.wait` does not cancel its tasks; only `DomainError` is caught, against "the queue does not fail as a whole" | patch |
| 7 | The `PageQueue` contract text leaves out the `stop_after: gate` exclusion (blind) | low | Docstring in `models/workflow.py`; direct correction | patch |
| 8 | One SPA test waits on a real 3 s poll with a 6 s wait, above the test timeout (blind) | medium | `TriageQueue.test.tsx`, "says plainly when a page was decided elsewhere"; a flake in waiting | patch |
| 9 | The rule "may be stored" exists twice, in `PagePrompt.tsx` and `TriageRow.tsx`, and the row's copy is tested for 5xx and 404 only (gap, blind) | medium | Filed with evidence; two copies will drift | patch |
| 10 | The SQL queue's `stop_after` exclusion is seen by no test against PostgreSQL (gap) | medium | Filed with evidence | patch |
| 11 | `WORKFLOW_PAGE_QUEUE_LIMIT` is not shown to reach the route (gap) | medium | Filed with evidence | patch |
| 12 | `web`'s bound, deadline and "time left" for the readings are unobserved (gap, blind) | medium | Filed with evidence | patch |
| 13 | An owed decision is lost when the underwriter leaves the screen or reloads (edge) | medium | carried: the same cause as story 1.10's finding 16, deferred there as a condition of story 2.4 (`workflow` wakes the orchestration itself) | defer (listed already) |
| 14 | Every poll reads the classifications of every case with a waiting page, with no cache (blind) | low | Bounded by the queue limit; one underwriter in a demo | reject |
| 15 | The spec's design note still names an `<img>`; the change log is empty (blind) | false | The Implementation Notes record the canvas and why; the change log is for review loop-backs. The fix asked for is an edit of this spec | reject |
| 16 | Tracking files disagree; deferred entries keep old pointers (blind) | false | The sprint status is synced at the end of the workflow; entries are append-only by rule | reject |
| 17 | `TriagePage.thumbnail_path` is any text; `GET /pages?status=awaiting_customer` has no caller; tie-break order untested; `TriageReader` typed against the concrete client; a long README line (blind) | low | No caller is harmed | reject |

## Design Notes

- `web` asks `classification` once per case that has a queued page, not once per page, and in parallel with a bound.
- The thumbnail is fetched by the browser as an image from `web`; the SPA's `fetch` rule (one client module) is about API calls, and an `<img>` address built by the client module keeps to it.
- The Azure environment is torn down (owner's rule in `CLAUDE.md`). Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice: plain default styling.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run` -- expected: all pass
- `docker build -f services/workflow/Dockerfile -t aiuw-workflow:dev . && docker build -f services/web/Dockerfile -t aiuw-web:dev .` -- expected: both build
