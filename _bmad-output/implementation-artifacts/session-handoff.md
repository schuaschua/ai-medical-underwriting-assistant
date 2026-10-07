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
| 2.7 result view | being implemented when this was written; spec `spec-2-7-...md`, not committed | — |
| 3.1 and 4.1 case set, page set, training set | being implemented; spec `spec-3-1-...md`, not committed | — |
| 3.2 rows `r1` and `r2` | being implemented; spec `spec-3-2-...md`, not committed | — |
| 2.8 agent log | spec written (`spec-2-8-...md`), not started: starts after 2.7 (both touch `web` and the SPA) | — |
| 3.3 to 3.8, 4.2, 4.3 | not started; context in `epic-3-context.md` and `epic-4-context.md` | — |

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
