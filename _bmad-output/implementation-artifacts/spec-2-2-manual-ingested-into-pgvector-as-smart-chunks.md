---
title: 'Story 2.2: Manual ingested into pgvector as smart chunks'
type: 'feature'
created: '2026-10-07'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '74e2c45197687500c0badc3cf388263e48683354'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
  - '{project-root}/docs/standards/azure.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The manual exists only as a PDF: nothing a search could use holds its rules.

**Approach:** Create the `retrieval` service and its one-off ingestion job. The job reads the manual PDF from the `manual` container, parses it with Document Intelligence's layout model, cuts it into `smart` chunks of exactly one rule each, has the chat model write a context line per chunk, embeds each chunk, and stores the chunk records in the `retrieval` schema, the same on every run. Because the Azure environment stays down while coding, layout parsing, the chat model and embeddings are proven against local stand-ins.

## Boundaries & Constraints

**Always:**
- Spine AD-12, AD-16, AD-4, AD-1; the stage and gateway patterns of Epic 1 as the epic context states them. The ingestion job runs on the `retrieval` image and identity, is started by hand or by the pipeline, and calls no other service of ours.
- A `smart` chunk holds exactly one rule: the rule's definition with the text that belongs to it, its parent section id, the impairment, the manual page its definition is on, and one context line written by the chat model that says where the rule sits in the manual. Its `rule_ids` are only the rules its text defines (the contracts' `rule_ids_defined_in`), never rules it refers to. The rules it refers to are stored separately, as references.
- `chunk_id` is derived from the chunk set and the rule, so it is the same on every run and can be the same in another store (Epic 3). The chunk record is the one source for every store: id, chunk set, text, context line, rule ids, references, section, impairment, manual page, embedding.
- The embedded text is the context line followed by the chunk text. Vectors come from the one embedding deployment (`text-embedding-3-large`, 3,072 dimensions), named only in settings, through the service's one model gateway. No approximate index: search will be exact.
- Idempotent: a second run over the same manual leaves the chunk set, the ids, the text and the vectors as they were, and makes no model call for a chunk whose text is unchanged. A changed manual replaces what changed and removes chunks whose rule is gone, in one transaction per run.
- The job checks its own result and fails loudly: a rule id defined twice, a definition with no text, a page with no text, or zero rules is an error, not a partial index.
- The table is ready for hybrid search: the chunk text also has a stored full-text column with an index, built by the migration, so story 2.3 needs no second migration for it.
- The rule table and everything else under `data/answer-key/` is never read by the job or the service and is in no index. The job learns the rules only from the manual.
- Logs carry ids, counts and timings, never manual text (it is synthetic, but the rule holds for every log).

**Never:**
- No search or rule-read operation (story 2.3): the service has only its probes for now. No `fixed` chunk set and no Azure AI Search (Epic 3).
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something. Do not edit `infra/demo/foundation`. No stand-in ships in a service image.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First run | The manual in the `manual` container, an empty table | One `smart` chunk per rule the manual defines, each with section id, impairment, manual page, context line and a 3,072-dimension vector | N/A |
| Count against the table | The ingested chunks and the rule table (in a test outside `services/`) | Every `rule_id` of the table is defined by exactly one chunk; no chunk defines a rule the table lacks | N/A |
| Cross-reference in a chunk | A rule's text mentions another rule | That rule is in the chunk's references, not in its `rule_ids` | N/A |
| Second run | Same manual | Same ids, text and vectors; no chat or embedding call | N/A |
| Changed manual | One rule's text changed, one rule removed | That chunk rewritten and embedded again; the removed rule's chunk gone; the rest untouched | A failure leaves the previous index whole |
| Layout parsing fails | Document Intelligence answers an error or times out | The job ends non-zero with a code; the index is untouched | N/A |
| Model unavailable | Chat or embedding fails after its retries | As above | N/A |
| Bad model output | A context line that is empty, multi-line or too long; a vector of the wrong size | Refused; the job fails | N/A |
| Manual missing | No blob of the configured name | The job ends non-zero saying so | N/A |
| Service probes | `retrieval` running with the schema at head | `/health` 200, `/ready` 200; not ready when the schema is behind | N/A |

</frozen-after-approval>

## Code Map

