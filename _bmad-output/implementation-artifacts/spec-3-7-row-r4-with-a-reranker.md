---
title: 'Story 3.7: Row r4 with a reranker'
type: 'feature'
created: '2026-10-08'
status: 'done'
baseline_commit: 'd28b713d6a863839d97d0eb59e9674e2f73371c0'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-3-row-r5-on-azure-ai-search.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
  - '{project-root}/docs/standards/azure.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Row `r5` adds a managed store and a ranker at once, so the ladder cannot say what reranking alone is worth on pgvector.

**Approach:** Build row `r4`: the fused candidates of `r3`, unchanged, put in a new order by a reranker, answered through the one search operation and the common result shape. The reranker is an LLM reranker on the chat deployment the project already has; Cohere Rerank on Foundry, which the epic prefers if it can be deployed in West US 3, cannot be checked while Azure is down and is left as a check for the final session.

## Boundaries & Constraints

**Always:**
- Spine AD-11, AD-12, AD-16; NFR4. `r4` differs from `r3` in one thing: after the same embedding, the same two searches and the same fusion, the best fused candidates (a setting, default 20, never fewer than `top_k`) are given to the reranker with the query, and the items are the reranker's order. Nothing else of `r3` changes, and `r3` itself answers as before.
- The reranker is one chat call with a structured answer: a relevance between 0 and 1 for every candidate it was given, by `chunk_id`. `rank` is by that relevance, ties in the fused order; `score` is the relevance. The prompt is a file under the service's prompts; the query and the chunk texts go in as data, never as instructions.
- An answer that is not the asked shape, leaves a candidate out, names one it was not given or holds a number outside 0 to 1 is no answer: the search fails with `model_unavailable`. `r4` never falls back to the fused order, which would be `r3`'s answer under `r4`'s name.
- `r4` has its own search deadline and its reranker call its own timeout (settings), since a chat call does not fit the 8 s of the other rows; every caller's timeout for a search stays above it (`verdict`'s for one search, `web`'s for one service call, the runner's). A search slow past that deadline says the reranker was waited for.
- `r4` is available where `retrieval` is given a chat deployment, and refused as not available where it is not; the other rows are unaffected. The three lists of rows stay equal: `r4` is named for `workflow` and `verdict` wherever `retrieval` has the chat deployment (the local stack, the `app` stack).
- The reranker shares the model gateway's cap on concurrent calls. Logs carry the row, counts and timings, never the query or a chunk's text.
- The scoreboard names the reranker: `r4`'s method in `evals/static-metrics.yaml` says it is an LLM reranker on the chat deployment.
- Tests follow the owner's rule in `CLAUDE.md`, inside the budgets.

**Never:**
- No Cohere deployment, no Terraform for one, no second reranker behind a switch. No reranking of any other row. No change to the fusion or to what `r3` answers.
- No agentic retrieval (story 3.8). Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something. Do not edit `infra/demo/foundation`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Search `r4` | A query, `r4`, `top_k` 5 | The 20 best fused candidates reranked; the 5 most relevant in the reranker's order, common shape, scores 0 to 1 | N/A |
| Same candidates | The same query on `r3` and `r4` | `r4`'s items are among `r3`'s fused candidates; only the order and the scores differ | N/A |
| `top_k` above the rerank depth | `top_k` 30 | 30 candidates are reranked | N/A |
| Fewer candidates than `top_k` | The fusion gives 3 | The 3, reranked | N/A |
| Bad answer | The reranker leaves a candidate out, or answers prose | No items | 503 `model_unavailable` |
| Reranker slow | The chat call outlasts `r4`'s deadline | No partial answer | `model_unavailable`; the log says the reranker was waited for |
| Not configured | No chat deployment set | `r4` refused; `r1`, `r2`, `r3` unaffected | 409 `retriever_not_available` |
| Verdict on `r4` | A case run with `r4` where it is available | A run keyed on the case and `r4`, like the other rows | N/A |

