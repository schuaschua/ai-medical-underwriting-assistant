# Review: inputs reconciled against the architecture spine

- **Spine:** `../ARCHITECTURE-SPINE.md` (draft, 2026-10-06)
- **Decision log:** `../.memlog.md`
- **Inputs:** `SPEC.md`, `flows.md`, `bake-offs.md`, `synthetic-data.md` (spec folder); `azure.md`, `security.md`, `terraform.md`, `coding-style.md` (`docs/standards/`)
- **Method:** every input line was read against the spine's ADs, conventions, map, exceptions, Deferred and Open questions. Nothing outside the project folder was read and nothing was checked online, so items marked "not verified" are flags, not facts.
- **Not reported, as instructed:** the dropped Entra sign-in (AD-9), the dropped Dapr Workflow (AD-5), the six-row retrieval ladder, missing rationale, and standards rules the spine inherits unchanged.

## Verdict

The pipeline path (CAP-1 to CAP-7, CAP-10) landed almost complete. The two bake-offs (CAP-8, CAP-9) and the synthetic data (CAP-11) did not: their metrics, winner rule, classifier training and Compare pairing have no carrier, and three ADs together make the classifier bake-off impossible to run as written. There are no critical findings; there are 7 high (S1, S2, S3, B1, B2, B3, B5), 27 medium and a list of low ones.

Severity scale: **high** = a capability or success signal cannot be met as the spine is written, or the bake-off result would not hold up; **medium** = two builders could plausibly diverge, or a rule is broken without being listed; **low** = small gap or wording.

## 1. SPEC.md

| # | Sev | Input | What did not land |
| --- | --- | --- | --- |
| S1 | high | CAP-9 (L46-48); constraint L63 | **The classifier bake-off cannot run as written.** AD-13 makes the contender "a setting" (an environment variable, per the Configuration convention). AD-6 keys a classification command by `case_id` + `page_id` with no contender, so a second contender on the same page "returns the stored result and writes nothing new". AD-17 has the runner drive the deployed pipeline through `web` "once per classifier contender". The runner cannot switch a setting through `web`, and the idempotency key would return contender A's answer for contender B. Retrieval does not have this problem, because `retriever_config` is in the verdict key. |
| S2 | high | CAP-8, CAP-9, success signal L77 | **Human waits block the eval runner.** The runner goes "the same path a user takes" (AD-17). Any page under 90%, and every junk page, stops at a customer or underwriter wait (AD-5, AD-10). The spine does not say who answers. If the runner sends `X-Demo-Role`, it writes `actor_kind: human` audit rows for a script, and retrieval's verdict accuracy then depends on the classifier, which breaks "only the component under test differs" (L61). |
| S3 | high | Success signal L77: "both scoreboards name a winner on measured numbers" | No winner rule anywhere. With six retrieval rows and four or five metrics, nothing says which metric decides or how ties are broken. |
| S4 | medium | Constraint L59: the result screen states "AI suggestion, not a decision" | AD-10 says a verdict is "stored and shown as a suggestion" but the exact label is not in the spine. The Deferred row "Visual design and wording" makes it look open. It is a spec constraint, not a UX choice. |
| S5 | medium | Constraint L60; CAP-6 (L39) | AD-15 stores a reason only if it cites at least one `fact_id` **and** one `rule_id`. A `refer` for "no matching rule" or for the step limit has no rule to cite, and a `standard` verdict ("no rule triggers a debit") may have none either. The entity diagram requires at least one reason per run (`VERDICT_RUN \|\|--\|{ REASON`). These verdicts would have no storable reason. |
| S6 | medium | CAP-6 (L39): "low confidence ... yield refer" | No confidence value is defined for a verdict run or a fact. AD-15 points at `flows.md` for the refer rules, but "low confidence" has no source field and no threshold setting. |
| S7 | medium | CAP-5 (L35): "Each extracted fact retrieves the matching rules" | AD-15 lets the agent decide what to search. Nothing guarantees one search per fact, so the per-fact result that CAP-5 and the recall metric need may not exist for every fact. See B2. |
| S8 | medium | CAP-8 (L45): Compare toggle shows "both retrievers" | AD-15 says Compare is "two runs of the same case". With six configurations the spine does not say which two, or who picks them. "Differences highlighted" (`bake-offs.md` L20) is also not carried. |
| S9 | medium | Constraint L66: demo by 10 October, "scope cuts favour the end-to-end demo path over breadth" | No cut line or build order. Seven services plus six ladder rows carry the same weight in the spine; nothing marks `r4`, `r5`, `r6` or the second classifier as cuttable. The memlog's "risk noted: weekend deadline" (L14) is also absent. |
| S10 | low | CAP-3 (L30): triage queue shows a thumbnail | No home for page thumbnails (intake renders them, or the SPA renders from the PDF). |
| S11 | low | CAP-7 (L42): "clicking a reason opens its manual rule" | Has an arrow (`web` reads `retrieval`) but the spine does not say whether this opens the chunk text or the page of the manual PDF. |
| S12 | low | CAP-1 (L23-24): per-page progress | The page status enum has no failure state, and the case-level statuses ("Page and case status", AD-4) are not listed. |
| S13 | low | Constraint L58: synthetic data only | Carried only by inheritance (`security.md` rule 1). Acceptable; noted because AD-9's accepted risk lets anyone upload any PDF, and nothing in the spine or the SPA wording says "synthetic files only". |

