---
title: 'Story 1.12: Audit trail view for a case'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'd886dea34bf8b33be8693bfc2c45b1b25b520678'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The audit trail exists only as an API answer: an underwriter cannot read on screen who or what did each step of a case and when, and a failed step does not say why.

**Approach:** Add an audit trail screen for the underwriter that lists a case's events in order with actor, action, page and time, reachable from the triage queue and by case id. Make the trail itself fit to be read: a stable order in which a cause never shows after its effect, and the error code of a failed stage on the record.

## Boundaries & Constraints

**Always:**
- Spine AD-8, AD-9, AD-19. The screen shows what `GET /api/cases/{case_id}/audit` answers and works nothing out: no status, order or actor is computed in the browser beyond formatting.
- Order: the trail is answered in the order `workflow` recorded the events (its own record time, then insertion order), so a page's classification always comes before its route and its route before a decision on it, whatever the clocks of other services say. Each event still shows the time the work was done (`occurred_at`).
- The audit record gains an optional `error_code`, set for `stage.failed` events from what `workflow` already stores (deferred item of story 1.6). The contracts model, its tests and the exported SPA types change in this story.
- Actors are shown as recorded: a human actor as its demo role in plain words; an AI actor as the service and the model deployment (or the rule, for the gate), both parts visible. Actions are shown in plain words from the strings module, with the page number where the event has a page. Redaction counts and the gate's route and threshold are shown from the event's detail; no other content of a document is.
- The trail is for the underwriter role only; the customer is refused by the route (already so) and has no link to it. The screen is reachable from each row of the triage queue and from a case id typed or pasted into a field on the underwriter's side.
- Times are shown in the viewer's local time with the UTC time available (for example as a title); the answer stays ISO 8601 UTC.
- The trail answer is bounded by a limit setting and says when more exist.