- `services/classification/` -- the newest full service and the pattern to copy in shape, sharing nothing by import: `settings.py`, `adapters/model.py` (the gateway: Entra token, retries with backoff, spans per attempt, token usage, process-wide cap), `adapters/db.py`, `adapters/telemetry.py` (`adapter_span`), `adapters/http/`, `migrations/`, `Dockerfile`, `prompts/`, tests with a gateway stub
- `services/intake/src/intake/adapters/blob.py`, `credential.py` -- Blob access with the service identity or the emulator's connection string; `adapters/language.py` -- a REST client to an Azure AI service with an Entra token, the model for the Document Intelligence call (the spine pins `azure-ai-documentintelligence` 1.0.2; use it if it fits the patterns, otherwise REST with `httpx2`, and say which in the notes)
- `packages/contracts/src/contracts/rules.py` -- `RULE_DEFINITION_PATTERN`, `rule_ids_defined_in`, `RULE_ID_PATTERN`; `models/retrieval.py` -- the search item's fields (`chunk_id`, `rule_ids`, `text`, `manual_page`, `impairment`) that the chunk record must be able to fill; `enums.py` -- any chunk-set or retriever enum that exists
- `packages/synthdata/src/synthdata/` -- the manual generator of story 2.1 (what the section headings, numbers and definition markers look like in the PDF's text); `foundry_standin.py` (chat completions only today: add embeddings, and a context-line answer); `language_standin.py` (the shape of a stand-in for an Azure AI service: add one for Document Intelligence layout that reads the PDF with PyMuPDF and answers in the service's result shape); `tests/support/synthdata_stack.py`
- `data/manual/` -- the PDF to ingest; `compose.yaml` -- `pgvector/pgvector:0.8.2-pg17` and Azurite; `tools/migrate-local.sh`, `tools/dev.sh`, `dapr.yaml`
- `infra/demo/app/` -- how `classification` was added; foundation outputs for the Foundry endpoint, model deployment names (`embedding`), Document Intelligence (id, endpoint, principal id), storage containers, `runtime_identities["retrieval"]`. Roles from `docs/standards/azure.md`: AcrPull, Monitoring Metrics Publisher, Foundry User, Cognitive Services User on Document Intelligence, Storage Blob Data Contributor on `manual` for `retrieval`; Storage Blob Data Reader on `manual` for Document Intelligence's own identity
- `infra/bootstrap/README.md` section 6 -- the database role steps to mirror; `.github/workflows/ci.yml`, `deploy.yml`

## Tasks & Acceptance

**Execution:**
- [x] `services/retrieval/` -- the service skeleton (settings, probes, telemetry, database adapter, migration `0001` with the `vector` extension, the chunk table and its full-text column and index, `Dockerfile`); domain: the chunker (one rule per chunk from parsed layout, section and page tracking, references), chunk ids, the ingestion plan (what to add, rewrite, remove) and its checks; adapters: Blob reader for the manual, Document Intelligence layout client, model gateway for the context line and embeddings, repository; the job's entry point (`python -m retrieval.ingest` or the package's equivalent); prompt for the context line; tests with fakes
- [x] `packages/synthdata/` -- stand-ins for Document Intelligence layout and for embeddings (deterministic vectors in which texts that share words are close), the context-line answer in the model stand-in; tests; one cross-service test that ingests the real `data/manual` PDF and compares the chunks with the rule table
- [x] `tools/`, `dapr.yaml`, `README.md`, `pyproject.toml`, `.github/workflows/ci.yml`, `.github/workflows/deploy.yml` -- the service and the job in the local run (upload the manual to the emulator, run the ingestion), the checks, the image build
- [x] `infra/demo/app/` -- the `retrieval` Container App, the ingestion job as a Container Apps job on the same image and identity, the role assignments, the settings; a new README section for the database role and for uploading the manual and starting the job
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- append the Azure checks this story cannot run
- [x] Tests for every matrix row

