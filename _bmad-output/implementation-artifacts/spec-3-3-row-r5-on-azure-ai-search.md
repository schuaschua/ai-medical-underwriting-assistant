---
title: 'Story 3.3: Row r5 on Azure AI Search'
type: 'feature'
created: '2026-10-08'
status: 'done'
baseline_commit: '42919df4f1ea4ee74535f612970355b839296f72'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
  - '{project-root}/docs/standards/azure.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every built row of the ladder searches pgvector, so the comparison cannot say what a managed search service does with the same chunks.

**Approach:** Add row `r5`: Azure AI Search queried with hybrid search and the semantic ranker over the same `smart` chunk records and the same vectors that pgvector holds. The ingestion job loads the index from the chunk records already stored, so the store is the only thing that differs. Because the Azure environment stays down while coding, the search service is proven against a local stand-in that speaks the same REST shape.

## Boundaries & Constraints

**Always:**
- Spine AD-11, AD-12, AD-16; NFR4. The index holds exactly the `smart` chunk records of the `retrieval` schema: the same `chunk_id`s, text, context line, rule ids, references, section, impairment, manual page and the same vectors; no text is re-chunked and nothing is re-embedded for the index. Vector search in the index is exhaustive (exact), not approximate.
- Loading the index is a step of the ingestion job, after pgvector is written, idempotent: it creates the index if it is missing, uploads what changed, removes documents whose chunk is gone, and then checks that the index's count and ids equal pgvector's; a mismatch fails the run. A failure of this step leaves pgvector as written and is reported with its own code.
- `r5` answers through the one search operation and the common result shape: the query is embedded once with the same embedding deployment, sent as a hybrid query (text and vector) with the semantic ranker, and the answer's items are mapped to `chunk_id`, `rule_ids`, `rank`, `score`, `text`, `manual_page` and `impairment`. `score` is between 0 and 1 and larger is better, by a stated mapping of the service's reranker score; `rank` is the service's order. The search keeps its own short deadline and the same-embedding-deployment guard.
- The service is reached with `retrieval`'s identity (no key); the index name, the endpoint and the API version are settings. With no search endpoint configured `r5` is refused as not available, and the other rows work as before.
- A rule read for `r5` answers the `smart` chunk from pgvector, as for the other rows on that set. A verdict run may be commanded for `r5` wherever the row is available; the three lists of available rows stay equal, with `r5` listed only where a search endpoint is set.
- Logs carry ids, counts, the row and timings, never the query or chunk text. Tests follow the owner's rule in `CLAUDE.md`, within the budgets.

**Never:**
- No agentic retrieval or knowledge base (story 3.8), no reranker on pgvector (story 3.7), no scoring (story 3.4).
- The stand-in never ships in a service image and cannot be the endpoint in Azure. Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something. Do not edit `infra/demo/foundation`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Load the index | pgvector holds the `smart` set, the index is empty or missing | The index is created and holds one document per chunk with the same ids and vectors | N/A |
| Stores compared | After a load | Counts and `chunk_id`s are identical in both stores | A difference fails the run |
| Reload | Nothing changed | No upload; the check still runs | N/A |
| Chunk changed or removed | The manual changed between runs | Changed documents replaced, removed ones deleted, the check passes | N/A |
| Search `r5` | A query, `r5`, `top_k` 5 | Up to 5 items in the service's reranked order, common shape, scores in 0 to 1 | N/A |
| Service unavailable | The search service is down or slow past the search deadline | No partial answer | `upstream_unavailable` |
| Not configured | No search endpoint set | `r5` refused; `r1` to `r3` unaffected | 409 `retriever_not_available` |
| Index behind pgvector | A search finds an id pgvector does not hold | That item is left out and counted in the log; the search still answers | N/A |
| Verdict on `r5` | A case run with `r5` where it is available | A run keyed on the case and `r5`, like the other rows | N/A |

</frozen-after-approval>

## Code Map

