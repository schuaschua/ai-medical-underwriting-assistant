# Bake-offs

Both comparisons run the same inputs through each contender; only the component under test differs.

## Retrieval (CAP-8)

Three contenders are compared on a six-row ladder. Each row changes one thing from the row before, so every gain or loss can be traced to its cause.

| Row | Store | Chunks | Method |
| --- | --- | --- | --- |
| `r1` | pgvector on Azure Database for PostgreSQL Flexible Server | Fixed-size | Vector only |
| `r2` | pgvector | Smart | Vector only |
| `r3` | pgvector | Smart | Hybrid: vector + PostgreSQL full-text search, fused with RRF |
| `r4` | pgvector | Smart | `r3` + reranker |
| `r5` | Azure AI Search | Smart | Hybrid + semantic ranker |
| `r6` | Azure AI Search | Smart | Agentic retrieval (knowledge base with LLM query planning, preview API) |

Contenders: pgvector (`r1` to `r4`), Azure AI Search classic hybrid (`r5`) and Azure AI Search agentic retrieval (`r6`). Rows `r1` to `r5` are driven by the verdict agent through one search tool; in `r6` Azure AI Search's own agentic pipeline answers the same per-fact need.

Smart chunks hold one rule each, with the parent section and an LLM-written context line. Fixed-size chunks are the baseline.

Shared by every row: the manual, the embedding model and its vectors, the LLM and the case set. The rule table is the answer key and is never indexed.

| Metric | Measure |
| --- | --- |
| Rule recall | Share of expected facts whose expected `rule_id` appears in the top 5, measured with one search per fact and a fixed query builder, so retriever quality is not mixed with the agent's query writing |
| Verdict accuracy | Suggested verdict matches the expected verdict, with all six rows run on the same extracted facts |
| Latency | Retrieval time per fact |
| Cost | Monthly cost at POC volume, from current Azure price lists |
| Effort | Code and setup needed |

Winner: highest verdict accuracy, then rule recall, then latency.

UI: a Compare toggle runs one upload through two rows (default `r4` and `r5`) and shows the two result panes side by side, differences highlighted.

## Classification (CAP-9)

| | Contender A | Contender B |
| --- | --- | --- |
| Engine | LLM classifier into page types; confidence from agreement across repeated runs | Azure AI Document Intelligence custom classification model |
| Shared | Labelled page set (medical, non-medical, edge pages) | |

Page types: lab report, attending physician statement, application form, ID document, invoice, other.

| Metric | Measure |
| --- | --- |
| Accuracy | Medical vs non-medical correct |
| Calibration | Of pages scored ≥ 90%, the share labelled correctly (should be ≥ 90%) |
| Queue rate | Share of pages routed to the triage queue |
| Cost | Cost per page |

Winner: highest accuracy among contenders whose calibration is at least 90%, then the lower queue rate.

The Document Intelligence classifier is trained on a separate page set that shares no pages with the scored set.
