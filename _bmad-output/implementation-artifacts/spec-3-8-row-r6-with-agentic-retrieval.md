---
title: 'Story 3.8: Row r6 with agentic retrieval'
type: 'feature'
created: '2026-10-08'
status: 'done'
baseline_commit: 'a61ea1c22df0726dd5711dff2fcd3a3e27a9f624'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-3-row-r5-on-azure-ai-search.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-7-row-r4-with-a-reranker.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
  - '{project-root}/docs/standards/azure.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The ladder compares retrieval that this project built. It cannot yet say how a managed agentic pipeline, Azure AI Search's own, does against the same need.

**Approach:** Build row `r6`: a knowledge base over the index that `r5` already uses, with the search service's own LLM query planning, answered through the one search operation and the common result shape. On `r6` the verdict agent's own search loop is off: the service makes one retrieval request per fact and the model composes the verdict from what returned, so that two agents are not stacked on each other. Azure stays down: everything is proven against the local stand-in.

## Boundaries & Constraints

**Always:**
- Spine AD-11, AD-12, AD-15, AD-16; NFR4. The knowledge base reads the same index, the same `smart` chunks and the same vectors as `r5`; nothing is chunked or embedded again. The ingestion job creates the knowledge source and the knowledge base after the index is loaded, idempotently, and a failure there has its own log line and leaves pgvector and the index as they are.
- The preview API (`2026-08-01-preview`, a setting) is used for `r6` only; `r5` stays on its stable version. The pinned pre-release `azure-search-documents` 12.1.0b2 is used for the knowledge base calls if it can be installed and fits the service's patterns (Entra token, no key, no redirect followed, a replaceable transport); if it cannot, the same calls are made over REST and the notes say why. No other pre-release package.
- The search service plans and runs its own queries with the chat deployment and, for vector queries over its sub-queries, the same embedding deployment, both reached with the search service's own identity. That identity is granted Cognitive Services User on the Foundry account, as the standards list.
- A search with `r6` sends the query as it is asked and takes only the references that return; a synthesised answer is not asked for and never used. References are mapped to the common result shape in the service's order; a reference whose chunk pgvector does not hold, or holds with other content, is left out and counted, as for `r5`. `score` is between 0 and 1 by a stated rule. `r6` has its own search deadline, as `r4` has, and every caller's timeout stays above it.
- A verdict run on `r6`: the facts are listed, one search is made per fact with the query the contracts' query builder makes from the fact's statement, each logged as a step like a tool call, and then the model is asked once to compose the proposal from the facts and the rules those searches returned, with no search tool. Every check of the other rows on a proposal still holds; a rule counts as read when a search of the run returned its chunk, and an effect is checked against that text. A run with more facts than the step limit refers as at the step limit.
- `r6` is available where `retrieval` is given a search endpoint and a chat deployment, and refused as not available elsewhere. The three lists of rows stay equal. Logs carry ids, counts, the row and timings, never a query, a sub-query or chunk text.
- Tests follow the owner's rule in `CLAUDE.md`, inside the budgets.

**Never:**
- No change to what rows `r1` to `r5` answer or to the agent's loop on them. No answer synthesis, no second knowledge base, no web or other knowledge source.
- The stand-in never ships in an image. Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something. Do not edit `infra/demo/foundation`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Knowledge base made | The index is loaded; no knowledge base yet | The job creates the knowledge source and the knowledge base over that index | N/A |
| Job run again | Both exist | Nothing is made anew; the job ends as before | N/A |
| Search `r6` | A query, `r6`, `top_k` 5 | Up to 5 items from the returned references, common shape, the service's order | N/A |
| No reference | The service returns none | An answer with no items | N/A |
| Reference not in pgvector | A returned chunk id pgvector lacks | Left out and counted in the log | N/A |
| Service down or slow | Unavailable, or past `r6`'s deadline | No partial answer | `upstream_unavailable` |
| Not configured | No search endpoint, or no chat deployment | `r6` refused; the other rows unaffected | 409 `retriever_not_available` |
| Verdict on `r6` | A case with 3 facts | Steps: the facts listed, 3 searches, no other search; one proposal composed and checked | N/A |
| Reason on an unseen rule | The model cites a rule no search returned | That reason is dropped, as on every row | The run refers as the rules of story 2.6 say |
| A search fails in the run | One of the per-fact searches fails | The step is logged as failed | The run ends as a failed search ends a run on the other rows |

