---
status: final
stepsCompleted: [1, 2, 3]
inputDocuments:
  - _bmad-output/specs/spec-underwriting-poc/SPEC.md
  - _bmad-output/specs/spec-underwriting-poc/flows.md
  - _bmad-output/specs/spec-underwriting-poc/bake-offs.md
  - _bmad-output/specs/spec-underwriting-poc/synthetic-data.md
  - _bmad-output/planning-artifacts/architecture/architecture-ai-medical-underwriting-assistant-2026-10-06/ARCHITECTURE-SPINE.md
  - docs/standards/azure.md
  - docs/standards/security.md
  - docs/standards/terraform.md
  - docs/standards/coding-style.md
---

# ai-medical-underwriting-assistant - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for ai-medical-underwriting-assistant, decomposing the requirements from the spec (used in place of a PRD) and the architecture spine into implementable stories. No UX design exists; Darrel chose to proceed without one (2026-10-06), so screens follow the spec's flows with plain default styling.

## Requirements Inventory

### Functional Requirements

Each requirement names the spec capability it comes from.

FR1: A customer uploads a PDF of at most 10 MB and sees each page's progress through the pipeline as a per-page status badge. (CAP-1, AD-3)
FR2: Personal identifiers (person names, addresses, phone numbers, email addresses, identity and policy numbers) are redacted from the uploaded document before any AI stage reads it; dates, ages and medical terms are kept; every later stage and screen uses only the redacted copy. (CAP-12, AD-21)
FR3: If redaction fails or passes its deadline, the case ends as failed, the original is never used instead, and the customer is told to upload again. (CAP-12, AD-21)
FR4: Each page is classified into a page type, as medical or non-medical, with a confidence and a one-line reason, before any extraction. (CAP-2, AD-13)
FR5: Pages are routed by the gate rules: medical at 90% or more goes to extraction; non-medical at 90% or more asks the customer to discard or keep; anything under 90% goes to the underwriter's triage queue. (CAP-2, flows.md, AD-7)
FR6: The customer prompt names the predicted type and confidence, and offers discard (page removed) or keep (page sent to the triage queue). (CAP-3, flows.md)
FR7: An underwriter sees the triage queue with thumbnail, predicted type, confidence and reason for each page, and accepts a page into extraction or denies it. (CAP-3, AD-10)
FR8: Medical facts are extracted from accepted pages, each with its page number and verbatim quote. (CAP-4, AD-14)
FR9: Every fact's quote is checked in code against the page text; a quote not found is flagged and never shown as verified. (CAP-4, AD-14)
FR10: Each extracted fact retrieves matching rules from the synthetic underwriting manual; results carry `rule_id`s. (CAP-5, AD-11)
FR11: The system suggests a verdict (standard, loaded premium with a loading, decline, or refer to underwriter), with reasons that each cite fact pages and `rule_id`s. (CAP-6, AD-15)
FR12: Low confidence, conflicting rules, an unverified quote on a deciding fact, no matching rule or the agent's step limit yields "refer to underwriter". (CAP-6, flows.md, AD-15)
FR13: The result view shows the redacted PDF beside the findings; selecting a fact's citation jumps to and highlights its line; selecting a reason opens its manual rule; the view states "AI suggestion, not a decision". (CAP-7, AD-10, AD-14)
FR14: Retrieval is compared on a six-row ladder (`r1` to `r6`); a Compare toggle shows two rows' results for the same upload side by side; a scoreboard reports rule recall, verdict accuracy, latency, cost and effort for all six rows and names a winner. (CAP-8, bake-offs.md, AD-11, AD-17)
FR15: Classification is compared between an LLM classifier and an Azure AI Document Intelligence custom classifier; a scoreboard reports accuracy, calibration, queue rate and cost per page and names a winner. (CAP-9, bake-offs.md, AD-13, AD-17)
FR16: Every redaction, classification, customer decision, accept, deny, extraction and verdict is recorded with actor and time, and for any case the audit trail reconstructs who or what did each step and when. (CAP-10, AD-8)
FR17: The verdict agent's log of searches and rule reads can be queried by run, and by case with tool and rule filters, from the audit trail and through the API. (CAP-10, AD-15)
FR18: The synthetic manual (about 200 pages from a rule table of about 40 impairments), about 20 case PDFs with expected facts, rules, verdicts and planted identifiers, the scored page set and a separate classifier training set exist with known right answers. (CAP-11, synthetic-data.md, AD-12, AD-13, AD-17)
FR19: A role switcher lets the person using the screen act as customer or underwriter; each screen and action is available only to its role. (SPEC constraint, AD-9)

### NonFunctional Requirements