- `services/retrieval/src/retrieval/domain/rows.py` (the ladder table; `r5` is on the `smart` set, store Azure AI Search), `domain/search.py` (the search operation, `SearchStats`, the deadline, the embedding-deployment guard), `domain/ingest.py` and `ingest.py` (the job, its run records, self-checks and failure codes), `adapters/index.py` (pgvector reads), `adapters/db.py` (the chunk table and the one chunk record), `adapters/model.py` (the embedding gateway), `adapters/layout.py` (a REST client to an Azure AI service with an Entra token: the model for the search client), `settings.py`
- `pyproject` of `retrieval` -- the spine pins `azure-search-documents` 12.1.0b2 for row `r6` only; for `r5` use it if it fits the patterns, otherwise REST with `httpx2`, and say which in the notes
- `services/verdict/src/verdict/domain` -- the runnable rows; `services/workflow/src/workflow/settings.py` -- `available_retriever_configs`; the test outside `services/` that holds the three lists equal (story 3.2)
- `packages/synthdata/src/synthdata/layout_standin.py`, `language_standin.py` -- stand-ins for Azure AI services (modes, loopback only); `tests/support/synthdata_stack.py` -- `ingested_manual`, `LocalRetrieval`
- `infra/demo/app/` -- `retrieval`'s roles today; foundation outputs for the search service (id, endpoint, principal id). Roles from `docs/standards/azure.md`: Search Index Data Contributor and Search Service Contributor for `retrieval` on the search service
- `dapr.yaml`, `tools/dev.sh`, `tools/ingest-local.sh`, `README.md`

## Tasks & Acceptance

**Execution:**
- [x] `services/retrieval/` -- the search-service client (create index with exhaustive vector search and a semantic configuration, upload, delete, list ids, hybrid query with the semantic ranker), the load step of the job with its comparison check, row `r5` in the table and the search with its score mapping, settings
- [x] `packages/synthdata/` -- a stand-in for the search service's REST routes that the client uses, holding documents in memory, answering a hybrid query by the stand-in's vectors and word overlap in a reranked order, with modes for unavailable and slow
- [x] `services/verdict/`, `services/workflow/`, `dapr.yaml`, `tools/`, `infra/demo/app/` -- `r5` available where a search endpoint is set; the two roles; the settings
- [x] Tests, within the budgets: the load and comparison, a search on `r5`, not configured, service unavailable; outside `services/` one test over the real manual (both stores identical; `r5` answers the common shape) 
- [x] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; the Azure checks

