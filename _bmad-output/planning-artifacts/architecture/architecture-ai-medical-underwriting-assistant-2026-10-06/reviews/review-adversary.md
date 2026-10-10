# Adversarial review — Architecture Spine, AI Medical Underwriting Assistant POC

Reviewed: `../ARCHITECTURE-SPINE.md` (status draft, 2026-10-06) against `SPEC.md`, `flows.md`, `bake-offs.md`, `synthetic-data.md`.
Method: for each hole, two builders who read only the spine and the spec, each obeying every AD and convention to the letter, are shown to build parts that do not fit together.

## Verdict

The spine fixes ownership, topology and vocabulary well, but it fixes almost no operation. Every request and response shape is delegated to `packages/contracts/`, which has no owner and no build order, and four capabilities (recording a human decision, the Compare toggle, both bake-off runs, DI classifier training) need a call or a read that the spine's own rules forbid. As written, seven independent builders will not integrate inside four days without a fix round.

## Ranking

| # | Hole | Severity | ADs |
| --- | --- | --- | --- |
| 1 | The contracts package is everyone's dependency and nobody's job; no operation list exists | critical | Conventions (Shared code), AD-3, AD-6 |
| 2 | Human decisions: owned by `classification`, but no legal path writes them | critical | AD-2, AD-4, AD-6, AD-8, AD-10 |
| 3 | Compare toggle and "once per `retriever_config`": the config is a setting, the orchestration is finished, `web` may only read `verdict` | critical | AD-2, AD-5, AD-6, AD-11, AD-15, AD-17 |
| 4 | Eval runner: needs writes the diagram and AD-2 forbid; classifier bake-off drags every page through human waits and extraction | critical | AD-2, AD-7, AD-13, AD-17, diagram |
| 5 | Stage commands: synchronous or not, and what a retry sees while the first call is still running | high | AD-5, AD-6 |
| 6 | Page files, page images and thumbnails: no producer, no format, and AD-3 says JSON | high | AD-3, AD-6, AD-13, AD-14, AD-19 |
| 7 | Search results carry no text; `read_rule` has no defined source; "open the rule" has no defined target | high | AD-11, AD-12, AD-15 |
| 8 | Manual ingestion and DI classifier training: no trigger, no owner, and the labels they need are locked to `evals/` | high | AD-2, AD-12, AD-13, AD-17 |
| 9 | Screen assembly: triage queue, case list, case status, failure status, and when the verdict starts | high | AD-4, AD-19, Conventions (Enums, API paths) |
| 10 | Quote highlight: one normalisation function in Python, a second reading of the page in the browser | high | AD-14 |
| 11 | Per-fact rule recall and latency cannot be computed from an agent that chooses its own queries | high | AD-11, AD-15, AD-17 |
| 12 | Audit record: who builds it, what `ref` points at, and how a retried activity avoids a duplicate row | medium | AD-6, AD-8 |
| 13 | The fact payload has provenance fields but no content fields | medium | AD-14, AD-15 |
| 14 | `is_medical` and `confidence` mean different things per contender | medium | AD-7, AD-13 |
| 15 | Loading arithmetic and "low confidence" for `refer` are not defined on either side of the answer key | medium | AD-12, AD-15, AD-17 |
| 16 | Upload: split timing, orphan cases, non-JSON body | medium | AD-2, AD-3 |
| 17 | Smaller items | low | various |

---

## 1. The contracts package has no owner and no operation list — critical

**Units:** any two services on either end of an arrow, for example `workflow` and `extraction`.

**What each builds.** The `workflow` builder writes its `httpx` client first and needs a request and result model for "extract facts from page". It adds `ExtractPageCommand {case_id, page_id}` and `ExtractPageResult {fact_ids: list[str]}` to `packages/contracts/` and posts to `/cases/{case_id}/pages/{page_id}/extractions`. The `extraction` builder, on its own branch, adds `ExtractionRequest {case_id, page_id, document_id}` and `ExtractionSummary {fact_count, unverified_count, audit}` and serves `/cases/{case_id}/pages/{page_id}/facts`.