</frozen-after-approval>

## Code Map

- `services/retrieval/src/retrieval/domain/search.py` -- `hybrid_search` holds the fusion and the truncation inline: take the part up to the fused list and the chunks out so `r3` and `r4` share it; `rank_items`, `candidate_depth`, `_SEARCHES`, `SearchPorts`, `SearchOptions`, `SearchStats`, `embed_query` (how a model failure becomes a domain error), the deadline and its `waited_for` in `search_rules`; `reranker_score` and `max_reranker_score` are `r5`'s semantic ranker and are not `r4`'s
- `domain/rows.py` -- `R4` is `(SMART, HYBRID_RERANKED)`, not built; `available_rows(search_service)`, `needs_search_service`, `row_to_search`; `domain/ports.py` -- `QueryEmbedder` has `embed` only
- `adapters/model.py` -- `ModelGateway.context_line` (a chat call with a strict `json_schema`, parsed in the domain), `_call` (retries, the concurrency cap), `chat_deployment(settings)`; `adapters/http/app.py` -- `build_query_gateway` gives the service the embedding deployment only and a 3 s client, `search_options`; `prompts/` (`load_prompt`, `chunk_context.md`); `settings.py` (`chat_deployment`, the search timeouts and the validators that hold them under the deadline)
- `packages/synthdata/src/synthdata/foundry_standin.py` -- `complete` tells tasks apart by the schema name (`rule_place_of`, `CONTEXT_SCHEMA_NAME`); `Mode`; `_words` for a word-overlap relevance
- `services/verdict/src/verdict/domain/run.py` (`RUNNABLE_RETRIEVER_CONFIGS`, `SEARCH_SERVICE_RETRIEVER_CONFIGS`, `DEFAULT_AVAILABLE_RETRIEVER_CONFIGS`), `settings.py` (`upstream_timeout_seconds` 12, said to be above the search's 8 s); `services/workflow/src/workflow/domain/cases.py` (`DEFAULT_AVAILABLE_RETRIEVER_CONFIGS`); `services/web/src/web/settings.py` (`service_timeout_seconds` 30); `evals/src/bakeoff/settings.py` (`request_timeout_seconds` 30)
- `packages/synthdata/tests/test_foundry_standin.py` -- the tail of the story 1.8 test holds the three lists equal and text-matches `dapr.yaml` and `infra/demo/app`; `test_manual_search_end_to_end.py` -- the test over the real manual that story 3.3 extended
- `dapr.yaml` (retrieval has no `RETRIEVAL_CHAT_DEPLOYMENT` yet; the two lists of rows), `tools/`, `infra/demo/app/terraform.tfvars`, `variables.tf` (the validation of `available_retriever_configs`), `locals.tf` (`RETRIEVAL_CHAT_DEPLOYMENT` is already given)
- `evals/static-metrics.yaml` (row `r4`), `evals/tests/test_scoring.py` (pins the rows to `retrieval`'s table)
- `services/retrieval/tests/` -- `test_retrieval_search.py`, `support/retrieval_fakes.py` (`StubModel`, `completion`)

## Tasks & Acceptance

**Execution:**
- [x] `services/retrieval/` -- the shared candidates step, the reranker port and its chat call with the prompt, row `r4` with its deadline and settings, availability by the chat deployment
- [x] `packages/synthdata/` -- the chat stand-in answers a rerank request by word overlap, with its failure modes
- [x] `services/verdict/`, `services/workflow/`, `dapr.yaml`, `tools/`, `infra/demo/app/` -- `r4` available where the chat deployment is set; the callers' timeouts above `r4`'s deadline
- [x] Tests, inside the budgets: a search on `r4` (order, scores, same candidates as `r3`), a bad answer, not configured, the slow reranker; the three-lists test and the test over the real manual extended in place
- [x] `evals/static-metrics.yaml`, `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- the reranker named; what changed; the Azure checks

**Acceptance Criteria:**
- Given the local stack, when a search runs with `r4`, then it takes `r3`'s candidates, reranks them and answers the common shape.
- Given `r4` exists, when the runner runs again, then the scoreboard shows `r4` with the reranker used.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- **The shared step:** `domain/search.py`, `fused_candidates` (the embedding, the deployment guard, the two reads in one view, the fusion) is called by `hybrid_search` (`r3`, which then ranks as before) and by `hybrid_reranked_search` (`r4`). `r4` gives the reranker the best `max(rerank_depth, top_k)` fused candidates; with none it asks no model and answers no items.
- **The reranker:** port `Reranker.relevance(query_and_candidates) -> str` (`domain/ports.py`); `domain/rerank.py` builds the message (one JSON object: `query`, and `candidates` with `chunk_id`, `impairment`, `text`, in the fused order) and reads the answer (`relevance_by_chunk`: the object `{"ranking": [{"chunk_id", "relevance"}]}`, every candidate once and no other, each a number from 0 to 1, else `RerankAnswerInvalid` with a reason code). `ModelGateway.relevance` (`adapters/model.py`) is one chat completion with the strict schema `rerank_relevance`, the prompt `prompts/rerank.md` as the system message and the message as the user turn, through `_call` (the same retries and the one semaphore), with a per-call timeout. The items are sorted by relevance, stable, so ties keep the fused order; `score` is the relevance.
- **Failures:** an unusable answer, a model that gave up after its retries and a passed deadline are `model_unavailable` (503); a call the model refuses (4xx) is `upstream_unavailable`, as for a query's embedding. The log has `rerank answer invalid: reason=... candidates=N` and `search deadline passed: retriever_config=r4 waited_for=reranker`. The fused order is never answered.
- **Budget:** `SearchOptions.rerank_depth` 20 and `rerank_deadline_seconds` 20 (`deadline_of(row, options)` picks the row's deadline); settings `RETRIEVAL_SEARCH_RERANK_DEPTH`, `..._TIMEOUT_SECONDS` (15), `..._DEADLINE_SECONDS` (20), `..._MAX_COMPLETION_TOKENS` (4000), with validators (timeout not over the deadline; the deadline not under the other rows'). `verdict`'s `upstream_timeout_seconds` went from 12 to 25; `web`'s 30 and the runner's 30 were already above and are untouched. The three-lists test holds all three above `r4`'s deadline.
- **Availability:** `rows.available_rows(search_service, reranker)`; `R4` is built and `needs_reranker`. `SearchPorts.reranker` is the query gateway where `RETRIEVAL_CHAT_DEPLOYMENT` is set, else None, and `r4` is then 409 `retriever_not_available` (said once at start-up). `verdict`: `RUNNABLE_RETRIEVER_CONFIGS` holds `r4`, new `RERANKER_RETRIEVER_CONFIGS`, defaults still `r1` to `r3`. `dapr.yaml` gives `retrieval` the chat deployment and the three rerank settings and lists `r4` for `workflow` and `verdict`; `infra/demo/app` lists `r4` (tfvars, the validation) and passes three new variables (`search_rerank_depth`, `search_rerank_timeout_seconds`, `search_rerank_deadline_seconds`, the last held under 25). `locals.tf` already gave the chat deployment. Formatted and validated only; nothing planned or applied; `infra/demo/foundation` untouched.
- **Stats:** `SearchStats.rerank_asked`, `reranked`, `rerank_ms`; the search log line gained `reranked=` and `rerank_ms=`, the span `retrieval.reranked` and `retrieval.rerank_ms` (for `r4` only). The gateway logs `model call: operation=rerank` with token counts.
- **Stand-in:** `foundry_standin.py` answers a request with the schema `rerank_relevance`: relevance = the share of the query's words the candidate's text holds. Modes `rerank_incomplete` (last candidate left out) and `rerank_slow` (`rerank_delay_seconds`, 30 s); `invalid` answers prose. `tools/dev.sh` names the two modes.
- **Scoreboard:** `evals/static-metrics.yaml`, `r4`: `Hybrid, then an LLM reranker on the chat deployment`. The runner's whole-path test (`evals/tests/test_whole_path.py`) now runs with `r4` available, scores it (recall, latency, two verdict runs through `workflow` and `verdict`) and reads that method off the board. It had to change: `retrieval` in that stack has a chat deployment, so `r4`'s search answered while `workflow` refused the row.
- **Tests:** `retrieval` 55 (two added, the chunker's three parametrised cases merged into one test), `verdict` 58, `workflow` 117, `synthdata` 50, all unchanged in number; the list is in `deferred-work.md`. Whole suite: 543 passed, coverage 91%.
- **Verification run (2026-10-08):** `docker compose up -d --wait`; `uv sync`, `ruff format --check`, `ruff check`, `mypy packages services evals` clean; `uv run pytest --cov` 543 passed, 90.84%; the `retrieval` image builds and holds `prompts/rerank.md`; `terraform fmt -check -recursive` and `validate` of `infra/demo/app` clean.
- **After review 1 (2026-10-08):** rerank calls take a slot of a part of the gateway's cap first (the cap less one), so one slot is always left for an embedding; a rerank call that timed out is not sent again; `rerank_ms` is recorded whatever the outcome and is in the `search deadline passed` line; an empty answer is `rerank_empty`; a huge whole number as a relevance is out of range, not a 500; the prompt asks for the whole range in five bands; the completion token cap is the variable `search_rerank_max_completion_tokens` and set in `dapr.yaml`; a chat deployment without the rest logs `row r4 is off` with what is missing; the three-lists test also holds the deadlines of `dapr.yaml` and `terraform.tfvars` under `verdict`'s wait; the slow-reranker case over the real manual has 3 s against a 6 s delay and restores the mode in a `finally`; the two `r4` tests cover a refused call, a candidate named twice, a timed-out call and non-default settings, with no new test case; the whole-path test asserts `r4`'s runs; the SPA's wording speaks of `r4` as not available somewhere, not as not built. Only the tests of the edited files were run after this.
- **Not proven here:** anything against a real model (Cohere's availability, the reranker's time and tokens, real recall): the checks are in `deferred-work.md`. `tools/dev.sh` was not started, so `r4` has not answered over the Dapr sidecars locally. A comment in `services/web/src/web/settings.py` still says "not built here" of the Compare fallback; only the SPA was open for edits.

## Spec Change Log

## Review Triage Log

Review 1 (2026-10-08): blind hunter (B), edge-case hunter (E), verification-gap (V). No intent gap, no bad spec.

| # | Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- | --- |
| B1, E3 | Five slow rerank calls hold every slot of the gateway's cap, so the other rows' query embeddings wait past their 8 s deadline | medium | patch | `relevance` goes through `_call`, which holds the one semaphore for up to 15 s. The cap stays shared, as the spec says; the reranker gets a smaller share of it so an embedding always finds a slot. |
| B2, E6 | A rerank call that timed out is sent again with under 5 s of the deadline left | medium | patch | 15 s per call, one retry, 20 s for the search: the second prompt is paid for and cancelled. |
| B3 | `rerank_ms` is missing when the reranker failed or was too slow | low | patch | It is set only after an answer; the Azure check reads it to judge the deadline. |
| B4 | An empty answer (a refusal, a filter, a cut at the token limit) is logged as `rerank_not_json` | low | patch | `_answer_of` gives `""` for all three; one reason code of its own. |
| B5 | The prompt names only 0, 0.5 and 1, so many candidates tie and fall back to the fused order | medium | patch | A tie is answered in `r3`'s order by a valid answer; the prompt can ask for the whole range. |
| B6, E2 | A failed `r4` search is sent again by `verdict`, so one tool call can cost several chat calls | low | reject | `verdict`'s resend of a 503 is its rule for every row and already recorded in `deferred-work.md` for the Azure session; B2's patch removes the gateway's share. |
| B7, E5, V-other 1 | The guard that callers wait longer than `r4`'s deadline compares code defaults only | low | patch | `dapr.yaml` and `terraform.tfvars` set the deadline as free values; the test already reads both files for the rows. |
| B8, E4 | No validation ties the rerank depth to the candidate depth or to the token cap | low | reject | Combinations of settings nobody ships; the depth is capped by what the fusion gives. |
| B9 | The reranker's completion token cap is a code default only | low | patch | The Azure checks name it as the setting most likely to need tuning; one more variable. |
| B10 | `r4`'s cost is still null on the scoreboard | low | reject | Every row's cost is null until the owner states them (`deferred-work.md`, story 3.4). |
| B11 | Sprint status beside spec status | false | reject | Sprint status moves at the end of the review. |
| B12, E9 | "Tests, inside the budgets" while three packages are over | low | reject | The overs predate the story; its counts did not grow. |
| B13, E8 | The slow-reranker case gives the whole search 0.5 s against a real database | medium | patch | An embedding and two reads over 0.5 s on a busy machine would answer 502 and fail the test; the mode is restored without `try/finally`. That the `r4` checks sit in story 3.3's test is the budget rule and rejected. |
| B14, V1 | A rerank call the model refuses (4xx) has no test | low | patch | Filed evidence: deleting the `except ModelCallFailed` fails nothing; one more search in the existing test. |
| B15, V-other 2 | The SPA's comments and test wording still call `r4` not built | low | patch | With `r4` listed locally and in the `app` stack, Compare's default pair is the live one. |
| B16 | The whole-path test asserts nothing of `r4`'s verdict runs | low | patch | One assertion that none of its runs failed. |
| B17 | Nothing was run over real HTTP or Dapr | low | reject | True and recorded; the local stack is run once when the stories are done, as the hand-off note asks. |
| V2 | Three of the four rerank settings are not shown to reach the code that uses them | medium | patch | Filed evidence: every default equals the setting's default. |
| V3 | The span's `r4` attributes are never observed | low | defer | Filed disposition; the log line is tested. |
| V4 | A candidate named twice, and four other refusals of `relevance_by_chunk`, are unpinned | low | patch | The duplicate only, as filed: one entry in the existing dict. The other four are deferred. |
| V5 | The two new settings validators have no test | low | defer | Filed disposition. |
| V6 | `r4` asking no model when there are no candidates has no test | low | defer | Filed disposition. |
| V-other 3 | The stand-in repeats the reranker's wire names | low | reject | No service may import the stand-in; the cross-service test catches a rename. |
| E1 | A huge whole number as a relevance raises `OverflowError` out of the domain | low | patch | `math.isfinite` on a large `int`; a direct correction. |
| E7 | A chat deployment set without a model endpoint leaves `r4` off with no log line | low | patch | One line beside the other "off" lines. |

## Design Notes

- **Azure checks for `deferred-work.md`:** whether Cohere Rerank can be deployed on Foundry in West US 3 (if so, the owner decides whether `r4` moves to it); how long the LLM reranker takes for 20 chunks on the real deployment and whether `r4`'s deadline fits; its token use per search against the shared limit; `r4`'s recall and accuracy beside `r3`'s, which only real models make meaningful.
- Owner to confirm: the LLM reranker was built first because Cohere's availability cannot be checked with Azure down.
- With `r4` listed locally, story 3.6's default Compare pair (`r4`, `r5`) comes into force there.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Do not run `tools/dev.sh` unless nothing else holds its ports, and stop what you start. Tests that use the scheduler emulator fail each other when two runs overlap: run them once at the end. Another agent may be working in `services/web` and its SPA: leave those alone.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07, and again on 2026-10-08, for the remaining stories to be built in order; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services evals && uv run pytest --cov` -- expected: all pass, coverage at least 80%
- `docker build -f services/retrieval/Dockerfile -t aiuw-retrieval:dev .` -- expected: builds
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
