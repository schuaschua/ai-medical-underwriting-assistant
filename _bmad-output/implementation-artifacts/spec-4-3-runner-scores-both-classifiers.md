---
title: 'Story 4.3: Runner scores both classifiers and the scoreboard shows the winner'
type: 'feature'
created: '2026-10-08'
status: 'ready-for-dev'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/data/README.md'
  - '{project-root}/evals/README.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Two classifiers can now serve the pipeline, but nothing measures which one labels pages better, how far its confidence can be trusted, or how many pages it sends to the underwriter's queue.

**Approach:** The bake-off runner gains a classification bake-off: it uploads the scored page set once per contender, started with that contender and stopped at the gate, compares every page's stored result with the answer key, and writes `classification.json`. `web` serves that file and the Scoreboard screen shows both contenders with the winner marked.

## Boundaries & Constraints

**Always:**
- Spine AD-17, AD-13, AD-7, AD-9; FR15, FR18. The runner talks to `web` only, with a demo role, as for the retrieval bake-off. Each file of the scored page set (`data/answer-key/page-set.json`) is uploaded once per contender and started with that contender, the run's `eval_run_id` and `stop_after` `gate`, so no extraction and no verdict runs and no page waits for a person. The cases stay out of the queues.
- Per contender, over every page of the set: accuracy is the share of pages whose stored `is_medical` equals the expected one; calibration is, of the pages scored 0.90 or more, the share labelled correctly; queue rate is the share of pages the gate routed to triage; cost per page is a stated figure from `evals/static-metrics.yaml` with its source. Every figure carries the counts behind it. A page with a failed result, or none, counts as wrong, is in no calibration count, and is listed by case and page.
- The winner is the more accurate contender among those whose calibration is at least 0.90, then the one with the lower queue rate; a contender with no page scored 0.90 or more has no calibration and cannot win. No winner when no contender qualifies.
- A contender that cannot be run here is recorded as not measured, with no numbers, and never stops the run. A case that fails or does not finish in time counts every one of its pages as wrong for that contender and is listed.
- The same run checks every stored `reason` of every contender for the planted identifiers of its case, with the redaction check's own rule; a reason that holds one fails the run like a leak, reported by case, page, contender and category, never the value.
- The file records when and against which address the run was made, its `eval_run_id` and whether the AI services were stand-ins; its shape is a contracts model. The rules of the retrieval bake-off hold unchanged: local figures go under `.work/` and say they are stand-in figures, only a whole run against the deployed environment writes `data/scoreboards/`, a run can be resumed, every wait has a deadline, concurrency is bounded, and the exit statuses mean what they mean there.
- `web` serves `classification.json` as it serves the other two (underwriter only, checked against its model, 404 until written). The Scoreboard screen shows a second table under the retrieval one: per contender accuracy, calibration, queue rate and cost per page with their counts, the winner marked in words, "not measured" for a contender without figures, the stand-in notice, and the pages not classified by count. Each table stands alone: one missing file does not hide the other.
- Tests follow the owner's rule in `CLAUDE.md`, inside the budgets (`evals` 15).

**Never:**
- No change to how either contender classifies or to the gate. The runner never routes, decides or labels a page itself, and scores on nothing but the stored result and the answer key.
- No training by the runner. No local scoreboard committed. Do not bring Azure up or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Both contenders scored | The page set, both contenders available | Each file uploaded twice, once per contender; both scored on every page; the file written | N/A |
| Accuracy | 94 pages, 90 with the expected `is_medical` | 90 of 94 | N/A |
| Calibration | 80 pages scored 0.90 or more, 78 of them right | 78 of 80 | No such page: no calibration, cannot win |
| Queue rate | 12 of 94 pages routed to triage | 12 of 94 | N/A |
| Winner | Both calibrated; one more accurate | The more accurate one | A tie on accuracy: the lower queue rate; no winner if none qualifies |
| Not calibrated | The more accurate contender has calibration 0.85 | The other wins if it is calibrated | N/A |
| Contender not available | `doc-intelligence` cannot be run here | Not measured, no numbers; `llm` is scored | The run goes on |
| Page not classified | One page's result is failed | Wrong for that contender, listed | N/A |
| Reason leaks | A stored reason holds a planted surname | The run exits as for a leak; the file names case, page, contender and category | N/A |
| Resume | The run is started again with its `eval_run_id` | No file is uploaded a second time for a contender | N/A |
| Scoreboard shown | `classification.json` with a winner | Two lines with figures and counts; the winner marked | 404 from `web`: "not run yet" for that table alone |