**Why both comply.** The convention says only that the package holds "pydantic models for every cross-service payload". AD-6 says commands carry ids and "small result summaries". The path convention gives "plural resource nouns, nested by owner id" and nothing more. Neither builder broke a rule.

**Collision.** Two models for one call, two paths, merge conflicts in the one shared folder, and the same thing fourteen more times (one per arrow). The error code catalogue and the audit action catalogue ("...") are in the same position: named, not written, not owned. This is the root cause of most holes below.

**Closing rule.** Add an AD: `packages/contracts/` is built first, by one named owner, before any service epic starts, and it is frozen except through that owner. Add to the spine an operation table with one row per arrow: caller, callee, method and path, request model, response model, idempotency key, sync or async. The rules proposed below fill most rows.

## 2. Human decisions have an owner but no legal write path — critical

**Units:** `workflow` (lifecycle epic) and `classification` (gate epic); `web` is a third party to the same clash.

**What each builds.**
- `workflow`: on the `accepted` external event it sets page status, writes `page.accepted` to `audit_event` in one transaction (AD-8) and moves on. It calls nothing, because AD-10 says "no stage API exposes" keep, discard, accept or deny.
- `classification`: AD-4 and the entity list make it owner of "triage and customer decisions" (`HUMAN_DECISION`). It builds `POST /cases/{case_id}/pages/{page_id}/decisions` and waits for `workflow` to command it (AD-2), or, reading AD-10 strictly, builds the table and no write route at all.
- `web`: AD-10 says keep, discard, accept and deny are "`web` routes that raise an external event on the case's orchestration". A literal builder adds `durabletask-azuremanaged` to `web` and raises the event itself. Another calls `workflow` over Dapr (diagram: "commands").

**Why all comply.** AD-4 gives ownership; AD-10 forbids the stage API; AD-2 says stage state changes only on a `workflow` command; AD-10's wording allows either reading of who raises the event.

**Collisions.**
1. `classification.human_decision` is never written, or is written by a route `workflow` never calls. Meanwhile `workflow` holds the same truth twice (page status `denied`, audit `page.denied`). Three records of one decision, one of them empty.
2. AD-8 says `ref` is "the id of the owning record". For `page.accepted` the owning record is the human decision, whose id only `classification` can generate ("generated by the owner"). `workflow` cannot fill `ref` without the forbidden call.
3. AD-6 keys every stage command by `case_id` plus `page_id`. A record-decision command and the classify command for the same page share that key. So do the customer's `keep` and the underwriter's later `accept` on the same page. An idempotent `classification` returns the stored `keep` when asked to record `accept`.
4. If `web` raises the event directly, it needs a Durable Task Scheduler role assignment the deployment diagram does not give it, and `workflow`'s domain check on `actor_kind` (AD-10) never runs.
5. A double click on Accept: Durable Task buffers the second event. Nothing says whether it is dropped, applied to a later wait, or answered with an error.

**Closing rule.** Move `HUMAN_DECISION` to `workflow` (it is a lifecycle fact, and the audit event already is its record); delete "triage and customer decisions" from `classification`'s row. Fix the one path: `web` calls `workflow` `POST /cases/{case_id}/pages/{page_id}/decisions` with `{decision: keep|discard|accept|deny, actor}`; only `workflow` talks to the scheduler; the event name is `page_decision:{page_id}`; a decision for a page not in the matching `awaiting_*` status returns 409 `page_not_awaiting_decision` and raises nothing.

## 3. Compare toggle and per-configuration verdict runs — critical

**Units:** `workflow` (lifecycle epic), `verdict` (agent epic), `web` plus SPA (result-view epic).

