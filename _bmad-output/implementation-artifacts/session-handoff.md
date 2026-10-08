# Build session hand-off

Written by the coding agent on 2026-10-08, at the end of the session that built the last eight stories. Read `CLAUDE.md` first (Azure stays down while coding; the test suite is kept at about 500 cases).

## Where the build stands

Every story of the four epics is built. Branch `architecture/spine-redaction-diagrams`, nothing pushed (26 commits ahead of the remote branch, this note included). Azure resource group `rg-aiuw-demo-wus3` is empty and stays torn down; nothing in this session touched Azure.

| Story | State | Commit |
| --- | --- | --- |
| 1.7 to 3.2, 4.1 | done in earlier sessions | up to `9bd6744` |
| 3.3 row `r5` on Azure AI Search | done | `b9ac324` |
| 3.4 bake-off runner | done | `672d314` |
| 3.5 retrieval scoreboard | done | `4991d65` |
| 3.6 Compare two rows on one case | done | `d28b713` |
| 3.7 row `r4` with a reranker (first an LLM reranker, `a61ea1c`; changed to Cohere Rerank on Foundry, not committed yet) | done | `a61ea1c` |
| 3.8 row `r6` with agentic retrieval | done | `a4de0a7` |
| 4.2 Document Intelligence classifier and its training job | done | `dbce613` |
| 4.3 runner scores both classifiers | done | `d03bd7a` |

The working tree was clean at `d03bd7a` and the last full run there was green: 545 Python tests (coverage 90.8%), 279 SPA tests, lint and types clean, the images build, `infra/demo/app` validates. No background agent was running, no local stack was left up, and the compose containers were stopped.

Sprint status marks every story `review`; no epic is marked done and no retrospective was run.

## What was proven locally, and what was not

Proven on 2026-10-08 against `./tools/dev.sh` with real Dapr sidecars and the stand-ins:

- The ingestion job loaded the search stand-in's index and made the knowledge base.
- The retrieval bake-off ran over all 22 cases: all six rows answered and were scored, redaction clean, both files written.
- The training pages were prepared through `web`, the training job trained the local classifier, and a second run trained nothing.
- The classification bake-off scored both contenders over 94 pages; the reasons check was clean.
- `web` answered the three scoreboard files to the underwriter and refused the customer; a case uploaded as the customer completed, and the two verdict runs Compare asks for (`r4`, `r5`) were made beside its `r3` run.

Every figure from that run is a stand-in figure and says nothing about the real models.

Not done: nobody has looked at any screen in a real browser. The result view, the agent log, the Scoreboard and Compare are proven by tests and by their routes answering. Compare's two panes stack by a container query that only tests have exercised.

## Decisions the coding agent made that the owner has not confirmed

Each spec of this session says it was not reviewed before implementation. The choices are listed in `deferred-work.md` as "Owner to confirm" or "Owner to decide" entries (35 in all, earlier sessions included). The ones that shape the design:

