---
title: 'Story 2.4: Fact extraction with checked quotes'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'e9382757925e85928fa948f7ef6834adf41f017d'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
  - '{project-root}/docs/standards/azure.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A page that passed the gate or was accepted in triage sits at `extracting` for ever: no medical fact is read from it, so there is nothing for a verdict to rest on and nothing an underwriter can check against the document.

**Approach:** Build the `extraction` service and have `workflow` command it for every page that reaches `extracting`. The model proposes facts, each with a verbatim quote; code checks every quote against the page text `intake` stored and marks the fact verified, with offsets, or unverified. `workflow` records each result, the page becomes `extracted`, and the case ends when every page is final. `workflow` also stops depending on the browser to wake a waiting case.

## Boundaries & Constraints

**Always:**
- Spine AD-14, AD-16, AD-6, AD-8, AD-5, AD-3, AD-4; the stage and model-gateway patterns of Epic 1 as the epic context states them. `POST /fact-sets` is keyed on `case_id` + `page_id`; `GET /cases/{case_id}/facts` lists a case's facts in page order.
- `extraction` reads only the page text `intake` stores (the redacted reading) and the page's number. One model answer per page, parsed into the contracts' extraction output; an answer that fails validation is a failed result (`invalid_model_output`), never passed on. A page with no medical fact is a done result with no facts.
- Quote check, in domain code, by one shared function in the contracts package: a quote is found when the normalised quote occurs in the normalised page text; the function answers the offsets of the match in the page text as stored (not the normalised text), counted the way `intake`'s word boxes count them, and the unit is stated beside it. A fact is `quote_verified` only when its quote is found, and then carries `quote_start` and `quote_end`; when the quote occurs more than once the first occurrence is taken. A fact whose quote is not found is stored and flagged, never dropped, never shown as verified, and never given offsets.
- No masked value is a fact: a proposed fact whose statement holds a mask token (such as `[Person]`), or whose quote is nothing but mask tokens and punctuation, is not stored; the number left out is logged as a count. A quote may contain a mask token beside real text.
- The audit record of a done result is `facts.extracted`, actor `extraction:<chat deployment name>`, the page, ref the fact set's id. A repeat of the command answers the stored result with no model call.
- `workflow`: every page that reaches `extracting` (by the gate, or by an accept) gets one extraction command; its result is recorded through the one recording path and the page becomes `extracted`. A failed extraction fails the page and the case. The case status follows the pages in the recording of every stage result too, so the last page becoming final completes the case, with its `case.completed` event, through the one path that writes it. The orchestration ends when the case is final.
- A waiting case no longer depends on the browser: `workflow` itself notices a stored decision whose event the orchestration has not been told, and tells it again until it is told, without polling inside the orchestration and without timers in it. A case that fails while its orchestration waits has that orchestration ended.
- The orchestration's body changes in this story. Nothing is deployed and the emulator keeps no state, so it is changed in place; the notes say so, and say how the next change must be made once a deployed case can be waiting.
- Logs carry ids, codes, counts and timings: never page text, quotes, statements or the model's output.