**What each builds.**
- `workflow`: one orchestration per case; after extraction it runs one verdict activity with the default `retriever_config` setting, then completes.
- `verdict`: a run is keyed by `case_id` and `retriever_config` (AD-15); it serves `POST /cases/{case_id}/verdict-runs {retriever_config}` and assumes someone sends the second config when Compare is on.
- SPA and `web`: Compare toggle on the result screen posts `/api/cases/{case_id}/verdict-runs` with the second config and polls.

**Why all comply.** AD-15 says only "the Compare toggle is two runs of the same case". AD-11 says the default is a setting. AD-5 says one orchestration per case with instance id `case_id`.

**Collisions.**
1. `web` may only read `verdict` (diagram), and `verdict` changes state only on a `workflow` command (AD-2). So `web` must ask `workflow`. But the case's orchestration has completed; a completed instance takes no external event, and a second instance cannot reuse `case_id` as its id (AD-5).
2. Nobody knows which two configs Compare compares. The spec says "pgvector against Azure AI Search"; the ladder has four pgvector rows and two Search rows.
3. AD-17 wants the runner to drive the pipeline "once per `retriever_config`" through `web`. The config is an environment setting; the runner cannot change it through `web`. The builder either redeploys six times, or uploads the 20 cases six times. Six uploads re-run classification and extraction with a non-deterministic model, so the six rows are scored on different facts, which breaks "only the component under test differs".
4. A page accepted late adds facts after a verdict run exists; AD-6 makes the re-run return the stale stored result.

**Closing rule.** The start-case command takes `retriever_configs: list` (default: the one setting). The orchestration runs one verdict activity per listed config after all pages are terminal. Compare and the runner use one more command, `web` to `workflow` `POST /cases/{case_id}/verdict-runs {retriever_config}`, which `workflow` runs as a sub-orchestration with instance id `{case_id}:verdict:{retriever_config}`. Name the Compare pair as two settings on `web` (suggest `r4` and `r5`). The retrieval bake-off uploads each case once and requests six runs. A verdict run starts only when no page is in a non-terminal status.

## 4. Eval runner: forbidden writes, human waits and unwanted extraction — critical

**Units:** `evals/` (runner epic), `web`, `classification`, `workflow`.

**What each builds.**
- Runner: uploads the labelled page set through `web`, reads each page's classification, computes accuracy, calibration and queue rate, posts scores "to `classification`" through `web` (the only public door, AD-18).
- `web`: exposes eval routes for `retrieval` only, because the diagram says `web -->|reads, eval runs| retrieval` and `web -->|reads| classification`.
- `classification`: accepts state changes only from `workflow` (AD-2: "the one write that does not come from `workflow` is the upload").
- `workflow`: knows nothing about eval runs; every uploaded page goes through the gate.

**Why all comply.** AD-17 states the runner's path and where scores go. The diagram and AD-2 state who may write. They contradict each other, and each builder followed the one that binds its unit.

**Collisions.**
1. Posting classifier scores needs a `web` to `classification` write that the diagram forbids, and both score posts are stage writes not commanded by `workflow`, which AD-2 forbids.
2. "Once per classifier contender": the contender is a setting (AD-13). The runner cannot switch it. If it re-uploads after a redeploy, fine; if it re-classifies the same pages, AD-6's key (`case_id`, `page_id`, no contender) returns contender one's stored result as contender two's.
3. The "same path a user takes" sends every high-confidence medical page on to extraction and a verdict run, and leaves every other page waiting for a customer or underwriter forever. The bake-off pays for extraction it does not score, and leaves dozens of orchestrations open and the triage queue full of eval pages on demo day.
4. Queue rate needs the route. AD-7 forbids copying the threshold into the runner, so the runner must read the route from `workflow`'s page status, which again requires the page to pass the gate.
5. Cost per page, monthly cost and effort cannot be measured by the runner, yet AD-17's purpose is to stop "numbers typed in by hand".