NFR1: Synthetic data only; no real personal or health data enters any environment. (SPEC constraint, security.md rule 1)
NFR2: The AI never issues a final decision; keep, discard, accept and deny can only come from a human role. (SPEC constraint, AD-10)
NFR3: Every displayed fact and reason traces to a document page or a manual `rule_id`; uncited claims are not shown as findings. (SPEC constraint, AD-15)
NFR4: Bake-off fairness: contenders share inputs, chunks, embedding vectors, the embedding model and the LLM; only the component under test differs. (SPEC constraint, AD-11, AD-12, AD-16)
NFR5: Hosted on Azure under the org standards in `docs/standards/`, with the recorded accepted exceptions. (SPEC constraint)
NFR6: Demo-ready by the weekend of 10 October 2026; scope cuts favour the end-to-end demo path over breadth. (SPEC constraint)
NFR7: Retrieval design and evaluation must hold up to scrutiny by a RAG expert: recall is measured per expected fact with a fixed query, separately from end-to-end verdict accuracy. (SPEC constraint, AD-17)
NFR8: No sign-in; the demo is open with two demo roles. Accepted risk: anyone with the URL can upload and spend model tokens, bounded by budget alerts, low model deployment capacity and compute ceilings. (AD-9)
NFR9: Managed identity only for every Azure service; key and password access disabled; no secrets in code, images or configuration. (azure.md rules 7-10, AD-18)
NFR10: One environment (`demo`) in West US 3; only `web` is reachable from the internet. (AD-18)
NFR11: Logs and traces carry ids, codes and timings, never page text, quotes or fact values; one trace follows each request across services. (security.md rules 31, 33; spine conventions)
NFR12: Audit and agent-step tables are append-only. (security.md rule 32, AD-8, AD-15)
NFR13: Every stage command is idempotent and finishes or fails within 180 seconds. (AD-6)
NFR14: Coverage thresholds of 80% for services and 60% for the SPA; AI behaviour is checked by the eval set, not coverage. (coding-style.md rule 25, spine conventions)
NFR15: Model deployments are pinned to an exact version with auto-upgrade off and the default content filter. (azure.md rules 24-25, AD-16)

### Additional Requirements

From the architecture spine. No starter template is specified; the first epic starts from the spine's source tree.

- The system is exactly seven services (`web`, `intake`, `classification`, `extraction`, `retrieval`, `verdict`, `workflow`), each one image, one Container App, one managed identity and one Dapr app id. (AD-1)
- `packages/contracts/` is built and frozen first: payload models for every operation in the spine's Operations table, the audit record, enums, error codes, the `rule_id` patterns, the `page_type` mapping, the eval query builder and text normalisation. (AD-20)
- Calls between services go through Dapr service invocation over HTTP with `httpx`; no Dapr SDK. (AD-3)
- One PostgreSQL database with one schema and one database role per service; one Alembic environment per service; migrations run from the pipeline. (AD-4, conventions)
- The case lifecycle runs on Azure Durable Task Scheduler in `workflow`; human waits are external events. (AD-5)
- Stage commands block, insert a `running` key row first, and return 409 `in_progress` on a repeat in flight. (AD-6)
- Terraform has two stacks, `foundation` then `app`, with Azure Verified Modules, GitHub Actions with OIDC, and a human-started deploy. (AD-18, terraform.md)
- Azure resources: Container Apps environment and registry, PostgreSQL Flexible Server with the `vector` extension, Storage account with four containers, Foundry account with one chat and one embedding deployment, Azure AI Search (Basic), Document Intelligence, Azure AI Language (single-service), Durable Task Scheduler, Log Analytics, Application Insights, budget and alerts. (AD-18, spine deployment section)
- Two one-off Container Apps jobs: manual ingestion and classifier training. (AD-2, AD-12, AD-13)
- Local development runs all seven services with the Dapr CLI, PostgreSQL and the scheduler emulator in containers, against the demo environment's AI services.
- The eval runner in `evals/` drives the deployed pipeline through `web` and writes the three scoreboard files. (AD-17)
- OpenTelemetry in every service; model and database calls need their own spans. (conventions)
- Row `r6` uses Azure AI Search's preview API and the pre-release `azure-search-documents` 12.1.0b2. (AD-11)
- Build order: contracts and the `foundation` stack; then the demo path with `r3` and the `llm` classifier; then the bake-offs. Cuts come from the end of the bake-offs first. (spine build order)

### UX Design Requirements

None. No UX design exists and none is planned before the demo (Darrel, 2026-10-06). Screens follow `flows.md`. The one fixed piece of wording is the label "AI suggestion, not a decision" (AD-10).

### FR Coverage Map