</frozen-after-approval>

## Code Map

- `evals/src/bakeoff/` -- `__main__.py` (the arguments and the exit statuses), `runner.py` (`run`, `run_cases`), `verdicts.py` (`CaseRunner`: upload, start, wait until final), `client.py` (`WebClient`, `upload_key(eval_run_id, case_key)`: the key needs the contender; no classifications read yet), `state.py` (what a run remembers, and what it refuses on a resume), `redaction.py` (`leaked_categories`, `read_page_texts`), `scoreboard.py` (`write_scoreboards`, the winner), `static_metrics.py`, `settings.py` (the published folder's rules), `answer_key.py`; `evals/static-metrics.yaml`; `evals/tests/` and `support/bakeoff_fakes.py`
- `data/answer-key/page-set.json` -- `documents[]` with `case_id`, `file_name`, `pages[]` (`page_number`, `page_type`, `is_medical`, `kind`); `data/README.md`
- `packages/contracts/src/contracts/models/web.py` -- `ScoreboardRun`, `StatedFigure`, `RetrievalScoreboard`, `RedactionScoreboard`, `share`: the home of the classification scoreboard model; `models/classification.py` (`ClassificationList`, the stored result's fields and status), `models/workflow.py` (`StartCaseOptions`: `classifier_contender`, `stop_after`, `eval_run_id`; `CaseProgress` with page statuses), `enums.py` (`ClassifierContender`, `StopAfter.GATE`, `PageStatus.AWAITING_TRIAGE`)
- `services/web/src/web/adapters/http/api.py` -- the classifications read of a case and the progress read (what the runner scores from), `RETRIEVAL_SCOREBOARD_PATH`; `scoreboards.py` (`ScoreboardReader._read`); `services/web/tests/test_scoreboards.py`
- `services/web/spa/src/screens/Scoreboard.tsx`, `scoreboard/scoreboard.ts`, `api/client.ts`, `strings.ts`, `Scoreboard.test.tsx`
- `services/classification` -- how a command for a contender that cannot be run is refused, and what a case then shows through `web` (the signal for "not measured"); `services/workflow` -- `stop_after` `gate` ends a case `completed` with its waiting pages left as routed
- `_bmad-output/implementation-artifacts/deferred-work.md` -- entries left for this story: the classifier's `reason` not checked for copied page content; a test that an unreadable page text leaves its case unchecked
- `.github/workflows/ci.yml` -- the checks on `data/scoreboards` and on the web image

## Tasks & Acceptance

**Execution:**
- [ ] `packages/contracts/` -- the classification scoreboard model; schema and SPA types regenerated
- [ ] `evals/` -- the classification bake-off as a second mode of the one command: the page set reader, the runs per contender, the four figures, the winner rule, the reasons check, the writer, the stated cost per page; `evals/README.md`
- [ ] `services/web/` -- the third scoreboard read; tests inside the budget
- [ ] `services/web/spa/` -- the classifier table on the Scoreboard screen; tests in its test file
- [ ] Tests, at most 15 in `evals` in all: the figures and the winner rule as pure parts, one whole-path run of two or three files with both contenders against the in-process system; the assertion on an unreadable page text that story 3.4's review left for this story
- [ ] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; the Azure run as a step of the final session after the training job

**Acceptance Criteria:**
- Given the scored page set, when the runner scores a contender, then it uploads the set started with that contender and `stop_after` set to `gate`, so no extraction runs.
- Given a finished run, when results are written, then `classification.json` holds accuracy, calibration, queue rate and cost per page for each contender, and the winner by the stated rule.
- Given the file, when the scoreboard is opened, then both contenders' results are shown with the winner marked.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Design Notes

- Accuracy is on medical against non-medical, as `bake-offs.md` says; the page type is not scored.
- The local stand-ins tell page types apart by the generator's headings, so local figures say nothing; they prove the plumbing.
- The LLM contender's confidence may always be 1.0 on the real model (`deferred-work.md`); the calibration figure will show it.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Do not run `tools/dev.sh` unless nothing else holds its ports, and stop what you start. Tests that use the scheduler emulator fail each other when two runs overlap: run them one at a time.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07, and again on 2026-10-08, for the remaining stories to be built in order; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services evals && uv run pytest --cov` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run lint && npm --prefix services/web/spa test -- --run` -- expected: clean
- `docker build -f services/web/Dockerfile -t aiuw-web:dev .` -- expected: builds; the image holds no runner and no answer key