## 2. flows.md

| # | Sev | Input | What did not land |
| --- | --- | --- | --- |
| F1 | medium | Gate rules L22-26 | Routing depends on medical or non-medical, but the spine never maps `page_type` to `is_medical`. `application_form` is the open case: `synthetic-data.md` L12 and L18 treat application forms as part of the medical case pages. The LLM contender and the Document Intelligence contender could label it differently, and so could the answer key. |
| F2 | medium | Verdicts L32-37 | Only the forced-`refer` rows are bound to domain code (AD-15). Whether `standard`, `loaded` (and the size of `loading_pct`) and `decline` are computed in code from triggered rules or chosen by the model is not said. The rule table's debit field is not readable by `verdict` (AD-12), so a builder must guess. |
| F3 | low | Pipeline L15-16 | The spine does not say when the verdict run starts: after every page is settled, or while some pages still wait for a person. |
| F4 | low | L28: customer prompt "This looks like a utility bill (96%)" | The `page_type` enum (AD-13) has no utility bill, payslip, passport or recipe; the prompt would read "other (96%)". The conflict is inside the spec (`bake-offs.md` L29 against `flows.md` L28) and the spine carried it forward without a note. |
| F5 | low | Result view L41 | The two-pane order (verdict, reasons, facts) is a spec requirement, now covered only by the Deferred row for visual design. |
| F6 | low | L26: deny and discard mean "removed" | Whether "removed" is only a status or also deletes the page text is not said. AD-8's append-only audit suggests status only. |

## 3. bake-offs.md