**Closing rule.** Amend AD-2: eval score posts are a second named exception, `web` to `retrieval` and `web` to `classification`, on `POST /eval-runs`; redraw the `classification` arrow as "reads, eval runs". The start-case command takes `classifier_contender` (default: the setting) and `stop_after: gate | none`; `workflow` passes the contender in the classify command and it joins the AD-6 key. With `stop_after: gate` the orchestration records the route in page status and ends. Cases started by the runner carry `eval_run_id`, and the triage queue and case list exclude them. Cost and effort are declared as hand-entered fields of the eval run with a `source` note.

## 5. Stage commands: synchronous or not, and in-flight retries — high

**Units:** `workflow` (activities) and `verdict` or `classification` (long stages).

**What each builds.** `workflow`: each activity is "one thin call" (AD-5), a blocking `httpx` post with a 30-second timeout and Durable Task's retry policy. `verdict`: an agent loop to a step limit takes one to several minutes; the builder either blocks the request for that long or returns 202 with a `verdict_run_id` and expects a poll or callback. `classification`'s LLM contender runs the model several times per page (AD-13) and has the same choice.

**Why both comply.** AD-6 says only that repeating a command "returns the stored result and writes nothing new". It does not say what happens when no result is stored yet.

**Collision.** With a blocking stage, the activity times out and retries while run one is still going; nothing is stored, so run two starts: two agent runs, double token spend, two sets of `agent_step`, and a race on the unique key. With a 202 stage, `workflow` treats 202 as done and starts nothing to collect the result; AD-5 forbids polling loops for human waits and says nothing here. Container Apps ingress also cuts requests at 240 seconds.

**Closing rule.** Every stage command is a blocking call that returns the result summary. The stage inserts its key row in status `running` under a unique constraint before doing work; a repeat while `running` returns 409 `in_progress`, a repeat after completion returns 200 with the stored result, and a stored failure is re-run. `workflow` treats 409 `in_progress` as retry-with-backoff. Set one client timeout per stage as a `WORKFLOW_` setting (suggest 200 s) and a verdict step limit that fits inside it.

## 6. Page files, page images and thumbnails — high

**Units:** `intake` and `classification`; `intake` and `web`/SPA.

**What each builds.**
- `intake`: `GET /cases/{case_id}/pages/{page_id}/text` returning JSON, and the whole PDF for download. AD-3 says calls are "HTTP and JSON", so no binary route between services.
- `classification`: the Document Intelligence contender must submit a file, so it expects a single-page PDF or PNG from `intake` ("page content"). The LLM contender may be built on page text or on a page image; the builder picks.
- SPA triage screen: CAP-3 needs a thumbnail. One builder renders it in the browser with `react-pdf` from the whole case PDF; another expects `GET /api/cases/{case_id}/pages/{page_id}/thumbnail` from `intake` (which has `pymupdf`).

**Why all comply.** The diagram labels the arrow "page content" and stops. AD-14 covers text only. No AD names a thumbnail producer.

**Collision.** `classification` asks for bytes `intake` does not serve; or gets base64 inside JSON that `intake` never built. The two contenders see different inputs (text for one, image for the other), which the fairness constraint forbids. The triage queue has no thumbnail, or downloads every full PDF to draw one.

**Closing rule.** `intake` serves three binary reads, exempt from the JSON rule: the document PDF (`application/pdf`), a single-page PDF per page, and a PNG render per page at a fixed width (suggest 1,000 px; the SPA scales it down for thumbnails). `web` proxies the PDF and PNG. Both classifier contenders take the same input, the page PNG (or both the single-page PDF); state which. Upload is the one multipart request.

## 7. Search results carry no text; `read_rule` and "open the rule" are undefined — high

**Units:** `retrieval` and `verdict`; `retrieval` and the SPA result view.