</frozen-after-approval>

## Code Map

- `_bmad-output/planning-artifacts/architecture/architecture-ai-medical-underwriting-assistant-2026-10-06/ARCHITECTURE-SPINE.md` -- AD-11 (the preview API and the SDK pin), AD-15 ("Row `r6`: the agent's search loop is replaced by one retrieval request per fact"), the deployment table (the search identity's role); `reviews/review-web-verification.md` -- agentic retrieval needs a vectorizer on the index for vector queries, and a reference's reranker score is optional
- `services/retrieval/src/retrieval/adapters/search_index.py` -- `SearchIndex` (REST, `ensure`, `hybrid`), `index_definition` (no vectorizer today; the index has never been created in Azure, so its definition may gain one), `search_token_for`; `domain/index_load.py`, `ingest.py` (`load_index`: where the knowledge source and base are made); `domain/rows.py` (`R6`, `AI_SEARCH_AGENTIC`, `needs_search_service`, the availability story 3.7 added for a chat deployment); `domain/search.py` (`ai_search_hybrid` as the model: the deployment guard, the left-out count, `_SEARCHES`, the per-row deadline of story 3.7); `settings.py`; `pyproject.toml` (no `azure-search-documents` yet)
- `packages/synthdata/src/synthdata/search_standin.py` -- routes as closures in `app()`, `Mode`, `self.query` for a ranked answer; its header on every answer
- `services/verdict/src/verdict/domain/run.py` (`run_verdict`, `_suggest`, `_work`, `RUNNABLE_RETRIEVER_CONFIGS`, `SEARCH_SERVICE_RETRIEVER_CONFIGS`), `domain/toolbox.py` (`Toolbox.call`, `_search_rules`, `_log`), `domain/state.py` (`RunState`: `returned_by_search`, `read`, `rule_texts`, `looked_things_up`), `domain/decide.py` (`keep_reasons`: `RULE_NOT_SEEN`, `RATING_NOT_READ`, `RULE_NOT_READ`, `EFFECT_NOT_IN_RULE`), `domain/ports.py` (`VerdictAgent`), `adapters/agent.py` (`FrameworkVerdictAgent`, `TOOLS`, `TASK`); `packages/synthdata/src/synthdata/verdict_standin.py` (how the agent's conversation is recognised and scripted)
- `packages/contracts/src/contracts/query.py` (`build_fact_query`), `models/retrieval.py` (`SearchItem`: whether `score` may be absent), `models/verdict.py` (`AgentStep`), `enums.py` (`ToolName`)
- `infra/demo/app/main.tf` (`retrieval`'s search roles; the search identity's role goes beside them: `local.foundation.search_principal_id`, `foundry_account_id`), `locals.tf`, `variables.tf`, `terraform.tfvars` (the rows, the API versions); `infra/bootstrap/state-backend.sh` already allows Cognitive Services User
- `packages/synthdata/tests/test_foundry_standin.py` (the three lists), `test_manual_search_end_to_end.py`, `test_verdict_end_to_end.py`; `dapr.yaml`, `tools/`; `evals/static-metrics.yaml` (row `r6`)

## Tasks & Acceptance

**Execution:**
- [x] `services/retrieval/` -- the knowledge source and knowledge base in the job, the retrieve call and its mapping, row `r6` with its deadline, availability and settings; the vectorizer on the index definition
- [x] `packages/synthdata/` -- the search stand-in's knowledge base routes with a retrieve answered from its documents, and the verdict stand-in's composing answer
- [x] `services/verdict/` -- the `r6` run: per-fact searches from code, one composing call without a search tool, the same checks
- [x] `services/workflow/`, `dapr.yaml`, `tools/`, `infra/demo/app/` -- `r6` available where it can run; the search identity's role; the settings
- [x] Tests, inside the budgets: the job making the knowledge base, a search on `r6`, not configured, the service down; a verdict run on `r6` with its steps; the three-lists test and the cross-service tests extended in place
- [x] `evals/static-metrics.yaml`, `README.md`, `infra/bootstrap/README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; the Azure checks

**Acceptance Criteria:**
- Given the local stack with the stand-in, when the ingestion job has run, then a search with `r6` answers through the same search operation and result shape.
- Given a verdict run on `r6`, when it runs, then the agent's own search loop is off, one retrieval request is made per fact, and the verdict is composed from what returns.
- Given `r6` exists, when the runner runs again, then the scoreboard shows all six rows.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- **Client: REST with `httpx2`, not the SDK.** `azure-search-documents` 12.1.0b2 can be installed (it resolves; it was unpacked under `.work/s38/sdk` and read, never run), and its models for `2026-08-01-preview` are where the request and answer shapes were read from. It does not fit the service's patterns: its async client sends through azure-core's own transport (`aiohttp`, which nothing in the workspace depends on), so neither the `httpx2` transport the tests stand a service in with nor the one client that follows no redirect can be given to it; its bearer-token policy refuses the plain-HTTP loopback stand-in; its retries and HTTP logging are its own. So the package is not a dependency, `uv.lock` is unchanged, and the three calls are written on the client `r5` already has.
- **Adapter:** `adapters/search_index.py` is split into `SearchServiceClient` (the token, the retries, a span per call; one REST version per client) and `SearchIndex` on it; `r5`'s calls are unchanged. New `adapters/knowledge_base.py`, `KnowledgeBase` on the same base: `ensure` (for each of the two a `GET`, and a `PUT` with `Prefer: return=representation` on 404; one that is there is left as it is) and `retrieve`. The knowledge source names the index, its semantic configuration `rules` and, as `sourceDataFields`, the fields a hybrid query answers. The knowledge base names that one source, the chat deployment as its `azureOpenAI` model (`resourceUri` is `RETRIEVAL_MODEL_ENDPOINT`, no key and no identity, so the service's own), `retrievalReasoningEffort: low` and `outputMode: extractiveData`. `index_definition` gained a vectorizer (the embedding deployment) on the profile of the vector field, given by the job only.
- **Job** (`retrieval/ingest.py`, `domain/index_load.py`): after an index load that ended well, `make_knowledge_base` runs `ensure_knowledge_base` (60 s, `RETRIEVAL_SEARCH_AGENTIC_ENSURE_DEADLINE_SECONDS`). Lines: `knowledge base done: source=... base=... source_created=yes|no base_created=yes|no` and `knowledge base failed: code=... reason=search_...`, which ends the job with 1. No knowledge base is made over an index whose load failed. Nothing there writes pgvector or the index.
- **Search** (`domain/search.py`, `ai_search_agentic`): one read of pgvector (the run record's deployment guard and each chunk's `content_hash`), then one retrieve request with the query as one user message, exactly as asked, `maxOutputDocuments` = `top_k`, `includeActivity` for counts, and one `knowledgeSourceParams` entry with `failOnError`. Only `references` is read. A reference is the index's document (`docKey`, `sourceData`, an optional `rerankerScore`); one of another source, or without a field, fails the search. Left out and counted as for `r5` (`left_out=`). `rank` is the service's order. **`score`, by one rule for a whole answer:** where every reference kept carries a `rerankerScore`, that score divided by 4, cut to 0 to 1, as for `r5`; where one carries none, 1/rank for all of them. `retrieval` embeds nothing for this row.
- **Failures and budget:** `SearchOptions.agentic_deadline_seconds` 20 (`deadline_of`), the retrieve call 15 s and not sent again after a time-out (`again_when_slow=False`); a 429 or 5xx is sent again once. Down, slow past the deadline, 206 or malformed: `upstream_unavailable` (502), no items. Settings `RETRIEVAL_SEARCH_AGENTIC_API_VERSION`, `..._KNOWLEDGE_SOURCE_NAME`, `..._KNOWLEDGE_BASE_NAME`, `..._REASONING_EFFORT`, `..._CHAT_MODEL_NAME`, `..._EMBEDDING_MODEL_NAME` (unset: the deployment's name), `..._TIMEOUT_SECONDS`, `..._DEADLINE_SECONDS`. `verdict`'s 25 s for one search, `web`'s 30 and the runner's 30 are above 20 and untouched; the three-lists test holds them.
- **Availability:** `R6` is built and `needs_knowledge_base`; `available_rows(search_service, reranker, knowledge_base)`. `SearchPorts.knowledge_base` is built only with `RETRIEVAL_SEARCH_SERVICE_ENDPOINT` and `RETRIEVAL_CHAT_DEPLOYMENT` (`row r6 is off` is said once otherwise). `verdict`: `RUNNABLE_RETRIEVER_CONFIGS` is all six, `SEARCH_SERVICE_RETRIEVER_CONFIGS` holds `r5` and `r6`, new `CHAT_DEPLOYMENT_RETRIEVER_CONFIGS` (`r4`, `r6`) and `SERVICE_PLANNED_RETRIEVER_CONFIGS` (`r6`); the defaults are still `r1` to `r3`. `workflow` needed a comment only. `dapr.yaml` and `infra/demo/app/terraform.tfvars` list all six rows.
- **Verdict on `r6`** (`verdict/domain/compose.py`, `run.py` `_work`): `searched_material` calls `Toolbox.call` for `list_facts` and then for `search_rules` once per fact, in order, with `build_fact_query(statement)`, so every step is validated, counted and logged as on the other rows; then `VerdictAgent.compose(toolbox, material)` once. The material is one JSON object: `facts` as `list_facts` answers them and `searches`, per fact its `fact_id` and the `rules` returned. `FrameworkVerdictAgent.compose` is one chat completion through the model gateway with `prompts/compose_verdict.md`, the same answer schema and no `tools`. `RunState.found(..., as_read=True)` marks each returned rule as read with the chunk's text, so `decide.py` is unchanged: an unseen rule is `rule_not_seen`, an effect is checked against the returned text. Step limit, time budget, a failed search (`ToolFailed`, the key row released) and the deadline behave as on every row. The agent's loop (`agent.run`) is not called.
- **Stand-ins:** `search_standin.py` has `GET`/`PUT /knowledgesources('{name}')` and `/knowledgebases('{name}')` (preview versions only; it refuses a source without its index or a planning base without a model) and `POST .../retrieve`: its "plan" is the query and its parts between commas, colons and semicolons (at most four), each run as a hybrid semantic query, with a vector from the model stand-in's `embed_text` where the index names a vectorizer; references with `docKey`, `sourceData`, `rerankerScore`, and an activity log. `verdict_standin.py` answers a composing request (`composing_of`: one user message that is the object above) at once with its final answer, every returned rule taken as read.
- **Infra** (`infra/demo/app`, formatted and validated only; nothing planned or applied; `infra/demo/foundation` untouched): `azurerm_role_assignment.search_foundry_user` (Cognitive Services User on `foundry_account_id` for `search_principal_id`), eight env values for `retrieval`, eight variables with their validations, `available_retriever_configs` with `r6`.
- **Tests:** `retrieval` 55, `verdict` 58, `synthdata` 50, all unchanged in number (one added and two merged into one in each of the first two; the rest extended in place); the list is in `deferred-work.md`. Whole suite: 543 passed, coverage 91%. The bake-off's whole-path test now runs with a search stand-in and all six rows are measured.
- **Verification run (2026-10-08):** containers up; `uv sync`, `ruff format --check`, `ruff check`, `mypy packages services evals` clean; `uv run pytest --cov` 543 passed, 90.9%; both images build, the `verdict` image holds `compose_verdict.md` and the `retrieval` image holds neither the stand-ins nor the SDK; `terraform fmt -check -recursive` and `validate` of `infra/demo/app` clean. The knowledge base calls were also run against the search stand-in over real HTTP on port 5913 (`.work/s38/http_smoke.py`), started and stopped for that.
- **After review 1 (2026-10-08):** the job compares a knowledge source or base that is there with what would be created and fails the step on a difference (`search_knowledge_source_differs`, `search_knowledge_base_differs`), and makes none over an index that names no vectorizer (`search_index_without_vectorizer`), all `stage_failed`; a retrieve is sent once and never again (`_send(once=True)`), whatever it was answered; a retrieve answered 404 is `retriever_not_available` (409, "not been ingested"), not 502; an answer without a list of `references`, with a text for `rule_ids` or with a `docKey` that is not the document's key is malformed; of two references to one chunk the first is kept, before the cut to `top_k`; the search log line says `score_rule=`; on `r6` a case with more facts than the run has steps left is referred as soon as the facts are listed (one refused step in the log, no search), and a search the toolbox refused fails the run (`stage_failed`); `RUNNABLE_RETRIEVER_CONFIGS` names its six rows one by one again and the settings validator is back; the adapter's `knowledge base ensured` line is gone (the job's `knowledge base done` is the one); two comments corrected. The `r6` search test, the job test and the `r6` verdict test were extended in place: no new test case. Only the tests of the edited files were run after this. The lines above that say otherwise (a 429 or 5xx sent again once; one that is there is left as it is; the searches before the limit are paid for) are superseded by this one.
- **Changed on 2026-10-10, after the first Azure bake-off:** on `r6` the run searches once per distinct fact statement (each fact with that statement gets the rules; the step names the first), and those searches are bounded by `VERDICT_COMPOSED_SEARCH_LIMIT` (default 40), not by the agent's step limit: real cases store more facts than that limit has steps, and 16 of 19 were referred before any search. A case with more distinct statements than the limit is still referred with `step_limit` before any search. The reasoning and the figures are in `deferred-work.md`, in the story's "Owner to confirm" entry.
- **Not proven here, or to be decided:** everything against the real service (the checks are in `deferred-work.md`). `tools/dev.sh` was not started. Listing the facts is a step, so a case with exactly as many facts as the step limit is referred too, and the searches before the limit are made and paid for. Every fact is searched for, not only the readings the agent picks on the other rows, and one after the other: a case with many slow facts can spend the agent's 150 s. The spec's `status` and `sprint-status.yaml` were left as the workflow set them.

## Spec Change Log

## Review Triage Log

Review 1 (2026-10-08): blind hunter (B), edge-case hunter (E), verification-gap (V). No intent gap, no bad spec.

| # | Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- | --- |
| B1, E1 | A knowledge source or base that exists is never compared with the index, model or source it should have | medium | patch | `_ensure` stops at a 200. After the index is given a new name, as both READMEs advise, the job says done and `r6` keeps reading the old index while `r5` reads the new one. |
| B2, E2, E21 | An index that exists without a vectorizer is not noticed when the knowledge base is made | medium | patch | `SearchIndex.ensure` leaves an existing index as it is; `r6` would then run on text alone and be scored as the agentic row. |
| B3, E3 | A knowledge base that was never made answers every `r6` search as a passing outage | low | patch | A 404 from the retrieve becomes 502, which callers send again; the chunk sets have a "not ingested" answer of their own. |
| B4, B5, E10 | A retrieve answered 429, 5xx or not at all is sent again, so the planning is paid for twice and the second cannot finish in the row's deadline | medium | patch | Only a time-out is exempt in `_send`; the wait before the resend is capped at the whole deadline. That `verdict` sends a failed search again is its rule for every row and stays recorded. |
| B6, E4 | An answer with no `references` is taken as nothing found | low | patch | `references or []`; only an empty list means none. |
| B7, E5, E6 | `_reference` takes a text `rule_ids` as a list of characters, and a `docKey` that is not the document's own key | low | patch | Direct checks, as for the page and the score. |
| E7 | The same document in two references gives two items of one chunk | medium | patch | The service runs several planned queries; nothing removes a repeat before the cut to `top_k`. |
| B8, E11, E19 | A per-fact search the toolbox refused is composed as if the manual held nothing | medium | patch | `found.get("rules", [])`. A verdict would rest on a search that was never made; the matrix says a search that fails ends the run as on the other rows. The blank statement is rejected: a fact's statement is never blank. |
| B9 | A case with more facts than steps is referred only after the searches were paid for | low | patch | The count is known after the facts are listed; one check before the first search. |
| B12 | The set of rows `verdict` can run became the whole enum and its validator was removed | low | patch | A row added to the contracts later would be run by the default loop with nothing failing. |
| B14 | Which score rule an answer used is said nowhere | low | patch | One word on the search's log line; the redundant half of the condition goes. |
| B17, B18 | A comment that omits the chat deployment, two names for one log event, the prompt's evaluation not stated | low | patch | Direct corrections. Sprint status beside spec status is false: it moves at the end of the review. |
| B10, E13 | The composing message has no size bound and repeats a rule's text per fact | low | reject | Token use on `r6` is an Azure check already recorded; a cap would be a rule about which rules the model sees. |
| B11, E12 | The per-fact searches are sequential against the agent's 150 s | low | reject | Recorded in `deferred-work.md` with the latency check; searches side by side would need a toolbox that numbers steps under concurrency. |
| B13 | Planning tokens are in one log line only; a sub-query count on `r5`'s span | low | reject | Enough for the Azure check; cost on the scoreboard is a stated figure. |
| B15 | Budgets and merged tests | low | reject | The overs predate the story; its counts did not grow. |
| B16, V-other 1 | Behaviours the diff's own list names as untested | low | reject | Recorded by the implementer in `deferred-work.md`. |
| E8 | Fewer than `top_k` items when a stale reference is left out | low | reject | As decided for `r5` in story 3.3. |
| E9 | The Foundry resource address may not be the form the search service takes | maybe-false | reject | Already an Azure check in `deferred-work.md`; would be low once seen, as the job fails with its own line. |
| E14, E15, E16, E17, E18 | The stand-ins and the test support on inputs only a broken client sends | low | reject | Dev tools called by the project's own client. |
| E20 | On `r6` a searched rule counts as read, so "not read" cannot fail | low | reject | The spec's own rule for the row, flagged to the owner; the effect is still checked against the returned text. |
| V1 | `r6`'s freshness and deployment guards are exercised by no `r6` test | medium | patch | Filed evidence: three mutations pass. |
| V2 | `r6`'s own deadline and retrieve timeout can fall back to other values unseen | medium | patch | Filed evidence: the test passed in 8 s on the other rows' deadline. The settings validator is deferred, as filed. |
| V3 | The cut of `r6`'s items to `top_k` is not observed | low | patch | Filed evidence; one more step in the existing test. |
| V-other 2 | The fallback to the source data's key is never taken | low | reject | Part of the recorded Azure check of what a reference carries. |

## Design Notes

- **Azure checks for `deferred-work.md`:** that the preview API and the SDK version take the knowledge source and knowledge base as written; that the vectorizer and the planning model work with the search identity's role; that references carry the chunk's key and what score they carry; `r6`'s latency per fact against its deadline and a whole run against the run's deadline; its token use; its recall and accuracy.
- Why no tool on `r6`: a model that could search again would be a second agent on top of the service's own, and the row would measure both.
- If `score` must be a number and a reference carries none, derive it from the rank by a stated rule and say so in the notes; do not change the contract for it.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Do not run `tools/dev.sh` unless nothing else holds its ports, and stop what you start. Tests that use the scheduler emulator fail each other when two runs overlap: run them once at the end.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07, and again on 2026-10-08, for the remaining stories to be built in order; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services evals && uv run pytest --cov` -- expected: all pass, coverage at least 80%
- `docker build -f services/retrieval/Dockerfile -t aiuw-retrieval:dev . && docker build -f services/verdict/Dockerfile -t aiuw-verdict:dev .` -- expected: both build
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