| # | Sev | Input | What did not land |
| --- | --- | --- | --- |
| B1 | high | Metric tables L12-18 and L31-36 | **No metric is named in the spine.** AD-17 says "scores" and does not point to `bake-offs.md` (AD-7 and AD-15 do point to `flows.md`). Rule recall, verdict accuracy, latency, cost, effort, accuracy, calibration (at least 90% correct among pages scored at least 90%), queue rate and cost per page have no carrier. The memlog (L21) lists four retrieval metrics and already drops Effort. |
| B2 | high | L14: rule recall "in the top 5 per fact" | Under AD-15 and AD-17 recall is measured on queries the agent writes, through the whole pipeline. The spine does not say which search counts for a fact when the agent searches several times or not at all, so the number mixes retrieval quality with the agent's query writing. The audience is a RAG expert (SPEC L66). |
| B3 | high | L14, and `synthetic-data.md` L5 (cross-references) | AD-12: a chunk "carries the `rule_ids` printed inside its text span". A chunk for rule A that cross-refers to rule B carries both, so retrieving A scores as a hit for B. Recall is inflated. It also conflicts with the same AD's "a `smart` chunk holds exactly one rule". |
| B4 | medium | L17-18 Cost and Effort; L36 Cost per page | AD-17 "prevents numbers typed in by hand", but Cost (from Azure price lists) and Effort (code and setup) cannot be produced by a runner. They have no home and the AD as written forbids them. |
| B5 | high | L26: Document Intelligence custom classification model | **Training has no home.** No service, folder, storage container or step trains the custom classifier. Memlog L45 item 7 mentions a Storage account for "classifier training data"; the spine's AD-4 table has only the `cases` container and the deployment diagram has no arrow from `classification` to Blob. `synthetic-data.md` has one labelled page set, so training and scoring on the same pages is the default outcome. AD-17 also says the page labels are "read only by the eval runner", which training contradicts. |
| B6 | medium | L26 with CAP-2 "one-line reason" | AD-13 requires `reason` and `is_medical` from both contenders. A Document Intelligence classifier returns a type and a confidence only. The spine does not say where its reason comes from. |
| B7 | medium | L10: shared chunks carry `rule_id` and `impairment` metadata | `impairment` is dropped: AD-12 and AD-11 carry `rule_ids`, parent section id and a context line only. |
| B8 | medium | L3 and L10: same LLM and embedding model for every contender | For `r5` and `r6` the spine does not say which model the Azure AI Search knowledge base uses for query planning, or how queries are vectorised. If the search service calls Foundry itself, it needs its own identity and role; the spine has "the seven runtime identities" only. AD-16 covers "all services", not the search service. |
| B9 | low | L26: confidence from agreement across repeated runs | Carried (AD-13) but the number of runs is not a named setting, and it drives cost per page. |
| B10 | low | L16: latency is "retrieval time per fact" | AD-11 puts `latency_ms` on every result item, not on the search call. |

## 4. synthetic-data.md

| # | Sev | Input | What did not land |
| --- | --- | --- | --- |
| D1 | medium | L6: manual "generated from a structured rule table" | No home for the generators of the manual, the case PDFs or the page set. The source tree has `data/` for outputs and `evals/` for the runner only. The generator must read the rule table, which AD-17 says is "read only by the eval runner". |
| D2 | medium | L5: prose, tables and cross-references | Memlog L20 says the manual is parsed from its PDF with a layout model. The spine shows this only as the `retrieval --> di` arrow; AD-12 says "ingests the manual PDF" and does not name the parser, how a table row becomes a rule chunk, or the size and overlap of the `fixed` chunk set. |
| D3 | medium | L5 | Nothing triggers ingestion. No caller is drawn for it: an arrow label, a job or a script. Who creates the Azure AI Search indexes and knowledge base, and who enables the pgvector extension and in which schema (it is database-wide, while AD-4 gives each role its own schema only), is also unassigned between Terraform, migrations and `retrieval`. |
| D4 | low | L20: rotated and handwritten edge pages | `intake` produces page text (AD-14) with `pymupdf` and has no arrow to Document Intelligence, so a scanned or handwritten page that an underwriter accepts has no text to extract from. The spine should say what happens (zero facts, or `refer`). |
| D5 | low | L8: each rule has a public source (ADA, AHA, WHO); L14: cases cover all four verdicts | Content rules for CAP-11 with no carrier. Reasonable to leave to the story, but the map row for CAP-11 cites only AD-12 and AD-17. |

## 5. docs/standards/azure.md

