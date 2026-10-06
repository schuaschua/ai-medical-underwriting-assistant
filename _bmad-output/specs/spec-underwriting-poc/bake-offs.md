# Bake-offs

Both comparisons run the same inputs through each contender; only the component under test differs.

## Retrieval (CAP-8)

| | Contender A | Contender B |
| --- | --- | --- |
| Engine | pgvector on Azure Database for PostgreSQL Flexible Server (with Postgres full-text search for hybrid) | Azure AI Search (vector + keyword + semantic ranker) |
| Shared | Manual chunks (one rule per chunk, `rule_id` and `impairment` metadata), embedding model, LLM, case set | |

| Metric | Measure |
| --- | --- |
| Rule recall | Share of cases whose expected `rule_id`s appear in the top 5 per fact |
| Verdict accuracy | Suggested verdict matches the expected verdict |
| Latency | Retrieval time per fact |
| Cost | Monthly cost at POC volume, from current Azure price lists |
| Effort | Code and setup needed |

UI: a Compare toggle runs one upload through both retrievers and shows the two result panes side by side, differences highlighted.

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
