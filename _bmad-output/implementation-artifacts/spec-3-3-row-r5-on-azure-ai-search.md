---
title: 'Story 3.3: Row r5 on Azure AI Search'
type: 'feature'
created: '2026-10-08'
status: 'ready-for-dev'
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
- [ ] `services/retrieval/` -- the search-service client (create index with exhaustive vector search and a semantic configuration, upload, delete, list ids, hybrid query with the semantic ranker), the load step of the job with its comparison check, row `r5` in the table and the search with its score mapping, settings
- [ ] `packages/synthdata/` -- a stand-in for the search service's REST routes that the client uses, holding documents in memory, answering a hybrid query by the stand-in's vectors and word overlap in a reranked order, with modes for unavailable and slow
- [ ] `services/verdict/`, `services/workflow/`, `dapr.yaml`, `tools/`, `infra/demo/app/` -- `r5` available where a search endpoint is set; the two roles; the settings
- [ ] Tests, within the budgets: the load and comparison, a search on `r5`, not configured, service unavailable; outside `services/` one test over the real manual (both stores identical; `r5` answers the common shape) 
- [ ] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; the Azure checks

**Acceptance Criteria:**
- Given the local stack with the stand-in, when the ingestion job has run, then the index and pgvector hold the same chunk ids and counts, and a search with `r5` answers the common shape.
- Given a service with no search endpoint, when `r5` is searched, then it is refused as not available and the other rows answer.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

## Spec Change Log

## Review Triage Log

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