| # | Sev | Rule | Finding |
| --- | --- | --- | --- |
| A1 | medium | Accepted exceptions (L121-126) | The spine says each exception "is recorded in the standards file it departs from". It is not: the tables in `azure.md` (L125-126) and `security.md` (L73-74) are empty. The spine's table also lacks the "approved by (role)" and "close by" columns the standards require. |
| A2 | medium | Rule 9, rule 31 | Runtime roles per identity are not listed (who gets Foundry User, search index roles, blob roles, the scheduler worker role, Document Intelligence access). Rule 31's condition on the deployment identity depends on that list. |
| A3 | medium | Rule 31, with `terraform.md` rule 29 | Local development uses the `demo` Foundry, Azure AI Search and Document Intelligence "through the developer's Azure sign-in". That needs data-plane roles for a human user. The deployment identity may assign roles only to service principals, so this is a manual step the spine does not record. |
| A4 | low | Rule 2, naming table | Four new resource types (Azure AI Search, Document Intelligence, Storage account for data, Durable Task Scheduler and task hub) have no abbreviations, and the `<role>` suffix (presumably the app id) and the `<org>` code are not set. |
| A5 | low | Rule 19 | Azure AI Search Basic and Document Intelligence S0 are not the cheapest tiers. The memlog's open question on the free search tier (L38) did not reach Open questions. |
| A6 | low | Rule 20 | Compute ceilings are recorded, but idle timeouts and the model deployment capacity (TPM) are not. TPM is one of the three bounds named in the accepted risk. |
| A7 | low | Rule 24 | The model retirement date is not recorded, and the embedding deployment has no version in the Stack table. |
| A8 | low | Rule 27 | No home for a scenario evaluation set that checks tool-call accuracy for the verdict agent; the bake-off metrics do not include it. |
| A9 | low | Rule 5 | Not verified: that Durable Task Scheduler (Consumption SKU) is available in `westus3`. Open questions cover Foundry, Cohere and pgvector but not this. |
| A10 | low | Commonly deferred table | AI gateway with rate limits, and Azure Policy and Defender, are deferred in practice but not listed in the spine's Deferred table. |

## 6. docs/standards/security.md

| # | Sev | Rule | Finding |
| --- | --- | --- | --- |
| X1 | medium | Rule 15: "Tools never take a record id the model chose" | `read_rule` takes a `rule_id` chosen by the model (AD-15). The allow-list of ids seen in the run is a sound control, but the rule is broken as worded and is not under Accepted exceptions. |
| X2 | medium | Rule 19 | The alert on AI writes outside the agent's own record is neither carried nor excepted. AD-15 makes such a write structurally impossible; say so in an exception row. |
| X3 | low | Rule 32: audit **and override** tables append-only | AD-8 covers `workflow.audit_event` only. `classification`'s human decision table is an override table and is not declared append-only. The spine also does not say who writes it: AD-10 sends human actions to `workflow`, while AD-4 gives the data to `classification`. |
| X4 | low | Rule 24 | With no auth cookie, the custom-header requirement is met only if `X-Demo-Role` is required on every non-GET call. AD-9 implies it; it is not stated as the CSRF control. |
| X5 | low | Rule 25 | "Self only, no inline scripts" needs the `react-pdf` worker served from the same origin. Not stated. |
| X6 | low | Rule 29 | Not verified: that `azure-search-documents` 12.0.0 (stable) exposes agentic retrieval. If `r6` needs a preview package or API version, the architecture must name it. |
| X7 | low | Rule 14 | Uploaded page text is untrusted input to the extraction and verdict prompts. The server-side limits in AD-15 are good; the spine has no line that says page text is data, never instructions. |

## 7. docs/standards/terraform.md

| # | Sev | Rule | Finding |
| --- | --- | --- | --- |
| T1 | medium | Rule 2: every resource in exactly one stack | Data-plane objects are unassigned: search indexes and the knowledge base, blob containers, the trained classifier model, the pgvector extension and the per-service schemas and grants. An infrastructure builder and the `retrieval` builder could each assume the other creates them (same as D3). |
| T2 | low | Apply order L42-48 | The spine gives "foundation then app" and omits the steps between (database principal bootstrap for seven roles, image build, six Alembic runs). They are inherited, but the principal bootstrap is larger than usual here and is not noted. |
| T3 | low | Rule 9 | Listed under Deferred and correctly so. |

## 8. docs/standards/coding-style.md

| # | Sev | Rule | Finding |
| --- | --- | --- | --- |
| C1 | medium | Rule 23: never run functional tests against a shared deployed environment; only a read-only smoke check | AD-17 uploads about 20 cases, six times, to the one deployed environment and posts scores to it. Not under Accepted exceptions. Eval cases and demo cases will also share one database. |
| C2 | medium | Rule 16, with AD-14 | AD-14 "prevents ... the PDF highlight ... using a different reading of the page", then has the SPA find the quote in the rendered PDF. That is a second reading (the browser's text layer against intake's stored text) and needs a second normalisation in TypeScript. A fact carries no offset or box. The highlight is part of the success signal (SPEC L77). |
| C3 | low | Rules 25, 7, 26, 27 | Coverage thresholds and Jira keys sit under Deferred, not Accepted exceptions. The stated reason ("no Jira site yet") does not explain deferring coverage thresholds. |
| C4 | low | Rule 9 | AD-5 says orchestrator code "holds only sequencing and gate routing"; AD-7 says routing lives "only in `workflow`'s domain package". Orchestrator functions import the durable task SDK, so they cannot sit in a framework-free domain. One sentence would settle it. |

