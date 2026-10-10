---
title: 'Story 3.6: Compare two rows on one case'
type: 'feature'
created: '2026-10-08'
status: 'done'
baseline_commit: '4991d657389a26ce6348c739fc9b41eb79426d87'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The scoreboard says which retrieval row wins on average, but an underwriter cannot see what two rows do differently on one real case: the result view shows one verdict run at a time.

**Approach:** A Compare toggle on the result view of a finished case. Turning it on asks `workflow`, through `web`, for a verdict run with each row of the configured pair that the case does not have yet, and shows the two runs side by side with their differences in verdict, reasons and retrieved rules marked.

## Boundaries & Constraints

**Always:**
- Spine AD-5, AD-11, AD-9, AD-2; FR14. `web` gains one route that passes the request for one more verdict run on to `workflow` (the operation exists; underwriter only), and passes `workflow`'s refusals on as they are. A run asked for this way is its own orchestration on the facts already extracted, never changes the case's status, and a repeated request answers the run that exists.
- The pair is a setting of `web`: default `r4` and `r5`, with a fallback pair `r3` and `r5`. `web` answers both pairs to the SPA and decides nothing. The SPA asks for the default pair first; when a row of it is refused as not available it uses the fallback pair, and when that is refused too it says that Compare is not available here, naming the rows. No service gains a list of rows for this.
- Only rows the case has no run for are asked for; a run that exists (done, failed or running) is shown as it is. The toggle is offered only for a case that is completed; turning it off returns to the result view as it was.
- Each pane shows what the result view shows of a run: the row, the verdict with its loading, the confidence, the system reasons, the reasons with rule and effect, and the rules the run retrieved (the rule ids of its agent steps, each once, in the order first seen). A running run says so and the view keeps reading until both are final; a failed run shows its failure.
- Differences are marked in words or a symbol with a text alternative, not by colour alone: a verdict or loading that differs; a reason whose rule the other run does not cite; a retrieved rule the other run did not retrieve. With one run not done, nothing is marked as a difference.
- The SPA holds no underwriting rule: it compares ids and values the services answered. Wording in the strings module; no model output rendered as HTML. Tests follow the owner's rule in `CLAUDE.md`, inside the budgets.

