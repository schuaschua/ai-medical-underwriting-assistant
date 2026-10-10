---
title: 'Story 3.5: Retrieval scoreboard'
type: 'feature'
created: '2026-10-08'
status: 'done'
baseline_commit: '672d314a34cdb9f054e51c41293488cec4fa52ac'
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

**Problem:** The bake-off runner writes its figures to a file, but nobody can see them: there is no screen that shows which retrieval row won and by how much.

**Approach:** `web` serves the scoreboard files read-only and the SPA gains a Scoreboard screen for the underwriter role: one line per ladder row with what the row is and what was measured, the winner marked, and "not measured" for a row without figures. The screen says plainly when the figures come from stand-ins.

## Boundaries & Constraints

**Always:**
- Spine AD-17, AD-9, AD-2; FR14. The scores are files: `web` reads `retrieval.json` and `redaction.json` from one folder (a setting, default the repository's `data/scoreboards`, the image's own copy in Azure), checks each against its contracts model and answers it as it is. No service stores, computes or accepts a score.
- The screen shows, per row `r1` to `r6`: the row, store, chunk set, method, rule recall, verdict accuracy, latency (median and 95th percentile), cost and effort. Recall and accuracy are shown as a percentage with the counts behind them (`38 of 39`). A stated figure shows its amount and unit, and its source on request; a figure nobody stated shows as not stated.
- A row that was not measured says "not measured" and shows no number, whatever the file holds for it. The winner is the row the file names, marked in words and not by colour alone; no winner is marked when the file names none.
- The screen says when and against which address the run was made, and when `stand_ins` is true it says above the table that these are stand-in figures and not results. It lists how many cases were not scored and how many searches failed, when any.
- Each row also shows how many of its verdict runs failed or are missing, apart from wrong verdicts: one more count on the row's contracts model, filled by the runner, so that a failure of the system is not read as a wrong answer.
- Below the table, one line from `redaction.json`: clean or not, pages and cases checked, leaks by count, cases not checked.
- No file yet: the screen says the bake-off has not been run, with no table of empty rows. A file that does not fit the model is an error the screen shows as such, never a half-drawn table.
- Underwriter role only, in the navigation; wording in the strings module; no model output rendered as HTML. Tests follow the owner's rule in `CLAUDE.md`, inside the budgets.