## 9. Memlog decisions the spine omits or states differently

| # | Sev | Memlog | Spine |
| --- | --- | --- | --- |
| M1 | medium | L15: the verdict agent "follows cross-references" | AD-15: `read_rule` "accepts only a `rule_id` returned by a search in the same run". A rule id found inside a rule's text cannot be read unless a search also returns it. Stated differently. |
| M2 | medium | L16: `r6` is "AI Search's own agentic pipeline answering the same per-fact need"; only the first two contenders are "driven by our verdict agent" | AD-11 and AD-15 put `r6` behind `search_rules`, so the verdict agent drives an agentic retriever. This may be intended; it is not what the memlog records. |
| M3 | medium | L45: eleven distillation assumptions "tagged in the spine" | Only four are tagged `[ASSUMPTION]` (AD-12, AD-17, AD-18 short name, compute ceilings), plus AD-5. Untagged: plain HTTP with `httpx` (AD-3), the contracts package, the Storage account, the three agent tools and step limit (AD-15), the status projection, the two Terraform stacks, polling (AD-19). Also untagged: Agent Framework (L17), the service list and owners (L27), `web` as aggregator (L28), agent steps kept in `verdict` (L30). |
| M4 | medium | L21: all six rows scored on "the same 20 cases (rule recall@5, verdict accuracy, latency, cost)" | Not in the spine (see B1). |
| M5 | low | L19: the Swiss Re guide is licensed and not usable; the manual follows the structure of public carrier guides; all content original | Omitted. It constrains CAP-11 content. |
| M6 | low | L20: manual parsed with the layout model; small-to-big with a parent section | The parser is only an arrow. The parent section id is carried, but what a search or `read_rule` returns (the chunk or its parent) is not. |
| M7 | low | L45 item 7: Storage account also holds classifier training data | The spine lists case PDFs and the manual PDF only (see B5). |
| M8 | low | L38: can the free Azure AI Search tier replace Basic? | Not in Open questions. |
| M9 | low | L30: agent steps linked from the audit trail "by case id" | AD-8 links by `ref` to the verdict run. More precise, and with two runs per case, needed. Noted as a difference only. |
| M10 | low | L41: Consumption SKU keeps scheduler data 30 days | Omitted. Harmless while the audit trail stays in PostgreSQL (AD-8). |

## 10. What landed cleanly

- Gate threshold and routing in one place (AD-7), human-only actions (AD-10), quote check and "flagged, never dropped" (AD-14), top 5 as `top_k` default (AD-11), one interface per contender (AD-11, AD-13), same model and embeddings (AD-16), append-only audit with actor and time (AD-8), one origin and no CORS (AD-19), environment, region, ingress and networking decisions (AD-18), and every package version in the memlog.
- The Entra-related exceptions (`azure.md` rule 12, `security.md` rules 4, 5, 7, 12, 18) and the added Azure services are listed.

## Suggested order of repair

1. Classifier bake-off: make the contender part of the classification key and a request parameter, or score the classifier outside the pipeline (S1, S2).
2. Give classifier training a home, with a train and test split (B5).
3. Add one AD or a table that names every metric, its definition, where it is measured, which are hand-entered, and the winner rule (B1, B2, B4, S3).
4. One rule id per chunk for scoring; cross-referenced ids kept in a separate field (B3). Decide whether `read_rule` may follow them (M1).
5. Fix the reason rule for `refer` and `standard` (S5) and carry the "AI suggestion, not a decision" label (S4).
6. Complete Accepted exceptions and write them into the standards files (A1, C1, X1, X2), and tag the remaining assumptions (M3).