**Never:**
- No verdict run and no retrieval call (story 2.5). No `web` route or SPA change for facts (story 2.7); the page badges already word `extracting` and `extracted`.
- The model never decides verification, offsets, page numbers or ids: code sets them.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something. Do not edit `infra/demo/foundation`. No stand-in ships in a service image.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Extract a page | Command for a page at `extracting` | Facts stored, each with `fact_id`, `page_id`, `page_number`, `statement`, `quote`, `quote_verified`; result `done` with the fact ids and the unverified count; `facts.extracted` event; page `extracted` | N/A |
| Quote found | The quote occurs in the page text, differing only in case, spacing or line breaks | `quote_verified` true; offsets such that the page text between them, normalised, equals the normalised quote | N/A |
| Quote not found | The model returns a quote that is not on the page | Fact stored, `quote_verified` false, no offsets; counted in `unverified_count` | N/A |
| Quote found twice | The same words occur twice on the page | Verified, with the first occurrence's offsets | N/A |
| Quote across a table row | A lab value whose label and number sit in different cells | Verified if the stored page text holds them in that order; otherwise unverified, never a guess | N/A |
| Masked value | The model proposes "Name: [Person]" as a fact | Not stored; counted in the log | N/A |
| Quote with a mask beside text | "[Person] has type 2 diabetes" | Stored and checked like any other | N/A |
| No facts | A page with nothing medical | Result `done`, no facts, event recorded, page `extracted` | N/A |
| Invalid model output | Not the contract's shape | Result `failed` with `invalid_model_output`; nothing stored | Page and case fail with `stage.failed` |
| Model unavailable, deadline, unknown page, repeat while running, repeat after the end | As for classification | As for classification: `model_unavailable`, `stage_timeout`, 404, 409 `in_progress`, the stored result | As for classification |
| Accepted later | A page accepted in triage after others were extracted | Extracted then; the case completes when the last page is final | N/A |
| Last page final | Every page is `extracted`, `discarded` or `denied` | Case `completed`, `case.completed` last in the trail, the orchestration ended | N/A |
| Decision stored, event lost | The event raise failed and nobody repeats the decision | `workflow` tells the orchestration by itself; the page goes on | Bounded retries with a log line each |
| Case fails while waiting | A page's extraction fails while another page waits for a person | Case `failed`; the orchestration ends; the waiting page takes no decision | N/A |
| Read facts | `GET /cases/{case_id}/facts` | The case's facts in page order, then in the order they were stored | Empty list for none; 404 is not used for a case with no facts |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/models/extraction.py` -- `ExtractedFact`, `ExtractionOutput`, `ExtractFactsCommand`, `FactSetResult`, `Fact`, `FactList`; `text.py` -- `normalise` (keeps mask tokens; the new quote finder belongs beside it); `models/intake.py` -- `PageText`, `WordBox` (`char_start`, `char_end` index the page text); `operations.py` -- `extract_facts`, `list_facts`, `read_page_text`; `audit.py` -- `FACTS_EXTRACTED`, `ai_actor`, the rule that ties `case.completed` to its path
- `services/classification/` -- the service to copy in shape, sharing nothing by import: `domain/classify.py` (key row, deadline, stale row, release on a passing fault or a cancelled request, stored result with its audit record), `adapters/model.py` (the gateway), `adapters/dapr.py` (reads `intake`), `adapters/db.py`, `adapters/http/`, `adapters/telemetry.py`, `prompts/`, `settings.py`, `migrations/`, `Dockerfile`, tests with a gateway stub
- `services/intake/src/intake/adapters/pdf.py`, `domain/pages.py` -- how page text and word offsets are produced, and how an offset range becomes boxes; the deferred item on text order in tables points here
- `services/workflow/src/workflow/adapters/orchestration.py` -- its header states the versioning rule; the per-page waits after the gate; `adapters/scheduler.py` -- `classify_page` as the model of a page stage activity, `SchedulerEngine.decision_made`; `adapters/dapr.py` -- `StageClient`; `domain/recording.py`, `transitions.py` (a done extraction moves `extracting` to `extracted`); `adapters/db.py` -- `_record` (refuses a recording that completes a case: completion goes through `_follow_pages`), `decide`, `settle_case`; `domain/decisions.py` -- `record_decision` (stores, then tells the engine); `human_decision` table; `adapters/http/app.py` -- the service's lifespan, where the worker starts
- `packages/synthdata/src/synthdata/foundry_standin.py` -- the model stand-in (classification, context lines, embeddings): add an extraction answer derived from the page text, with modes for a quote that is not on the page, a masked value and invalid output; `cases.py` -- what the synthetic pages hold; `tests/support/synthdata_stack.py`
- `infra/demo/app/` -- how `classification` was added (Container App, identity, AcrPull, Monitoring Metrics Publisher, Foundry User); `infra/bootstrap/README.md` -- the database role sections; `dapr.yaml`, `tools/dev.sh`, `tools/migrate-local.sh`, `pyproject.toml`, `.github/workflows/ci.yml`, `deploy.yml`, `README.md`