FR1: Epic 1 - Upload and per-page progress
FR2: Epic 1 - PII redaction before any AI stage
FR3: Epic 1 - Redaction failure ends the case
FR4: Epic 1 - Page classification with confidence and reason
FR5: Epic 1 - Gate routing at the 90% threshold
FR6: Epic 1 - Customer keep or discard prompt
FR7: Epic 1 - Underwriter triage queue
FR8: Epic 2 - Fact extraction with page and quote
FR9: Epic 2 - Quote check in code
FR10: Epic 2 - Rule retrieval per fact (row r3)
FR11: Epic 2 - Suggested verdict with cited reasons
FR12: Epic 2 - Rules that force "refer to underwriter"
FR13: Epic 2 - Auditable result view
FR14: Epic 3 - Retrieval bake-off, Compare toggle and scoreboard
FR15: Epic 4 - Classifier bake-off and scoreboard
FR16: Epic 1 (redaction, classification, human decisions) and Epic 2 (extraction, verdict) - Audit trail
FR17: Epic 2 - Queryable agent log
FR18: Epic 1 (first case PDFs with planted identifiers and junk pages), Epic 2 (manual and rule table), Epic 3 (all 20 cases and answer key), Epic 4 (scored page set and training set) - Synthetic data
FR19: Epic 1 - Role switcher

## Epic List

### Epic 1: A customer uploads a document and it is redacted, gated and triaged
A customer uploads a PDF and watches each page progress. Personal identifiers are redacted first. Each page is classified, and the gate routes it on to extraction, back to the customer to keep or discard, or to the underwriter's triage queue to accept or deny. Every step is in the audit trail. Carries the shared contracts package, the Azure foundation, the deploy pipeline and the seven service skeletons. LLM classifier only.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6, FR7, FR16 (part), FR18 (part), FR19

### Epic 2: An underwriter gets a cited, auditable suggested verdict
Facts are extracted from accepted pages with checked quotes. The verdict agent searches the manual and suggests a verdict with cited reasons. The underwriter reads it beside the redacted PDF, follows every citation, and can query the agent's log. Retrieval row `r3` only. With Epics 1 and 2 the end-to-end demo path works.
**FRs covered:** FR8, FR9, FR10, FR11, FR12, FR13, FR16 (part), FR17, FR18 (part)

### Epic 3: The retrieval comparison names a winner on measured numbers
The remaining ladder rows are built, the bake-off runner scores all six on the full case set, the scoreboard shows the results, and the Compare toggle shows two rows side by side.
**FRs covered:** FR14, FR18 (part)

### Epic 4: The classifier comparison names a winner on measured numbers
The Document Intelligence classifier is trained and added behind the same interface, the runner scores both contenders on the labelled page set, and the scoreboard shows the results.
**FRs covered:** FR15, FR18 (part)

Dependencies: Epic 2 builds on Epic 1; Epics 3 and 4 build on Epic 2 and are independent of each other. Cut order if time runs short: Epic 4, then rows `r6` and `r4` of Epic 3.

## Epic 1: A customer uploads a document and it is redacted, gated and triaged

A customer uploads a PDF and watches each page progress. Personal identifiers are redacted first. Each page is classified, and the gate routes it on to extraction, back to the customer to keep or discard, or to the underwriter's triage queue to accept or deny. Every step is in the audit trail.

### Story 1.1: Shared contracts package

As a developer building any of the seven services,
I want one frozen package of payload models, enums, error codes and shared functions,
So that every service speaks the same shapes without inventing its own.

**Acceptance Criteria:**

**Given** the spine's Operations table and conventions
**When** `packages/contracts/` is built
**Then** it holds a pydantic model for every request and response in that table, the audit record, the enums, the error shape and code catalogue, the `rule_id` patterns, the `page_type` to `is_medical` mapping, the eval query builder and the text normalisation function (AD-20)
**And** the package imports no web framework, ORM or HTTP library

**Given** a sample payload for each operation
**When** it is parsed and serialised again
**Then** field names are `snake_case` and the round trip is lossless
**And** a payload with an unknown enum value or a malformed `rule_id` is rejected

**Given** two strings that differ only in whitespace and letter case
**When** the normalisation function is applied to both
**Then** the results are equal, and mask tokens such as `[Person]` are preserved (AD-14)

### Story 1.2: Azure foundation for the demo environment

As the project owner,
I want the `demo` environment's shared Azure resources created from code,
So that every service has somewhere to run and nothing is set up by hand.

**Acceptance Criteria:**

**Given** the bootstrap script has been run once
**When** the `foundation` Terraform stack is planned, approved and applied
**Then** West US 3 holds the resources the spine assigns to that stack: Log Analytics, Application Insights, container registry, Container Apps environment, PostgreSQL Flexible Server with `vector` allow-listed, Storage account with containers `originals`, `cases`, `manual` and `classifier-training`, Foundry account with one chat and one embedding deployment, Azure AI Search, Document Intelligence, Azure AI Language, Durable Task Scheduler with a task hub, seven runtime identities, and the budget with its alerts (AD-18)
**And** every name comes from one naming block using workload `aiuw`, environment `demo` and region code `wus3`, and every resource carries the six required tags

**Given** any data or AI resource in the stack
**When** its access settings are inspected
**Then** key and password access is disabled and only managed identity is accepted (NFR9)
**And** each model deployment is pinned to an exact version with auto-upgrade off (NFR15)

**Given** a pull request that changes `infra/`
**When** CI runs
**Then** it runs format check, validate and plan for the stack and posts the plan on the pull request, signed in to Azure through OIDC