**Never:**
- No Compare (story 3.6), no classification scoreboard (story 4.3), no chart, no sorting or filtering.
- No write route, no upload of scores, no reading of the answer key or of `.work/` by an image. No stand-in figures committed to `data/scoreboards/`.
- Do not bring Azure up or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Scoreboard shown | `retrieval.json` with `r1`, `r2`, `r3`, `r5` measured, winner `r5` | Six lines; four with figures and counts; `r5` marked as the winner | N/A |
| Row not measured | `r4` and `r6` with `measured` false | "not measured", no numbers | N/A |
| Stand-in figures | `run.stand_ins` true | A notice above the table: stand-in figures, not results | N/A |
| No winner | `winner` null | No row is marked | N/A |
| Not run yet | The folder holds no `retrieval.json` | "The bake-off has not been run yet" | 404 `not_found` from `web` |
| File does not fit | `retrieval.json` is not valid JSON or breaks the model | An error message, no table | 500 `internal_error`; the log names the file, never its content |
| Runs failed | A row with 2 runs that failed | The line shows them apart from its right runs | N/A |
| Customer role | The customer asks for a scoreboard route | Refused as every underwriter route is | 403 |
| Redaction not read | `retrieval.json` is there, `redaction.json` is not | The table is shown; the redaction line says not available | N/A |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/models/web.py` -- `RetrievalScoreboard`, `RetrievalRowScore` (add the count of runs that failed or are missing; null when not measured, like the other numbers), `RedactionScoreboard`, `ScoreboardRun`, `StatedFigure`; `packages/contracts/tests/test_scoreboards.py` (`RUN`, `LADDER`, `row()` builders)
- `evals/src/bakeoff/verdicts.py` (`run_is_right`, `CaseOutcome.runs`), `scoreboard.py` (`retrieval_scoreboard`, `_NUMBERS`) -- where the new count is filled: for a scored case, a row with no run or a run whose status is not done; `evals/tests/test_runner.py`
- `services/web/src/web/settings.py` -- `spa_dir` is the model for a `scoreboards_dir` setting; `adapters/http/api.py` -- routes `web` answers itself have a literal path and sit on `role_checked` with `underwriter_only` (`TRIAGE_PATH`, `TriageReader` on `app.state` wired in `adapters/http/app.py`); `adapters/http/spa.py` -- safe file reading
- `services/web/Dockerfile`, root `.dockerignore` -- `data` is ignored today: let `data/scoreboards` alone into the web image and set `WEB_SCOREBOARDS_DIR`; the folder must exist in git (a short `README.md` in it, which `web` ignores)
- `dapr.yaml` -- locally point `web` at `.work/scoreboards`, where a local run of the runner writes
- `services/web/tests/` -- `conftest.py` (`settings`, `client`), a new `test_scoreboards.py`; `test_api.py` walks every `/api` route for the role check
- `services/web/spa/src/navigation.tsx` (`SCREENS`), `api/client.ts` (`request<T>`, a shape guard per response, `ApiError`), `strings.ts` (`percentage`), `polling/polled.ts` (`usePolled`, `"settled"`), `screens/CaseList.tsx` with `cases/caseList.ts` and `CaseList.test.tsx` (the model screen and its test), `audit/auditTrail.ts` (a `not_found` state), `test/server.ts`, `api/contracts.gen.ts` (regenerate)
- `.github/workflows/ci.yml` -- the web image check that no answer key is in the image stays true

## Tasks & Acceptance

**Execution:**
- [x] `packages/contracts/`, `evals/` -- the count of failed or missing runs per row, in the model, the runner and their existing tests; schema and SPA types regenerated
- [x] `services/web/` -- the setting, a reader that checks a file against its model, `GET /api/scoreboards/retrieval` and `GET /api/scoreboards/redaction`, tests inside the budget (merge to stay at 45)
- [x] `services/web/spa/` -- the Scoreboard screen, its path and reader, strings, navigation entry, one test file over the matrix
- [x] `services/web/Dockerfile`, `.dockerignore`, `data/scoreboards/README.md`, `dapr.yaml` -- the files reach the image; local runs show the local figures
- [x] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; the Azure check that the deployed `web` shows the published file

**Acceptance Criteria:**
- Given a `retrieval.json`, when the underwriter opens the Scoreboard, then every row of the ladder has a line with its figures or "not measured", and the winner is marked.
- Given the web image, when it is built, then it holds `data/scoreboards` and nothing else of `data/`.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- The count is `RetrievalRowScore.failed_runs`: required on a measured row, null otherwise; `right_runs + failed_runs <= cases`. The runner fills it from `CaseOutcome.run_failed` (a scored case with no run of the row, or one whose status is not done). A case that was not scored adds nothing to it.
- `web`: `Settings.scoreboards_dir` (`WEB_SCOREBOARDS_DIR`), `adapters/http/scoreboards.py` (`ScoreboardReader`), two routes on `role_checked` with `underwriter_only`. Missing file 404 `not_found`; a file that is unreadable, over 1 MB, not JSON or not the model 500 `internal_error`, logged by file name and error type only.
- SPA: `scoreboard/scoreboard.ts` (`SCOREBOARD_PATH`, `useScoreboard` on `usePolled` with a read that never fails and a judgement of `settled`, so the files are read once and again on request), `screens/Scoreboard.tsx`, two guarded readers in `api/client.ts`, strings under `strings.scoreboard`.
- Image: `.dockerignore` now ignores `data/*` except `data/scoreboards`; the `web` Dockerfile copies it to `/app/data/scoreboards`. CI checks that `/app/data` holds that folder only and that the route answers 200 or 404.
- A file written by the runner of story 3.4 has no `failed_runs` and is refused (500): the local files in `.work/scoreboards/` must be written again by a new local run.

## Spec Change Log

## Review Triage Log

Review 1 (2026-10-08): blind hunter (B), edge-case hunter (E), verification-gap (V). No intent gap, no bad spec.

| # | Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- | --- |
| B1, B2 | The screen does not say that a failed or missing run counts as wrong and so can decide the winner | medium | patch | Accuracy still divides right runs by all cases and the winner rule is unchanged; the count is shown without its meaning. One sentence, shown when any row has such runs. |
| B3, E1 | Nothing checks that `redaction.json` is of the same run as `retrieval.json` | medium | patch | The redaction line is shown under the retrieval run's header; a stale or stand-in report beside a deployed table would read as that run's "clean". |
| B4, E2 | A wrong or missing folder looks like "not run yet", and nothing is logged | low | patch | `is_file()` false is 404 for any folder; one log line of the resolved folder at start, a warning when it is not there. |
| B5 | A file of an older runner gets the general error wording | low | reject | Only the local scratch files of story 3.4 are such files; a second wording for one transition is not worth a branch. |
| B6 | "No stand-in figures in `data/scoreboards`" is prose only | low | patch | One CI line keeps a committed file with `stand_ins` true out. |
| B7, V1 | The CI image check accepts 404, which a wrong folder also answers | medium | patch | Filed evidence: deleting `WEB_SCOREBOARDS_DIR` from the Dockerfile passes CI. |
| B8, E5 | "Read again" shows no sign of the read | low | reject | The file does not change while the screen is open; a reading state over a shown board is more than a correction. |
| B9, V3 | The 1 MB limit is seen by no test | low | patch | Filed evidence; one more entry in the existing loop. |
| B10 | The SPA guard's comment says more than its code for a row that is not measured | low | patch | `counts.every(isNullOr(isCount))` accepts counts; a direct correction. |
| B11 | The ladder is written out again in the SPA | low | reject | `web` checks the file against the model before answering; a seventh row is a contracts change that fails the SPA's tests. |
| B12 | The intro words the winner rule a second time | low | reject | Cosmetic; the rule is the spine's and does not change per run. |
| B13 | The changed runner test no longer has a row with right, wrong and failed runs together | low | patch | Moving the failed run to another case restores it. |
| B14 | Sprint status beside spec status | false | reject | Sprint status moves at the end of the review. |
| B15, V-other 1 | "1 runs failed", and "failed" for runs that are missing | low | patch | A direct correction of the wording. |
| E3 | A file removed between the check and the read answers 500 | low | reject | Not everyday use for a file copied into an image. |
| E4 | A measured row with no cases shows "0 of 0" | low | reject | The runner refuses an empty case set. |
| E6 | `az acr build` may not honour the `.dockerignore` exception | maybe-false | reject | Already an Azure check in `deferred-work.md`; a failing image build would be seen at once. |
| E7 | Any file in `data/scoreboards` reaches the image | low | reject | CI builds from a checkout, which holds only committed files. |
| V2 | The checkout default of the folder is exercised by no test | low | patch | Filed evidence; one assertion in an existing test. |
| V4 | The runner's printed count of failed runs is asserted nowhere | low | defer | Filed disposition. |
| V5 | Several SPA shape guards are not exercised | low | defer | Filed disposition; the server's model check is tested. |
| V-other 2 | An answer-key entry with no outcome is in no count | false | reject | `run_cases` records an outcome for every entry whenever a row is available. |
| V-other 3 | `dapr.yaml`'s folder is relative to the app's working directory | maybe-false | reject | Dapr starts an app in its `appDirPath`; B4's log line shows the folder at start, and the browser check of the local stack will confirm it. |

## Design Notes

- The count of failed runs comes from the review of story 3.4 (`deferred-work.md`): `run_is_right` is false for a failed run as for a wrong verdict.
- A static file does not change while the screen is open: read it once, with a way to read again.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Do not run `tools/dev.sh` unless nothing else holds its ports, and stop what you start. Tests that use the scheduler emulator fail each other when two runs overlap: run them one at a time.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07, and again on 2026-10-08, for the remaining stories to be built in order; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services evals && uv run pytest --cov` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run lint && npm --prefix services/web/spa test -- --run` -- expected: clean, SPA coverage threshold met
- `docker build -f services/web/Dockerfile -t aiuw-web:dev .` -- expected: builds; the image holds `data/scoreboards` and no answer key, case file or runner