**Never:**
- No editing, filtering by actor, export or search of the trail. No agent-step log (story 2.8). No case list across sessions.
- No write path to the audit table is added or changed in meaning; it stays append-only.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Read a decided case | A case redacted, classified, routed and decided on | Every redaction, classification, route and human decision in order, each with actor, action, page and time | N/A |
| AI actor | `classification:<deployment>`, `intake:azure-ai-language`, `workflow:gate` | Service and model deployment (or rule) both shown | N/A |
| Human actor | `customer`, `underwriter` | The demo role in plain words | N/A |
| Failed stage | A `stage.failed` event | Shown with its error code in plain words | An unknown code is shown as it is |
| Order across clocks | A route whose `occurred_at` is earlier than its classification's | The classification is listed first | N/A |
| Statuses against events | Every page status change of a finished case | Each has a matching event: `uploaded` by `document.redacted`, `classified` by `page.classified`, a route by `page.routed`, a decision by its action, `failed` by `stage.failed` | N/A |
| Unknown case | A case id that was never started | "No such case" | 404 passed on |
| Malformed id | Text that is not a case id | Refused in the field, no call made | N/A |
| Customer | Customer role calls the audit route | Refused | 403 `role_not_allowed` |
| Long trail | More events than the limit | The first events up to the limit, and a note that more exist | N/A |
| Case still running | The trail is open while the case moves | New events appear by polling, without a reload | Polling stops when the case is final |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/audit.py` -- `AuditRecord` (add `error_code`), `AuditAction`, `RouteDetail`, `ai_actor` (how an AI actor is composed: read it before splitting one for display); `models/workflow.py` -- `AuditTrail` (add the "more exist" flag); exported schema and SPA types
- `services/workflow/src/workflow/adapters/db.py` -- `audit_event` table (`error_code` and the record time are stored already), `SqlCaseStore.audit_trail` (ordered by `occurred_at` today); `domain/ports.py`; `domain/cases.py` -- `read_audit_trail`; `settings.py`; `tests/support/workflow_fakes.py` -- the fake store must order the same way
- `services/web/src/web/adapters/http/api.py` -- `read_audit_trail` is underwriter-only already; `adapters/dapr.py`
- `services/web/spa/src/navigation.tsx` -- the one table navigation and routing read (the triage queue of story 1.11 is the newest entry); the triage queue screen and its rows; `api/client.ts` -- how a contracts answer is checked; `cases/caseProgress.ts` -- the polling pattern (back-off, hidden tab, stop when final); `strings.ts` -- page status, page type and error code wording
- `packages/synthdata/tests/` -- cross-service tests; `support/synthdata_stack.py`

## Tasks & Acceptance

**Execution:**
- [x] `packages/contracts/` -- `error_code` on the audit record (set only with `stage.failed`), the trail's "more exist" flag, tests, exported schema and SPA types
- [x] `services/workflow/` -- the trail in record order with the limit; `error_code` on the answer; the fake store in step; tests against PostgreSQL, including the order-across-clocks row and a check that walks a finished case's page statuses against its events
- [x] `services/web/` -- tests that the new fields pass through
- [x] `services/web/spa/` -- the audit trail screen (table of time, actor, action, page, detail), the link from each triage row, the case id field, polling while the case is not final, the empty, unknown and error states; strings for every action and error code; tests
- [x] `packages/synthdata/tests/` -- one cross-service test through `web`: a case run to decisions, whose trail read as the underwriter holds every expected event in causal order with the expected actors
- [x] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; mark the items this story closes

**Acceptance Criteria:**
- Given the local stack with the stand-in in `mixed` mode, when a case is run, one page discarded, one kept and then accepted, and the underwriter opens its trail, then the screen lists the redaction, every classification, every route and every decision in causal order, AI actors show service and model deployment, and human actors show the demo role.
- Given that case, when its page statuses are compared with its trail, then every status change has a matching event.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- Contracts: `AuditRecord.error_code` (optional; refused on any action but `stage.failed`; a stage result's record may not name another code than the result), `AuditTrail.has_more` (required). Stage services do not set the code: `workflow` fills it from the column it already stored.
- Order: the first build ordered by `recorded_at`, then the event id. The review found that not stable (ties fall to random bits of a UUIDv7, the record time is read before the case's row is locked), so migration `0004` adds `audit_event_seq`, numbered by the database, and the trail and the failure read of the progress are ordered by it alone. The Code Map's "no migration needed" no longer holds. The migration numbers existing rows by the former order with the append-only row trigger disabled for that one statement and enabled again in the same transaction; the index on `(case_id, occurred_at)` is replaced by one on `(case_id, audit_event_seq)`.
- Limit: `WORKFLOW_AUDIT_TRAIL_LIMIT`, default 500, 1 to 5000; the domain function refuses a limit under 1 and the store takes no default.
- A stored row with a code outside the catalogue is answered as `stage_failed`, a code on a row that is no failure is left out; each is logged with the case id and event id only.
- Screen: `/underwriter/audit?case=<id>`. The trail's events name a page by id and say nothing of the case status, and the contracts changes allowed are the two above, so the screen reads `GET /api/cases/{id}/progress` before each read of the trail, for the page numbers and for the status that ends the polling. Polling also ends when `has_more` is true, when the case is unknown, and on a refusal (4xx other than 429). The field and its refusal are derived from the address.
- Wording of the threshold: "Confidence the gate asked for: 92.5%", exactly as recorded. The word "threshold" is not used because story 1.9's test `works out no status or route in the browser` forbids it in `strings.ts`; that test was left as it is.
- Actors: `classification`, `extraction`, `retrieval` and `verdict` show "model deployment <name>", `intake` "redaction service <name>", `workflow:gate` and `workflow:case-lifecycle` their own wording, any other actor its two parts. A failed step shows its trace id as a reference.
- Earlier tests changed: story 1.6's two "time order" tests (domain and PostgreSQL) now assert the order of writing; its test of the audit route expects `has_more` and the code on the failed event; two `classification` tests and the contracts round-trip sample gained `error_code: null`; the list of bundled migrations and the SPA's list of the underwriter's navigation links gained an entry; two tests that called the store's `audit_trail` now pass a limit.
- Review round (2026-10-07): the fourteen findings of the first review were fixed as above. Docker was not available on the build machine in that round (disk full), so the two image builds, the migration `0004`, and every container-backed test were not run after it; the container-free Python tests and the whole SPA suite were.

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | The case id field and the refusal go out of step with the address when only the query changes (blind, edge) | medium | `AuditTrail.tsx` seeds `typed` at mount only; back, forward and the navigation link leave a stale id or a stale refusal | patch |
| 2 | A trail over the limit can never show a new event, yet the screen says it will and keeps polling (blind, edge) | medium | The store answers the first events; `Trail` prints the "new events appear" line whenever the case is not final | patch |
| 3 | One stored row with a code outside the catalogue, or a code on another action, turns every read of that trail into a 500 (blind, edge) | medium | `_audit_record` calls `ErrorCode(row.error_code)` and validates; no constraint ties the column to `stage.failed` | patch |
| 4 | "Insertion order" is a UUIDv7 whose bits within a millisecond are random, and the record time is read before the case row is locked; the fake store breaks ties by position, and the old index serves no read any more (blind, edge x2, implementer) | medium | `db.py` orders by `recorded_at`, `audit_event_id`; the intent asks for a stable order in which a cause never shows after its effect | patch |
| 5 | The fake store sets `error_code` without the validation the real store applies (blind) | low | `model_copy(update=...)` in `workflow_fakes.py`; direct correction | patch |
| 6 | The hook retries for ever an error no retry can mend, and sends a queued read after unmount (blind, edge x2) | medium | `auditTrail.ts` settles only on `not_found` | patch |
| 7 | The gate's threshold is shown through `percentage()`, which rounds down and caps at 99% (edge) | medium | A threshold of 0.925 would read 92%, and 1.0 would read 100% only by the exact-one rule; the figure shown must be the one used | patch |
| 8 | The client refuses a trail whose event omits `error_code`, which the generated type allows (blind, edge, gap) | low | `isAuditEvent` in `client.ts`; direct correction | patch |
| 9 | The limit is range-checked only as a setting: a caller can pass 0 or less, and the adapters default it (blind, edge) | low | `SqlCaseStore.audit_trail(limit=DEFAULT...)`; direct correction | patch |
| 10 | The checks in `getAuditTrail` are tested only together; the limit's bounds are untested and its default is asserted only in a container test (gap x2, blind) | medium | Filed with evidence | patch |
| 11 | An unknown service's second part is labelled "model deployment", and `workflow:case-lifecycle` reads as a rule (blind) | low | `actorText`; the next services will not all be model deployments; direct correction | patch |
| 12 | A failed step shows no reference to follow into the logs (blind) | low | `AuditEventRow` drops `trace_id`; `ErrorMessage` already words a reference; small addition for `stage.failed` rows only | patch |
| 13 | The spec's task boxes are unticked and its Implementation Notes empty (blind) | low | Read in the spec; the implementer recorded its decisions only in `deferred-work.md` | patch |
| 14 | The trail does not say who started a case or when it ended, and a finished case can be reached only by its id (blind) | medium | True: the catalogue has no case-level action but `stage.failed`, and the intent rules out a case list. An owner decision | defer |
| 15 | "Check again" pressed while a read is out is untested (gap) | low | Filed as defer by the reviewer: at most one poll of delay | reject |
| 16 | Redaction categories are shown as the service names them; the UTC time is in a title only; decision and classification rows have no detail (blind) | low | The intent allows the title; the categories are a setting whose names are not fixed yet; the record carries no more | reject |
| 17 | The status walk infers, test helpers are copied, planning text still says "time order", the limit is not in the `app` stack, tracking files disagree (blind) | low | No caller is harmed; the sprint status is synced at the end of the workflow | reject |

## Design Notes

- Why record order and not `occurred_at`: two services' clocks set `occurred_at`, and `workflow` is the single writer, so its own order is the causal one (deferred item of story 1.9). The spine says "events in time order"; this keeps that meaning and removes the clock dependence.
- This closes Epic 1. The Azure environment is torn down (owner's rule in `CLAUDE.md`). Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice: plain default styling.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run` -- expected: all pass
- `docker build -f services/workflow/Dockerfile -t aiuw-workflow:dev . && docker build -f services/web/Dockerfile -t aiuw-web:dev .` -- expected: both build
