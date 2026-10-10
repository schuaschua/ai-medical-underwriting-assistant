---
title: 'Story 2.3: Search the manual for rules'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'e9382757925e85928fa948f7ef6834adf41f017d'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The manual's rules are indexed, but nothing can ask for them: a medical fact cannot bring back the rules that apply to it, and a rule cannot be read by its id.

**Approach:** Add the two read operations of `retrieval`: one search operation for every ladder row, with row `r3` built as hybrid search (pgvector nearest-neighbour plus PostgreSQL full-text, fused with reciprocal rank fusion), and a rule read by `rule_id`. The other rows are named and refused as not available until Epic 3 builds them.

## Boundaries & Constraints

**Always:**
- Spine AD-11, AD-12, AD-16, AD-3; NFR7 (the design must hold up to a RAG expert). `POST /searches` takes a query, a `retriever_config` and `top_k` (default 5, contract maximum) and answers `retriever_config`, `latency_ms` and ranked items with `chunk_id`, `rule_ids`, `rank`, `score`, `text`, `manual_page` and `impairment`. One operation and one result shape for every row; nothing is stored.
- `r3`: the query is embedded once with the one embedding deployment through the service's model gateway (the same model and the same text preparation as at ingestion); the vector side is exact cosine nearest-neighbour over the `smart` chunks; the full-text side is PostgreSQL full-text search over the stored full-text column; the two ranked lists are fused with reciprocal rank fusion, constant 60, each side contributing a candidate list deeper than `top_k` (a setting, default 50). A chunk found by one side only is still ranked. Ties are broken by `chunk_id`, so the same query on the same index always gives the same order.
- A `rule_id` written in a query (for example `UW-DM-001`) must be findable as a whole by the full-text side; hyphenated ids must not be lost to the text-search parser. The text-search configuration is a named constant, the same at ingestion and at search.
- `score` is the fused score as computed, between 0 and 1, larger is better; `rank` is 1-based and dense. `latency_ms` is the time `retrieval` spent on the search, the embedding call included.
- `GET /rules/{rule_id}` answers the `smart` chunk that defines the rule: its text, manual page and impairment, the chunk id and chunk set, and the rule ids it refers to (a contracts addition, needed by story 2.5's `read_rule` rule). An unknown rule is 404. A `retriever_config` parameter is accepted as the contract defines it; rows on the `smart` set answer the same chunk, and `r1` is refused as not available until the `fixed` set exists.
- A `retriever_config` that is not a ladder row is refused by validation (422) with a plain message; a row that is not built yet (`r1`, `r2`, `r4`, `r5`, `r6`) is refused with a plain message and an error code that says it is not available, distinct from an unknown one.
- The search is read-only and uses bound parameters only. The model gateway's rules hold: retries, the concurrency cap, a span per attempt, token usage recorded; an embedding failure after retries is `model_unavailable`. Database reads have their timeouts and spans.
- Logs carry ids, counts, the row and timings, never the query text.

**Never:**
- No reranker, no Azure AI Search, no `fixed` chunks, no query rewriting or expansion by a model (Epic 3 and the agent decide those). No caching of searches.
- No `web` route or SPA change: the rule view arrives with story 2.7.
- The answer key is not read; tests that compare results with the rule table live outside `services/`.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Search `r3` | A query, `r3`, `top_k` 5, the ingested manual | Up to 5 items, ranked, each with every field; `retriever_config` `r3`; `latency_ms` | N/A |
| Named rule | A query naming an impairment and threshold from the rule table (for each rule, in a test outside `services/`) | The expected `rule_id` is in the top 5 | N/A |
| Rule id in the query | "UW-DM-001" | That rule's chunk is ranked first | N/A |
| Found by one side only | A query whose words appear nowhere but whose meaning is near a rule, or the reverse | The chunk is still returned | N/A |
| Same query twice | Unchanged index | The same items in the same order with the same scores | N/A |
| `top_k` | 1, 5, the maximum; 0 or above the maximum | That many items at most | 422 `validation_failed` outside the bounds |
| Blank query | Empty or only spaces | Refused | 422 |
| No words the text search keeps | A query of stop words only | The vector side's results, ranked | N/A |
| Unknown row | `retriever_config` `r9` | Refused with a plain message | 422 |
| Row not built | `r1`, `r2`, `r4`, `r5`, `r6` | Refused with a plain message saying the row is not available | Its own code; not 5xx |
| Empty index | No chunks ingested | No items, 200 | N/A |
| Embedding unavailable | The model fails after retries | No partial result | 503 `model_unavailable` |
| Read a rule | A `rule_id` of the manual | Its chunk text, manual page, impairment, chunk id, chunk set and the rules it refers to | N/A |
| Unknown rule | A well-formed id the manual does not define; a malformed id | Not found; refused | 404; 422 |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/models/retrieval.py` -- `SearchRequest`, `SearchItem`, `SearchResponse`, `RuleReadQuery`, `RuleText` (add the referred rule ids), `DEFAULT_TOP_K`, `MAX_TOP_K`; `operations.py` -- `search_rules`, `read_rule` (callers `verdict` and `web`); `enums.py` -- `RetrieverConfig`, `ChunkSet`; `errors.py` -- the catalogue (a code for "not available" must be added with its HTTP status if none fits); `base.py` -- `Score`, `Milliseconds`
- `services/retrieval/` (story 2.2) -- `adapters/db.py` (the chunk table, its vector column, the stored full-text column and index, the references), `adapters/model.py` (the gateway with the embedding call and how text is prepared for it at ingestion), `domain/` (chunk record, chunk ids), `adapters/http/` (app factory, probes, error handlers), `settings.py`, `prompts/`, tests with fakes
- `packages/synthdata/` -- the embedding stand-in (deterministic vectors in which texts that share words are close), the manual and the rule table, `tests/support/synthdata_stack.py` and the ingestion end-to-end test of story 2.2 (the fixture that gives an ingested index)
- `services/classification/src/classification/adapters/http/routes.py` -- a service's routes built from the operations table, with validation errors in the one error shape
- `dapr.yaml`, `README.md`, `infra/demo/app/` -- `retrieval` is already declared; add any new setting

## Tasks & Acceptance

**Execution:**
- [x] `packages/contracts/` -- the referred rule ids on the rule read answer; the "row not available" error code and status; tests; exported schema and SPA types
- [x] `services/retrieval/` domain -- the retriever-row table (which rows exist, which chunk set and method each uses, which are built), reciprocal rank fusion as a pure function with its tie rule, the search and rule-read operations with their ports
- [x] `services/retrieval/` adapters -- the vector search and the full-text search as two bound-parameter queries (or one statement, if it stays readable) with candidate depth from settings; the query embedding through the gateway; the rule read; the two routes; settings; a migration only if the full-text column of story 2.2 does not keep rule ids whole
- [x] `services/retrieval/tests/` -- every matrix row with fakes, and against PostgreSQL with hand-made chunks and vectors (fusion order, one-side-only, ties, rule id as a query, empty index, stop words)
- [x] `packages/synthdata/tests/` -- against the ingested real manual with the stand-ins: for every rule of the table a query built from its impairment and threshold finds it in the top 5; a rule read for every rule; references match the table
- [x] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- how to search locally; the Azure checks this story cannot run

**Acceptance Criteria:**
- Given the local stack with the manual ingested, when each rule's impairment and threshold is searched with `r3`, then its `rule_id` is in the top 5 for every rule, and the test prints how many were first, in the top 3 and in the top 5.
- Given a reader of the code, when they follow one search, then the embedding, the two searches, the fusion and the ranking are each a named step they can test alone.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- **Where the steps are.** `retrieval/domain/rows.py` is the row table (every ladder row, its chunk set and method, and `built`); `domain/fusion.py` is reciprocal rank fusion as a pure function; `domain/search.py` holds `embed_query`, `rule_ids_named_in`, `rank_items`, `hybrid_search`, `search_rules` and `read_rule`; `adapters/index.py` holds the two searches and the rule read as three statements of their own (`nearest_statement`, `matching_statement`, `defining_statement`) behind the port `ChunkIndex`. A vector-only row calls `nearest` alone; a reranker takes `hybrid_search`'s list. No migration: story 2.2's column already keeps rule ids whole.
- **Full-text side.** Query function: `plainto_tsquery` in configuration `english` (the constant `TEXT_SEARCH_CONFIG`, as the stored column), after rule ids are written as one word with the same pattern as at ingestion, in either case. It takes any text and reads nothing a caller writes as an operator. Its `&` are replaced by `|` in the query's printed form, so a chunk that holds some of the words matches. Ranking function: `ts_rank` with normalisation 1 (divided by 1 + log of the chunk's length): it adds up each query word's share and lets repeats count for less, which suits an "any word" query; `ts_rank_cd` ranks by how near the words stand, which means little when only some are required. A chunk that defines a rule the query names by its id is ordered before the others, because the chunks that only refer to the rule print the id as often. Ties on both sides go by `chunk_id`.
- **Rule id as the query: the limit of two lists.** The full-text side puts the rule's own chunk first; the fusion still weighs the vector side as much. With hand-made vectors (PostgreSQL test) and in the fakes the rule's chunk is first. Over the real manual with the stand-in's vectors 100 of 111 rule ids came first and 11 second or third. Making it certain needs a third list or an answer outside the fusion, which the frozen text ("the two ranked lists") rules out: recorded in `deferred-work.md` for the owner.
- **Measured (stand-in vectors, real manual, `r3`, `top_k` 5):** 111 queries of the form `<impairment>: <threshold words>`: first 85, in the top 3 111, in the top 5 111.
- **Contracts.** `ErrorCode.RETRIEVER_NOT_AVAILABLE` (`retriever_not_available`, 409); `RuleText.reference_rule_ids` (required). Schema and SPA types regenerated; `strings.audit.failure` got the one line the new code needs to compile (it must word every code), no screen changed.
- **Other choices.** `RETRIEVAL_SEARCH_CANDIDATE_DEPTH` (default 50, at least 50, the largest `top_k`); a search never asks a side for fewer than `top_k`. The query is embedded exactly as asked. A NUL character in a query is 422. A model that refuses the call is `upstream_unavailable` (502); a vector of the wrong size `invalid_model_output` (502). The gateway's chat deployment is now optional: the service embeds only. A service without `RETRIEVAL_MODEL_ENDPOINT` or `RETRIEVAL_EMBEDDING_DEPLOYMENT` answers probes and rule reads, and searches with `model_unavailable`.
- **Changed tests of story 2.2:** `test_retrieval_api.py` (the route list now holds the two reads; the 404 probe uses another path) and the `RuleText` built in `test_manual_ingestion_end_to_end.py` (the new field).
- **Verification run (2026-10-07):** format, lint and mypy clean; `uv run pytest --cov`: 2812 passed, 1 failed, coverage 98.76%. The failure is story 2.4's own `test_story_2_4_the_app_stack_sets_only_variables_the_settings_read` (the app stack does not configure `extraction` yet); left as it is, as the design notes ask. `contracts:check` clean; SPA typecheck, lint and tests pass; the `retrieval` image builds; `terraform fmt` and `validate` pass with Terraform 1.16.5 (`.work/bin`). The containers were already running and were left running: story 2.4's session uses them.

- **After the review (2026-10-07).** A search has its own budget (embedding timeout 3 s, one retry, an 8 s deadline over the whole search; `model_unavailable` or `upstream_unavailable` when it passes). Database failures on a search or a rule read are `upstream_unavailable` (502), logged by type. `MAX_QUERY_CHARS` 2,000 in the contract. An all-zero vector and a value beyond a 4-byte float are refused. A search is refused when the last ingest run recorded another embedding deployment; the record is read in the search's transaction on every search (nothing is cached, so nothing goes stale), and readiness is left alone so that rule reads stay up. Both lists are read on one connection in one read-only, repeatable-read transaction (port `ChunkIndex.snapshot()`). Only the `&` between two words is made `|`. One pattern (`NAMED_RULE_ID`) says what a rule id in a query is, for Python and SQL. Depth is the setting or twice `top_k`. A service without model settings says so once at start-up and answers "not configured to search". The route opens the span `retrieval.search`. `create_app` takes the model transport, and the end-to-end fixture uses `create_app(settings)`. With the default single retry a throttled deployment is asked twice, not four times.

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | A search has no deadline of its own: it reuses the ingestion job's model settings and can hold a caller for minutes (blind, edge) | high | 60 s per call, three retries, waits up to 30 s; the verdict agent searches several times per fact inside a 180-second stage | patch |
| 2 | A database failure on a search is a 500, where readiness calls the same cause `upstream_unavailable`; two tests cement it (blind) | medium | `SqlChunkIndex._read` maps nothing; a caller cannot tell an outage from a bug | patch |
| 3 | The query has no upper length, so an over-long one is the model's refusal reported as 502 (blind, edge) | medium | `SearchRequest.query` is `NonEmptyStr`; a contract maximum answers 422 | patch |
| 4 | An all-zero vector, or a value outside what the database can hold, passes the checks and ranks every chunk by its id (blind, edge x2) | medium | `embed_query` checks size and finiteness only; cosine distance to a zero vector is NaN | patch |
| 5 | Nothing checks that the query is embedded by the deployment the chunks were embedded with (blind, implementer) | medium | The intent asks for the same model; the last ingest run stores the name; a wrong deployment of the same size answers plausible nonsense | patch |
| 6 | The two lists come from two connections and two snapshots, and nothing makes the reads read-only (blind, edge) | medium | `_read` opens a connection per statement; an ingestion can commit between them | patch |
| 7 | The `&` to `|` rewrite also changes an `&` inside a quoted word, though its docstring says not (blind, edge) | low | `func.replace` on the whole text; direct correction | patch |
| 8 | Python and SQL each decide what a rule id in a query is, with different word boundaries (blind, edge) | medium | `_NAMED_RULE_ID` against `RULE_ID_AS_WORDS`; "UW-DM-001_x" is named by one and not collapsed by the other | patch |
| 9 | At `top_k` 50 the candidate list is not deeper than `top_k` (blind, implementer) | low | `depth = max(candidate_depth, top_k)` with a default of 50; the intent says deeper | patch |
| 10 | A service without its model settings passes readiness and answers every search "try again shortly", logging an error per request (blind) | low | `NoEmbeddingModel`; one line at start-up and a truthful message are direct corrections | patch |
| 11 | The named-rule test asserts only the top 5 although it prints first and top 3; the statement-timeout test only reads a setting; a full-text read that fails after the vector read is untested; a fixture's mode is not set back; test gateways and engines are not closed (blind, gap) | medium | Read in `test_manual_search_end_to_end.py`, `test_retrieval_search_integration.py`, `retrieval_fakes.py` | patch |
| 12 | The server's own wiring of a working search is never run; nothing sees the named rule ids reach the full-text side; the length normalisation's order is unasserted (gap x3) | medium | Filed with evidence | patch |
| 13 | A search has no span of its own carrying the row, `top_k` and each side's counts (blind) | medium | Only a log line holds them; Epic 3's comparison reads latency per row | patch |
| 14 | A rule id as the whole query is ranked first for 100 of 111 rules, not all, and the order key that puts a defining chunk first is an addition to plain full-text ranking (blind, implementer) | medium | The matrix row and "two lists fused" cannot both hold with equal weights; put to the owner on 2026-10-07 with a recommendation (keep pure fusion, ask for the top 3) | awaits the owner; no code change until answered |
| 15 | The two reads run one after the other (blind) | low | They share one connection once finding 6 is fixed; milliseconds at this size | reject |
| 16 | The review file left out files this story also changed; the spec's own verification had one failure from story 2.4 (blind) | false | The file was assembled from this story's own files because three stories share the tree; the full run is repeated before the commit | reject |

## Design Notes

- **Azure checks for `deferred-work.md`:** with real `text-embedding-3-large` vectors, the named-rule test over the real index (recall at 1, 3 and 5), since the stand-in's vectors only reflect shared words; the search's latency with a real embedding call; exact search over 3,072 dimensions on the B1ms server at this size.
- Reciprocal rank fusion: `score(chunk) = sum over the sides that returned it of 1 / (60 + rank on that side)`. With two sides the largest possible score is 2/61, which is inside the contract's 0 to 1.
- The full-text query should match a chunk that holds some of the query's words, ranked by how well, not only chunks that hold all of them: a fact statement is a sentence, not a keyword list. Say in the notes which query function and ranking function were chosen and why.
- Epic 3 adds rows behind this operation: keep the row table and the two searches separable so `r1` and `r2` (vector only) and `r4` (a reranker over `r3`'s list) are additions, not rewrites.
- The working tree holds uncommitted work: story 2.2 (`services/retrieval/` as it stands, reviewed and verified, which this story builds on) and story 2.4, which is being implemented at the same time (`services/extraction/`, `services/workflow/`, `packages/contracts/src/contracts/text.py`, an extraction answer in `foundry_standin.py`, its own lines in infra, CI, `dapr.yaml`, `README.md`, `deferred-work.md`). Leave story 2.4's work as it is: add beside it in shared files, regenerate the contracts schema and SPA types from the tree as it is, and if a test of `extraction` or `workflow` fails in a full run, report it and do not fix it.
- A local stack from an earlier run may still hold ports 5100 to 5102 and 8000 to 8004 (a `tools/dev.sh` that was not stopped). Do not try to stop it and do not run `tools/dev.sh`; use the test fixtures, which choose their own ports.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation. He asked in that session whether hybrid search would use `tsvector` with vectors on pgvector: yes, as above.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run contracts:check` -- expected: clean
- `docker build -f services/retrieval/Dockerfile -t aiuw-retrieval:dev .` -- expected: builds
