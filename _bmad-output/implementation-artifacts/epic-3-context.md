# Epic 3 Context: The retrieval comparison names a winner on measured numbers

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Build the five retrieval rows that are still missing, complete the synthetic case set and its answer key, and write the one runner that scores all six rows on the same cases. The retrieval scoreboard then shows measured numbers and marks a winner, and the Compare toggle shows two rows' results for one upload side by side. The audience is an architect expert in RAG, so the comparison must be fair and traceable: each row changes one thing from the row before. Stories are ordered so the last two (`r4`, then `r6`) can be cut. There is no UX design; screens are plain.

## Stories

- Story 3.1: Full synthetic case set with its answer key
- Story 3.2: Baseline rows r1 and r2
- Story 3.3: Row r5 on Azure AI Search
- Story 3.4: Bake-off runner scores the rows
- Story 3.5: Retrieval scoreboard
- Story 3.6: Compare two rows on one case
- Story 3.7: Row r4 with a reranker
- Story 3.8: Row r6 with agentic retrieval

## Requirements & Constraints

- **The ladder.** `r1` pgvector, `fixed` chunks, vector only. `r2` pgvector, `smart`, vector only. `r3` pgvector, `smart`, vector plus PostgreSQL full-text fused by rank (built). `r4` is `r3` plus a reranker. `r5` Azure AI Search, `smart`, hybrid plus semantic ranker. `r6` Azure AI Search agentic retrieval (knowledge base with LLM query planning, preview API).
- **Fairness.** Every row shares the manual, the chunk records, the embedding model and its vectors, the chat model and the case set. Both stores hold the same `chunk_id`s and vectors and search by exact nearest neighbour. No row gets its own endpoint or result shape. The rule table is the answer key and is never indexed.
- **Case set:** about 20 case PDFs of a few pages each, covering all four verdicts, each with planted identifiers. Each answer-key entry lists expected facts with pages, expected `rule_id`s, expected verdict, planted identifiers and each page's expected label. Synthetic data only.
- **Rule recall** is measured apart from the agent: one eval search per expected fact per available row, with the query from the one query builder in contracts; a hit is the expected `rule_id` in the top 5.
- **Verdict accuracy:** each case is uploaded once and started with every available row and an `eval_run_id`, so all rows judge the same extracted facts. The runner answers the human waits from the answer key's page labels.
- **Other metrics:** latency is retrieval time per fact; cost (monthly, at POC volume) and effort are declared inputs in `evals/static-metrics.yaml`, each with its source. Winner: verdict accuracy, then rule recall, then latency.
- **Published as files.** The runner writes `data/scoreboards/retrieval.json` and `data/scoreboards/redaction.json`; `web` serves them read-only; no service stores or accepts scores. A row not scored shows "not measured" and no numbers.
- **Redaction check in the same run:** the run fails if any planted identifier, or any part of a planted name, is found in any stored page text after the contracts normalisation. It also reports how many expected-fact quotes are no longer found.
- **Eval cases stay out of the way:** cases with an `eval_run_id` are not in the triage queue or the case list (already so).
- **Compare:** the toggle asks `workflow` for one more verdict run on a finished case; the pair is a setting, default `r4` and `r5`, falling back to `r3` and `r5` while `r4` is not built. Differences in verdict, reasons and retrieved rules are highlighted.
- **`r6`:** the agent's own search loop is off; one retrieval request per fact; the verdict is composed from what returns. The search service's own identity is granted access to the chat deployment. It needs the pre-release `azure-search-documents` 12.1.0b2, the only pre-release package allowed.
- **`r4`:** Cohere Rerank on Foundry (`Cohere-rerank-v4.0-fast`, the owner's choice of 2026-10-08, after Azure's catalogue listed it for West US 3); the LLM reranker the row was first built with is removed. The scoreboard names the reranker used. Its deployment and its call are unproven in Azure; if it cannot be deployed there, the row is left out (`infra/bootstrap/README.md`).
- Still in force: the AI never decides; logs carry ids, codes and timings, never page text; coverage 80% services, 60% SPA.

## Technical Decisions

**Owner rules (project `CLAUDE.md`)**

- **Azure stays down while coding.** Azure AI Search (`r5`, `r6`), the reranker deployment, real embeddings and the deployed runner are built against fakes or stand-ins; each Azure check is added to `deferred-work.md` for the one final test session.
- **Small test suite:** about 500 Python cases in all, one test per acceptance criterion plus the few edge cases that guard a real risk; merge or remove weaker tests to stay inside the budget. Budgets: `retrieval` 55, `verdict` 55, `workflow` 110, `web` 45, `synthdata` 45, `contracts` 60. `web` and `synthdata` are already slightly over. `evals/` has no budget and is not among pytest's test paths.

**What the earlier epics built that this epic stands on**

