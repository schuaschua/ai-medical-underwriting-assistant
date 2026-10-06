# Rubric review — ARCHITECTURE-SPINE.md

- **Reviewed:** `../ARCHITECTURE-SPINE.md` (status: draft, 2026-10-06)
- **Against:** `SPEC.md`, `flows.md`, `bake-offs.md`, `synthetic-data.md`; `docs/standards/*.md` consulted only to check the operational envelope and the exceptions claim
- **Reviewer stance:** independent; rationale gaps are not findings (they live in the decision log)
- **Date:** 2026-10-06

## Verdict

A strong, terse spine whose structure (ownership, Dapr calls, single audit trail, single retrieval and classifier interface) is sound, but it is **not yet safe to hand to epics**: four rules contradict each other on exactly the paths the two bake-offs and the triage flow depend on, and five divergence points the epics will hit on day one are left silent.

Severity scale: **critical** = two epics cannot both follow the spine as written; **high** = epics will very likely diverge or a spec capability cannot be met; **medium** = likely rework or a weak spot under expert scrutiny; **low** = polish.

## Findings at a glance

| # | Severity | Where | Finding |
| --- | --- | --- | --- |
| F1 | critical | AD-6, AD-13, AD-17 | The classifier bake-off cannot run as specified: contender is a deployment setting, the classify command key has no contender, yet the runner must run "once per classifier contender" |
| F2 | critical | AD-10, AD-4 table, ERD | Human decisions are owned by `classification`, but AD-10 forbids any stage API from exposing keep, discard, accept or deny |
| F3 | high | AD-2, AD-17, dependency diagram | Three state changes do not come from `workflow` and have no trigger or arrow: manual ingestion, Document Intelligence classifier training, eval score posts |
| F4 | high | AD-6, AD-5 (missing rule) | Stage command call style (blocking or accepted-then-polled), timeouts and model retry are undecided |
| F5 | high | AD-11, AD-15, AD-17 | Retrieval results cannot be attributed to a fact, so "rule recall in the top 5 per fact" cannot be scored, and the retriever is confounded with the agent's query writing |
| F6 | high | AD-15, ERD, Conventions | The verdict contract is underspecified and self-contradictory: no reason shape, no loading derivation, no confidence, and the citation rule forbids the reasons that `refer` and `standard` need |
| F7 | medium | Conventions (Enums), ERD | No case status enum and no failure status for a page or case |
| F8 | medium | AD-8 | Audit writes are not idempotent under activity retry; the action catalogue is left open with "..." |
| F9 | medium | AD-11 table | The ladder has no like-for-like pgvector versus Azure AI Search pair; the Compare toggle's two configurations are not named |
| F10 | medium | AD-14 | The highlight still uses a third reading of the page (the browser's PDF text layer); pages without a text layer are undecided |
| F11 | medium | Deployment diagram, AD-12, AD-13 | `retrieval --> di` has no AD behind it; classifier training storage and the search-to-Foundry call for `r6` are missing |
| F12 | medium | Accepted exceptions | "Each is recorded in the standards file it departs from" is false today: all three standards tables are empty, and the SPEC still says Entra sign-in |
| F13 | medium | Sequence diagram | Three disagreements with the dependency diagram and AD-19 |
| F14 | low | AD-13, AD-12, ERD, AD-1/3/5/7/9 | Smaller gaps and duplicated statements (listed under items 1, 6 and 8) |

---

## 1. Fixes the real divergence points and misses none

**What it gets right.** Service set, call direction, call mechanism, data ownership, routing in one place, one audit trail, one retrieval interface, one ingestion, one classifier interface, one model, one eval runner, one origin. These are the right seams for seven independently built services.

**Missed divergence points.**

- **F4 (high) — stage command call style.** AD-5 says "each activity is one thin call to a stage service" and AD-6 says commands are idempotent, but nothing says whether a stage command blocks until the work is done or returns "accepted" and is polled. A verdict run (an agent loop with a step limit) and an LLM classification by repeated runs can each take minutes; the Container Apps ingress request timeout is 240 seconds by default, and the spine does not say whether sidecar-to-sidecar calls are subject to it. Each stage epic will pick its own answer, and `workflow` has to match all of them. Also silent: the retry and back-off convention for model 429s, which are certain given "low model deployment capacity" (Accepted risk) times repeated classifier runs times 20 cases times 6 configurations.
  *Fix:* add one rule to AD-6: either "a stage command blocks, must finish within N seconds, and the activity retry policy is X" or "a stage command returns 202 with the key; the activity polls `GET` on the same key". State one retry and back-off rule for model calls in Conventions.

- **F5 (high) — fact attribution in retrieval.** `bake-offs.md` scores rule recall "in the top 5 per fact". AD-11's search takes "a fact query" and its result items carry no `fact_id`; AD-15's `search_rules` tool lets the agent write any query, any number of times, for any or no fact. The runner (AD-17) therefore cannot say which results belong to which expected fact, and the measured number mixes retriever quality with the agent's query writing. The audience is a retrieval expert; this is the first thing they will ask.
  *Fix:* make `search_rules` take a `fact_id` (the query text is derived from the fact by one function in `verdict`'s domain, the same for every configuration), log `fact_id` on each `agent_step`, and have the runner score recall on the first search per fact. Alternatively add a direct per-fact retrieval eval through `web -> retrieval` (the "eval runs" arrow already exists) and keep verdict accuracy as the end-to-end metric.

- **F6 (high) — verdict contract.** See item 2 (AD-15).

- **F7 (medium) — statuses.** Conventions list a page status enum only. `workflow` owns "page and case status" (AD-4) but there is no case status enum, and neither level has a failure value, although Conventions say an invalid model response "is an error". The SPA epic and the `workflow` epic will invent these separately. Also undecided: when the verdict runs (all pages terminal?) and what happens when every page is discarded or denied.
  *Fix:* add a case status enum (for example `processing`, `awaiting_human`, `judging`, `suggested`, `failed`), add `failed` to page status, and one sentence: "the verdict runs once every page is `extracted`, `discarded`, `denied` or `failed`; a case with no facts gets `refer`".

- **F3 (high) — out-of-pipeline triggers.** See item 2 (AD-2).

- **Low — page content for classification.** The diagram says `classification -->|page content| intake`; AD-14 only fixes page *text*. The LLM contender could read text or an image; the Document Intelligence contender needs the page as a file. If the two contenders see different inputs the spec's fairness constraint ("contenders share inputs") is at risk. *Fix:* one line in AD-13 naming the input both contenders receive (for example the single-page PDF from `intake`).

- **Low — thumbnails (CAP-3).** The triage queue needs a page thumbnail; no owner is named (rendered by `intake`, or by the SPA from the PDF). One line under AD-19 or the ownership table.

## 2. Every AD's Rule is enforceable and prevents its stated divergence

| AD | Enforceable | Prevents its divergence | Note |
| --- | --- | --- | --- |
| AD-1 | Yes | Yes | |
| AD-2 | Partly | **No** | F3 |
| AD-3 | Yes (import ban, one client module) | Yes | "Any call not drawn is forbidden" has no enforcement; see low note |
| AD-4 | Yes (database roles) | Yes | Ownership of human decisions conflicts with AD-10 (F2) |
| AD-5 | Yes | Yes | Assumption still open; see item 3 |
| AD-6 | Yes | **No** for classification | F1 |
| AD-7 | Yes | Yes | |
| AD-8 | Partly | Partly | F8 |
| AD-9 | Yes | Yes | |
| AD-10 | Yes | Yes, but contradicts AD-4 | F2 |
| AD-11 | Yes | Partly | F5, F9 |
| AD-12 | Yes | Yes | Low notes |
| AD-13 | Yes | Partly | F1; low notes |
| AD-14 | Partly | Partly | F10 |
| AD-15 | Partly | Partly | F6 |
| AD-16 | Yes | Yes | Reranker and `r6` caveat (item 3) |
| AD-17 | Partly | Partly | F1, F3 |
| AD-18 | Yes | Yes | |
| AD-19 | Yes | Yes | |

- **F1 (critical) — AD-6 / AD-13 / AD-17.** Three rules that cannot all hold:
  - AD-13: "The pipeline's contender is a setting" (an environment variable of `classification`, fixed at deploy time).
  - AD-6: a stage command is keyed by `case_id` plus `page_id`; "repeating a command with the same key returns the stored result and writes nothing new".
  - AD-17: the runner drives the deployed pipeline "once per classifier contender".
  With these, the second contender's run either needs a redeploy between runs or returns the first contender's stored result. The ERD (`PAGE ||--o{ CLASSIFICATION`) already assumes several classifications per page, so the diagram and AD-6 disagree too. Retrieval does not have this problem because `retriever_config` is in the verdict key.
  *Fix:* treat the contender exactly like `retriever_config`: a per-command parameter with a default from settings, and part of the idempotency key (`case_id`, `page_id`, `contender`). State which classification the gate routes on (the default contender's) and that the other is scored only. The cheapest eval path is then "classify the labelled page set with both contenders through one eval route", not two full pipeline runs.

- **F2 (critical) — AD-10 versus AD-4.** AD-10: "No agent tool and no stage API exposes [keep, discard, accept, deny]." The AD-4 table gives "triage and customer decisions" to `classification`, the ERD gives it `HUMAN_DECISION`, and the capability map lists `classification` under CAP-3. For `classification` to store a decision it must expose an API that records one, which AD-10 forbids; and AD-6's key (`case_id`, `page_id`) cannot hold both a customer "keep" and a later underwriter "accept" on the same page.
  *Fix (simplest):* move human decisions to `workflow`. The decision is already the external event plus the `page.kept` / `page.accepted` audit event plus the page status, all in `workflow`. Delete `HUMAN_DECISION` from `classification` in the AD-4 table, the ERD owner line and the CAP-3 row.

- **F3 (high) — AD-2.** "The one write that does not come from `workflow` is the upload." At least three more exist and none has a trigger, an owner or an arrow:
  1. **Manual ingestion** into `retrieval` (AD-12). `retrieval` has internal ingress only (AD-18), so the pipeline cannot call it; `web -> retrieval` is labelled "reads, eval runs".
  2. **Training the Document Intelligence custom classifier** (AD-13, CAP-9): who trains it, from which pages, stored where. If it is trained on the same labelled pages it is scored on, the classifier scoreboard does not survive expert scrutiny; the spine needs a train/test split rule.
  3. **Eval score posts** (AD-17): "posts the scores to the owner ... classifier scores to `classification`", but `web -> classification` is labelled "reads" only, and the route is on the open public entry, so AD-17's "prevents numbers typed in by hand" is not actually prevented.
  A Container Apps job for any of these would be an eighth deployable and break AD-1.
  *Fix:* extend AD-2's exception list to "upload, manual ingestion, classifier training, eval score write"; add the `web -> retrieval` "ingest" and `web -> classification` "eval runs" labels; state that all three are driven by the runner or a seed script through `web`. Better for AD-17: have the owning service compute and store the score from results it already holds plus expected labels sent by the runner, so nobody posts a finished number.

- **F6 (high) — AD-15.**
  - "A reason is stored only if it cites at least one `fact_id` and one `rule_id` seen in that run." `flows.md` forces `refer` on "no matching rule" and the spine forces it at the step limit; neither can cite a rule. `standard` means "no rule triggers a debit", which may cite nothing. CAP-6 says "each verdict lists its reasons" and the ERD says `VERDICT_RUN ||--|{ REASON` (at least one) and `REASON }o--|{ RULE` (at least one). These verdicts would have zero storable reasons.
  - "The rules in `flows.md` that force `refer` are applied in `verdict`'s domain code, not left to the prompt." Detecting "conflicting rules" and computing `loading_pct` in code needs a structured effect per cited rule, but the rule table is unreadable to `verdict` (AD-12), so the effect can only come from the model reading chunk text. No payload shape is fixed for that.
  - "Low confidence" forces `refer` (`flows.md`, CAP-6), but no verdict or fact confidence exists anywhere in the spine.
  *Fix:* fix the verdict payload in AD-15: `verdict`, `loading_pct`, `reasons[{rule_id, fact_ids, effect: debit_pct | decline | none}]`, plus `system_reasons[code]` from a small enum (`no_matching_rule`, `step_limit`, `unverified_quote`, `conflicting_rules`, `low_confidence`) that are exempt from the citation rule. Say how `loading_pct` is derived (for example the sum of cited debits) and define "low confidence" or drop it explicitly. Change the ERD to `VERDICT_RUN ||--o{ REASON`.

- **F8 (medium) — AD-8.** The table is append-only and written by an activity; Durable Task activities run at least once, so a retried activity appends a second identical audit row. AD-6 makes stage commands idempotent but says nothing for the audit write. Separately, the `action` catalogue ends in "..."; the catalogue is the divergence point CAP-10 depends on (it names "classification, override, accept, deny, extraction and verdict").
  *Fix:* add "unique on (`action`, `ref`); a repeat is a no-op" and list the full catalogue (it is about ten values, including `case.uploaded`, `page.route_decided` and a failure action).

- **F10 (medium) — AD-14.** The AD's aim is one reading of the page, but "the SPA highlights by finding the quote on that page of the rendered PDF" introduces a third reading: the browser's PDF text layer, which differs from `intake`'s extracted text in spacing, ligatures and line order, and the normalisation function lives in a Python package the SPA cannot import. Verified quotes will sometimes fail to highlight during the demo.
  *Fix:* either have `extraction` store the match as character offsets into `intake`'s page text and `intake` serve word boxes so the SPA draws rectangles, or state that the SPA ports the normalisation function and falls back to a page-level highlight. Also say what `intake`'s page text is for a page with no text layer (the page set includes rotated and handwritten pages, and triage can accept them): OCR, or empty text and no facts.

- **Low — AD-3.** "Any call not drawn is forbidden" is review-only. A cheap enforcement: each service's one client module declares the app ids it may call and a unit test compares that to the diagram.

- **Low — AD-10.** "`web` routes that raise an external event on the case's orchestration" reads as if `web` talks to the scheduler; the deployment diagram allows only `workflow`. Reword to "`web` routes that call `workflow`, which raises the event". The same sentence is also tied to Durable Task wording, while AD-5 claims only AD-2, AD-6 and AD-7 survive the fallback.

- **Low — AD-13.** The number of repeated runs behind the LLM confidence is not fixed. With five runs confidence can only be 0.2 to 1.0, so the 0.90 threshold means "unanimous"; and if the pinned model does not allow sampling variation the agreement rate will sit at 1.0 and the calibration metric becomes meaningless. Fix the run count as a setting with a default and note the risk. Also state where the `page_type` to `is_medical` mapping lives (one function in contracts).

## 3. Nothing under Deferred or Open questions lets two epics diverge

Mostly clean. Items that could bite:

- **Medium — AD-5 engine assumption.** The orchestration engine is still an unconfirmed assumption with a hand-written fallback. It only touches the `workflow` epic, but AD-10 and the local development setup (scheduler emulator) are written against it. With four days, confirm before the `workflow` epic starts; list it under Open questions so it is visible there.
- **Low — reranker for `r4`.** Contained in `retrieval`, so no cross-epic divergence. Note that a Cohere reranker is a second model and an LLM reranker reuses the chat deployment; either way AD-16 ("one LLM deployment ... for everything") needs a one-line carve-out. The same applies to `r6`, where Azure AI Search calls a chat model itself for query planning: say it must be the AD-16 deployment.
- **Low — pgvector 3,072 dimensions.** As far as I know pgvector stores vectors up to 16,000 dimensions and only its approximate indexes are limited to 2,000, which AD-12's exact search avoids. This question can probably be closed rather than left open.
- **No issue:** sign-in, private networking, Key Vault, second environment, availability, Terraform versions, visual design, Jira, model choice, deployment type, scheduler price.

## 4. Covers every spec capability (CAP-1 to CAP-11)

All eleven appear in `binds` and in the capability map. Coverage quality:

| CAP | Covered | Gap |
| --- | --- | --- |
| CAP-1 | Yes | Failure status missing (F7) |
| CAP-2 | Yes | Classifier input not fixed (item 1, low) |
| CAP-3 | Partly | Decision ownership contradiction (F2); thumbnail owner silent |
| CAP-4 | Yes | Pages without a text layer (F10) |
| CAP-5 | Partly | Per-fact attribution (F5); `impairment` chunk metadata required by `bake-offs.md` is absent from AD-12 |
| CAP-6 | Partly | Reasons for `refer` and `standard`, loading, confidence (F6) |
| CAP-7 | Partly | Highlight reading (F10); the map lists only `web`, SPA but the view also needs `intake` (PDF) and `retrieval` (rule text) |
| CAP-8 | Partly | F5, F9; how the Compare toggle starts the second verdict run after the orchestration has finished is not stated (`web -> verdict` is reads only, so it must be a `workflow` command) |
| CAP-9 | **No, as written** | F1, F3 (training) |
| CAP-10 | Yes | F8 |
| CAP-11 | Partly | Loading the data into the environment has no trigger (F3) |

- **F9 (medium) — AD-11 ladder.** The spec's headline comparison is pgvector against Azure AI Search with "only the component under test differs". In the ladder, `r4 -> r5` changes the store and the reranker together, and there is no Azure AI Search row without the semantic ranker to set against `r3`. The Compare toggle "shows both retrievers" but the spine does not say which two of six. *Fix:* add an Azure AI Search hybrid-only row (or state that `r3` versus `r5` is the named head-to-head and why the ranker counts as part of the engine), and name the Compare pair as a setting.
- **Low:** "Cost" and "Effort" (retrieval) and "Cost per page" (classification) are bake-off metrics that no runner can measure; say they are entered by hand, or capture token usage on classifier and verdict results so cost per page is computed.

## 5. Every dimension the altitude owns is decided, deferred or open

| Dimension | State |
| --- | --- |
| Deployment and environments | Decided (AD-18, stacks, deployment diagram) |
| Infrastructure and provider strategy | Decided (Stack, AD-18, service-set exception) |
| CI/CD | Not stated in the spine, but `docs/standards/terraform.md` rules 31 to 36 and its apply order cover it and the spine lists that file as a source. Acceptable; one line under Conventions pointing there would remove doubt. Image tagging is the only piece neither document fixes. |
| Local development | Decided in one paragraph. Gap: Blob Storage is not mentioned (emulator or the `demo` account), and local PostgreSQL will not have the Entra-mapped roles from Conventions. |
| Observability | Decided (tracing and logs convention, Application Insights) |
| Security and identity | Decided (AD-9, AD-18, exceptions) |
| Data ownership and schema change | Decided (AD-4, Conventions) |
| Testing | Decided (Conventions) |
| Cost | Decided (budget alerts, compute ceilings, accepted risk) |
| **Operations** | **Silent (medium).** No word on seeding and resetting the demo environment (manual ingestion, classifier training, loading the case set, clearing cases between rehearsals) or on the demo-day warm-up beyond the `min_replicas` variable. This overlaps F3. |
| **Failure handling and resilience** | **Silent (high, see F4, F7).** Timeouts, retries, failure statuses. |
| **Scope cut order** | **Silent (medium).** The spec says "scope cuts favour the end-to-end demo path over breadth". The spine adds breadth (six retriever rows including agentic retrieval, a managed orchestration engine, seven deployables, two Terraform stacks) and names no walking-skeleton order or cut line. For a four-day build, one line such as "build order: `r3` and the `llm` contender end to end first; `r1`, `r2`, `r4`, `r6` and `doc-intelligence` are cuttable in that order" would keep epics from spending day one on breadth. |

## 6. Internal consistency

Every arrow and owner was checked across the dependency diagram, AD-4 table, deployment diagram, sequence diagram, ERD and capability map.

**Dependency diagram versus ADs**

- `web -> classification` is "reads"; AD-17 needs a score write there (F3).
- `web -> retrieval` is "reads, eval runs"; ingestion has no arrow (F3).
- No arrow carries a human decision into `classification`, consistent with AD-10 and inconsistent with the AD-4 table (F2).
- All other arrows match AD-2, AD-14 and AD-15.

**F13 (medium) — sequence diagram**

- `F-->>W: page awaiting triage` is drawn as an unprompted return from `workflow` to `web`. There is no `workflow -> web` arrow and AD-19 says the SPA polls. Draw it as `W->>F: poll progress` followed by the reply.
- `classification -> intake` (page content) and `workflow -> intake` (page list) exist in the dependency diagram but are absent from the sequence.
- The human decision is not recorded anywhere in the sequence, which hides F2.

**F11 (medium) — deployment diagram**

- `retrieval --> di`: no AD mentions `retrieval` using Document Intelligence (presumably to parse the manual PDF for smart chunks). AD-12 should say so, or the arrow should go.
- `intake` has no path to Document Intelligence, so page text has no OCR route (ties to F10).
- Training a Document Intelligence custom classifier needs training files in Blob Storage that the Document Intelligence resource can read; neither `classification --> blob` nor that resource-to-storage access is shown.
- For `r6`, Azure AI Search calls the chat model itself; `search --> foundry` and the search service's identity are missing.
- Consistent: only `workflow` reaches the scheduler; `web` has no database; six services reach PostgreSQL, matching AD-4.

**ERD versus AD-4 table and ADs**

- `PAGE ||--o{ CLASSIFICATION` (many) contradicts AD-6's key (F1).
- `HUMAN_DECISION` owner contradicts AD-10 (F2).
- `VERDICT_RUN ||--|{ REASON` and `REASON }o--|{ RULE` contradict the verdicts that have no citable rule (F6).
- `CHUNK }o--|{ RULE` says every chunk carries at least one rule; `fixed` chunks can carry none (AD-12 says only "the `rule_ids` printed inside its text span"). Use `}o--o{`.
- `PAGE_STATUS` is present but there is no case status entity (F7).
- Eval runs are in the AD-4 table and not in the ERD; acceptable for "core entities".
- Owner line under the ERD otherwise matches the AD-4 table.

**Capability map versus ADs**

- CAP-3 lists `classification`; follows F2 and should change with it.
- CAP-7 omits `intake` and `retrieval`.
- CAP-10 omits AD-2, which binds CAP-10 in its own Binds line. Trivial.
- AD-5 binds CAP-1 to CAP-3 but is not listed in the map under any of them. Trivial.

**F12 (medium) — Accepted exceptions**

"Each is recorded in the standards file it departs from" is not true at review time: the accepted-exceptions tables in `docs/standards/azure.md`, `security.md` and `terraform.md` are all empty. `SPEC.md` (which calls itself the canonical contract) still carries the Entra sign-in constraint that AD-9 overrides. *Fix:* record the five exceptions in the standards files and amend the SPEC constraint, or change the sentence to "to be recorded".

## 7. Mermaid syntax

All four diagrams were read line by line; no syntax errors found. No mermaid renderer is installed in the project, so this is a static check only and the diagrams were not rendered.

- Dependency flowchart: valid.
- Deployment flowchart: valid. `cae --> obs` is an edge from a subgraph, which mermaid accepts. The subgraph title contains a colon and commas inside square brackets; this parses, but quoting it (`cae["Container Apps environment: demo, West US 3"]`) is safer across renderer versions. Same advice for `pg[(PostgreSQL: one schema per service)]` and `foundry[...]`.
- Sequence diagram: valid, including the second colon in `external event: accepted`.
- ER diagram: valid; all cardinality tokens are legal.

## 8. Terseness

The spine is terse overall; no AD is filler. Trims:

- **Duplicates:** "Dapr Workflow is not used" appears in AD-3 and AD-5 (and the AD-5 copy carries rationale that belongs in the decision log). The classifier result fields are listed in both AD-7 and AD-13. The verdict run key is stated in AD-6 and AD-15. AD-9's last sentence repeats the Accepted exceptions table.
- **AD-1** largely restates the paradigm table; it could shrink to its last two sentences.
- **AD-19:** "CORS stays off" follows from "one origin"; the polling half is the real decision.
- **Not verified:** the package versions in the Stack table. Checking them needs sources outside the project folder, which this review was not allowed to use; several (for example `openai`, `agent-framework`, `azure-search-documents`) should be confirmed against the registries before they are pinned.

## Suggested order of repair

1. F1 and F2 (rule contradictions; about ten lines changed).
2. F3 and F4 (add the missing triggers and the call-style rule).
3. F5 and F6 (the two contracts the retrieval expert will probe).
4. F7, F8, F12, F13, then the rest.