### Story 1.3: Deployable web entry with the role switcher

As a customer or underwriter,
I want to open the application and choose which role I am acting as,
So that I see the screens and actions for that role.

**Acceptance Criteria:**

**Given** the `app` stack and the deploy workflow
**When** the owner starts a deploy from `main`
**Then** the `web` Container App serves the built SPA and `/api/*` from one origin with external ingress, and CORS is off (AD-19)
**And** the readiness, liveness and startup probes pass

**Given** the SPA is open
**When** the user picks Customer or Underwriter in the role switcher
**Then** every API call carries `X-Demo-Role` with that value, and the navigation shows only that role's screens (FR19)

**Given** an API call with a missing or unknown `X-Demo-Role`
**When** `web` receives it
**Then** it returns 400 in the standard error shape (AD-9)

**Given** a developer's machine with the Dapr CLI and Docker
**When** they run the documented local start command
**Then** `web` and the SPA run locally with PostgreSQL and the scheduler emulator in containers

### Story 1.4: First synthetic case documents

As a demo presenter,
I want a small set of synthetic case PDFs with known contents,
So that upload, redaction and the gate can be shown and tested with right answers.

**Acceptance Criteria:**

**Given** the synthetic data rules
**When** the first case set is generated into `data/cases/`
**Then** it holds at least three case PDFs of a few pages each, containing attending physician statements, lab reports and application forms, plus non-medical pages (invoice, payslip, utility bill) and edge pages (blank, rotated) (FR18)
**And** each case carries a planted name, address, phone number, email address, identity number and policy number

**Given** a generated case
**When** its answer-key entry in `data/answer-key/` is read
**Then** it lists every planted identifier and the expected label of every page
**And** no real person's data appears anywhere (NFR1)

### Story 1.5: Upload a document and see the case

As a customer,
I want to upload a PDF and see that my case was received,
So that I know the document is being processed.

**Acceptance Criteria:**

**Given** the customer role and a PDF of at most 10 MB
**When** the customer uploads it
**Then** `web` passes it to `intake`, which stores the original in the `originals` container, creates the case and document records in its own schema and returns `case_id` and `document_id` (AD-2, AD-21)
**And** the upload screen shows the case with status `running`

**Given** a file over 10 MB or one that is not a PDF
**When** the customer uploads it
**Then** the upload is rejected with a plain message and nothing is stored

**Given** a stored original
**When** any API is asked for it
**Then** no route returns the original file (AD-21)

### Story 1.6: Case lifecycle and audit trail

As an underwriter,
I want every case to follow a fixed lifecycle that records each step,
So that I can trust the order of events and see who or what did each one.

**Acceptance Criteria:**

**Given** a case created by upload
**When** `web` asks `workflow` to start it
**Then** `workflow` starts one orchestration on Durable Task Scheduler with the `case_id` as its instance id, and starting the same case again changes nothing (AD-5, AD-6)

**Given** a stage result or human decision reaching `workflow`
**When** it is recorded
**Then** one row is appended to `workflow.audit_event` with actor kind, actor, action, time, case, page, reference, detail, trace id and eval run id, in the same transaction as the status change (AD-8)
**And** a retried activity adds no second row, and no code path updates or deletes a row (NFR12)

**Given** a case
**When** `web` reads its progress or its audit trail
**Then** it gets the case status with the page list, and the events in time order

### Story 1.7: PII redaction before anything else reads the document

As a customer,
I want my personal identifiers removed before any AI reads my document,
So that names, addresses and identity numbers are never sent to a model or shown on a screen.

**Acceptance Criteria:**

**Given** a started case
**When** `workflow` commands redaction as its first stage
**Then** `intake` calls Azure AI Language's document redaction with the entity mask, and the redacted PDF lands in the `cases` container under the case's prefix (FR2, AD-21)
**And** `intake` splits the redacted PDF into pages and stores each page's text, word boxes and thumbnail

**Given** a synthetic case with planted identifiers
**When** redaction has finished
**Then** none of the planted identifiers appears in any stored page text, while dates, ages and medical terms are still present

**Given** redaction is still running
**When** the page list or the document file is requested
**Then** the page list is empty and the file returns 409 with code `not_redacted`

**Given** redaction fails or passes its 180-second deadline
**When** `workflow` receives the failure
**Then** no pages are created, the case ends as `failed` with a `stage.failed` audit event, and the customer sees a message asking them to upload again (FR3)

**Given** a successful redaction
**When** the audit trail is read
**Then** it holds a `document.redacted` event with a count per category and no redacted values (FR16)

### Story 1.8: Page classification with a confidence and a reason

As an underwriter,
I want each page classified as medical or not, with a confidence and a reason,
So that non-medical and doubtful pages can be caught before extraction.

**Acceptance Criteria:**

**Given** a redacted page
**When** `workflow` commands classification with the `llm` contender
**Then** `classification` reads the page image and text from `intake` and stores `page_type`, `is_medical`, `confidence` between 0 and 1, `reason` and `contender` (FR4, AD-13)
**And** the confidence is the agreement rate across repeated model runs