- **Row table:** `retrieval/domain/rows.py` names every row with its chunk set, method and a `built` flag; only `r3` is built. A search on an unbuilt row, and a rule read on a chunk set not yet ingested (`fixed`), answer 409 `retriever_not_available`. Building a row is a change to its line and one more branch in the search. The domain is already split so that a vector-only row calls the nearest-neighbour statement alone and a reranker takes the hybrid list.
- **One chunk record for every store:** table `retrieval.chunk` has a `chunk_set` column and one row per chunk: `chunk_id`, `rule_ids` (defined), `reference_rule_ids`, section id and title, impairment, manual page, text, context line, 3,072-dimension vector, content hash and the two deployment names. A `smart` chunk is one rule's definition paragraph; its id is `smart-<rule_id>` (valid as an Azure AI Search key). The embedded text is the context line followed by the chunk text; the full-text column indexes the chunk text only, with rule ids written as one word. Today: 111 `smart` chunks from a 211-page manual.
- **Ingestion job** (the `retrieval` image, calling no service): layout parsing over REST, chunking that checks its own cut without the rule table, an LLM context line per chunk, embeddings, all stored in one transaction under an advisory lock. Self-checks: an unchanged manual ends the run before any Azure call (`retrieval.ingest_run` keeps the manual hash, prompt digest and deployments per chunk set); unchanged chunks cost no model call; one small embedding call first; removal of more than a tenth of the stored chunks is refused without an override. The deploy does not start the job yet.
- **Search** has its own short budget: embedding timeout 3 s, one retry, 8 s over the whole search, then `model_unavailable` or `upstream_unavailable`. Both lists are read in one read-only transaction. A search is refused when the last ingest run recorded another embedding deployment; Azure AI Search needs the same guarantee. A reranker or agentic row must fit this budget or the budget must be set per row.
- **Rule read:** `GET /rules/<rule_id>?retriever_config=` reads from the row's chunk set; without the parameter, `smart`. `web`'s rule route passes no row on today.
- **Verdict run:** keyed by case and row in `verdict` (one stored run per pair; a repeat returns it). `POST /cases/{case_id}/verdict-runs` on `workflow` starts one orchestration per case and row (instance id `<case_id>:verdict:<retriever_config>`), answers its state, then the run id or error code; 409 `pages_not_terminal` until the case is `completed`. A run asked for afterwards never changes the case status. A case started with several rows gets one run per row after its last page is final, and completes only when each row has its `verdict.suggested` event.
- **Rows a case may run with:** `WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS` (default `r3`). A start, or a later run, naming another row is 409 `retriever_not_available` and stores nothing. `workflow`, `verdict` and `retrieval` each hold their own notion of built rows and nothing checks that they agree: a new row must be switched on in all three.
- **Changing the orchestration:** waiting cases are replayed from history, so a change to what `case_lifecycle` yields is a new orchestration name beside it, or deployed only when no case waits.
- **Rule table** (`data/answer-key/rule-table.json`, 40 impairments, 111 rules): per impairment, when its rules apply (a diagnosis on file or any reading, and what rules it out); per rule, its band, debit or decline and the rules it refers to (`adds` or `replaces`). A debit of 0 is a valid rule. The three existing cases meet `UW-DM-002` (`case-001`, loaded +50), `UW-HT-002` with `UW-TOB-001` (`case-002`) and none (`case-003`, standard); a test keeps that true. Case entries have no expected facts, rules or verdicts yet.
- **Guard from story 1.4:** nothing under `services/` or `packages/contracts/` may name `synthdata`, the answer key folder or the rule table file. Tests that read the answer key, run a stand-in or run several real services live in `packages/synthdata/tests`; service tests use fakes.
- **Dev-only stand-ins in `packages/synthdata`:** Azure AI Language redaction, Document Intelligence layout, chat completions (classification, context lines, extraction), embeddings, and the verdict agent's conversation, each with failure modes. Services refuse a non-loopback plain-HTTP endpoint. The stand-in's vectors only count shared words, so recall measured locally proves the plumbing, not the retriever; meaningful recall exists only in Azure. Azure AI Search and the reranker of `r4` have stand-ins too (stories 3.3, 3.7 and 3.8).
- **Open with the owner:** a rule id typed as the whole query does not always rank its defining chunk first. Recommended and not yet confirmed: keep pure two-list fusion and require the top 3. Do not add a third list or an answer outside the fusion until answered.
- **Starting cases:** every case is created and started through `web` with a demo role. Start options (rows, classifier, `stop_after`, `eval_run_id`) are accepted only from the underwriter role; decisions need the role that owns them. `web` has no route yet for an eval search, page text, a verdict-run request or the scoreboard files.
- **Load:** `workflow` runs 5 activities at once and a stage activity can hold a thread for about 200 s; the chat deployment's 100 thousand tokens a minute are shared by all stages; each service caps its own concurrent model calls. Twenty cases with six runs each will meet these limits.
- **Answer key gaps recorded for this epic:** occurrence counts per page, strings that may legitimately be redacted, and expected clinical facts. The extraction prompt, the agent's confidence figure and the chunk context lines have no evaluation other than this runner.
- **Query builder:** `contracts.query.build_fact_query(statement)` collapses whitespace and nothing else.

## UX & Interaction Patterns

- Scoreboard: one line per row with store, chunk set, method, rule recall, verdict accuracy, latency, cost and effort; the winner marked; "not measured" for a row without numbers.
- Compare: two result panes side by side on the same case, differences highlighted. The existing result view already lets the underwriter pick among a case's runs and keeps polling while a requested run is `running`.
- As elsewhere: the SPA polls through its one API client, sends `X-Demo-Role`, keeps wording in the strings module, never renders model output as HTML.

## Cross-Story Dependencies

- 3.1 supplies the cases and answer key that 3.4 scores; 3.2 adds the `fixed` chunk set to the ingestion job; 3.3 loads Azure AI Search from the same records and is needed by 3.6's fallback pair and by 3.8.
- 3.4 scores whatever rows are available and is rerun after 3.7 and 3.8; 3.5 reads 3.4's file; 3.6 needs two built rows.
- 3.3, 3.7 and 3.8 can only be proven in Azure; their checks join the deferred list, as do the real scoreboard numbers.
- Epic 4's scored page set draws its medical pages from 3.1, and its story 4.3 extends the runner of 3.4.
