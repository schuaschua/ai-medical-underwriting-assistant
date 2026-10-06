# Epic 1 Context: A customer uploads a document and it is redacted, gated and triaged

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

A customer uploads a synthetic PDF and watches each page progress. Personal identifiers are redacted before anything else reads the document; each page is then classified (LLM classifier only) and the gate sends it on to extraction, back to the customer to keep or discard, or to the underwriter's triage queue to accept or deny. Every step lands in one audit trail. This epic also lays the base everything else stands on: the frozen shared contracts package, the Azure foundation, the deploy pipeline and the seven service skeletons. It is the first half of the end-to-end demo path (demo-ready by the weekend of 10 October 2026; cuts favour that path over breadth). There is no UX design and no architecture principles document; both were skipped on purpose.

## Stories

- Story 1.1: Shared contracts package
- Story 1.2: Azure foundation for the demo environment
- Story 1.3: Deployable web entry with the role switcher
- Story 1.4: First synthetic case documents
- Story 1.5: Upload a document and see the case
- Story 1.6: Case lifecycle and audit trail
- Story 1.7: PII redaction before anything else reads the document
- Story 1.8: Page classification with a confidence and a reason
- Story 1.9: Gate routing and per-page progress
- Story 1.10: Customer keeps or discards a non-medical page
- Story 1.11: Underwriter triage queue
- Story 1.12: Audit trail view for a case

## Requirements & Constraints

- **Synthetic data only.** No real personal or health data in any environment, fixture, prompt or test.
- **Upload:** PDF only, at most 10 MB; anything else is rejected and nothing stored. The original is kept but no API ever serves it and no later stage reads it.
- **Redaction first:** person names, addresses, phone numbers, emails, identity and policy numbers are replaced by type tokens such as `[Person]`; dates (including date of birth), ages and medical terms stay. The redacted PDF is the document of record for every later stage and screen. On failure or timeout the case ends `failed`, no pages are created, the original is never used instead, and the customer is told to upload again.
- **Classification:** per page, before extraction; returns page type, medical or not, confidence (0 to 1) and a one-line reason. LLM confidence is the agreement rate across repeated runs.
- **Gate:** medical at 0.90 or more goes to extraction; non-medical at 0.90 or more asks the customer (discard removes it, keep sends it to triage); anything under 0.90 goes to triage (accept sends to extraction, deny removes). Exactly 0.90 counts as "or more".
- **Humans only:** keep, discard, accept and deny can only come from a human role; the AI never decides. Enforced in domain code, not prompts.
- **Audit:** every redaction, classification and human decision is recorded with actor and time; the table is append-only and every page status change has a matching event.
- **No sign-in:** open demo with two roles (customer, underwriter); each screen and action is limited to its role. Accepted exception with the related ownership, row-level-security and rate-limit rules; accepted risk that anyone with the URL can spend tokens.
- **Azure posture:** managed identity only, key and password access off, no secrets anywhere; one environment `demo` in West US 3; only `web` is internet-reachable; HTTPS only; six required tags on every resource; names built in one naming block (workload `aiuw`, region code `wus3`); cheapest workable tiers; resource-group budget with alerts; Log Analytics daily cap; model deployments pinned to an exact version, auto-upgrade off, default content filter.
- **Logs and traces** carry ids, codes and timings, never page text, quotes or identifier values; one trace per request across services.
- **Stage commands** are idempotent and finish or fail within 180 seconds.
- **Quality gates:** every acceptance criterion has a test named for its story; coverage 80% services, 60% SPA; unit tests use fakes (no real Azure, model or network), integration tests use a containerised database; exact version pins and committed lock files; dependency scans in CI; server-side validation of every input; security headers on every response; a custom header on every non-GET call.

## Technical Decisions

