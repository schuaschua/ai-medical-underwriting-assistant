---
title: 'Story 3.2: Baseline rows r1 and r2'
type: 'feature'
created: '2026-10-08'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '12a74c8bc9e8f5d34a6518d8e405534587b4f40c'
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Only row `r3` of the retrieval ladder exists, so nothing can show what chunking alone, or adding full-text search to vectors, is worth.

**Approach:** Add the two baseline rows behind the same search operation: `r1`, vector search over plain fixed-size chunks, and `r2`, vector search over the one-rule `smart` chunks. The ingestion job gains the `fixed` chunk set from the same parsed manual and the same embedding model. A verdict run on `r1` reads rules from the `fixed` set.

## Boundaries & Constraints

**Always:**
- Spine AD-11, AD-12, AD-15, AD-16; NFR4 (fairness: the same manual, the same embedding model, the same search operation and result shape; only the row differs).
- `fixed` chunks are cut from the manual's body text in reading order, page furniture left out, at a fixed size with an overlap. Size and overlap are settings (default 350 words with 35 words of overlap) and are part of what the ingestion run records, so a change re-cuts the set. A `fixed` chunk has no model-written context line: it is the plain baseline, and its embedded text is its own text. Its `chunk_id` is derived from the chunk set and its position, stable across runs of the same manual and settings. Its `rule_ids` are the rules whose definition marker lies inside its text; rules it only mentions are its references. Its section, impairment and manual page are those of where it starts.
- The ingestion job writes both sets (which sets is a setting, default both), each idempotent and each with its own self-checks, removal guard and run record; a failure in one set leaves the other as it was.
- `r1` is exact cosine nearest-neighbour over `fixed`; `r2` the same over `smart`. Neither uses full-text search. Both answer the common result shape, with `score` between 0 and 1, larger is better, and a deterministic tie rule. Both keep the search's short deadline, the same-embedding-deployment guard and the read-only snapshot.
- `GET /rules/{rule_id}?retriever_config=r1` answers from the `fixed` set: the chunk that holds the rule's definition marker, with the rule ids it refers to. Rows on the `smart` set answer the `smart` chunk as before.
- A verdict run may be commanded for `r1` and `r2`. On `r1` the effect of a reason is read from that rule's own definition inside the fixed chunk (from its marker to the next marker or the chunk's end); a definition cut off before its rating cannot bear out a debit or decline, so the reason is dropped and the run refers. That is the baseline's honest weakness, not a fault to work around.
- The rows a case may run with are named in three places (`retrieval`'s row table, `verdict`'s runnable rows, `workflow`'s setting): all three gain `r1` and `r2`, and one test outside `services/` holds them equal.
- Tests follow the owner's rule in `CLAUDE.md`: stay inside the budgets (`retrieval` 57 of 55, `verdict` 58 of 55, `workflow` 117 of 110, `synthdata` 49 of 45) by merging or replacing weaker tests.

**Never:**
- No reranker, no Azure AI Search, no agentic retrieval (stories 3.3, 3.7, 3.8). No scoring (story 3.4). No change to `r3`'s behaviour or to the `smart` chunks.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Ingest `fixed` | The manual, an empty `fixed` set | Fixed-size chunks covering the body text in order, each with the rules defined in its span, embedded with the same model | N/A |
| Every rule is somewhere | The `fixed` set against the rule table (test outside `services/`) | Every rule's definition marker lies in at least one `fixed` chunk | N/A |
| Rerun | Same manual and settings | Nothing written, no model call | N/A |
| Changed size | Another chunk size | The `fixed` set is cut again; `smart` untouched | N/A |
| Search `r1` | A query, `r1`, `top_k` 5 | Up to 5 `fixed` chunks by vector similarity, common shape | N/A |
| Search `r2` | The same query, `r2` | Up to 5 `smart` chunks by vector similarity only (a query that matches only by a rare word does not get the full-text boost `r3` gives) | N/A |
| Read a rule on `r1` | `GET /rules/UW-DM-002?retriever_config=r1` | The `fixed` chunk holding that definition, chunk set `fixed` | 404 for a rule no chunk defines |
| Verdict on `r1` | A case run with `r1` | A run keyed on the case and `r1`, reasons checked against the fixed chunk's own definition of each rule | A definition cut off before its rating: the reason is dropped, the run refers |
| Verdict on `r2` | A case run with `r2` | As `r3`, with `r2`'s search | N/A |
| Rows still not built | `r4`, `r5`, `r6` | Refused as not available, as today | 409 `retriever_not_available` |
| Start with three rows | A case started with `r1`, `r2`, `r3` | One run per row on the same facts, then the case completes | N/A |

</frozen-after-approval>

## Code Map

- `services/retrieval/src/retrieval/domain/rows.py` -- the ladder table (chunk set, method, built or not); `domain/chunker.py` -- the `smart` cut from parsed layout (sections, furniture, self-checks) whose walk the `fixed` cut reuses; `domain/ingest.py` -- the plan (add, rewrite, move, remove), the removal guard, the content hash, the run record; `domain/search.py` -- `hybrid_search`, `rank_items`, `read_rule`, `SearchStats`; `domain/fusion.py`; `adapters/index.py` -- `nearest_statement`, `matching_statement`, `defining_statement`, the read-only snapshot; `adapters/db.py` -- the chunk table (chunk set column, context line, references), `ingest_run`; `ingest.py` -- the job; `settings.py`; `migrations/` (head `0001`, already deployed nowhere but edited in place before: add a new migration if the table must change)
- `packages/contracts/src/contracts/enums.py` -- `ChunkSet` (`fixed`, `smart`), `RetrieverConfig`; `models/retrieval.py` -- `RuleReadQuery`, `RuleText`
- `services/verdict/src/verdict/domain/` -- the runnable rows constant, `effects.py` (`rating_in`: the rating read off a rule's text), `state.py`, `toolbox.py` (`read_rule` passes the run's row to `retrieval`), `adapters/dapr.py`
- `services/workflow/src/workflow/settings.py` -- `available_retriever_configs`; `dapr.yaml` and `infra/demo/app/` -- where the three services' settings are passed
- `packages/synthdata/tests/` -- `test_manual_ingestion_end_to_end.py`, `test_manual_search_end_to_end.py`, `test_verdict_end_to_end.py`, `support/synthdata_stack.py` (`ingested_manual`, `LocalRetrieval`, `LocalVerdict`); `verdict_standin.py` (reads rule text as the agent would)

## Tasks & Acceptance

**Execution:**
- [x] `services/retrieval/` -- the `fixed` cut and its checks; the job writing both sets with a run record each; rows `r1` and `r2` in the table and the search (vector only, their score and tie rule); the rule read by chunk set; settings; a migration only if the table needs one
- [x] `services/verdict/` -- `r1` and `r2` runnable; the effect read from a rule's own definition inside a chunk that holds several
- [x] `services/workflow/`, `dapr.yaml`, `infra/demo/app/` -- the available rows; the job's settings
- [x] Tests, within the budgets: `retrieval` (the fixed cut, `r1` and `r2` against PostgreSQL with hand-made vectors, the rule read on `r1`), `verdict` (the effect inside a multi-rule chunk and the cut-off definition), and outside `services/` one test over the real manual (every rule in a `fixed` chunk; `r1`, `r2`, `r3` answer the same shape; the three row lists agree) and one case run with all three rows
- [x] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; the Azure checks (real embeddings for about 150 more chunks; recall of `r1` and `r2` is only meaningful there)

**Acceptance Criteria:**
- Given the local stack with the manual ingested, when the same query is searched with `r1`, `r2` and `r3`, then each answers the common shape from its own chunk set and method.
- Given a case started with `r1`, when its verdict run reads a rule, then the text comes from the `fixed` set.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

Decisions made while building, where the spec left room.

- **The cut.** `cut_fixed_chunks` walks the manual as the `smart` cut does (same furniture, same headings) and cuts the body's words into runs. In a chunk's text the words of a paragraph are a space apart and two paragraphs a line break, so a rule's own definition (one paragraph in the manual) can be told from its neighbours. The real manual gives 150 chunks.
- **Ids.** `fixed-0001`, `fixed-0002`, by position. Four digits; a set of more than 9,999 chunks is refused, since the ids would no longer sort as text.
- **Front matter.** Text before the first section's own heading has section `0` and impairment "Front matter" (a search result must name an impairment). This also holds when the layout has no roles and the contents page's lines read as headings.
- **`Chunk.rule_ids`** is the stored field (none, one or several); `rule_id` is a property for a `smart` chunk. A rule whose marker lies in an overlap is in two chunks; a rule read answers the later one.
- **Run record without a migration.** For the `fixed` set `ingest_run.prompt_digest` holds a digest of size and overlap, and the chat deployment and context line are empty. A column of its own would be plainer; open with the owner in `deferred-work.md`.
- **One job, a run per set.** The manual is read and parsed once, a failed or stopped parse is remembered, and one deadline covers all sets. Whatever one set raises, the others are run and reported; the job exits 1 if any failed. Its last log line is one per set and carries `chunk_set=`.
- **Sets on different manuals.** At its end the job compares the manual each stored set was last built from; when they differ it logs `chunk_sets_built_from_different_manuals` with the sets named and exits 1. The job's span names the sets that failed.
- **Removal guard.** It counts the rules the stored chunks define and the new chunks no longer do, for both sets (in `smart` a chunk is a rule, so nothing changes there). A recut to another size is therefore never refused for its number of chunks, whatever else changed, and needs no special case. `RETRIEVAL_INGEST_ALLOW_LARGE_REMOVAL` is now a list of chunk sets, not a flag.
- **Size and overlap.** Words, default 350 and 35. The overlap is from 1 word to half the size, in the settings and in Terraform: with none, a cut inside a marker leaves a rule in no chunk.
- **Self-checks of the fixed cut.** Pages and headings as for `smart`; a rule defined twice; page furniture inside a paragraph (headings excepted, checked per paragraph, never across two); a marker formed only by joining two paragraphs; a rule referred to and defined nowhere; a rule whose marker is in no chunk; too many chunks.
- **Search.** `vector_search` serves `r1` and `r2`. Score is `(1 + cosine similarity) / 2`; ties go by `chunk_id`. Scores are comparable within a row only: `r3`'s fused score tops out at 2/61.
- **A set never ingested.** A search or a rule read on a row whose chunk set has no run record is `retriever_not_available` (409), for every row, in place of an empty answer or a 404. This changes `r3` on an empty index too.
- **Rule read on `fixed`.** The text is the whole chunk. The references answered are those of the rule's own definition (marker to the end of its paragraph), including rules defined elsewhere in the same chunk; the chunk's stored references (everything it mentions) are not used for the read.
- **`verdict`.** `RUNNABLE_RETRIEVER_CONFIGS` is a constant. `effects.definition_of` ends a definition at the end of its paragraph (or at the next marker in the same paragraph), so a rating printed in a worked example bears out nothing. The prompt says `read_rule` returns "the manual text that holds the rule's definition".
- **The three row lists.** `retrieval`'s `BUILT_ROWS`, `verdict`'s constant and `workflow`'s default; the two user messages are built from the constants. `packages/synthdata/tests/test_foundry_standin.py` holds them equal with the list in `terraform.tfvars`, without containers. `dapr.yaml` passes no list (the default). Terraform accepts only built rows, each once.
- **Not done here.** `workflow`'s own setting still accepts any row of the ladder (its tests run with `r4`). The model stand-in of `packages/synthdata` reads a definition to the next marker, not to the end of its paragraph. `web`'s rule route passes no row, so the result view shows the `smart` chunk for an `r1` run (story 3.6).
- **Tests.** `retrieval` 57 (two added in the review round, for the un-ingested set and the rule read's references), `verdict` 58, `workflow` 117, `synthdata` no net change. What was merged is listed in `deferred-work.md`.

## Spec Change Log

## Review Triage Log

Two layers ran (blind, edge-case); the verification-gap layer is left out since the owner's rule of 2026-10-08 keeps the suite small.

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | A `fixed` set that was never ingested answers "nothing found": a search on `r1` is 200 with no items and every rule read is 404, so a verdict run refers as if the manual had no rule and the bake-off scores `r1` at zero (blind, edge x2) | high | `rows.py` marks `r1` built unconditionally and the guard on ingested sets was removed; the `fixed` run may fail on the real layout while `smart` is written | patch |
| 2 | The two sets can be built from different manuals and the rows go on answering (blind, edge) | medium | Each set runs on its own; NFR4 asks for the same manual for every row | patch |
| 3 | Each set gets the whole job deadline, so the job can run twice as long as the platform allows, and a parse cancelled by the deadline is sent to the layout model again (blind, edge x2) | medium | `ingest_manual` opens its own timeout per set; `_ReadOnce.parse` caches only `LayoutFailed`; an error that is not an `IngestError` in the second set hides the first set's outcome | patch |
| 4 | The removal guard means positions for `fixed`, not rules, one switch opens it for both sets, and a changed size with a changed manual is refused (blind, edge) | medium | `_prepare` in `ingest.py` | patch |
| 5 | On `r1` reading one rule unlocks the references of its neighbours in the chunk, and a rule referred to by a rule in the same chunk cannot be read (blind) | medium | `cut_fixed_chunks` gives a chunk every rule it mentions and drops those it defines; `verdict` grants `read_rule` by those references (AD-15) | patch |
| 6 | On a `fixed` chunk a rule's definition runs to the next marker or the chunk's end, across paragraphs, so a rating printed in a worked example could bear out a debit the rule itself does not state (blind, edge) | medium | `effects.definition_of`; the manual prints "Probable rating" in worked examples too. It touches a stored verdict | patch |
| 7 | The fixed cut: page furniture is looked for as a substring of the joined run, a marker can be formed across two paragraphs, contents-page lines can be taken for headings when the layout has no roles, and ids stop sorting as text past 9,999 chunks (edge x4, blind) | medium | `cut_fixed_chunks`, `_body_words`, `fixed_chunk_id` with `ORDER BY chunk_id` | patch |
| 8 | Terraform accepts an overlap over half the size, an overlap of 0 that is known to fail, and available rows that are not built or repeated (blind, edge x2) | low | `variables.tf`; direct validations | patch |
| 9 | The fakes disagree with PostgreSQL: the first chunk holding a rule, not the later one, and no distance on nearest chunks (edge x2) | low | `retrieval_fakes.py`; direct corrections | patch |
| 10 | The built rows are spelled out in seven places and in two messages, and the check that holds the lists equal runs only with containers up (blind x2) | low | `ROW_NOT_AVAILABLE_MESSAGE` texts, `dapr.yaml`; story 3.3 adds `r5` next | patch |
| 11 | The agent's prompt says `read_rule` returns the full text of one rule, which is untrue on `r1` (blind) | low | `suggest_verdict.md`; one row-neutral sentence | patch |
| 12 | The job's span does not say which set failed; the spec's notes are empty (blind) | low | Direct corrections | patch |
| 13 | `verdict` and `workflow` are still over their test budgets (blind) | low | Both were over after the owner's cut, which he accepted at 536 cases; this story added none | reject |

## Design Notes

- Words, not model tokens, measure the fixed size: no tokeniser is in the stack, and the baseline only needs to be fixed and stated. About 45,000 words at a step of 315 gives about 145 chunks.
- Other stories are being implemented in this working tree at the same time: 2.7 (`services/web` and its SPA) and 3.1 with 4.1 (`packages/synthdata/src` and `data/`: new cases and page sets). Touch only what this story needs, leave their files alone, do not stop the compose containers and do not run `tools/dev.sh`. Tests that use the scheduler emulator (the `workflow` integration tests and the cross-service tests) fail each other when two runs overlap: run them once at the end, and if one fails in a way your change cannot explain, run it again alone before reporting it.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov` -- expected: all pass, coverage at least 80% (a failure in files of the other stories in progress is reported, not fixed)
- `docker build -f services/retrieval/Dockerfile -t aiuw-retrieval:dev . && docker build -f services/verdict/Dockerfile -t aiuw-verdict:dev .` -- expected: both build
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