**What each builds.**
- `retrieval`: result items with exactly the AD-11 fields: `rule_ids`, `chunk_id`, `rank`, `score`, `retriever_config`, `latency_ms`. No text. For "rule read" it returns the `smart` chunk for that `rule_id`, since only a `smart` chunk "holds exactly one rule".
- `verdict`: the agent's `search_rules` tool expects snippets to reason over; `read_rule(rule_id)` is expected to return the rule as the current configuration sees it.
- SPA: "clicking a reason opens its manual rule" (CAP-7). One builder shows chunk text in a side panel; another opens the manual PDF at the rule's page and needs a manual page number and a `web` route for the manual PDF.

**Why all comply.** AD-11 lists the item fields and AD-15 says only that `read_rule` accepts a `rule_id` from a search in the same run. `RULE` "is not a table", and the rule table is unreadable by `retrieval`.

**Collisions.**
1. The agent gets ids and scores but no content from search, so it must call `read_rule` for everything, or the two builders disagree on a `text` field.
2. If `read_rule` always serves the `smart` chunk, an `r1` run reads clean one-rule chunks after a `fixed`-chunk search, and the `r1` versus `r2` verdict-accuracy gap, the point of ladder rows 1 and 2, mostly disappears. If it serves `fixed` chunks under `r1`, a rule split across two chunks has no single answer.
3. Row 6 (agentic retrieval) returns a synthesised answer with references and no per-item score; its `latency_ms` includes model planning. Whether `rank`, `score` and `latency_ms` are per item or per query is open.
4. The spec's chunk metadata includes `impairment`; the spine's chunk does not, and the rule table that knows it is off limits.
5. The SPA has nothing to open: no rule-read response shape, no manual page number.

**Closing rule.** A search response is `{retriever_config, latency_ms, items[]}` with `latency_ms` once per query; each item adds `text`, `section_id`, `manual_page` and `impairment` (parsed from the manual heading at ingestion). `r6` returns its references as items, `score` nullable, synthesised answer discarded. One rule-read operation, `GET /rules/{rule_id}?retriever_config=`, returns the chunks of that config's chunk set that carry the id, with `text` and `manual_page`; `verdict` and `web` both use it; the SPA shows the text in a panel.

## 8. Manual ingestion and DI classifier training have no trigger and no label access — high

**Units:** `data/` (synthetic-data epic), `retrieval`, `classification`, `infra/`.

**What each builds.**
- `data/`: renders the manual PDF from the rule table, printing ids in its own style ("Rule DM2-003", or "R-017" in a table cell), with cross-references in prose ("see HTN-002").
- `retrieval`: a parser with its own regex for "the `rule_ids` printed inside its text span"; ingestion as a start-up hook, a CLI, or a `POST /ingestions` route, the builder's choice.
- `classification`: a Document Intelligence custom classifier needs a trained model: labelled sample files per class in Blob Storage.
- `infra/`: creates or does not create the Search index, the agentic knowledge base, the Blob containers.

**Why all comply.** AD-12 says what ingestion produces, not who starts it. AD-2 says a stage changes state only on a `workflow` command; `workflow` has no reason to ingest a manual. AD-17 says the answer key, including page labels and the rule table, is "read only by the eval runner".

**Collisions.**
1. No one starts ingestion; or `retrieval` exposes a route no caller on the diagram may use. The manual sits in `data/manual/` in the repository while `retrieval` "owns the manual PDF" in Blob; no step moves it.
2. The id format is unfixed, so the parser misses ids. Cross-references put a second `rule_id` on a chunk that "holds exactly one rule", so an expected rule is "found" through a chunk that only mentions it and recall is inflated.
3. Training the DI classifier needs page labels that AD-17 locks to `evals/`. Training on the labelled page set and then scoring on it is leakage; the LLM contender sees no training data. An expert reviewer will ask.
4. `classification` has no Blob access in the deployment diagram, and no one owns the trained model id.
5. The manual generator itself must read the rule table that only `evals/` may read.
6. Index and knowledge-base creation: Terraform or `retrieval` code, undecided; `r6` also needs a model connection, and AD-16 allows only the one chat deployment.