**Given** the same classify command sent twice
**When** the second arrives after the first finished
**Then** the stored result is returned and no new model call is made
**And** a repeat while the first is still running returns 409 with code `in_progress` (AD-6)

**Given** a model response that does not match the contract
**When** it is parsed
**Then** the stage stores a `failed` result and nothing invalid is passed on

**Given** a classified page
**When** the audit trail is read
**Then** it holds a `page.classified` event naming the service and model deployment as actor

### Story 1.9: Gate routing and per-page progress

As a customer,
I want to see where each page of my document is in the pipeline,
So that I know what was accepted and what needs my attention.

**Acceptance Criteria:**

**Given** a classified page
**When** `workflow` applies the gate
**Then** medical at 0.90 or more goes on to extraction, non-medical at 0.90 or more becomes `awaiting_customer`, and anything under 0.90 becomes `awaiting_triage` (FR5, AD-7)
**And** the threshold is a setting that lives only in `workflow`

**Given** a case in progress
**When** the customer watches the upload screen
**Then** each page shows a status badge that updates by polling, without a page reload (FR1, AD-19)

**Given** a page at exactly 0.90 confidence
**When** it is routed
**Then** it is treated as 0.90 or more

### Story 1.10: Customer keeps or discards a non-medical page

As a customer,
I want to be asked about pages that look non-medical,
So that I can remove a page I uploaded by mistake or insist it is relevant.

**Acceptance Criteria:**

**Given** a page in `awaiting_customer`
**When** the customer opens the case
**Then** a prompt names the predicted type and confidence, for example "This looks like a utility bill (96%). Discard or keep?" (FR6)

**Given** that prompt
**When** the customer chooses discard
**Then** the page becomes `discarded` and a `page.discarded` audit event names the customer role as actor

**Given** that prompt
**When** the customer chooses keep
**Then** the page becomes `awaiting_triage` and a `page.kept` audit event is recorded

**Given** a decision for a page that is not awaiting one, or a decision whose actor is not a human role
**When** `workflow` receives it
**Then** it is rejected, with 409 for the wrong state (AD-10)

### Story 1.11: Underwriter triage queue

As an underwriter,
I want a queue of the pages the gate could not settle,
So that I can accept the relevant ones into extraction and deny the rest.

**Acceptance Criteria:**

**Given** the underwriter role
**When** the triage queue is opened
**Then** it lists every page in `awaiting_triage` across cases, each with thumbnail, predicted type, confidence and reason (FR7)
**And** pages belonging to an eval run are not listed

**Given** a page in the queue
**When** the underwriter accepts it
**Then** the page moves on to extraction and a `page.accepted` audit event names the underwriter role as actor

**Given** a page in the queue
**When** the underwriter denies it
**Then** the page becomes `denied` and a `page.denied` audit event is recorded

**Given** the customer role
**When** the triage queue route is called
**Then** the request is refused

### Story 1.12: Audit trail view for a case

As an underwriter,
I want to read a case's audit trail on screen,
So that I can reconstruct who or what did each step and when.

**Acceptance Criteria:**

**Given** a case that has been redacted, classified and decided on
**When** the underwriter opens its audit trail
**Then** every redaction, classification and human decision appears in time order with actor, action, page and time (FR16)
**And** AI actors show the service and model deployment, and human actors show the demo role

**Given** the audit trail of a case
**When** it is compared with the case's page statuses
**Then** every status change has a matching event

## Epic 2: An underwriter gets a cited, auditable suggested verdict

Facts are extracted from accepted pages with checked quotes. The verdict agent searches the manual and suggests a verdict with cited reasons. The underwriter reads it beside the redacted PDF, follows every citation, and can query the agent's log. Retrieval row `r3` only.

### Story 2.1: Synthetic underwriting manual and rule table

As a demo presenter,
I want an original synthetic underwriting manual generated from a rule table,
So that retrieval has a realistic document to search and every rule has a known right answer.

**Acceptance Criteria:**

**Given** the synthetic data rules
**When** the manual is generated into `data/manual/`
**Then** it is a PDF of about 200 pages with prose, tables and cross-references, covering about 40 impairments in the structure impairment, key questions, probable rating (FR18)
**And** all content is original; no text is copied from any carrier's or reinsurer's manual

**Given** the rule table in `data/answer-key/`
**When** a rule is read
**Then** it has a `rule_id` matching `UW-[A-Z]{2,4}-[0-9]{3}`, an impairment, a threshold, a debit percentage or decline, and the public source its threshold is grounded in

**Given** any rule in the table
**When** the manual is searched for its definition
**Then** exactly one place prints `Rule <rule_id>:` for it, and other mentions are cross-references (AD-12)

### Story 2.2: Manual ingested into pgvector as smart chunks

As an underwriter,
I want the manual indexed one rule per chunk,
So that a search returns the rule itself and not an arbitrary slice of a page.

