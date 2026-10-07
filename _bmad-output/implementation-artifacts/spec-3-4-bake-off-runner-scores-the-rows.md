---
title: 'Story 3.4: Bake-off runner scores the rows'
type: 'feature'
created: '2026-10-08'
status: 'ready-for-dev'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/data/README.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The ladder rows exist, but nothing measures them: there is no number for how often a row finds the right rule, how often the suggested verdict is right, or whether redaction left an identifier behind.

**Approach:** Build the bake-off runner in `evals/`: one command that drives the running system through `web`, as a user would, over the synthetic case set and its answer key. It measures rule recall with a fixed query per expected fact, measures verdict accuracy by running every case with every available row, checks redaction on every stored page text, and writes the scoreboard files. Retriever quality and the agent's query writing are measured apart.

## Boundaries & Constraints

**Always:**
- Spine AD-17, AD-11, AD-9; NFR4, NFR7. The runner talks only to `web`, with a demo role on every call; it never reads a database, a blob or another service, and no service ever reads the answer key. It lives in `evals/` (a workspace member, not a service, never in an image) and is the only code besides the generator's tests that reads `data/answer-key/`.
- Rule recall, per row: for every expected fact of every case that expects at least one rule, one search with the query the contracts' query builder makes from the fact's statement and `top_k` 5; a hit when any of that fact's expected rule ids is among the answered items' rule ids. Recall is hits over searches. The same queries go to every row. The search's own `latency_ms` gives the row's latency (median and 95th percentile).
- Verdict accuracy, per row: each case is uploaded once, started once with every available row and one `eval_run_id` for the whole bake-off run; the runner answers the human waits from the answer key's page labels (a page that waits for the customer is kept if its expected label is medical and discarded if not; a page in triage is accepted if medical and denied if not); when the case is final the runner reads its verdict runs. A run is right when its verdict equals the expected verdict and, for a loaded case, its loading equals the expected loading. Accuracy is right runs over cases, per row; a case that failed or did not finish in time counts as wrong for every row and is listed.
- A row that answers "not available" is recorded as not measured, with no numbers; it never stops the run.
- Redaction check, in the same run: the text of every page of every case is read through `web` and normalised with the contracts' function; the run fails if any planted identifier, or any part of a planted name, appears. `redaction.json` reports pages checked, identifiers looked for, any leak by case, page and category (never the value), and how many of the allowed "may also be redacted" strings were masked.
- `retrieval.json` holds, per row: store, chunk set, method, rule recall, verdict accuracy, latency, cost and effort, the counts behind each figure, and the winner: highest verdict accuracy, then rule recall, then lower latency, among measured rows. Cost and effort come from `evals/static-metrics.yaml`, each figure with its source. Both files also record when and where the run was made (the `web` address, the `eval_run_id`, and whether the AI services were stand-ins), and their shape is a contracts model so that `web` and the SPA can show them.
- Figures from a run against local stand-ins are not results: the stand-in's vectors count shared words and its agent is scripted. Such a run writes its files under `.work/` unless told otherwise, and the files say so. Only a run against the deployed environment writes `data/scoreboards/`.
- The runner is gentle on the system: cases run with a bounded concurrency (a setting, default 2) and searches with a small one; every wait has a deadline; a run can be resumed for cases already finished under the same `eval_run_id`.
- `web` gains what the runner needs and nothing more: an eval search (the search operation passed through, underwriter only) and a page's text (underwriter only). Cases started by the runner never appear in the triage queue or the case list (the `eval_run_id` already keeps them out).
- Tests follow the owner's rule in `CLAUDE.md`. `evals` gets a budget of 15 test cases, added to the list there; `web` stays in its budget by merging.