## Tasks & Acceptance

**Execution:**
- [x] `packages/contracts/` -- the quote finder (normalised match, offsets into the stored text, the unit stated) with tests on case, spacing, line breaks, ligatures, mask tokens, repeated quotes and no match
- [x] `services/extraction/` -- the service: settings; domain (extract operation with the stage pattern, the quote check, the masked-value rule, the result with its audit record, the read); ports; adapters (Dapr client to `intake`, model gateway, repository, HTTP routes, telemetry); prompt; migration `0001`; probes; `Dockerfile`; tests with a gateway stub
- [x] `services/workflow/` -- extract command and activity; the orchestration: pages at `extracting` are extracted, waits and extractions run side by side, the case ends when it is final; the case status and `case.completed` in the recording of a stage result; the notice-and-tell-again of stored decisions (a mark on the decision that the orchestration was told, and a task of the service that tells those not yet told); ending the orchestration of a failed case; tests
- [x] `packages/synthdata/` -- the extraction answer in the model stand-in with its modes; cross-service tests: a synthetic case run to `completed` with facts whose quotes are verified against the real page text, a table page among them; the lost-event case
- [x] `dapr.yaml`, `tools/`, `README.md`, `pyproject.toml`, `.github/workflows/` -- the service in the local run, the checks and the image build
- [x] `infra/demo/app/` -- the `extraction` Container App with its identity and roles, and a new README section for its database role
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- mark what this story closes (the quote finder, the lost event, the completion by stage result, the text order of a table page); append the Azure checks
- [x] Tests for every matrix row