**Acceptance Criteria:**
- Given the local stack with the stand-in, when the ingestion job has run, then the index and pgvector hold the same chunk ids and counts, and a search with `r5` answers the common shape.
- Given a service with no search endpoint, when `r5` is searched, then it is refused as not available and the other rows answer.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- **Client: REST with `httpx2`, not the SDK.** `services/retrieval/src/retrieval/adapters/search_index.py` (`SearchIndex`) follows `adapters/layout.py`: a replaceable transport, no redirect followed, an Entra token for `https://search.azure.com/.default` and never a key, a span per call, a visible retry. `azure-search-documents` 12.1.0b2 stays pinned by the spine for `r6` only and was not added to `retrieval`'s dependencies; `r5` needs five calls of the stable API (`2024-07-01`, a setting).
- **Index** (`index_definition`): key `chunk_id`, the chunk record's fields, `embedding` with 3,072 dimensions on an `exhaustiveKnn` cosine profile, semantic configuration `rules`. Each document also carries `content_hash`, `embedding_deployment` and a `document_hash` of all its fields but the vector.
- **Load** (`domain/index_load.py`, called as the job's last step from `retrieval/ingest.py`): reads the stored `smart` records (`ChunkRepository.chunk_records`), creates the index if missing, uploads documents whose `document_hash` is new or differs, deletes those whose chunk is gone, then compares the service's count, ids and hashes with pgvector, several times a second apart before failing. It runs whenever a search endpoint is set, whatever the chunk sets' runs said, and never writes pgvector. Its failure is logged on a line of its own (`index load failed: code=... reason=search_...`) with codes of the existing catalogue (`upstream_unavailable`, `stage_failed` for `search_index_differs`, `stage_timeout`); no new `ErrorCode` was added to the contracts.
- **Search** (`domain/search.py`, `ai_search_hybrid`): one embedding call, one read of pgvector (run record for the deployment guard, and each stored chunk's `content_hash`), one hybrid query (`queryType: semantic`, `semanticErrorHandling: fail`, vector query `exhaustive: true` with `k` = `r3`'s candidate depth, `top` = `top_k`). The largest raw reranker score is on the span and in the log line. `score` = `@search.rerankerScore` / 4, cut to 0 to 1; `rank` is the service's order, counted without a gap. A document pgvector does not hold, or holds with another content hash, is left out and counted (`left_out=`); a document embedded with another deployment refuses the search (`model_unavailable`). Service down, malformed or slow past the deadline: `upstream_unavailable`.
- **Availability:** `rows.available_rows(search_service)`; `SearchPorts.search_service` is None without `RETRIEVAL_SEARCH_SERVICE_ENDPOINT`, and `r5` is then 409 `retriever_not_available`. `verdict`'s rows became the setting `VERDICT_AVAILABLE_RETRIEVER_CONFIGS` (default `r1` to `r3`; `RUNNABLE_RETRIEVER_CONFIGS` now holds `r5`). `dapr.yaml` and `infra/demo/app` name `r5` for `workflow` and `verdict` and give `retrieval` the endpoint; the test in `packages/synthdata/tests/test_foundry_standin.py` holds the lists equal, with and without a search service.
- **Stand-in:** `packages/synthdata/src/synthdata/search_standin.py`, port 5103, modes `ok`, `unavailable`, `slow`; indexes in memory. Started by `tools/dev.sh` and `tools/ingest-local.sh`.
- **Infra:** two role assignments for `retrieval` on the search service, four env values, variables `search_index_name` and `search_api_version`, `available_retriever_configs` with `r5`, also passed to `verdict`. `infra/demo/foundation` untouched. Formatted and validated only; nothing planned or applied.
- **Tests:** `retrieval` 55 (was 57), `verdict` 58, `workflow` 117, `synthdata` 50, all unchanged or lower; merges are listed in `deferred-work.md`. Whole suite: 534 passed, coverage 90%.
- **Not proven here:** everything against the real service; the checks are in `deferred-work.md`.

## Spec Change Log

## Review Triage Log

Review 1 (2026-10-08): blind hunter (B), edge-case hunter (E), verification-gap (V). No intent gap, no bad spec.

| # | Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- | --- |
| B1, E1 | `ai_search_hybrid`: an index document that is stale but whose `chunk_id` pgvector still holds is answered | medium | patch | Real after a failed load: only ids are compared, so `r5` can answer old text while the rule read answers the new chunk. Same handling as the matrix row "Index behind pgvector": compare the record's `content_hash`, leave out and count. |
| B2 | An index with few or no documents answers 200 | low | reject | Real only after a load that failed, which ends the job non-zero with its own line; a guard adds a branch for a state the job already reports. |
| B3 | A 4xx from the service is told as "try again shortly" | low | reject | Real, but the log's reason carries the status (`search_query_status_403`); a second message and code path for the caller is more than a direct correction. |
| B4 | The platform can end the job during the index load | medium | patch | `ingest_deadline_seconds` 1800 plus the load's 300 equals `ingest_timeout_seconds` 2100: no room left. |
| B5, E5 | `SEARCH_STANDIN_MODE=unavailable ./tools/dev.sh` stops the ingestion | medium | patch | `is_standin search` greps for the 404 wording only; in that mode the stand-in answers 503, so `ensure_standin` says the port is taken and the manual is not ingested. |
| B6 | Nothing ties `r5` in the row list to the endpoint in `infra/demo/app` | low | reject | The endpoint is passed unconditionally from the foundation, so in Azure it is always set; a precondition guards a state the stack cannot reach. |
| B7 | The raw reranker score is not recorded anywhere | low | patch | The Azure check "scores stay within 0 to 4" has nothing to read; one number on the span and the log line is a direct addition. |
| B8 | The semantic ranker may score against the backslash-escaped text | maybe-false | defer | Cannot be told without the real service; added to the Azure checks. |
| B9 | `r5` asks for `k = top_k` vector candidates while `r3` hands 50 a side to its fusion | medium | patch | AD-12 fairness: the store is the only thing that may differ, so the vector side gets the same candidate depth as `r3`. The "no over-fetch" half is rejected: a left-out document is rare and already counted. |
| B10, E3, V-other | `load_index`: `build_search_index` is outside the `try` | low | patch | The docstring promises the error is answered, not raised; moving one line keeps the chunk sets' reports. |
| B11 | `seconds=` in the load's lines is the whole job's time | low | patch | The Azure check "measure the load" cannot be read from it. |
| B12, E4 | An empty `smart` set empties the index and logs `documents=0` | low | reject | Mirroring pgvector is what the spec asks of the load; an empty `smart` set already fails the job on its own line. |
| B13 | `document_hash` has no version and stands for the vector by `content_hash` | low | reject | The job never re-embeds a chunk whose content hash is unchanged, and a changed index definition is a new index name by design. |
| B14 | The 207 branch and paging past 1,000 have no test | low | reject | Owner's test rule: about 111 documents, and a refused item still fails the comparison that is tested. |
| B15, E9 | "Tests, within the budgets" ticked while `verdict`, `workflow` and `synthdata` are over | low | reject | The overs predate the story (536 cases at the baseline, accepted by the owner); this story lowered the total to 534 and records the counts in `deferred-work.md`. |
| B16 | Sprint status `in-progress` beside spec `in-review`; `last_updated` went backwards | false | reject | The workflow moves sprint status at the end of the review; the earlier time stamp was ahead of the clock. |
| B17, E8 | `search_api_version` has no Terraform validation | low | patch | A typo passes the plan and fails at container start; one validation block. The "yet" to "here" wording is cosmetic and rejected. |
| V1 | The run-record deployment guard on `r5` is seen by no test | medium | patch | Filed evidence: deleting the check fails no test; one more search inside the existing `r5` test. |
| V2 | The comparison's repeated attempts are never needed by a fake | medium | patch | Filed evidence: a loop that gives up at the first difference passes every test, and Azure is where it matters; a lagging listing in the existing integration test. |
| V3 | A load ending in an unexpected exception: failed status unpinned | low | defer | Filed disposition; the adapter turns every malformed answer into `SearchServiceUnavailable`. |
| V4 | The load's own deadline (`stage_timeout`) is not exercised | low | defer | Filed disposition; the job still ends non-zero through the tested per-call path. |
| V-other 2 | The three-lists checks over infra files are text matches | low | reject | True and already an Azure check in `deferred-work.md`. |
| V-other 3 | `is_standin search` copies the stand-in's wording | low | reject | Same pattern as the layout probe; B5's patch touches the same line. |
| E2 | `_ranked` lets a contract-breaking document through to `SearchItem` | low | reject | The service answers documents this job uploaded from validated records; not reachable. |
| E6 | Stand-in: a query reads documents without the lock | low | reject | Dev tool; a 500 from the stand-in during a concurrent load is retried by the client. |
| E7 | Stand-in: bad paging answers 500, not 400 | low | reject | Only the project's own client calls it, with fixed paging. |

## Design Notes

- **Azure checks for `deferred-work.md`:** the real index definition (3,072 dimensions with exhaustive search on the Basic tier), the semantic ranker being enabled and its score range, the two roles being enough, the stable API version for hybrid plus semantic queries, the load's speed for about 110 documents, and `r5`'s recall and latency, which only real embeddings make meaningful.
- The reranker's score is not a probability; map it to 0 to 1 by a fixed, stated rule (for example dividing by the service's documented maximum) and keep the service's order as the rank.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Do not run `tools/dev.sh` unless nothing else holds its ports, and stop what you start. Tests that use the scheduler emulator fail each other when two runs overlap: run them once at the end.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov` -- expected: all pass, coverage at least 80%
- `docker build -f services/retrieval/Dockerfile -t aiuw-retrieval:dev .` -- expected: builds
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
