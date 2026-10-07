---
title: 'Story 3.8: Row r6 with agentic retrieval'
type: 'feature'
created: '2026-10-08'
status: 'ready-for-dev'
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
- [ ] `services/retrieval/` -- the knowledge source and knowledge base in the job, the retrieve call and its mapping, row `r6` with its deadline, availability and settings; the vectorizer on the index definition
- [ ] `packages/synthdata/` -- the search stand-in's knowledge base routes with a retrieve answered from its documents, and the verdict stand-in's composing answer
- [ ] `services/verdict/` -- the `r6` run: per-fact searches from code, one composing call without a search tool, the same checks
- [ ] `services/workflow/`, `dapr.yaml`, `tools/`, `infra/demo/app/` -- `r6` available where it can run; the search identity's role; the settings
- [ ] Tests, inside the budgets: the job making the knowledge base, a search on `r6`, not configured, the service down; a verdict run on `r6` with its steps; the three-lists test and the cross-service tests extended in place
- [ ] `evals/static-metrics.yaml`, `README.md`, `infra/bootstrap/README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; the Azure checks

**Acceptance Criteria:**
- Given the local stack with the stand-in, when the ingestion job has run, then a search with `r6` answers through the same search operation and result shape.
- Given a verdict run on `r6`, when it runs, then the agent's own search loop is off, one retrieval request is made per fact, and the verdict is composed from what returns.
- Given `r6` exists, when the runner runs again, then the scoreboard shows all six rows.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

## Spec Change Log

## Review Triage Log

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