**Closing rule.** Ingestion and classifier training are pipeline jobs, not APIs: `python -m retrieval.ingest` and `python -m classification.train`, run after migrations, each idempotent, both named in AD-2 as set-up writes. Fix the `rule_id` format as a regex in the contracts package and a definition marker (a rule is defined where its id starts a heading; ids elsewhere are references and do not enter `rule_ids`). Add `data/classifier-training/`, disjoint from the scored page set, readable by the training job; reword AD-17 to "read only by `evals/` and the `data/` generators". `retrieval` code creates the Search index and knowledge base; Terraform creates only the service. The trained model id is a `CLASSIFICATION_` setting.

## 9. Screen assembly: triage queue, case list, statuses — high

**Units:** `web`/SPA and `workflow`; `web` and `classification`.

**What each builds.**
- `workflow`: `GET /cases/{case_id}/pages` with statuses, as the path convention prescribes ("nested by owner id").
- `web`: the triage queue is all pages awaiting triage across all cases (AD-9: no scoping). It needs a cross-case query, then type, confidence and reason from `classification`, then a thumbnail from `intake`.
- `classification`: `GET /cases/{case_id}/pages/{page_id}/classifications` returning a list, since a page has many classifications.
- SPA: polls for a case status to know when to show the result view; shows an error badge when a page fails.

**Why all comply.** AD-19 says `web` "assembles each screen by calling the owning services". The enum row lists page statuses only.

**Collisions.**
1. No cross-case read exists under the path convention; `web` would have to list every case from `intake` and fan out.
2. No case list operation and no case status enum exist, although `workflow` "owns case status". The SPA, `web`, `workflow` and the eval runner each invent values (`running`, `complete`, `awaiting_input`, ...).
3. The page status enum has no failure value and nothing for the classifying or verdict phases. A page whose model output fails validation (an error by convention) stays `uploaded` and the badge spins forever.
4. "The" classification of a page is ambiguous when there are several rows (repeated runs, two contenders).
5. Nothing says when the verdict starts. One reading: when every page is terminal, so a customer who never answers a discard-or-keep prompt blocks the verdict for good. Another: as soon as one page is extracted, so the verdict misses later pages.
6. `uploaded` exists before `workflow` has heard of the case; `intake` holds no status, so the first poll returns 404.

**Closing rule.** Add to the enum row: page status `classifying` and `failed`; case status `processing`, `awaiting_human`, `judging`, `suggested`, `failed`. `workflow` serves `GET /cases` (id and status), `GET /cases/{case_id}` (case and page statuses, verdict run ids) and `GET /pages?status=`, the one allowed un-nested read. `classification` serves one current classification per page and accepts a batch of `page_id`s. The verdict runs when every page is `extracted`, `discarded`, `denied` or `failed`. Start-case creates the `uploaded` rows synchronously before it returns.

## 10. Quote highlight uses a second reading of the page — high

**Units:** `extraction` plus contracts (Python) and the SPA (TypeScript).

**What each builds.** `extraction` verifies with the Python normalisation function against `pymupdf` text held by `intake`. The SPA "highlights by finding the quote on that page of the rendered PDF" (AD-14), that is, in the pdf.js text layer, with its own TypeScript normalisation.

**Why both comply.** AD-14 says normalisation "is one function in the contracts package" and also tells the SPA to find the quote in the rendered PDF. A Python function cannot run in the browser.

**Collision.** pdf.js and `pymupdf` differ on spaces, ligatures, hyphenation, line order and table cell order. A fact shown as `quote_verified` fails to highlight, on the one interaction the success signal names. Rotated and handwritten edge pages have no text layer at all.

**Closing rule.** Do not search in the browser. `intake` stores word boxes with the page text; `extraction` stores the match's character span; `intake` serves `GET .../pages/{page_id}/boxes?start=&end=` returning rectangles in page coordinates; the SPA draws them. If that is too much for four days: keep the browser search, ship the normalisation as a TypeScript port with a shared test-vector file in the contracts package that both implementations must pass, and fall back to a page-level highlight when no match is found.