**Never:**
- No scoreboard screen (story 3.5), no Compare (3.6), no classifier scoring (4.3).
- The runner never decides a page from anything but the answer key, and never scores a row on a query other than the fixed one.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something. No local scoreboard is committed to `data/scoreboards/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Recall | The case set, rows `r1` to `r3` available | One search per expected fact with a rule, per row, with the built query; hits counted in the top 5 | A search that fails is counted as a miss and listed |
| Fact with no rule | An expected fact that meets no rule | No search, not in the denominator | N/A |
| Verdict accuracy | A case expected `loaded` 75 | Uploaded once, started with every available row, waits answered from the key, each row's run compared on verdict and loading | N/A |
| Human waits | A blank page waits for the customer; an unsure medical page is in triage | Discarded; accepted | A wait the key cannot answer fails that case, listed |
| Row not available | `r5` answers `retriever_not_available` | Recorded as not measured; the others are scored | N/A |
| Case fails or hangs | A case ends `failed`, or is not final within its deadline | Wrong for every row, listed with its status | The run goes on |
| Winner | Two rows tie on accuracy | The higher recall wins; then the lower latency | No winner when no row was measured |
| Redaction leak | A page text holds a planted name's surname | The run exits non-zero; `redaction.json` names case, page and category | N/A |
| Clean redaction | No planted identifier anywhere | `redaction.json` says so, with the counts | N/A |
| Triage queue | Cases started by the runner | None is listed for the underwriter | N/A |
| Local run | The runner against the local stack with stand-ins | Files under `.work/`, marked as stand-in figures | Refuses to write `data/scoreboards/` unless told the run is against the deployed environment |
| Resume | The runner stopped half-way and is started again with the same `eval_run_id` | Finished cases are not uploaded again | N/A |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/query.py` (`build_fact_query`), `text.py` (`normalise`), `models/retrieval.py` (`SearchRequest`, `SearchResponse`), `models/workflow.py` (`StartCaseOptions`, `CaseProgress`, `PageDecisionRequest`), `models/verdict.py` (`VerdictRunList`), `models/intake.py` (`PageText`, `PageList`), `models/web.py` (payloads only `web` answers: the home of the scoreboard models), `operations.py`, `enums.py` (`RetrieverConfig`)
- `data/answer-key/cases/*.json` and `data/README.md` -- the key's fields: page labels, identifiers with occurrences, `may_also_be_redacted`, expected facts (statement, pages, rule ids), expected rule ids, expected verdict with loading and reason
- `services/web/src/web/adapters/http/api.py`, `adapters/dapr.py` -- the routes that exist: upload (customer only), start with options (underwriter only), progress, decisions, triage, verdict runs, pages, facts; the pattern for a pass-through route and for a bytes route
- `services/retrieval/src/retrieval/domain/rows.py` -- store, chunk set and method of each row (what the scoreboard prints beside each row); `services/workflow/src/workflow/settings.py` -- the rows a case may run with
- `packages/synthdata/tests/support/synthdata_stack.py` -- the whole system running in one process for tests (`web` in front of the real services and the stand-ins): what the runner's own whole-path test drives
- `pyproject.toml` -- workspace members, mypy paths, pytest `testpaths` (`packages`, `services`), coverage `source_pkgs`; `packages/synthdata/tests/test_synthetic_cases.py` -- the guard that nothing under `services/` or `packages/contracts/` names the answer key (`evals/` is allowed to)
- `CLAUDE.md` -- the test budgets per package

## Tasks & Acceptance

**Execution:**
- [ ] `packages/contracts/` -- the scoreboard models (retrieval and redaction), with tests inside the budget; exported schema and SPA types
- [ ] `services/web/` -- the eval search and the page text routes, underwriter only; tests inside the budget
- [ ] `evals/` -- the runner as a small package: its settings (address of `web`, concurrency, deadlines, where to write), the client of `web`, the answer-key reader, recall, verdict accuracy with the answering of human waits, the redaction check, the winner rule, the writers; `static-metrics.yaml` with a source for every figure; a command line entry; a README on how to run it locally and against the deployed environment
- [ ] `pyproject.toml`, `CLAUDE.md` (the budget line for `evals`), `.github/workflows/ci.yml` -- the new member in the checks
- [ ] Tests, at most 15 cases in `evals`: the pure parts (recall counting, the verdict comparison, the winner rule, the leak check on normalised text, answering a wait from the key) and one whole-path run of two or three cases against the in-process system with the stand-ins, writing both files
- [ ] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; the Azure run of the bake-off as a step of the final session

**Acceptance Criteria:**
- Given the local system with the stand-ins, when the runner runs over the case set, then it writes both files with recall, accuracy and latency for `r1`, `r2` and `r3`, the rows that are not built as not measured, a winner, and a clean redaction report, all marked as stand-in figures.
- Given the runner's code, when it is read, then recall uses only the built query and the answer key's rule ids, and the verdict comparison uses only the answer key's expected verdict.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Design Notes

- Why recall and accuracy are apart: recall with a fixed query says how good a row's search is; accuracy says how good the whole agent is with that row. An agent that writes better queries must not make a weak retriever look good (NFR7).
- Static metrics: cost per 1,000 searches and build effort per row are not measured by the runner; they are stated figures with sources (the price list, the story sizes). Keep them few and sourced.
- The local case set takes minutes with the stand-ins; in Azure each case costs model calls for every page and every row, against a shared token limit. That is why concurrency is low and resumable.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Do not run `tools/dev.sh` unless nothing else holds its ports, and stop what you start. Tests that use the scheduler emulator fail each other when two runs overlap: run them one at a time.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services evals && uv run pytest --cov` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa run typecheck` -- expected: clean
- `docker build -f services/web/Dockerfile -t aiuw-web:dev .` -- expected: builds, and the image holds nothing of `evals` or the answer key
