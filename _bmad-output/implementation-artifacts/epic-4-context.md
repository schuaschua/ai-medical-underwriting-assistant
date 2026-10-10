# Epic 4 Context: The classifier comparison names a winner on measured numbers

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Add an Azure AI Document Intelligence custom classifier as the second contender behind the classifier interface the LLM contender already uses, assemble a labelled page set for scoring and a separate one for training, and have the runner score both contenders so the classifier scoreboard names a winner on measured numbers. This epic is the first to be cut if time runs short. There is no UX design; screens are plain.

## Stories

- Story 4.1: Scored page set and separate training set
- Story 4.2: Document Intelligence classifier trained and available
- Story 4.3: Runner scores both classifiers and the scoreboard shows the winner

## Requirements & Constraints

- **Contenders:** `llm` (page types from the chat model, confidence from agreement across repeated runs) and `doc-intelligence` (a custom classification model). Only the classifier differs; both see the same labelled pages, one page at a time as a one-page document.
- **Same result shape:** `page_type`, `is_medical`, `confidence` (0 to 1), `reason` and `contender`. Page types: `lab_report`, `attending_physician_statement`, `application_form`, `id_document`, `invoice`, `other`; the first three are medical, by the one mapping in contracts.
- **Scored page set:** medical pages from the case set; non-medical pages (invoice, payslip, passport, recipe, utility bill); edge pages (blank, rotated, handwritten doctor's note, a mixed file of medical and non-medical pages). Every page has its expected label in the answer key. Synthetic data only.
- **Training set:** in `data/classifier-training/`, sharing no page with the scored set, with at least five pages per `page_type`.
- **Training pages pass through the same redaction as case pages** before training, so the classifier is trained and scored on the same kind of page (masked identifiers included). They are placed in the Blob container `classifier-training`, which `classification` owns.
- **Training job:** a one-off, idempotent Container Apps job on the `classification` image and identity, started by the pipeline, calling no other service.
- **Per case:** a case is started with one `classifier_contender`; the gate routes on that contender's result only. A page classified by both contenders has two stored results, one each.
- **Scoring:** the runner uploads the scored set once per contender, started with that contender, an `eval_run_id` and `stop_after` `gate`, so no extraction runs.
- **Metrics:** accuracy (medical against non-medical correct); calibration (of pages scored 0.90 or more, the share labelled correctly); queue rate (share of pages routed to the triage queue); cost per page. Winner: the more accurate contender among those with calibration of at least 0.90, then the lower queue rate.
- **Published as a file:** `data/scoreboards/classification.json`, written by the runner and served read-only by `web`; no service stores or accepts scores.
- The routing threshold stays only in `workflow`; `classification` never routes. Logs carry ids, codes and timings, never page text.

## Technical Decisions

**Owner rules (project `CLAUDE.md`)**

- **Azure stays down while coding.** Training and calling a custom classifier can only be proven in Azure: write them against a fake or a stand-in and add each Azure check to `deferred-work.md` for the one final test session.
- **Small test suite:** about 500 Python cases in all, one test per acceptance criterion plus the few edge cases that guard a real risk; merge or remove weaker tests to stay inside the budget. Budgets: `classification` 40, `synthdata` 45, `workflow` 110, `web` 45, `contracts` 60. `web` and `synthdata` are already slightly over.

**What the earlier epics built that this epic stands on**

- **One interface with a contender field.** The classify command is keyed by case, page and contender, and the stored result carries `contender`. The contracts enum already holds `doc-intelligence`; the service's set of contenders it can run holds only `llm`, and a command naming `doc-intelligence` is refused today as `validation_failed` before any key row. Adding the contender means adding it to that set and giving it its own path to the same result.
- **The `llm` contender** reads the page's stored text and its 320-pixel thumbnail from `intake`, runs the model `CLASSIFICATION_CLASSIFIER_RUNS` (5) times, and stores what the runs agree on with the agreeing share as confidence. No temperature or seed is set; whether real runs ever differ is unverified. If they never do, confidence is always 1.0 and the calibration figure says nothing.
- **Stage pattern to keep:** key row inserted as `running` before work, one deadline for the whole command, the stored result returned on a repeat, 409 `in_progress` while running, a failed result as a stored 200. Model output is parsed into a contracts model; a failed parse is a failed result.
- **`workflow`:** the default contender is a setting (`llm`). `stop_after: gate` already works: the case ends `completed` after routing, its waiting pages stay as routed and take no decision, and they are left out of the triage queue, as is every case with an `eval_run_id`. Start options are accepted through `web` only from the underwriter role.
- **Two contenders on screen:** the triage queue already shows the classification of the case's own contender. The customer's prompt still takes the first classification listed for a page and must pick the case's contender once two exist.
- **Redaction** is one `intake` operation keyed by case (Azure AI Language document redaction, entity mask); only `intake`'s identity may call the Language service. Nothing redacts a page outside a case today.
- **Identities already planned:** `classification` holds Cognitive Services User on Document Intelligence and Blob Data Contributor on `classifier-training`; Document Intelligence's own identity may read that container. `retrieval` calls Document Intelligence over REST with `httpx` and an Entra token, not the SDK; follow that adapter's shape.
- **What the page generator in `packages/synthdata` can draw today:** application form, attending physician statement, lab report, invoice, payslip, utility bill and blank page, each with a real text layer and a "synthetic" footer (the owner decided to keep the footer). Payslip, utility bill and blank map to `other`. Rotation is only the PDF's rotation flag on an upright page, so extracted text is unchanged; drawing truly rotated content is recorded for story 4.1. It cannot yet draw a passport (the only source of `id_document` pages), a recipe or a handwritten note, and it draws no images or scans. `case-002` is already a mixed file; `case-003` holds the blank and rotated pages. Output is deterministic: a second run changes nothing, and a test keeps the committed files equal to what the generator writes.
- **Answer key:** each case entry lists per page `page_type`, `is_medical`, `layout` (finer than the page type) and `rotation`.
- **Guard from story 1.4:** nothing under `services/` or `packages/contracts/` may name `synthdata` or the answer key. Tests that read the answer key or run a stand-in live in `packages/synthdata/tests`; service tests use fakes.
- **Stand-ins in `packages/synthdata`** (dev only, loopback only): Language redaction, layout, chat and embeddings. The chat stand-in tells page types apart by the headings the generator prints and has modes in which runs disagree. There is no stand-in for a Document Intelligence classifier.
- **Load:** every page of a case is classified at once, five model runs each, against a chat deployment of 100 thousand tokens a minute; `workflow` runs 5 activities at once. A scored set uploaded as large files will meet these limits.

## UX & Interaction Patterns

- Classifier scoreboard: both contenders' accuracy, calibration, queue rate and cost per page, with the winner marked. Plain styling; wording in the strings module; read through the SPA's one API client with `X-Demo-Role`.

## Cross-Story Dependencies

- 4.1 supplies the pages 4.2 trains on and 4.3 scores. Its medical pages come from the case set that story 3.1 completes; only three cases exist until then.
- 4.2 needs redaction (Epic 1) for the training pages and can only be proven in Azure.
- 4.3 uses the runner and the scoreboard file serving that Epic 3 (stories 3.4 and 3.5) builds; if Epic 3 is not done first, 4.3 must create the runner in `evals/` itself.