**Acceptance Criteria:**

**Given** the manual PDF in the `manual` container
**When** the ingestion job runs
**Then** it parses the PDF with Document Intelligence's layout model and writes `smart` chunks into the `retrieval` schema, each holding exactly one rule, its parent section id, an LLM-written context line, the manual page and a 3,072-dimension embedding (AD-12)
**And** each chunk's `rule_ids` are only the rules defined in its text

**Given** the job has already run
**When** it runs again
**Then** the chunk set is unchanged and `chunk_id`s are the same

**Given** the rule table
**When** the ingested chunks are counted
**Then** every `rule_id` in the table is defined by exactly one `smart` chunk
**And** the rule table itself is in no index and no service can read it

### Story 2.3: Search the manual for rules

As an underwriter,
I want a medical fact to bring back the rules that apply to it,
So that a verdict can rest on the manual's own wording.

**Acceptance Criteria:**

**Given** the ingested manual
**When** a search is sent with a query, `retriever_config` `r3` and `top_k` 5
**Then** `retrieval` runs vector search and PostgreSQL full-text search, fuses them with reciprocal rank fusion, and returns `retriever_config`, `latency_ms` and up to 5 items, each with `chunk_id`, `rule_ids`, `rank`, `score`, `text`, `manual_page` and `impairment` (FR10, AD-11)

**Given** a query naming a specific impairment and threshold from the rule table
**When** it is searched with `r3`
**Then** the expected `rule_id` is in the top 5

**Given** a `rule_id`
**When** the rule read operation is called
**Then** it returns that rule's chunk text, manual page and impairment, and an unknown `rule_id` returns 404

**Given** an unknown `retriever_config`
**When** a search is sent
**Then** it is rejected with a plain error

### Story 2.4: Fact extraction with checked quotes

As an underwriter,
I want each medical fact shown with its page and the exact words it came from,
So that I can verify it against the document in seconds.

**Acceptance Criteria:**

**Given** a page that passed the gate or was accepted in triage
**When** `workflow` commands extraction
**Then** `extraction` reads the page text from `intake` and stores facts, each with `fact_id`, `page_id`, `page_number`, `quote` and `quote_verified` (FR8, AD-14)

**Given** an extracted fact
**When** its normalised quote is found in the normalised page text
**Then** it is marked verified and carries `quote_start` and `quote_end` offsets
**And** when the quote is not found, the fact is stored and flagged as unverified, never dropped and never shown as verified (FR9)

**Given** a page containing mask tokens such as `[Person]`
**When** facts are extracted
**Then** no masked value is extracted as a fact

**Given** a finished extraction
**When** the audit trail is read
**Then** it holds a `facts.extracted` event for the page (FR16)
**And** repeating the command returns the stored facts without a new model call

### Story 2.5: Suggested verdict with cited reasons

As an underwriter,
I want a suggested verdict where every reason points to a fact and a rule,
So that I can audit the suggestion instead of taking it on trust.

**Acceptance Criteria:**

**Given** a case whose pages are all in a final status
**When** `workflow` commands a verdict run for `retriever_config` `r3`
**Then** the agent in `verdict` uses only `list_facts`, `search_rules` and `read_rule`, and stores a run with `verdict`, `loading_pct`, `confidence`, `reasons` and `system_reasons` (FR11, AD-15)
**And** every reason carries a `rule_id`, one or more `fact_ids`, an `effect` and a `debit_pct`

**Given** a reason that cites a fact or rule the agent did not see in that run
**When** the run is stored
**Then** that reason is not stored (NFR3)

**Given** `read_rule` called with a `rule_id`
**When** that rule was neither returned by a search nor cross-referenced by a rule already read in the same run
**Then** the call is refused

**Given** a finished run
**When** the audit trail and step log are read
**Then** the trail holds a `verdict.suggested` event referencing the run, and `verdict.agent_step` holds one append-only row per tool call (FR16)

**Given** a case with pages still awaiting a human decision
**When** a verdict run is requested
**Then** it is refused with 409

### Story 2.6: Rules that send a case to the underwriter

As an underwriter,
I want doubtful cases referred to me instead of guessed at,
So that the AI never presents a weak suggestion as a confident one.

**Acceptance Criteria:**

**Given** a verdict run
**When** no rule matches, rules conflict, a deciding fact has an unverified quote, the agent hits its step limit, or its confidence is under the configured floor (default 0.70)
**Then** the stored verdict is `refer` with the matching system reason code (FR12)
**And** this is decided in `verdict`'s domain code, not by the prompt

**Given** a run whose cited rules carry debits and none says decline
**When** the verdict is stored
**Then** it is `loaded` and `loading_pct` is the sum of the cited `debit_pct` values

**Given** a run where a cited rule says decline
**When** the verdict is stored
**Then** it is `decline`

**Given** a run where no rule triggers a debit and no system reason applies
**When** the verdict is stored
**Then** it is `standard`