- **4.2, how training pages are redacted.** The spine says the training job calls no service and only `intake` may call Azure AI Language, so nothing in it says who redacts the training pages. Built: a dev tool (`python -m bakeoff.training_pages`) uploads each page through `web` as a hidden case, fetches the redacted file, and an operator uploads the folder to `classifier-training`. The job refuses any blob whose content is not the one the prepared list names. The owner was asked on 2026-10-08 and had not answered; the alternatives are a job that calls `intake`, or `classification` calling the Language service itself.
- **3.8, a verdict run on `r6`.** The service lists the facts, makes one search per fact itself, and asks the model once, with no tools, to compose the proposal. A rule counts as read when a search returned its chunk, so the "rule not read" check cannot fail on that row.
- **3.8, REST and not the SDK.** `azure-search-documents` 12.1.0b2 installs but sends through its own transport, refuses the plain-HTTP stand-in and brings its own retries, so the knowledge base calls are REST on `2026-08-01-preview`. The package is not a dependency.
- **3.7, the reranker.** Cohere Rerank on Foundry (`Cohere-rerank-v4.0-fast`, the owner's choice of 2026-10-08, after the catalogue listed it for West US 3), in place of the LLM reranker the row was first built with (`spec-3-7b-row-r4-on-cohere-rerank.md`). Built against the local stand-in; the deployment and the call are unproven in Azure.
- **3.7, `verdict`'s wait for one upstream call** went from 12 s to 25 s so that it stays above `r4`'s and `r6`'s 20 s deadline. It is one setting and applies to every row and to `extraction` calls.
- **3.6, the Compare pair** is a setting of `web` (`r4`, `r5`; fallback `r3`, `r5`); the SPA finds out which rows can run by asking.
- **4.2, the role on the training container.** `classification` holds write access as `azure.md` lists, though the job only reads.
- **Costs.** Every cost on both scoreboards is empty: no figure was entered from memory.

## Things a later change must respect

- The three lists of rows (`retrieval`, `verdict`, `workflow`) are held equal by a test in `packages/synthdata/tests/test_foundry_standin.py`, which also reads `dapr.yaml` and `infra/demo/app`. All six rows are listed locally and in the `app` stack.
- A retrieve on `r6` and a timed-out rerank call on `r4` are never sent again: a second attempt could not finish inside the row's deadline.
- The index definition has a vectorizer; an index made before story 3.8 has none and the job refuses to make the knowledge base over it. No index exists in Azure yet.
- The local stand-ins keep the search index, the knowledge base and the classifier in memory: `./tools/dev.sh` reloads the first two at every start; the classifier needs `./tools/train-local.sh` after every start.
- A scoreboard file from before story 3.5 does not fit the model and is shown as an error; run the runner again.
- Tests are over budget in `verdict` (58 of 55), `workflow` (117 of 110), `synthdata` (50 of 45), `contracts` (64 of 60), `intake` (55 of 50) and `classification` (42 of 40); the suite is 545 against about 500. No story of this session raised a package's count except `evals`, which is new and at its 15.

## How each story was built

The `bmad-build` workflow from the project's own copy (`.claude/skills/bmad-build`; the global copy does not render with this project's BMad 6.12.1): spec, implementation by a subagent given only the spec, three reviewers on the diff (blind, edge-case, verification-gap), triage into the spec's Review Triage Log, fixes by the same implementer, full verification by the main session, local commit.

Things learned:

- Two test runs that use the scheduler emulator at the same time fail each other. Stories were built one at a time for that reason and because they share `README.md`, `deferred-work.md` and `dapr.yaml`.
- A reviewer's "this has no test" finding is acted on only for real risk, and then inside an existing test.
- `./tools/dev.sh` started in the background does not stop on a signal to its wrapper: send `TERM` to the `bash ./tools/dev.sh` process itself, then check ports 8000 to 8006, 5100 to 5103 and 3500 to 3506.
- The machine's disk fills with Docker's build cache. `docker builder prune -f` and `docker image prune -f` are safe; the owner agreed to clearing unused images and containers. Volumes were left alone.
- Scratch files go in `.work/` inside the project, never outside it (owner's global rule).

## Open with the owner (from earlier sessions, still open)

- A rule id typed as a whole search query: keep pure fusion and require the top 3 (recommended, tests assume it), or guarantee first place.
- Decided by the owner on 2026-10-08, and built: the manual's page footer no longer says every number is invented. It says the document is synthetic, its ratings are invented, its clinical thresholds follow public guidelines, and how many of the items to check have been checked ("0 of 145 items checked against their sources", counted from the review file).
- Decided by the owner on 2026-10-08: no Dapr access policy is added for the demo, so any service in the environment may call `workflow`'s decision operation.
- Decided by the owner on 2026-10-08, and built: an attempt by the model to call a tool that does not exist is logged as a refused step (`AgentStep.tool` null, `AgentStep.asked_tool` the name asked for; migration `0002` of `verdict`).
- Decided by the owner on 2026-10-08, and built: the manual is generated from a review file, `packages/synthdata/src/synthdata/manual-review.yaml`, which lists its 38 citations, 80 band edges said to follow a guideline, 18 unit conversions and 9 reading rules. All 145 are still `unreviewed`: the medical expert or anyone with the sources works through it (`uv run python -m synthdata review`; how to record a check is in `data/README.md`). A corrected citation or conversion is applied on generation; a corrected band edge or reading rule is refused until a developer carries it into the manual's definition and the cases.
- Whether the SPA's tests (279) should be cut as the Python suite was.

## Next

1. The owner reads the unconfirmed decisions above and the "Owner to confirm" entries of `deferred-work.md`.
2. Look at the screens once in a real browser against `./tools/dev.sh`.
3. With the owner's go-ahead, the one Azure session: bring the environment up (`infra/bootstrap/README.md` section 2), run every check listed in `deferred-work.md` as "Final Azure test session" (79 entries), then tear down (section 3). The seeding order is the ingestion job, the training pages and the training job (section 10), then the two bake-offs. The database role steps for each service are sections 4 to 9; migrations are a manual step because the deploy has none.