**Acceptance Criteria:**
- Given the local stack with the stand-ins and the manual uploaded, when the ingestion job runs, then the `retrieval` schema holds exactly one `smart` chunk per rule of the rule table, and running it again changes nothing and calls no model.
- Given the repository, when it is searched, then nothing under `services/` reads or names the answer key, and the guard test covers `services/retrieval`.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- **Document Intelligence: REST with `httpx2`, not the SDK.** The call is one submit and a poll; written out (`retrieval/adapters/layout.py`) it follows the other adapters: a transport a test replaces, no redirect followed, an Entra token from the service's `EntraToken`, a span per step, a retry that can be seen. The manual is sent as bytes (`base64Source`). `azure-ai-documentintelligence` is not a dependency.
- **The chunk** is the definition paragraph, from its marker to the end of the paragraph; a paragraph with two markers gives two chunks. `chunk_id` is `smart-<rule_id>`. `section_id` is the numbered part the definition is printed in (`2.4`), `impairment` the heading of its section. Sections are found by their printed numbers in order (the contents page lists them first, so the count may begin again at 1); page furniture by role, or by repeating on more than half the pages.
- **Unchanged-chunk detection:** `content_hash` is over the rule as the chat model is shown it (section, part, text), the prompt's digest and the two deployment names. A rule that only moved to another page is updated without a model call.
- **Full-text column:** `text_search`, generated and stored, `english`, GIN index. Rule ids are written as one word before parsing (`UW-DM-001` to `UWDM001`), because the parser splits them at their dashes; story 2.3 must write a query the same way (`retrieval.adapters.db.full_text_of`).
- **Errors:** the job uses the contracts' catalogue and a `reason` in its log line; no contracts change (another change to contracts is in review).
- **Local run:** `tools/migrate-local.sh` migrates and uploads the manual, `tools/ingest-local.sh` runs the job against the stand-ins (model on 5101, layout on 5102), `tools/dev.sh` does both and adds `retrieval` (8004, sidecar 3504).
- **Infrastructure:** `module.retrieval` and `module.retrieval_ingest` (AVM `avm-res-app-job` 0.2.2; `caj` added to the names table of `azure.md`), six role assignments, section 7 of `infra/bootstrap/README.md`. Validated, never planned.
- **After review:** headings are taken only where they are ones (the layout's heading role when roles are present; in order, none twice, none skipped) and the chunker checks its cut without the rule table (one id code per section, every referred rule defined, definitions end at a sentence's end). Migration `0001` also creates `retrieval.ingest_run` (manual SHA-256, prompt digest, deployments of the last successful run): an unchanged manual ends the run before Document Intelligence is called. A run reads the store and checks the schema first, makes one small embedding call before the context lines, refuses to remove more than `RETRIEVAL_INGEST_MAX_REMOVED_SHARE` (0.1) of the stored chunks without `RETRIEVAL_INGEST_ALLOW_LARGE_REMOVAL`, and stores under an advisory lock. `retrieval` holds Storage Blob Data Reader on `manual`; Document Intelligence holds no storage role.
- **Not done:** the deploy does not start the job (it needs the manual database step first). The Azure checks are in `deferred-work.md`.

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | A stray numbered line ("3 months", "1 to 2 episodes") is taken for a section heading, the real heading is then refused, and every later rule is stored under the wrong section and impairment with the run reporting success (blind, edge x2, gap) | high | `_Place.take_heading` in `chunker.py` accepts any short paragraph that starts with the next number or with 1; the real layout result is unproven | patch |
| 2 | The job has no check of its own for a wrong section, though the rule id's code gives one without the answer key; a rule mentioned but never defined, a definition given a furniture role, and a heading merged into a definition paragraph all pass silently (blind, edge x3) | high | `cut_chunks` checks none of these; each is the trace a miscut by the real service would leave | patch |
| 3 | A partly miscut manual deletes good chunks: everything the parse did not find is removed, and the only floor is zero rules (blind) | medium | `plan_ingestion` | patch |
| 4 | The job spends the layout analysis before it knows the database is usable, and every chat call before the first embedding call (blind) | medium | `ingest_manual`, `write_records`; a wrong deployment name wastes 111 calls and keeps nothing | patch |
| 5 | A second run still sends all 206 pages to Document Intelligence, and the index cannot say which manual it was built from (blind) | medium | Nothing stores the manual's hash; the planned "run it twice" check in Azure pays twice | patch |
| 6 | The layout client: failed polls are invisible and unbounded, the submit ignores the parse deadline and an uncapped `Retry-After`, and a failed analysis loses the service's error code (blind, edge) | medium | `adapters/layout.py` `_look`, `_submit`, `_wait` | patch |
| 7 | Two runs at once plan from their own earlier read and can undo each other (blind, edge) | medium | No lock in `SqlChunkRepository.apply`; the job module's `parallelism` bounds replicas, not executions | patch |
| 8 | The job does not flush telemetry before it exits, and its root span carries no outcome (blind) | medium | `ingest.py` returns straight after `asyncio.run`; a short process may end before the batch is sent | patch |
| 9 | `retrieval` gets Storage Blob Data Contributor on `manual` though nothing writes there in Azure, and Document Intelligence gets a reader role nothing uses because the manual is sent as bytes (blind, implementer) | medium | `infra/demo/app/main.tf`; least privilege (`azure.md` rule 9). The roles table in `azure.md` names both and must follow | patch |
| 10 | The job's deadline is a Python default repeated as a literal in Terraform; the deploy checks the service's image and not the job's; the bootstrap step can leave the operator with a data role when the upload fails (blind, edge) | medium | `variables.tf`, `deploy.yml`, `infra/bootstrap/README.md` section 7 | patch |
| 11 | A `TimeoutError` from elsewhere is reported as the job's deadline; a settings failure before the `try` prints a traceback that may hold values; the deadline can fire during the commit and report "unchanged"; an embedding answer with wrong indexes is paired with the wrong chunks (blind, edge x3) | medium | `ingest.py` `run` and `main`; `adapters/model.py` | patch |
| 12 | Furniture found by repetition, and the content hash covering the rule's section, are seen by no test (gap x2); repetition could also take a part heading or a repeated definition for furniture (edge) | medium | Filed with evidence | patch |
| 13 | `tools/ingest-local.sh` takes whatever listens on the stand-ins' ports for a stand-in (blind) | low | A TCP check only; direct correction | patch |
| 14 | A chunk holds the definition paragraph only, narrower than "the definition with the text that belongs to it" (blind, implementer) | false | Since story 2.1's review every definition is self-contained (impairment, section, threshold, rating, note, references, source), which is the text that belongs to the rule; the rating table and worked examples belong to the section. A cheap guard against a definition cut short is added under finding 2 | reject |
| 15 | The local scripts are checked as text, never run (gap) | low | Filed as defer by the reviewer; earlier stories check them the same way | reject |
| 16 | A scope constant and a `Retry-After` reader exist twice; `EntraToken` lives in the database adapter (blind) | low | No caller diverges | reject |
| 17 | Tracking files disagree; `uv.lock` was not in the reviewed diff (blind) | false | The sprint status is synced at the end; the lock file was left out of the review file on purpose and is checked by `uv sync` and the image build | reject |