### Story 2.7: Result view with clickable citations

As an underwriter,
I want the document beside the findings with every citation one click away,
So that I can check the suggestion against its sources without searching.

**Acceptance Criteria:**

**Given** a case with a verdict run
**When** the underwriter opens the result view
**Then** the redacted PDF is on the left and, on the right, the verdict, then the reasons, then the facts (FR13)
**And** the view shows the exact label "AI suggestion, not a decision"

**Given** a verified fact
**When** its citation is selected
**Then** the PDF scrolls to the page and the quote is highlighted using the word boxes `intake` returns for the fact's offsets (AD-14)

**Given** an unverified fact
**When** it is shown
**Then** it is visibly flagged and has no highlight

**Given** a reason
**When** its `rule_id` is selected
**Then** the manual rule's text, impairment and manual page open beside it

### Story 2.8: Query the agent's log

As an underwriter,
I want to see exactly which searches and rules led to a verdict,
So that I can explain or challenge it.

**Acceptance Criteria:**

**Given** a verdict run
**When** its steps are requested by run
**Then** every tool call is returned in order with step number, tool, arguments, fact, rules returned or read, latency and time (FR17)

**Given** a case
**When** its agent steps are requested with a `tool` or `rule_id` filter
**Then** only matching steps are returned

**Given** the audit trail view
**When** the underwriter selects a "verdict suggested" event
**Then** the run's steps open as a drill-down

## Epic 3: The retrieval comparison names a winner on measured numbers

The remaining ladder rows are built, the bake-off runner scores all six on the full case set, the scoreboard shows the results, and the Compare toggle shows two rows side by side. Stories are ordered so the last two can be cut.

### Story 3.1: Full synthetic case set with its answer key

As a reviewer of the retrieval design,
I want about 20 cases with known facts, rules and verdicts,
So that every row of the ladder is scored against the same right answers.

**Acceptance Criteria:**

**Given** the synthetic data rules
**When** the case set is completed in `data/cases/`
**Then** it holds about 20 case PDFs covering all four verdicts, each with planted identifiers (FR18)

**Given** a case
**When** its answer-key entry is read
**Then** it lists the expected facts with pages, the expected `rule_id`s, the expected verdict, the planted identifiers and each page's expected label

### Story 3.2: Baseline rows r1 and r2

As a reviewer of the retrieval design,
I want a fixed-chunk baseline and a smart-chunk vector-only row,
So that the gain from chunking alone can be measured.

**Acceptance Criteria:**

**Given** the manual
**When** the ingestion job runs for the `fixed` chunk set
**Then** fixed-size chunks are stored with the same embedding model, and each carries the `rule_ids` defined inside its span

**Given** a search with `r1` or `r2`
**When** it runs
**Then** `r1` searches `fixed` chunks by vector only, `r2` searches `smart` chunks by vector only, and both return the same result shape as `r3` (FR14, AD-11)

**Given** `read_rule` in a verdict run on `r1`
**When** a rule is read
**Then** its text comes from the `fixed` chunk set

### Story 3.3: Row r5 on Azure AI Search

As a reviewer of the retrieval design,
I want Azure AI Search queried on the same chunks and vectors,
So that the store is the only thing that changes.

**Acceptance Criteria:**

**Given** the `smart` chunks and their vectors
**When** the ingestion job loads Azure AI Search
**Then** the index holds the same chunk records, `chunk_id`s and vectors as pgvector, searched with exact nearest-neighbour (NFR4, AD-12)

**Given** a search with `r5`
**When** it runs
**Then** it uses hybrid search with the semantic ranker and returns the common result shape

**Given** the two stores
**When** their chunk counts and `chunk_id`s are compared
**Then** they are identical

### Story 3.4: Bake-off runner scores the rows

As a reviewer of the retrieval design,
I want recall and verdict accuracy measured separately by one runner,
So that retriever quality is not confused with the agent's query writing.

**Acceptance Criteria:**

**Given** the case set and the answer key
**When** the runner measures rule recall
**Then** it sends one eval search per expected fact per available `retriever_config`, with the query built by the contracts query builder, and counts a hit when the expected `rule_id` is in the top 5 (NFR7, AD-17)

**Given** the case set
**When** the runner measures verdict accuracy
**Then** each case is uploaded once, started with every available `retriever_config` and an `eval_run_id`, and the runner answers the human waits from the answer key's page labels

**Given** a finished run
**When** the results are written
**Then** `data/scoreboards/retrieval.json` holds rule recall, verdict accuracy, latency, cost and effort per row and the winner by verdict accuracy, then rule recall, then latency
**And** cost and effort come from `evals/static-metrics.yaml`, each with its source

**Given** the same run
**When** the redaction check runs
**Then** the run fails if any planted identifier appears in any stored page text, and `data/scoreboards/redaction.json` reports the result (FR2)

**Given** cases started by the runner
**When** the triage queue is opened
**Then** none of them appears

### Story 3.5: Retrieval scoreboard

