---
title: 'Story 3.7: Row r4 with a reranker'
type: 'feature'
created: '2026-10-08'
status: 'ready-for-dev'
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
- [ ] `services/retrieval/` -- the shared candidates step, the reranker port and its chat call with the prompt, row `r4` with its deadline and settings, availability by the chat deployment
- [ ] `packages/synthdata/` -- the chat stand-in answers a rerank request by word overlap, with its failure modes
- [ ] `services/verdict/`, `services/workflow/`, `dapr.yaml`, `tools/`, `infra/demo/app/` -- `r4` available where the chat deployment is set; the callers' timeouts above `r4`'s deadline
- [ ] Tests, inside the budgets: a search on `r4` (order, scores, same candidates as `r3`), a bad answer, not configured, the slow reranker; the three-lists test and the test over the real manual extended in place
- [ ] `evals/static-metrics.yaml`, `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- the reranker named; what changed; the Azure checks

**Acceptance Criteria:**
- Given the local stack, when a search runs with `r4`, then it takes `r3`'s candidates, reranks them and answers the common shape.
- Given `r4` exists, when the runner runs again, then the scoreboard shows `r4` with the reranker used.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

## Spec Change Log

## Review Triage Log

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