## Design Notes

- **Azure checks for `deferred-work.md`:** the real layout result for the manual (reading order, tables, page numbers, whether headings come back as roles) and whether the chunker's cuts hold on it; the Document Intelligence call with the service identity and whether it reads the blob by address or needs the bytes; the embedding call and its limits for about a hundred chunks; whether the real context lines are useful; the `vector` extension being creatable by the migration role; the job's run in Container Apps; the database role step.
- The chunker works from the parsed layout, not from the generator's knowledge: it finds definitions by the contracts' pattern and sections by their printed numbers. That keeps the answer key out and makes the stand-in replaceable by the real service.
- Every page of the manual carries a header (`Synthetic Underwriting Manual`, `Page N`) and the synthetic-document footer in its text layer. They are page furniture: no chunk text may contain them (story 2.1's review).
- Unchanged-chunk detection: store a hash of the chunk text with the model and deployment names used; a rerun embeds only where the hash differs.
- The working tree holds uncommitted work of another change that is in review at the same time (case events and the underwriter's case list: files under `packages/contracts`, `services/workflow`, `services/web`, some tests under `packages/synthdata/tests/`, `README.md`, `deferred-work.md`). Leave that work as it is: add to `README.md` and `deferred-work.md` without rewriting what is there, and expect its tests to be present in a full run.
- The Azure environment is torn down (owner's rule in `CLAUDE.md`). Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation, and it is over the 1,600-token target because the story adds a whole service and a job.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `docker build -f services/retrieval/Dockerfile -t aiuw-retrieval:dev .` -- expected: builds
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate` -- expected: clean