As a reviewer of the retrieval design,
I want the measured numbers for every row on one screen,
So that I can see which approach won and by how much.

**Acceptance Criteria:**

**Given** `data/scoreboards/retrieval.json`
**When** the scoreboard is opened
**Then** it shows one line per row with store, chunk set, method, rule recall, verdict accuracy, latency, cost and effort, and marks the winner (FR14)

**Given** a row that has not been scored
**When** the scoreboard is shown
**Then** that row says "not measured" and shows no numbers

### Story 3.6: Compare two rows on one case

As an underwriter,
I want two retrieval rows' results for the same upload side by side,
So that I can see where they differ on a real case.

**Acceptance Criteria:**

**Given** a finished case and the Compare toggle
**When** the underwriter turns it on
**Then** `web` asks `workflow` for a verdict run with the second configured row, which runs as its own orchestration on the same extracted facts (FR14, AD-5, AD-11)

**Given** two verdict runs for a case
**When** the Compare view is shown
**Then** the two result panes sit side by side and differences in verdict, reasons and retrieved rules are highlighted

**Given** the Compare pair setting
**When** it is read
**Then** it defaults to `r4` and `r5`, and falls back to `r3` and `r5` while `r4` is not built

### Story 3.7: Row r4 with a reranker

As a reviewer of the retrieval design,
I want a reranker added to the pgvector hybrid row,
So that reranking's contribution is measured on its own.

**Acceptance Criteria:**

**Given** a search with `r4`
**When** it runs
**Then** it takes the `r3` candidates and reranks them, with Cohere Rerank on Foundry if deployable in West US 3 and otherwise an LLM reranker, and returns the common result shape

**Given** `r4` exists
**When** the runner runs again
**Then** the scoreboard shows `r4` and the reranker used

### Story 3.8: Row r6 with agentic retrieval

As a reviewer of the retrieval design,
I want Azure AI Search's own agentic retrieval measured against the same need,
So that a managed agentic pipeline is compared with the one built here.

**Acceptance Criteria:**

**Given** the Azure AI Search index
**When** a knowledge base is created over it with LLM query planning on the preview API
**Then** a search with `r6` answers through the same search operation and result shape (AD-11)
**And** the search service's own identity is the one granted access to the Foundry chat deployment

**Given** a verdict run on `r6`
**When** it runs
**Then** the agent's own search loop is off, one retrieval request is made per fact, and the verdict is composed from what returns (AD-15)

**Given** `r6` exists
**When** the runner runs again
**Then** the scoreboard shows all six rows

## Epic 4: The classifier comparison names a winner on measured numbers

The Document Intelligence classifier is trained and added behind the same interface, the runner scores both contenders on the labelled page set, and the scoreboard shows the results.

### Story 4.1: Scored page set and separate training set

As a reviewer of the classification design,
I want labelled pages for scoring and different pages for training,
So that the trained classifier is never scored on pages it has seen.

**Acceptance Criteria:**

**Given** the synthetic data rules
**When** the scored page set is assembled
**Then** it holds medical pages from the case set, non-medical pages (invoice, payslip, passport, recipe, utility bill) and edge pages (blank, rotated, handwritten doctor's note, a mixed file), each with its expected label in the answer key (FR18)

**Given** `data/classifier-training/`
**When** it is compared with the scored set
**Then** the two share no page, and the training set has at least five pages per `page_type`

### Story 4.2: Document Intelligence classifier trained and available

As a reviewer of the classification design,
I want a trained Document Intelligence classifier behind the same interface as the LLM one,
So that either can serve the pipeline and both can be compared.

**Acceptance Criteria:**

**Given** the training set
**When** the training job runs
**Then** the training pages pass through the same redaction as case pages, are placed in the `classifier-training` container, and a custom classification model is trained from them (AD-13)

**Given** a case started with `classifier_contender` `doc-intelligence`
**When** a page is classified
**Then** the page is sent as a one-page document and the stored result has the same fields as the LLM contender's, with `contender` set (FR15)

**Given** one page classified by both contenders
**When** the results are read
**Then** two results exist for the page, one per contender, and the gate used the one the case was started with (AD-7)

### Story 4.3: Runner scores both classifiers and the scoreboard shows the winner

As a reviewer of the classification design,
I want accuracy, calibration, queue rate and cost for both classifiers,
So that the choice between them rests on measured numbers.

**Acceptance Criteria:**

**Given** the scored page set
**When** the runner scores a contender
**Then** it uploads the set started with that contender and `stop_after` set to `gate`, so no extraction runs (AD-17)

**Given** a finished run
**When** results are written
**Then** `data/scoreboards/classification.json` holds accuracy, calibration (of pages scored 0.90 or more, the share labelled correctly), queue rate and cost per page for each contender
**And** the winner is the more accurate contender among those with calibration of at least 0.90, then the one with the lower queue rate

**Given** that file
**When** the classifier scoreboard is opened
**Then** it shows both contenders' numbers and marks the winner (FR15)