**Acceptance Criteria:**
- Given the local stack with the stand-ins, when `case-001` is uploaded and started, then its three pages end `extracted`, the case is `completed`, every stored fact has a page number and a quote, and every verified fact's offsets select text on the page that equals its quote once both are normalised.
- Given a stand-in told to return a quote that is not on the page, when the case runs, then that fact is stored unverified with no offsets and the case still completes.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- **Quote finder.** `contracts.text.find_quote(page_text, quote)` answers a `QuoteMatch(start, end)` or `None`. It normalises the page piece by piece (a piece is the smallest run of characters that NFKC treats on its own) and keeps, for every normalised character, the stored range it came from; the first occurrence that stands whole (its ends on piece boundaries, and not inside a word or a number) is the answer, and `normalise(page_text[start:end]) == normalise(quote)` is checked before it is given. The unit is stated beside it as `QUOTE_OFFSET_UNIT = "unicode_code_point"` (Python string indices, as `intake/adapters/pdf.py` counts `char_start` and `char_end`), in half-open ranges. Also there: `MASK_TOKEN_PATTERN`, `has_mask_token`, `is_only_mask_tokens`.
- **`services/extraction/`** is `classification`'s shape with nothing imported from it: key row `fact_set` (unique on case and page), table `fact` (one row per fact, `position` for the stored order, a check constraint that ties offsets to verification), migration `0001`, port 8005, Dapr app id `extraction`, sidecar port 3505 locally. One model call per page with the structured output `extraction_output`; a page whose stored text is blank is done with no facts and no call. A single entry of the answer that cannot be a fact is left out and counted; an answer cut off at the token limit (the gateway reads the finish reason) fails the page with `invalid_model_output` and the log reason `answer_cut_off`. `check_facts` sets everything but statement and quote, with one `QuoteFinder` per page. A key row left `running` past its deadline and stale margin is taken over by the next repeat and the page extracted, not settled as failed: extraction has no side effect before it finishes. The settings refuse a model worst case (attempts and waits) that does not fit inside the stage deadline.
- **`workflow`.** `Recording.follows_pages` is set by `plan_recording` for a done page stage result; `SqlCaseStore._record` then calls `_follow_pages` after the result's own event, so `case.completed` is written there and is the trail's last event. The orchestration (changed in place; its header says why that was safe and how the next change must be made) starts an `extract_facts` activity for every page that is `extracting` after the gate or after an accept, waits for decisions and extractions together with `when_any`, and returns when the case is final or failed. A failed extraction therefore ends the orchestration while other pages' waits are still open: that is how "a case that fails while its orchestration waits has that orchestration ended" is met; nothing terminates an orchestration from outside.
- **Telling a decision again.** Migration `0006` adds `workflow.decision_told` (decision id, time; SELECT and INSERT only), a table of its own so that `human_decision` stays append-only. `record_decision` marks a decision once its event was raised. `tell_untold_decisions` (domain) raises the events of unmarked decisions older than the grace time and marks them; `tell_decisions_again` (app lifespan, beside the worker) runs it every `WORKFLOW_DECISION_TELL_INTERVAL_SECONDS`. No decision is given up: after a failed look the wait doubles up to `WORKFLOW_DECISION_TELL_MAX_INTERVAL_SECONDS`. The engine answers whether there was an orchestration to tell (`Told`); when there is none, or a dead one, the decision is not marked and the case is failed through `fail_case`. The orchestration marks the case failed if a page is in a status it has no next step for.
- **Stand-in.** `synthdata.foundry_standin` answers extraction requests (told by the schema name) with the labelled values and table rows of the synthetic medical pages, quoting the page's words joined by spaces; modes `quote_not_on_page` and `masked_value` were added beside `invalid` and `throttled`.
- **Tests of earlier stories.** Extraction now follows every page that reaches `extracting`, so tests of stories 1.6 to 1.13 that expected such a case to stay `running` with pages `extracting` were changed to expect `extracted` and `completed` (and the `facts.extracted` and `case.completed` events), or hold the stand-in stage's answer (`FakeStages.extraction_hold`) where the earlier state is the point. The orchestrator drivers of the unit tests answer extraction tasks through `workflow_fakes.finish_extractions`. Test helpers that classified and routed page by page now classify every page first, as the lifecycle does.
- **Not done here.** No run with real Dapr sidecars (`./tools/dev.sh`): another local run of the application was using the ports while this was built. `tools/migrate-local.sh` was not run against the shared local database either, so that run was not disturbed. `terraform init -backend=false` was run in `infra/demo/app` to install the new module call for `validate`; nothing was planned or applied.

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | The quote finder matches inside a word or number: "4 %" is found in "7.4 %", so a fact with the wrong value is stored as verified (blind, implementer) | high | `find_quote` takes any substring of the normalised text; the implementer recorded it as an owner question, but a verified wrong value defeats the check | patch |
| 2 | The map from normalised to stored text is wrong for some inputs: a letter with two combining marks, and a character whose compatibility form starts with a space; a quote that is on the page comes back not found, and no test compares the map with `normalise` (blind, edge x2) | medium | Both confirmed by the edge reviewer by running the function; the failure is on the safe side (unverified) but wrong | patch |
| 3 | The invisible characters in `text.py` and in tests became raw literals (blind, gap) | medium | `_INVISIBLE` holds a literal soft hyphen, joiners and byte order mark; an editor can drop them unseen | patch |
| 4 | A mask token is recognised by a narrow shape: `[US_SSN]`, `[PERSON_1]` or a lower-case token would not be, and their values could be stored as facts (edge, blind) | medium | `MASK_TOKEN_PATTERN` is `\[[A-Z][A-Za-z]+\]`; the real service's tokens are unverified, and missing one stores an identifier's placeholder as a fact | patch (widen); the real shape is an Azure check |
| 5 | One proposal that breaks the contract (a line break in a statement, an empty quote, a NUL) fails the page and the case, and an answer cut off at the token limit reads as garbage (blind, edge) | medium | `_propose` refuses the whole answer; `_answer_of` never reads `finish_reason`; a stored failed result is never retried | patch |
| 6 | The model's retry budget can exceed the stage deadline, so an outage is stored as `stage_timeout`, not `model_unavailable`, and nothing validates it (blind) | low | Defaults: four attempts of 60 s against 180 s; direct validation in the settings | patch |
| 7 | The tell-again task gives up for good: after 20 failed looks (about five minutes of scheduler outage) a decision is skipped until the process restarts; and a decision is marked told when the orchestration is missing or dead, so nobody looks again (blind x2, edge) | high | `tell_untold_decisions` counts per look into an in-memory skip set; `decision_made` returns normally for a missing instance and the mark is written. Both recreate the stuck case this story set out to remove | patch |
| 8 | A page whose status is none of the expected ones is silently skipped by the orchestration, which then ends with the case `running` (edge) | medium | `orchestration.py`, the loop over settled pages | patch |
| 9 | `begin` can answer "inserted" when the conflicting key row was released between its two statements; a request cancelled while `begin` commits leaves the row unowned; a process that died mid-page fails the case 240 s later though extraction has no side effect before it finishes (edge x2, blind) | medium | `extraction/adapters/db.py` and `domain/extract.py`; the pattern is classification's, where a call is short | patch |
| 10 | `check_facts` rebuilds the page's map for every proposed fact (blind) | low | One `find_quote` call per proposal; direct correction | patch |
| 11 | Nothing pins the finder going on to a later occurrence after a half-character candidate, nor the tell-again settings reaching the task the app starts (gap x2) | medium | Filed with evidence | patch |
| 12 | The bootstrap section grants `extraction`'s role UPDATE and DELETE on every table though facts are never changed, and names the wrong service in one sentence (blind) | low | `infra/bootstrap/README.md` section 8; direct corrections | patch |
| 13 | Three new `workflow` files were untracked and missing from the reviewed diff (blind, gap) | low | True of the review file; the files exist, were read by one reviewer from the tree, ran in the full suite, and are added with the commit | fixed by the main session |
| 14 | No run with real Dapr sidecars, and `tools/migrate-local.sh` not run, with no tracked item for either (blind) | medium | True: a leftover local stack holds the ports and the owner has been asked to stop it | defer |
| 15 | A quote that is a label and a mask token ("Patient name [Person]") is stored when the statement avoids the token (blind) | low | The intent leaves out a fact whose statement holds a token or whose quote is only tokens; such a fact names no value | reject |