## 11. Per-fact recall and latency are not computable — high

**Units:** `evals/` and `verdict`; `evals/` and `retrieval`.

**What each builds.** Runner A reads `verdict.agent_step` through `web` and scores the searches the agent made. Runner B calls `retrieval`'s search through `web` once per expected fact from the answer key. `verdict` logs tool calls as the framework emits them: query text and results, no `fact_id`. `retrieval` takes "a fact query", read as free text by one builder and as a fact object by another; it cannot take a `fact_id` because it may not call `extraction`.

**Why all comply.** AD-17 says the runner uses "the same path a user takes"; the diagram gives `web` "eval runs" on `retrieval`; `bake-offs.md` defines recall "per fact"; AD-15 logs "every tool call" with no shape.

**Collision.** The agent may search zero, one or three times for a fact, or once for two facts, so "top 5 per fact" has no denominator. Runner B's direct calls give clean recall but on answer-key facts, not extracted ones, and bypass the agent, so recall and verdict accuracy come from two different paths and the six rows are not comparable to what the demo shows.

**Closing rule.** Split the two metrics by path and say so in AD-17. Recall and latency: the runner calls `web` `POST /api/retrieval/searches` once per expected fact per config, with a query string built by one function in the contracts package from the fact's fields; `verdict`'s `search_rules` tool builds its query with the same function and takes `fact_id` as its argument. Verdict accuracy: from the verdict runs of hole 3. `agent_step` records `fact_id`, `retriever_config`, `chunk_id`s and `latency_ms` per search.

## 12. Audit record: builder, `ref`, duplicates — medium

**Units:** `workflow` and any stage service.

**What each builds.** A stage returns ids only (AD-6) and `workflow` composes the audit row; or the stage returns a full audit record ("every stage result ... carries one audit record") including `occurred_at` and `actor`. `extraction` returns a list of `fact_id`s; `workflow` wants one `ref`.

**Collision.** `occurred_at` is "set by the service that did the work" but `workflow` has no field to receive it. `ref` for `facts.extracted` is a "fact set", which is not an entity and has no id; `classification_id` and a decision id are missing from the id convention. A retried activity whose first attempt committed writes a second identical row; append-only forbids cleaning it up. The `actor` string format (`classification:gpt-5.4`?) is free, and the DI contender has no "model deployment name".

**Closing rule.** Every stage result is `{ref, occurred_at, actor, summary}`; `workflow` copies the first three into the row. Add `classification_id` and `extraction_id` (one per page extraction) to the id convention. `audit_event` has a unique constraint on (`action`, `ref`), and an insert conflict is ignored. `actor` for AI is `<app-id>/<deployment-or-model-id>`.

## 13. The fact has no content fields — medium

**Units:** `extraction`, `verdict`, SPA, `data/` answer key.

**What each builds.** AD-14 lists `fact_id`, `page_id`, `page_number`, `quote`, `quote_verified`. `extraction` adds `{name, value, unit}`; `verdict`'s prompt expects a sentence; the answer key records expected facts in a third form; the runner must match extracted to expected facts.

**Collision.** Four shapes for the central payload. `page_number` is ambiguous between document and case once a case has more than one document (the entity diagram allows it; no flow creates one).

**Closing rule.** Fix the fact in the contracts package: `fact_id`, `case_id`, `document_id`, `page_id`, `page_number` (within the document), `impairment` (from a fixed list shared with the manual), `attribute`, `value`, `unit`, `quote`, `quote_verified`. The answer key uses the same model. One upload creates one case with one document.

## 14. `is_medical` and `confidence` differ per contender — medium

**Units:** the two contenders inside `classification`; `data/` labels; `workflow`'s gate.

