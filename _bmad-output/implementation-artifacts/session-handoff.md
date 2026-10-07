# Build session hand-off

Written by the coding agent on 2026-10-08 so that a fresh session can carry on. Read `CLAUDE.md` first (Azure stays down while coding; the test suite is kept at about 500 cases).

## Where the build stands

Branch `architecture/spine-redaction-diagrams`, nothing pushed. Azure resource group `rg-aiuw-demo-wus3` is empty (confirmed 2026-10-07) and stays torn down.

| Story | State | Commit |
| --- | --- | --- |
| 1.7 to 1.12 | done | `7ab21ba` to `22eafb8` |
| 1.13 (owner's addition: case started and completed events, the underwriter's case list) | done | `e938275` |
| 2.1 synthetic manual and rule table | done | `74e2c45` |
| 2.2 and 2.3 `retrieval`: ingestion and hybrid search | done | `7ec6b97` |
| 2.4 fact extraction | done | `9bf5997` |
| 2.5 and 2.6 verdict agent and refer rules; suite cut to 536 tests | done | `cc11e47` |
| local run with real Dapr sidecars recorded | done | `12a74c8` |
| 2.7 result view | done | `e3ebaae` |
| 2.8 agent log | implemented, reviewed, review fixes applied; NOT committed. Verified on its own paths only | — |
| 3.2 rows `r1` and `r2` | implemented, reviewed, review fixes applied; NOT committed. Cross-service tests over the real manual not rerun since the fixes | — |
| 3.1 and 4.1 case set, page set, training set | implemented, reviewed; its review fixes were STILL BEING APPLIED by a background agent when this was written (answer key corrections: see the spec's Review Triage Log); NOT committed | — |
| 3.3 row `r5` on Azure AI Search | spec written (`spec-3-3-...md`), not started | — |
| 3.4 bake-off runner | spec written (`spec-3-4-...md`), not started | — |
| 3.5 to 3.8, 4.2, 4.3 | not started; context in `epic-3-context.md` and `epic-4-context.md` | — |

State of the working tree when this was written (2026-10-08): uncommitted changes of three stories share it. Before anything else:

1. Check that no background agent is still editing: `git status` twice a minute apart should show the same files, and `packages/synthdata` should pass `uv run pytest packages/synthdata/tests/test_synthetic_cases.py packages/synthdata/tests/test_underwriting_manual.py`. If the data fixes look half-done, read `spec-3-1-...md` (Review Triage Log) and finish them: every row marked `patch` there.
2. Run the full checks once: `docker compose up -d --wait`, `uv sync`, `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy packages services`, `uv run pytest --cov`, the SPA checks (`npm --prefix services/web/spa run lint`, `typecheck`, `contracts:check`, `test -- --run`), `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive` and `validate`.
3. Commit by path, one commit per story: 2.8 (`services/web`, `packages/contracts`, the step reads in `services/verdict/src/verdict/adapters`, `packages/synthdata/tests/test_agent_log_end_to_end.py`); 3.2 (`services/retrieval`, `services/verdict/src/verdict/domain`, `services/workflow`, `dapr.yaml`, `infra/demo/app`, `tools/`); 3.1 and 4.1 (`packages/synthdata`, `data/`). Shared files (`README.md`, `deferred-work.md`, `infra/bootstrap/README.md`) go with whichever commit comes first. Set each spec's `status` to `done` and its line in `sprint-status.yaml` to `review`.
4. Then: a look at the result view and the agent log in a real browser against `./tools/dev.sh` (never done), story 3.3, story 3.4, and the rest in order.

If the working tree holds uncommitted changes when you start, they belong to the stories marked "being implemented". Their implementers ran as background agents of the earlier session and may not have finished. For each: read the spec (its task boxes and Implementation Notes show how far it got), run the spec's Verification commands, review, fix, then commit by path (2.7: `services/web`; 3.1 and 4.1: `packages/synthdata`, `data/`; 3.2: `services/retrieval`, `services/verdict`, `services/workflow`, plus their lines in `dapr.yaml` and `infra/demo/app`).

## How each story is built

The `bmad-build` workflow from the project's own copy (`.claude/skills/bmad-build`; the global copy of that skill does not render with this project's BMad 6.12.1): spec, implementation by a subagent given only the spec, three reviewers on the diff (blind, edge-case, verification-gap), triage into the spec's Review Triage Log, fixes by the same implementer, full verification by the main session, local commit. Each spec says the owner asked for unattended work and did not review it.

Things learned in the session:

- Two test runs that use the scheduler emulator at the same time fail each other (shared test task hub). Run `workflow` integration tests and the cross-service tests in `packages/synthdata/tests` one run at a time.
- A reviewer's "this has no test" finding is acted on only for real risk: the owner cut the suite from about 3,500 to 536 cases on 2026-10-08 (budgets per package in `CLAUDE.md`).
- A subagent once left `./tools/dev.sh` running; check for a leftover stack (ports 8000 to 8006, 5100 to 5102) before a local run, and stop what you start.
- The machine's disk filled once (Docker build cache). `docker builder prune -f` and `docker image prune -f` are safe; the owner agreed to clearing unused images and containers. Volumes were left alone.
- Scratch files go in `.work/` inside the project, never outside it (owner's global rule).

## Decisions the owner made in the session (also in `deferred-work.md`)

- The audit trail records `case.started` and `case.completed`; the underwriter has a Cases list (1.13).
- A verdict run refers when any proposed debit or decline had to be dropped.
- A verdict run refers when two bands of one impairment are cited on different facts; how several readings of one condition should be rated needs a medical expert.
- The Python suite is kept at about 500 test cases.

## Open with the owner

- A rule id typed as a whole search query: keep pure fusion and require the top 3 (recommended, tests assume it), or guarantee first place. Not confirmed.
- The manual's page footer says every number on the page is invented, which is not true of its clinical thresholds.
- Whether to add a Dapr access policy so only `web` can call `workflow`'s decision operation.
- Whether an attempt by the model to call a tool that does not exist should be logged as a step.
- The manual's citations and conversion factors were written without network access and need a check against their sources.
- Whether the SPA's tests (about 250) should be cut as the Python suite was.

## After all stories

Bring Azure up once (`infra/bootstrap/README.md` section 2), run every check listed in `deferred-work.md` as "Final Azure test session", then tear down (section 3). The database role steps for each service are sections 4 to 9 of that README; migrations are a manual step because the deploy has none.