**Never:**
- No change to `workflow`, `verdict` or `retrieval`; no new row; no scoring of which run is right (the answer key never reaches the SPA).
- No Compare for the customer, none across two cases, no more than two rows.
- Do not bring Azure up or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Compare turned on | A completed case with a run on `r3` only; pair `r3`, `r5` in force | One request for `r5`; two panes; the `r5` pane says running, then shows its run | N/A |
| Both runs exist | Runs on both rows of the pair | No request; two panes at once | N/A |
| Default pair not available | `r4` is refused as not available | The fallback pair is asked for and shown | 409 `retriever_not_available` is not shown as an error |
| No pair available | A row of the fallback pair is refused too | "Compare is not available here", with the rows | No panes |
| Verdict differs | `standard` beside `loaded` 50 | Both verdict lines are marked as different | N/A |
| Reasons differ | One run cites `UW-DM-002`, the other does not | That reason is marked as only in this run | N/A |
| Retrieved rules differ | A rule retrieved by one run only | Marked as only in this run; rules both retrieved are not marked | N/A |
| A run failed | The second run ends failed | Its pane shows the failure; nothing is marked as a difference | N/A |
| Case not finished | A case still at triage | No Compare toggle | 409 `pages_not_terminal` from `web` if asked directly |
| Customer role | The customer posts a verdict-run request | Refused as every underwriter route is | 403 |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/operations.py` -- `request_verdict_run` (POST `/cases/{case_id}/verdict-runs`, caller `web`, owner `workflow`); `models/workflow.py` -- `VerdictRunRequest`, `VerdictRunRequested`; `models/verdict.py` -- `VerdictRun`, `Reason`, `VerdictRunList`, `AgentStep.rule_ids`; `models/web.py` -- the home of a small model for the two pairs
- `services/web/src/web/adapters/dapr.py` -- `ServiceClient._call`, `record_decision` and `start_case` as the model for a POST passed on, the sets of refusals passed on (`_SEARCH_REFUSALS`); `adapters/http/api.py` -- `record_decision` (a POST with a body), `TRIAGE_PATH` (a route `web` answers itself), `underwriter_only`; `settings.py`
- `services/workflow/src/workflow/adapters/http/routes.py`, `domain/verdicts.py` (`request_verdict_run`) -- what the operation answers and refuses (`pages_not_terminal`, `retriever_not_available`, `not_found`); not to be changed
- `services/web/spa/src/screens/ResultView.tsx` (`VerdictPane` with its run picker, `RunOnScreen`, `ReasonItem`, `RulePanel`), `ResultView.css`, `result/result.ts` (`useResult`, the `judge` that keeps reading while a run is running, `refresh`), `components/AgentSteps.tsx` and the client's steps reader (retrieved rules), `api/client.ts`, `strings.ts`, `screens/ResultView.test.tsx`, `test/server.ts`
- `services/web/tests/test_result_view.py`, `test_lifecycle.py` -- where a route passed on to `workflow` is tested; `test_api.py` walks every `/api` route for the role check
- `dapr.yaml`, `infra/demo/app/` -- `web`'s environment, if the pair is to be set there (the defaults serve both)

## Tasks & Acceptance

**Execution:**
- [x] `packages/contracts/`, `services/web/` -- the pair setting and its read, the verdict-run request route passed on to `workflow`; schema and SPA types regenerated; tests inside the budget (merge to stay at 45)
- [x] `services/web/spa/` -- the Compare toggle, the requests with the fallback, the two panes, the retrieved rules of a run, the marking of differences, strings; tests over the matrix in the result view's test file
- [x] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; the Azure check of Compare on a real case

**Acceptance Criteria:**
- Given a completed case and the Compare toggle, when the underwriter turns it on, then `web` asks `workflow` for a verdict run with each row of the pair that has none, and the two results are shown side by side with their differences marked.
- Given the pair setting, when it is read, then it is `r4` and `r5` with the fallback `r3` and `r5`, and with `r4` not built the fallback is what is shown.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- `web`: `GET /api/compare-pairs` answers the settings `WEB_COMPARE_PAIR` (`r4`, `r5`) and `WEB_COMPARE_FALLBACK_PAIR` (`r3`, `r5`) as `ComparePairs`; `POST /api/cases/{case_id}/verdict-runs` passes `request_verdict_run` on to `workflow` with `not_found`, `validation_failed`, `pages_not_terminal` and `retriever_not_available` passed on as they are. Both underwriter only.
- SPA: `result/compare.ts` (the pair with its fallback, the requests, the retrieved rules of a run, the two comparisons), `Compare` and `ComparePane` in `screens/ResultView.tsx`, which reuse `RunOnScreen`. The run picked in the one-run view is held by the screen, so it is still picked after Compare.
- A row is asked for once for as long as the screen is open (a repeat can reschedule a run that ended without a result): the request is kept in `Result`. The run list is read every 3 s, with `usePolled`'s back-off, until `verdict` lists the run or 100 reads have been made; then the result's own reading follows it.
- No change to `workflow`, `verdict`, `retrieval`, `dapr.yaml` or `infra/`. Choices and the Azure check are in `deferred-work.md`.

## Spec Change Log

## Review Triage Log

Review 1 (2026-10-08): blind hunter (B), edge-case hunter (E), verification-gap (V). No intent gap, no bad spec.

| # | Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- | --- |
| B5, E1, E4, E5, E6 | A row can be asked for more than once: "Try again" after a later row failed, Compare turned off and on, a pair tried after a row of it was accepted, a double mount | medium | patch | `rowsAtStart` and the answers live in `Compare`, which is unmounted by the toggle; `workflow` schedules a run again when the earlier one ended without a stored result, so a repeat can spend model tokens. |
| B1, E8 | Compare's own reading skips the back-off of `usePolled` | medium | patch | `refresh` is `readNow(true)`; with the services failing the screen reads four routes every 3 s where every other screen slows down. |
| B2, E7 | Waiting for a run that is never listed has no end | medium | patch | The pane says the suggestion is being made for as long as Compare is on. |
| B7, E13 | "Safe to repeat, starts nothing new" is said beside "asking again can start a run once more" | low | patch | `workflow/domain/verdicts.py` schedules again when no run was stored; a direct correction of the wording. |
| B10 | `requestVerdictRun` accepts any text as the status | low | patch | A direct tightening of the guard to the three statuses. |
| B12 | The two panes stack only under the breakpoint of the whole screen, so each is about a quarter of the window | low | patch | One breakpoint in the stylesheet. |
| B13 | The pane's heading and the heading inside it are the same level; an expected "not available" is an alert | low | patch | Direct corrections. |
| B15 | The wording "not both rows of a pair can be run here" is hard to read | low | patch | A direct correction. The settings being absent from `dapr.yaml` and the rule held in two places are rejected: the defaults serve both environments. |
| B9, E2, E15, V-other 1 | A run started for the first row of a pair is not shown when the second row is refused | low | reject | With the shipped pairs the row that is not built is asked first, so nothing is started; the order is recorded for the owner in `deferred-work.md`. No service can say a row is available without starting a run. |
| B3, E12 | `has_more` of the run list is ignored | low | reject | A case has at most one run per row, six in all, far under the list's bound. |
| B4 | The first run of a row is the oldest | false | reject | `verdict` stores one run per case and row. |
| B6 | A failed run cannot be tried again | low | reject | The spec: a run that exists is shown as it is. |
| B8 | A reason with the same rule and another effect is not marked | low | reject | The spec marks a reason by its rule; a different loading is marked on the verdict line. |
| B11 | Branches without a test | low | reject | Counted as V's findings. |
| B14 | Status and budget bookkeeping; the story's checks sit in tests named for story 2.7 | low | reject | Sprint status moves at the end of the review; the overs predate the story; merging into existing tests is the owner's budget rule, and the story is named in the test's comments. |
| E3 | A fallback pair that is the default pair reversed is asked again | low | reject | A setting nobody ships. |
| E9, E10 | A failed read of a run's steps has no retry; pages are still read after the pane is gone | low | reject | "Check again" on the screen reads again; a few reads for nobody. |
| E11 | A pane with neither a run nor an answer says "being made" | false | reject | A row is either in the list at the start or asked for. |
| E14 | A listed running run relies on the result view's own reading, which settles after failed reads | low | reject | The result view's behaviour since story 2.7, not this story's. |
| V1 | A loading that differs under the same verdict is seen by no test | medium | patch | Filed evidence; money is on that half of the mark. |
| V2 | The run picked before Compare is not checked to be still picked after it | low | patch | Filed evidence; the refactor exists for it. |
| V3 | `workflow`'s `validation_failed` is passed on and never exercised | low | patch | Filed evidence; one line in the existing tuple. |
| V4 | The skip for a fallback pair equal to the default is not exercised | low | defer | Filed disposition. |
| V5 | The SPA's shape guards for the two Compare answers are not exercised | low | defer | Filed disposition; the server side is tested. |
| V6 | The hidden-tab rule of Compare's own reading is not tested | low | defer | Filed disposition. |
| V-other 2 | Sprint status beside spec status | false | reject | As B14. |

## Design Notes

- Why the fallback is tried by asking: only `retrieval`, `verdict` and `workflow` know which rows they answer, each for itself. `workflow` refuses a row it may not run before anything is started, so a refusal is the one cheap, true answer to "is `r4` built here".
- When story 3.7 builds `r4` and the three services list it, the default pair comes into force with no change here.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Do not run `tools/dev.sh` unless nothing else holds its ports, and stop what you start. Tests that use the scheduler emulator fail each other when two runs overlap: run them one at a time.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07, and again on 2026-10-08, for the remaining stories to be built in order; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services evals && uv run pytest --cov` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run lint && npm --prefix services/web/spa test -- --run` -- expected: clean, SPA coverage threshold met
- `docker build -f services/web/Dockerfile -t aiuw-web:dev .` -- expected: builds