**What each builds.** The LLM contender's confidence is agreement across N runs, on `page_type`. With N = 5 only 1.0 clears a 0.90 threshold; two runs saying `lab_report` and three saying `attending_physician_statement` give 0.6 and a trip to triage for a page every run called medical. The DI contender returns the service's confidence for the document type. The mapping from `page_type` to `is_medical` is unwritten: is `application_form` medical? `synthetic-data.md` draws medical pages from case PDFs that contain application forms.

**Collision.** The gate and the calibration metric compare two different quantities, and the answer key's label for application forms may disagree with the service's mapping. Queue rate then measures N, not the classifier.

**Closing rule.** `confidence` is confidence in `is_medical`, for both contenders (LLM: share of runs agreeing on `is_medical`; DI: sum of class confidences on the winning side). The mapping `page_type` to `is_medical` is one table in the contracts package, used by both contenders and by the answer key. N is a setting with a stated default (suggest 10, so one dissent still clears 0.90).

## 15. Loading arithmetic and `refer` triggers — medium

**Units:** `verdict` and `data/` (expected verdicts).

**What each builds.** `verdict` cannot read the rule table, so debits come from chunk text through the model; the builder sums debits. The data builder computes expected verdicts from the rule table with its own combination (sum, maximum, or a cap). "Low confidence" forces `refer`, but no verdict confidence is defined.

**Collision.** Expected and suggested loadings differ by construction; whether verdict accuracy compares `loading_pct` is unstated; "low confidence" is implemented from nothing.

**Closing rule.** `read_rule` output is parsed into a contracts model `{rule_id, outcome: debit|decline|none, loading_pct}`; the loading is the sum of distinct triggered rules' debits; verdict accuracy compares the verdict enum only and reports loading match separately. Define the forced-`refer` conditions as code-checkable: any deciding fact unverified, no rule found for a fact, both a decline and a non-decline rule for one impairment, or the step limit reached. Drop "low confidence" or define it.

## 16. Upload: split timing, orphans, body type — medium

**Units:** `web`, `intake`, `workflow`.

**What each builds.** `intake` returns `case_id` at once and splits pages in the background; `workflow` asks for the page list on start and gets none. Or `web`'s start call fails after `intake` succeeded and the user re-uploads.

**Collision.** An orchestration over zero pages completes at once; orphan cases with no orchestration appear in lists; a retry makes a duplicate case. The upload is multipart, against AD-3's "HTTP and JSON".

**Closing rule.** The upload call returns only after the PDF is stored and all pages and page texts exist, and returns `case_id`, `document_id` and `page_ids`. Start-case is idempotent on `case_id`. A case with no page status rows is shown as `failed`, with a retry that calls start again.

## 17. Smaller items — low

- **pgvector extension.** No one owns `CREATE EXTENSION vector` or the server allow-list; `retrieval`'s migration and Terraform will each assume the other. Rule: Terraform allow-lists it; the pipeline's migration role creates it before `retrieval`'s migrations.
- **Scale to zero under polling.** Six services wake on the first poll; cold starts will look like failures to a 30-second activity timeout. Rule: `min_replicas = 1` during any eval or demo run, stated as a runner precondition.
- **Display names.** The spec's prompt says "utility bill (96%)"; the enum has no such type. Rule: `page_type` display names live in the SPA; the prompt shows the enum's display name.
- **Deferred items that let builders diverge.** The reranker for `r4` is deferred, but its choice changes AD-16 (a second model) and the row's latency and cost; decide it before the retrieval epic starts, not when `r4` is built. With no `EXPERIENCE.md`, the Compare pair, the polling interval and the eval-case filter are each one builder's guess; holes 3, 4 and 9 fix them here instead.
- **Eval run storage shape.** "Retrieval eval runs" and "classifier eval runs" have owners but no fields. Rule: one contracts model, `{eval_run_id, started_at, contender_or_config, metrics{}, per_item[]}`, used by both.
- **Dapr body limit.** Sidecar HTTP requests default to 4 MB; a case PDF passed from `web` to `intake` can exceed it. Rule: set the limit in the `app` stack and state a maximum upload size that `web` enforces.