## Design Notes

- **Azure checks for `deferred-work.md`:** the extraction prompt on the real model (do quotes come back verbatim, how many are unverified on the synthetic cases, are masked values left alone); token use per page against the shared rate limit; the new activities and the tell-again task on the real scheduler; the `extraction` deploy, identity and database role step.
- **Offsets.** Normalisation changes lengths (case folding, NFKC, collapsed spaces), so the finder must map a match in the normalised text back to positions in the stored text. Build the map while normalising, not by searching again.
- **Telling the orchestration again.** The decision is stored by the HTTP call and the event is raised after it. Mark each decision when the raise succeeded; a small task of the service looks, at an interval that is a setting, for decisions not marked after a grace time, and raises their events. That is a timer in the service, not in the orchestration (AD-5 forbids only the latter).
- **Where the case completes.** In this story a case completes when its last page is final. Story 2.5 puts the verdict runs before that point.
- The working tree holds uncommitted work of story 2.2 (`services/retrieval/`, `packages/synthdata/src/synthdata/layout_standin.py`, additions to `foundry_standin.py`, `infra/demo/app/`, `dapr.yaml`, `tools/`, CI and deploy workflows, `README.md`, `deferred-work.md`), which is in review at the same time and may still change. Leave that work as it is: add beside it in shared files without rewriting it, do not touch `services/retrieval/`, and if a test of `retrieval` or of the manual ingestion fails in a full run, report it and do not fix it.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation, and it is over the 1,600-token target because the story adds a service and changes the lifecycle.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run` -- expected: all pass
- `docker build -f services/extraction/Dockerfile -t aiuw-extraction:dev . && docker build -f services/workflow/Dockerfile -t aiuw-workflow:dev .` -- expected: both build
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
