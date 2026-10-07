---
title: "Story 2.8: Query the agent's log"
type: 'feature'
created: '2026-10-08'
status: 'ready-for-dev'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The verdict agent logs every search and rule read, but an underwriter cannot see that log: there is no way on screen to tell which searches and rules led to a suggestion, so the suggestion cannot be explained or challenged.

**Approach:** Expose the agent's step log through `web` to the underwriter, by run and by case with `tool` and `rule_id` filters, and open a run's steps as a drill-down from the "verdict suggested" event in the audit trail and from the result view.

## Boundaries & Constraints

**Always:**
- Spine AD-15, AD-8, AD-9, AD-19; FR17. `web` adds two read routes, underwriter only, each a pass-through of `verdict`'s operation: a run's steps, and a case's steps with optional `tool` and `rule_id` filters. A filter value that is not a tool or not a well-formed rule id is refused with 422; an unknown run is 404.
- Every step is shown in order with its step number, tool, arguments, the fact it was about, the rules returned or read, its outcome (done, refused or failed, with the code in plain words), latency and time. A refused or failed call is as visible as a done one.
- Steps beyond a list's limit can be read: the reads take a cursor (the last step number seen) and the screen offers "more" when the answer says more exist (deferred item of stories 2.5 and 2.6).
- The drill-down opens from a `verdict.suggested` event in the audit trail (the event's reference is the run) and from the run shown in the result view; the event's row names the retriever configuration from its detail. On the drill-down the underwriter can narrow the steps by tool and by rule id; the narrowing is done by the server, not in the browser.
- Arguments, queries and rule ids come from a model: they are rendered as text, never as HTML, and long values wrap or are cut on screen with the whole value available.
- Wording in the strings module; plain default styling; read by the one API client with shape checks.

**Never:**
- No write to the step log from anywhere but `verdict`'s agent; no editing, deleting or re-running from the screen.
- No customer access to the log. No Compare view (story 3.6).
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Steps of a run | Underwriter reads a finished run's steps | Every tool call in order with all its fields | 404 for an unknown run |
| Filter by tool | A case's steps with `tool=search_rules` | Only the searches | 422 for a value that is not a tool |
| Filter by rule | A case's steps with `rule_id=UW-DM-002` | Only steps that returned or read that rule | 422 for a malformed id |
| Both filters | `tool=read_rule&rule_id=UW-DM-002` | Only reads of that rule | N/A |
| Refused step | A `read_rule` the agent was not allowed | Shown with outcome refused and its code in plain words | N/A |
| More than the limit | A run with more steps than one answer holds | The first steps, a flag that more exist, and the rest with the cursor | N/A |
| Drill-down from the trail | The underwriter selects a "verdict suggested" event | That run's steps open beside or under it | A run that cannot be read shows a plain fault |
| Drill-down from the result | The underwriter selects "how was this reached" on a run | The same steps | N/A |
| Customer | Customer role calls either route | Refused | 403 `role_not_allowed` |
| Run still running | Steps asked for while the agent works | The steps so far; reading again shows more | N/A |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/models/verdict.py` -- `AgentStep` (with `outcome` and `error_code`), `AgentStepQuery` (add the cursor), `AgentStepList` (`has_more`); `operations.py` -- `list_run_steps` and the case agent-steps read; `audit.py` -- the detail of `verdict.suggested` (its retriever configuration)
- `services/verdict/src/verdict/adapters/http/routes.py`, `domain/run.py`, `adapters/db.py` -- the two step reads as they answer today (bounded, no cursor)
- `services/web/src/web/adapters/dapr.py`, `adapters/http/api.py` -- `ServiceClient._call`, `underwriter_only`, and the read routes story 2.7 added for the result view (the pattern for these two)
- `services/web/spa/src/` -- `screens/AuditTrail.tsx` and `components/AuditEventRow.tsx` (where the event row is drawn and the drill-down opens); the result view of story 2.7 (the run on screen); `api/client.ts`; `polling/polled.ts`; `strings.ts`
- `packages/synthdata/tests/test_verdict_end_to_end.py` -- a case run to its verdict through the real services, with a step log to read

## Tasks & Acceptance

**Execution:**
- [ ] `packages/contracts/`, `services/verdict/` -- the cursor on both step reads; schema and SPA types regenerated
- [ ] `services/web/` -- the two routes and client calls, underwriter only, filters validated and passed on
- [ ] `services/web/spa/` -- the steps drill-down (table of steps, the two filters, "more"), opened from the audit trail's "verdict suggested" row and from the result view; client functions with shape checks; strings
- [ ] Tests, within the budgets of `CLAUDE.md`: one whole-path test through `web` (a run's steps in order, the two filters, the cursor, the customer refused), and SPA tests for the drill-down opening, a refused step being shown, and the filters asking the server
- [ ] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; mark the cursor item done

**Acceptance Criteria:**
- Given a case run to its verdict on the local stack, when the underwriter selects its "verdict suggested" event, then the run's steps open in order with tool, arguments, fact, rules, outcome, latency and time, and narrowing by tool or rule shows only matching steps.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Design Notes

- Tests follow the owner's rule in `CLAUDE.md` ("Tests: keep the suite small"): a test per acceptance criterion and for the guards of real risk, whole paths before parts, the Python budgets per package kept, and the same restraint in the SPA's tests.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers and local stacks you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice: plain default styling.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run` -- expected: all pass
- `docker build -f services/web/Dockerfile -t aiuw-web:dev . && docker build -f services/verdict/Dockerfile -t aiuw-verdict:dev .` -- expected: both build
