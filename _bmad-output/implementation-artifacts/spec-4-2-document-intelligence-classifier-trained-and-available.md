---
title: 'Story 4.2: Document Intelligence classifier trained and available'
type: 'feature'
created: '2026-10-08'
status: 'ready-for-dev'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/data/README.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
  - '{project-root}/docs/standards/azure.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Only the LLM classifies pages, so the classifier comparison has one contender and nothing to compare.

**Approach:** Add the `doc-intelligence` contender behind the same classifier interface: a custom classification model of Azure AI Document Intelligence, trained once by a job from the training set and asked one page at a time. Azure stays down: training and classifying are built against a local stand-in.

## Boundaries & Constraints

**Always:**
- Spine AD-13, AD-7, AD-16, AD-21; FR15. The contender returns the same fields as the LLM contender (`page_type`, `is_medical` by the contracts' one mapping, `confidence` 0 to 1, `reason`, `contender`) and keeps the stage pattern: key row before work, one deadline, the stored result on a repeat, a failed result as a stored answer. A page classified by both contenders has two stored results, and nothing of one contender's result reaches the other.
- The page is sent as a one-page document: `intake` gains one read that answers one redacted page as a one-page PDF, for `classification` only. `classification` still reads no blob of a case.
- The model's confidence is the service's own for the page's document type. A document type that is no `page_type`, or an answer without one, is a failed result. The `reason` is a fixed sentence naming the classifier and the type it gave, never page content.
- **How training pages are redacted (decided by the coding agent, for the owner to confirm):** the training job calls no other service, as the spine says, and only `intake` may call Azure AI Language. So the pages are redacted before the job, by the pipeline itself: a dev tool beside the bake-off runner uploads each training page through `web` as a case of an eval run (so it is redacted as any case page and hidden from the queues), fetches the redacted file `web` serves, and writes it with its label into a local folder. An operator uploads that folder to the `classifier-training` container, as the manual is uploaded to its container. The tool talks to `web` only and is in no image.
- The training job runs on the `classification` image and identity, reads the labelled pages from `classifier-training`, asks Document Intelligence to build the classifier from that container, waits for it, and ends 0. It is idempotent: when the classifier of the configured id exists it trains nothing. It refuses to train when a page type has fewer than five pages or a page has no label. The classifier id and the API version are settings.
- The contender is available where `classification` is given a Document Intelligence endpoint and a classifier id; elsewhere a command naming it is refused as today. `workflow` routes a case on the result of the contender it was started with, and the customer's prompt shows that contender's result.
- Identity only, no key: `classification` holds Cognitive Services User on Document Intelligence and Blob Data Contributor on `classifier-training`; Document Intelligence's own identity reads that container. Logs carry ids, codes, counts and timings, never page text.
- Tests follow the owner's rule in `CLAUDE.md`, inside the budgets.

**Never:**
- No scoring of the contenders and no scoreboard (story 4.3). No change to the LLM contender or to the gate's threshold. No unredacted training page in the container, and no training page that is also a scored page.
- The job is not started by the deploy; the stand-ins ship in no image. Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something. Do not edit `infra/demo/foundation`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Classify with the new contender | A redacted lab report page, contender `doc-intelligence` | A stored result: `lab_report`, medical, the service's confidence, the fixed reason, `contender` set | N/A |
| Both contenders | One page classified by each | Two stored results, one per contender; the gate used the one the case was started with | N/A |
| Unknown type | The service answers a type that is no `page_type`, or none | A failed result | Stored as failed, like a bad model answer |
| Service down or slow | Document Intelligence unavailable, or past the classify deadline | No result invented | A failed result with the stage's codes |
| Not configured | No endpoint or no classifier id | The command is refused; `llm` unaffected | 422 `validation_failed` |
| Training pages prepared | The training set, the local stack | One redacted one-page PDF per training page, with its label | A page whose case fails stops the tool, naming the page |
| Train | The container holds the labelled redacted pages; no classifier yet | The classifier of the configured id is built; the job ends 0 | N/A |
| Train again | The classifier exists | Nothing is trained; the job ends 0 | N/A |
| Too few pages | A page type with four pages | Nothing is trained | The job ends 1 with its own reason |
| One page read | `classification` asks `intake` for page 2 of a redacted file | A PDF of that one page | 404 for an unknown page; 409 `not_redacted` before redaction |

</frozen-after-approval>

## Code Map

- `services/classification/src/classification/domain/classify.py` -- `AVAILABLE_CONTENDERS` (only `llm`), `classify_page` (the refusal of an unknown contender), `_classify`, `_run_model`, `_store_done`, `_fail`: there is no per-contender path yet; `domain/ports.py` (`PageModel`, `PageReader`, `ClassificationRepository`), `entities.py` (`PageContent`: text and thumbnail only); `adapters/dapr.py` (`IntakeClient`), `adapters/model.py`, `adapters/credential.py`; `settings.py` (no Document Intelligence or blob setting yet); `__main__.py` (the service only); the Dockerfile copies the service's source alone
- `services/retrieval/src/retrieval/adapters/layout.py` -- `DocumentLayout`: REST to Document Intelligence with an Entra token, `_submit`, `_wait`, `_look`, the retry and the reasons logged: the model for the classifier client. `services/retrieval/src/retrieval/ingest.py` and `module "retrieval_ingest"` in `infra/demo/app/main.tf` (a manually started Container Apps job on a service's image and identity, `caj-<suffix>-ingest`): the model for the training job. `retrieval`'s blob adapter for `manual`: the model for reading a container
- `packages/contracts/src/contracts/operations.py` -- `intake`'s reads (`read_page_text`, the thumbnail, `read_document_file`); `services/intake` -- where the redacted file of a case is read and a thumbnail is made from one page (the place for a one-page PDF); `domain/entities.py` (the redacted file's blob path)
- `data/classifier-training/` -- 46 unredacted one-page PDFs in six folders by `page_type`, and `pages.json` (`file`, `page_type`, `layout`); `packages/synthdata/src/synthdata/training.py`
- `evals/src/bakeoff/` -- `client.py` (`WebClient`: upload, start with `StartCaseOptions`, progress), `state.py`, `settings.py`, `__main__.py`: what the preparing tool reuses; `web` already serves the redacted document file
- `packages/synthdata/src/synthdata/layout_standin.py` -- routes under `/documentintelligence/`, `Mode`: the model for classifier routes (build, operation status, read, analyze); `language_standin.py` -- the local redactor
- `services/workflow/src/workflow/settings.py` (`default_classifier_contender`), `domain/gate.py`; `services/web/spa/src/cases/classifications.ts` (`found[page_id] ??= classification`: the first classification listed is taken, recorded in `deferred-work.md` for this story), `services/web/src/web/adapters/http/triage.py` (already by contender)
- `infra/demo/app/main.tf` -- `classification` has no Document Intelligence role and no `classifier-training` role yet, and Document Intelligence's own identity has no reader role there; `local.foundation.document_intelligence_*`, `storage_container_ids`; `dapr.yaml`, `tools/`, `infra/bootstrap/README.md` (section 7: how the manual is uploaded and its job started)

## Tasks & Acceptance

**Execution:**
- [ ] `packages/contracts/`, `services/intake/` -- the one-page PDF read
- [ ] `services/classification/` -- the classifier client, the contender's path to the same result, availability and settings, the training job as a second entry point of the image
- [ ] `packages/synthdata/` -- the stand-in's classifier routes (build from a container, status, analyze by the generator's headings), with failure modes
- [ ] `evals/` -- the tool that prepares the redacted training pages through `web`
- [ ] `services/web/spa/`, `services/workflow/` if needed -- the customer's prompt shows the case's own contender's result
- [ ] `infra/demo/app/`, `dapr.yaml`, `tools/` -- the roles, the job, the settings; the local start can train and classify against the stand-ins
- [ ] Tests, inside the budgets: classify with the new contender, both contenders on one page, an unknown type, not configured, the job (train, again, too few pages), the one-page read; one cross-service test of a case started with `doc-intelligence`
- [ ] `README.md`, `infra/bootstrap/README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- the seeding steps; the Azure checks; the choices for the owner

**Acceptance Criteria:**
- Given the training set, when its pages are prepared and the training job runs, then the pages have passed the same redaction as case pages, are in the `classifier-training` container, and a custom classification model is trained from them.
- Given a case started with `classifier_contender` `doc-intelligence`, when a page is classified, then it is sent as a one-page document and the stored result has the same fields as the LLM contender's, with `contender` set.
- Given one page classified by both contenders, when the results are read, then two results exist for the page and the gate used the one the case was started with.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Design Notes

- **Why the pages are prepared outside the job:** the spine says the job calls no other service, only `intake` may call the Language service, and `classification` may write only its own container. The pipeline is the one redactor; the operator's upload is the one hand-over, as for the manual. The owner was asked on 2026-10-08 and may choose otherwise: the alternatives are a job that calls `intake`, or `classification` calling the Language service itself.
- **Azure checks for `deferred-work.md`:** the build request and its API version; the least pages per type the service takes; that Document Intelligence's identity can read the container; how long training takes; the document types and confidences it answers for a one-page PDF; that redacted pages (masked tokens, the "synthetic" footer) still train a useful classifier; the cost of preparing 46 pages through the pipeline.
- Preparing the pages runs each through the gate, so the LLM classifies them too; `stop_after` `gate` keeps extraction off.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Do not run `tools/dev.sh` unless nothing else holds its ports, and stop what you start. Tests that use the scheduler emulator fail each other when two runs overlap: run them once at the end.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07, and again on 2026-10-08, for the remaining stories to be built in order; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services evals && uv run pytest --cov` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run lint && npm --prefix services/web/spa test -- --run` -- expected: clean
- `docker build -f services/classification/Dockerfile -t aiuw-classification:dev . && docker build -f services/intake/Dockerfile -t aiuw-intake:dev .` -- expected: both build; the classification image holds no training page
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