- **Seven services, fixed:** `web`, `intake`, `classification`, `extraction`, `retrieval`, `verdict`, `workflow`. Each is one image, one Container App, one user-assigned identity, one Dapr app id. Inside each: `domain/` (pure, no framework imports, strict typing), `adapters/`, `prompts/`, `migrations/`, `tests/`, `settings.py` (one `pydantic-settings` object, env prefix is the app id in upper case).
- **Contracts first:** `packages/contracts/` holds pydantic models for every service operation, the audit record, enums, error catalogue, `rule_id` patterns, the page-type-to-medical mapping, the eval query builder and text normalisation (case- and whitespace-insensitive, keeps mask tokens). It imports no framework, ORM or HTTP library, is the only shared code, and is frozen before any service; a change is one pull request touching every affected service.
- **Only `workflow` sequences.** Stage services change state only on its command and never call the next stage. The one exception here: `web` asks `intake` to create the case, then asks `workflow` to start it. Allowed calls in this epic: `web` to `intake`, `workflow` and `classification` (reads); `workflow` to `intake` and `classification`; `classification` to `intake`.
- **Service calls:** HTTP through the local Dapr sidecar by app id, using `httpx` from one client module per service; no Dapr SDK, state store, pub/sub or Dapr Workflow. JSON bodies except page files and thumbnails. `web` and `intake` sidecars raise the request size limit to 16 MB.
- **Data:** one PostgreSQL database, one schema and one database role per service; no cross-schema grants or foreign keys, only id columns; `web` owns no schema. `intake` owns cases, documents, pages, page text, word boxes, originals (`originals` container) and redacted PDFs plus thumbnails (`cases` container, prefix `<case_id>/`). `classification` owns classifications. `workflow` owns case and page status, human decisions and the audit trail. Alembic per service, migrations run from the pipeline, never at startup; expand-then-contract.
- **Lifecycle:** one Durable Task Scheduler orchestration per case, instance id = `case_id`; orchestrator holds only sequencing and gate routing; each activity is one stage call; human waits are external events, never polling or timers.
- **Idempotency:** a stage inserts its key row as `running` before work; a repeat returns the stored result, or 409 `in_progress` while running; `workflow` retries with backoff. Stage deadline 180 s, activity timeout 200 s. Commands carry ids and small summaries only. Keys: redaction by `case_id`; classification by `case_id` + `page_id` + `contender`; start by `case_id`.
- **Gate lives only in `workflow`'s domain**, threshold a setting defaulting to 0.90. `classification` never routes; the SPA never computes routes.
- **Audit:** `workflow.audit_event` is the only audit table, written in the same transaction as the status change, unique on case, page, action and reference so retries add nothing. Fields: actor kind, actor (demo role, or service plus model deployment; redaction is `intake` plus `azure-ai-language`), action, occurred-at, case, page, reference, detail, trace id, eval run id. Redaction detail is a count per category, never values.
- **Decisions:** one `workflow` operation reachable only from `web`; it stores the decision, writes the audit row and raises the orchestration event. Non-human actor is rejected; wrong page state is 409.
- **Roles:** the SPA sends `X-Demo-Role` on every call; `web` returns 400 for a missing or unknown value, checks each route's role, and passes the role on as the human actor. Internal services trust it.
- **Redaction** is Azure AI Language document PII redaction with the entity mask; categories are a setting. `intake` then splits pages and stores one text per page with a box per word and a PNG thumbnail. Until done, the page list is empty and the document file returns 409 `not_redacted`.
- **Classifier:** one interface with a `contender` field (`llm` here). Page types: `lab_report`, `attending_physician_statement`, `application_form` (medical); `id_document`, `invoice`, `other` (not). One page at a time. Model output is parsed into a contracts model; a failed parse is a `failed` result, never passed on.
- **Model access:** one shared chat and one embedding deployment on one Foundry account, names reaching code only as settings; one gateway module per service that retries 429/5xx up to three times, then raises `model_unavailable`, and opens its own trace span (as does the database adapter).
- **Conventions:** UUIDv7 ids generated by the owner; `snake_case` everywhere with the same field name in database, API and SPA; singular table names; ISO 8601 UTC; confidence as a 0-to-1 float (SPA formats percentages); error shape `{"error": {"code", "message", "trace_id"}}` with no stack traces, SQL or paths.
- **Infrastructure:** Terraform stacks `foundation` then `app` under `infra/demo/`, state in the central backend with Entra auth; Azure Verified Modules first, exact pins, no provisioners; pull requests run format, validate and plan with the plan posted; deploy is a hand-started workflow on `main` signed in by OIDC. For the first build the agent may apply plans and start deploys without per-plan human review. Each app: 0.5 vCPU, 1 GiB, at most 2 replicas; `workflow` held at 1 replica; startup, readiness and liveness probes on all.
- **Local run:** all services through the Dapr CLI multi-app file, with PostgreSQL and the scheduler emulator in containers, against the demo environment's AI services.
- **Open at build time:** the Language redaction API version; whether a synthetic policy number is masked; whether the redaction result file holds found text (if so it belongs with the original); whether a Dapr call cleanly wakes a scaled-to-zero service.

## UX & Interaction Patterns

- No design system; plain default styling. `web` serves the SPA and `/api/*` from one origin, CORS off.
- The SPA reads progress by polling through its one API client module; no WebSockets or server-sent events. Each page shows a status badge that updates without reload.
- The customer prompt names the predicted type and confidence, for example "This looks like a utility bill (96%). Discard or keep?"
- The triage queue lists pages across cases with thumbnail, predicted type, confidence and reason; eval-run pages are hidden.
- The audit view lists events in time order with actor, action, page and time.
- User-facing text sits in one strings module; no business rules in the SPA; never render model output as HTML.

## Cross-Story Dependencies

- 1.1 comes before every service story; 1.2 before anything deployed; 1.3 provides the `app` stack, deploy workflow and local start that later stories extend.
- 1.4 supplies the cases and answer key (planted identifiers, expected page labels) used to test 1.7 to 1.11.
- Chain: 1.5 upload, 1.6 lifecycle and audit, 1.7 redaction, 1.8 classification, 1.9 gate, then 1.10 and 1.11 decisions; 1.12 reads what 1.6 to 1.11 wrote.
- Epic 2 picks up pages that pass the gate or are accepted; page statuses `extracting` and `extracted`, and audit actions for extraction and verdict, already belong in the contracts. Epic 4 adds the second classifier behind the same interface. The eval runner (Epic 3) relies on `eval_run_id` and `stop_after` being accepted at case start.
