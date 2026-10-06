---
id: SPEC-underwriting-poc
companions:
  - flows.md
  - bake-offs.md
  - synthetic-data.md
  - ../../../docs/standards/azure.md
  - ../../../docs/standards/security.md
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# AI Medical Underwriting Assistant — POC

## Why

Opportunity and proof. Medical underwriters lose hours reading medical records to find the few facts that move a rating. This POC proves, on synthetic data, that an LLM can screen an uploaded document, extract the meaningful medical facts with page citations, compare them against underwriting guidelines through RAG, and suggest a verdict that an underwriter can audit end to end. It also showcases two head-to-head comparisons (retrieval and classification) so the build choice is made on evidence.

## Capabilities

- **CAP-1**
  - **intent:** A customer uploads a PDF and sees each page's progress through the pipeline.
  - **success:** Uploading a synthetic case PDF shows a per-page status badge within the upload screen.
- **CAP-2**
  - **intent:** Each page is classified as medical or non-medical, with a confidence score, before any extraction.
  - **success:** Every page carries a type, a confidence and a one-line reason; routing follows the gate rules in `flows.md`.
- **CAP-3**
  - **intent:** An underwriter triages pages the gate could not settle, accepting them into extraction or denying them.
  - **success:** Pages below 90% confidence, or kept by the customer despite a non-medical verdict, appear in the triage queue with thumbnail, predicted type, confidence and reason; accept and deny both take effect.
- **CAP-4**
  - **intent:** Medical facts are extracted from accepted pages, each with its page number and verbatim quote.
  - **success:** Every fact's quote is checked against the page text; a quote not found is flagged, never shown as verified.
- **CAP-5**
  - **intent:** Each extracted fact retrieves the matching rules from the synthetic underwriting manual.
  - **success:** On the case set, retrieved results carry `rule_id`s and the expected rules appear in the top 5 (scored in `bake-offs.md`).
- **CAP-6**
  - **intent:** The system suggests a verdict: standard, loaded premium (with loading), decline, or refer to underwriter.
  - **success:** Each verdict lists its reasons, each citing fact pages and `rule_id`s; low confidence or conflicting rules yield "refer to underwriter".
- **CAP-7**
  - **intent:** The result is auditable: the PDF sits beside the findings and every citation is navigable.
  - **success:** Clicking a fact's citation jumps to and highlights its line in the PDF; clicking a reason opens its manual rule.
- **CAP-8**
  - **intent:** Retrieval is compared head to head: pgvector against Azure AI Search.
  - **success:** A Compare toggle shows both retrievers' results for the same upload side by side, and a scoreboard reports the metrics in `bake-offs.md` over the case set.
- **CAP-9**
  - **intent:** Classification is compared head to head: an LLM classifier against an Azure AI Document Intelligence custom classifier.
  - **success:** A scoreboard reports the metrics in `bake-offs.md` over the labelled page set.
- **CAP-10**
  - **intent:** Every classification, override, accept, deny, extraction and verdict is recorded with actor and time.
  - **success:** For any case, the audit trail reconstructs who or what did each step and when.
- **CAP-11**
  - **intent:** The synthetic manual, case PDFs and negative pages exist with known right answers.
  - **success:** The artifacts in `synthetic-data.md` exist and every case and page has its expected labels, rules and verdict.

## Constraints

- Synthetic data only. No real personal or health data enters any environment.
- The AI never issues a final decision. The result screen states "AI suggestion, not a decision".
- Every displayed fact and reason traces to a document page or a manual `rule_id`; uncited claims are not shown as findings.
- Bake-off fairness: contenders share inputs, chunking, embedding model and LLM; only the component under test differs.
- Hosted on Azure under the org standards in `docs/standards/`. Azure AI Search and Document Intelligence are outside the Azure standard and need recorded architecture decisions.
- Retrieval and classification each sit behind one interface, so either contender can serve the pipeline.

## Non-goals

- Production readiness: scale, high availability, multi-tenant use, real policy administration integration.
- Real medical records or proprietary underwriting manuals (Swiss Re, Munich Re and similar).
- Automated final decisions, premium calculation beyond the suggested loading, or customer notifications.
- Jira sync (no Jira site configured yet).

## Success signal

- In a live demo, a synthetic case PDF goes from upload through the classification gate to a cited verdict that matches its expected verdict, with every citation clickable to its source; a junk page is caught by the gate; and both scoreboards name a winner on measured numbers.

## Assumptions

- Two roles: customer (uploader) and underwriter.
- Classification runs per page, not per file, so mixed documents are handled.

## Open Questions

- Which LLM and embedding model on Microsoft Foundry: Azure OpenAI or Claude via Foundry?
- Does the customer sign in? The Azure standard requires Entra sign-in on every route.
- No architecture principles are agreed yet (`docs/architecture/architecture.md` is missing). Run Da Vinci's guardrails round next.
- Who is the demo audience, and is there a date?
