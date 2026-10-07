# Deferred work

- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-shared-contracts-package.md`
  summary: Add the role-to-decision mapping (customer: keep, discard; underwriter: accept, deny) to the contracts package and settle how `actor_not_human` is reached.
  evidence: `DecisionRequest` accepts any role with any decision, and a non-human actor fails as a validation error before the domain check. Pick up in stories 1.10 and 1.11.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-shared-contracts-package.md`
  summary: Add one shared function that finds a quote in page text and returns raw offsets, and state the offset unit.
  evidence: Verification compares normalised text but `quote_start` and `quote_end` index the raw page text; each service would map them differently. Pick up in story 2.4.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-shared-contracts-package.md`
  summary: Let progress, verdict-run and agent-step payloads carry a failure reason and a refused-call outcome, give `VerdictRunRequested` a run id, and bound list responses.
  evidence: `CaseProgress`, `PageProgress` and `AgentStep` have no error or outcome field. Pick up in stories 1.9, 2.5 and 2.8.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-shared-contracts-package.md`
  summary: Export the contract to the SPA (JSON Schema or generated TypeScript types) with a drift check.
  evidence: The SPA must use the same field names and enums; nothing generates or checks them. Done in story 1.3.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-shared-contracts-package.md`
  summary: Add dependency vulnerability scans to CI.
  evidence: `security.md` rule 28 requires them on every pull request; the story's CI runs four checks and no scanner. Done in story 1.3.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-4-first-synthetic-case-documents.md`
  summary: Draw the rotated edge page with truly rotated content, not only the PDF rotation flag.
  evidence: `render.py` draws upright and calls `set_rotation(90)`, so text extraction is unchanged. Pick up in story 4.1.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-4-first-synthetic-case-documents.md`
  summary: DECIDED 2026-10-07 (owner): keep the footer. Decide whether synthetic pages keep a visible "SYNTHETIC TEST DOCUMENT" footer in their text layer.
  evidence: The footer is on every non-blank page and could cue a classifier or model in a way no real document would. Owner's call (Darrel).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-4-first-synthetic-case-documents.md`
  summary: Extend the answer key with occurrence counts per page, strings that may legitimately be redacted, and expected clinical facts.
  evidence: The key lists pages per identifier and a prose summary only. Pick up in stories 3.1 and 3.4.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-4-first-synthetic-case-documents.md`
  summary: STILL OPEN after story 1.7, which ran against a local stand-in; it is in that story's list for the final Azure test session. Check whether Azure AI Language recognises the fictional identifiers (identity numbers with area 000, state ZZ, POL-SYN policy numbers) and adjust the synthetic formats if not.
  evidence: Unverified; needs a live redaction call. Would be medium if true. Pick up in story 1.7.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-azure-foundation-for-the-demo-environment.md`
  summary: Run the bootstrap twice, then the first real init, plan and apply, and record the evidence; open the first infrastructure pull request to prove the PR workflow.
  evidence: The permission system refused the bootstrap because it grants roles; nothing has run against the real backend. Needs the owner (Darrel).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-azure-foundation-for-the-demo-environment.md`
  summary: Give the pull-request plan its own read-only identity, or record acceptance that it runs as the deployment identity.
  evidence: `infra-pr.yml` signs in with the identity that holds Contributor and conditional RBAC Administrator; `azure.md` rule 31 prescribes one identity. Owner decision.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-azure-foundation-for-the-demo-environment.md`
  summary: Add plan-time assertions (terraform test or check blocks) for key, password and local auth off, pinned model versions and prevent_destroy.
  evidence: Flipping any of these still passes fmt, validate and plan. `terraform.md` defers policy tooling until the pipeline works.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-3-deployable-web-entry-with-the-role-switcher.md`
  summary: Move the `Me` and `Health` payloads into the contracts package and add error codes for method not allowed, payload too large, unsupported media type and too many requests.
  evidence: Both payloads are hand-written in `web` and in the SPA; framework 405, 413, 415 and 429 are answered as 422. Needs a contracts change. Done in story 1.5.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-3-deployable-web-entry-with-the-role-switcher.md`
  summary: Have the deploy show its plan for review before applying, once the first-build exception is closed.
  evidence: `deploy.yml` plans and applies in one job; `terraform.md` rule 26 wants a reviewed plan. Covered until the demo environment is up by the recorded exception.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-3-deployable-web-entry-with-the-role-switcher.md`
  summary: DONE 2026-10-07. Run `dapr init` once on the developer machine and prove `./tools/dev.sh` end to end.
  evidence: The Dapr runtime is not initialised here and `dapr init` installs outside the project folder, which the agent may not do. Needs the owner (Darrel).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-5-upload-a-document-and-see-the-case.md`
  summary: Make an upload safe to retry (an idempotency key from the browser, or deduplication) so a timeout does not create a second case.
  evidence: `POST /cases` creates a new case on every call and the stored hash is unused. Done in story 1.6.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-5-upload-a-document-and-see-the-case.md`
  summary: PARTLY DONE 2026-10-07: the owner keeps originals 30 days and a storage lifecycle rule deletes them after that (not yet applied); document rows are not removed with them. Find and remove originals that no document row references, and decide how long originals are kept.
  evidence: A crash between the blob write and the insert leaves an unreferenced original; nothing sweeps the container and no retention rule exists. Owner decision on retention.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-5-upload-a-document-and-see-the-case.md`
  summary: Add the migration step to the deploy, run the database bootstrap for `intake`, and prove a real upload over 4 MB through Dapr in the deployed environment, including a call to a service scaled to zero.
  evidence: The deploy builds `intake` but cannot migrate; the sidecar body limit and scale-from-zero were tested only with a stand-in. Pick up in the first deploy session.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-5-upload-a-document-and-see-the-case.md`
  summary: STILL OPEN after story 1.7, which ran against a local stand-in; it is in that story's list for the final Azure test session. Confirm whether the redaction service's 10 MB limit is decimal or binary and align the upload limit.
  evidence: The upload rule allows 10 x 1024 x 1024 bytes. Unverified; low if true. Pick up in story 1.7.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-case-lifecycle-and-audit-trail.md`
  summary: Let the audit record carry the error code of a failed stage, so the audit trail API and screen can show it.
  evidence: The code is stored in `workflow.audit_event.error_code`, but `AuditRecord` (spine AD-8) has no field for it, so `GET /cases/{case_id}/audit` leaves it out. Needs a contracts change. Pick up in story 1.12, with the failure reason on progress payloads (story 1.9).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-case-lifecycle-and-audit-trail.md`
  summary: DONE in story 1.7, at redaction instead of at start: `intake` answers 404 for a case it does not hold, and `workflow` ends that case as failed without retrying. Check that a case exists in `intake` before `workflow` starts it.
  evidence: `POST /cases/{case_id}/start` accepts any UUIDv7 and stores a case for it; no `intake` operation says whether a case exists. A made-up id would fail at redaction. Pick up in story 1.7.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-case-lifecycle-and-audit-trail.md`
  summary: Prove `workflow` in the deployed environment: the database bootstrap (section 5), sign-in to the Durable Task Scheduler with the service identity, the id reuse policy on the real scheduler, and a start through real Dapr sidecars.
  evidence: The environment is torn down; everything was proven against the emulator and a stand-in sidecar. The deploy still has no migration step, and when it gets one it must set `WORKFLOW_DATABASE_SERVICE_ROLE`. Pick up in the first deploy session.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-case-lifecycle-and-audit-trail.md`
  summary: PARTLY CONFIRMED in story 1.7 against the redaction stage (a done redaction tracks its pages as `uploaded`; a failed one fails the case with one case-level event; a failed case takes no further result) and in story 1.8 against the classification stage (a done classification moves its page from `uploaded` to `classified`; a failed one fails its page and the case with one page-level event; the other pages of a failed case take no further result and stay where they were); the routes out of `classified` and the `extracting` rule still wait for story 1.9. Confirm the recording rules and the status transition table that later stories inherit.
  evidence: Chosen here without a stage to test them against: a failed page stage fails the whole case; a failed case takes no further result; a page number is the position in `page_ids`; a page reaches `extracted` only from `extracting`, which the gate or a decision must set first (`workflow/domain/transitions.py`). A case whose orchestration cannot go on is marked failed by the orchestration; if that last activity also fails on every retry, the case stays `running` until a repeat start finds the dead orchestration. Pick up in stories 1.7 to 1.9.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-5-upload-a-document-and-see-the-case.md`
  summary: DECIDED 2026-10-07 (owner): one replica each for the demo (`min_replicas = 1`); the wake-from-zero question no longer matters for the demo. Hold the services at one replica for the demo, and find out whether a Dapr call wakes a service scaled to zero.
  evidence: Observed 2026-10-07: after 8 idle minutes `web` was at zero replicas and the next request took 30 s; `intake` had not scaled down, so the Dapr wake question is still open. `min_replicas` exists as a variable. Owner decision for the demo.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-3-deployable-web-entry-with-the-role-switcher.md`
  summary: Run the deploy workflow itself from `main`, and the CI and infrastructure pull-request workflows.
  evidence: The first deploy was done by hand from the branch; no GitHub workflow has run yet. Needs a pull request and a merge (owner).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Final Azure test session: confirm the Azure AI Language API version and the exact request and result shape of document PII redaction, and correct `intake/adapters/language.py` and the stand-in if they differ.
  evidence: Written from the documented shape and proven only against the local stand-in: `POST /language/analyze-documents/jobs` with one document (`source.location`, `target.location`), task kind `PiiEntityRecognition`, `redactionPolicy.policyKind = entityMask`, `piiCategories`; job id taken from `operation-location`; job status and `tasks.items[].results.documents[].targets[].location`; cancel at `jobs/<id>:cancel`. `language_api_version` is `2026-05-01` in `infra/demo/app/terraform.tfvars` (the spine names it as generally available; the how-to samples used a preview version).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Final Azure test session: check that the target location may carry the `<case_id>` prefix, and where the service really writes its files.
  evidence: `intake` sends `target.location = <cases container URL>/<case_id>` and accepts output only under `<case_id>/` in the `cases` container (anything else is refused as `job_output_outside_case`, so that another case's blob can never be taken for this one's); it then copies the files to `<case_id>/<document_id>.redacted.pdf` and `.redaction-result.json` and removes the service's own. If the service takes a container address only, or writes outside the prefix, every case fails with `redaction_failed` until this is changed.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Final Azure test session: check that Language reads `originals` and writes `cases` with its own identity and the three new role assignments, and that `intake` may submit, read and cancel a job with Cognitive Services User.
  evidence: The role assignments `intake_language_user`, `language_originals_reader` and `language_cases_contributor` are in `infra/demo/app/main.tf`, validated but never planned or applied. Whether the service uses its system-assigned identity for the blob addresses without further settings is unverified.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Final Azure test session: check whether fictional identity numbers (area 000) and `POL-SYN` policy numbers are masked, which category names the service reports, and whether it accepts `PolicyNumber` in `piiCategories`.
  evidence: `PolicyNumber` is this project's own name, used by the stand-in; the real service may refuse it at submit (every case would then fail with `redaction_failed`) or ignore it. It is one line in `redaction_categories` in `infra/demo/app/terraform.tfvars`. If policy numbers are not masked, the synthetic format or the approach has to change (story 1.4's deferred item). Would be high if a planted identifier stays in the page text: the eval runner's redaction check (AD-17) fails the run.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Final Azure test session: look at the service's result file. If it holds the text of what it found, store it with the original and not in `cases`.
  evidence: `intake` reads only category names from it for the counts and keeps the file as `<case_id>/<document_id>.redaction-result.json` in `cases` (spine AD-21). The stand-in's file holds categories only. A file with found text in `cases` would put identifiers in the store every later stage may read.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Final Azure test session: confirm whether the redaction service's 10 MB limit is decimal or binary and align the upload limit; and whether it redacts every page of the larger synthetic cases within the 180-second deadline.
  evidence: The upload rule allows 10 x 1024 x 1024 bytes. Only the stand-in's timing is known (under a second for six pages).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Final Azure test session: prove the 180-second stage deadline and the 200-second activity timeout through real Dapr sidecars and Container Apps' own request timeout, and the retry of `in_progress`.
  evidence: Proven locally with the real Dapr runtime only for a redaction that takes about a second. A command that outlives the sidecar's or the ingress's timeout would be retried by `workflow` and answered `in_progress`; the stage policy retries for about five minutes (`WORKFLOW_STAGE_MAX_ATTEMPTS`, `WORKFLOW_STAGE_MAX_RETRY_SECONDS`).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Add the `intake` migration `0003` (redaction, page, page_text, word_box) to the manual database step of the final test session.
  evidence: The deploy still has no migration step (story 1.5's item); `intake` reports not ready until its schema is at `0003`.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Let a redaction be linked to the trace of the request that started the case.
  evidence: `workflow` passes the trace context of the activity on to `intake`, and locally the Dapr sidecar gave the call a trace id; whether the Durable Task library carries the start request's trace into the activity in Azure, so that one trace spans start and redaction, is unverified. Pick up with the audit view (story 1.12) or in the final test session.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Final Azure test session: cancel a running redaction job and watch the `cases` container, to see whether the service still writes files after the cancel.
  evidence: Unverified; medium if true. `intake` removes everything under `<case_id>/` right after asking for the cancel; a job that writes later would leave a redacted PDF and result file for a failed case. The same holds for a job accepted without an id, or whose id was not stored before the process died.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Sweep redaction key rows that stay `running` when no repeat of the command ever comes.
  evidence: A stale row is settled only by a repeat of the command. Once `workflow`'s retries are spent nothing repeats it, so the row stays `running`, the Language job is not cancelled and its files stay. Needs a start-up or timed sweep in `intake`.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Final Azure test session: look at what the redacted PDF carries besides its page text (metadata, bookmarks, annotations, form fields, attachments, images) and whether a planted identifier survives there.
  evidence: Unverified; medium if true. `GET /documents/{id}/file` serves the service's PDF as it is, and the planted-identifier check reads page text only. Images are the spine's open question for the owner.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Check the page text of a table or multi-column page: words are ordered by position while line breaks come from block and line numbers.
  evidence: Unverified; medium if true, because quotes are checked against this text. Needs such a page; the layout test uses simple pages. Pick up in story 2.4.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-pii-redaction-before-anything-else-reads-the-document.md`
  summary: Keep stage activities from holding every worker thread of `workflow` when many cases run at once.
  evidence: Unverified; medium if true. `worker_max_concurrent_activities` is 5 and a stage activity blocks its thread for up to 205 s, so the short activities of other cases wait. Matters when the eval runner starts 20 cases. Pick up in story 3.4.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-page-classification-with-a-confidence-and-a-reason.md`
  summary: Final Azure test session: make one model call against the real Foundry endpoint with the `classification` identity, and correct `classification/adapters/model.py` and the stand-in if the endpoint form, the token scope or the API shape differ.
  evidence: Written from the documented shape and proven only against the local stand-in: `POST <foundry_endpoint>/openai/v1/chat/completions` through `openai` 3.24.0 with `model` set to the deployment name (`CLASSIFICATION_CHAT_DEPLOYMENT`, from `model_deployment_names["chat"]`), a bearer token for the scope `https://cognitiveservices.azure.com/.default`, and no `api-version`. `CLASSIFICATION_MODEL_ENDPOINT` is the foundation output `foundry_endpoint` (the account's endpoint), not one of `foundry_project_endpoints`. The role is Foundry User on the Foundry project (`classification_foundry_user` in `infra/demo/app/main.tf`), validated but never planned or applied; whether that is enough to call a deployment of the account is unverified. If any of these is wrong every classification fails with `stage_failed` (a 401, 403 or 404 is not retried) and so does every case.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-page-classification-with-a-confidence-and-a-reason.md`
  summary: Final Azure test session: check that the chat deployment accepts the page as it is sent and answers in the structured output asked for.
  evidence: Each run sends the prompt as the system message and one user message with the page text and the thumbnail as a `data:image/png;base64` URL, with `response_format` of type `json_schema` (strict; `page_type` as an enum of the six page types, `reason` a string). Unverified against `gpt-5.4`: that it takes an image and a strict schema in one call, and that its answer parses into `ClassifierOutput`. A reason of more than one line, or text around the JSON, fails the page with `invalid_model_output`, and a failed page fails the case. A request the content filter refuses is answered 400 and stored as `stage_failed`, not as a code of its own.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-page-classification-with-a-confidence-and-a-reason.md`
  summary: Final Azure test session: check whether repeated runs of the model on one page ever differ, and whether the thumbnail is large enough for the model to read.
  evidence: Confidence is the share of `CLASSIFICATION_CLASSIFIER_RUNS` (5) runs that agree. No temperature or seed is set, and the stand-in's runs always agree unless it is told otherwise. Would be high if true: a confidence that is always 1.0 makes the gate's 0.90 threshold meaningless (story 1.9) and the calibration figure of the classifier bake-off empty (story 4.3). The picture is `intake`'s thumbnail, 320 pixels wide (`INTAKE_THUMBNAIL_WIDTH_PX`); the page text goes with it, so the model need not read the picture, but whether the picture adds anything at that size is unknown.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-page-classification-with-a-confidence-and-a-reason.md`
  summary: Final Azure test session: run a case of several pages and watch the chat deployment's token rate limit.
  evidence: Every page of a case is classified at once and each page runs the model 5 times at once: 30 calls for a six-page case, held to 25 at a time by `WORKFLOW_WORKER_MAX_CONCURRENT_ACTIVITIES` (5). The deployment has 100 thousand tokens a minute, shared later with extraction and the verdict agent. The service itself has at most 10 calls under way at once (`model_max_concurrent_calls` in `infra/demo/app/terraform.tfvars`). The gateway retries a 429 three times, waiting as `Retry-After` says or else 1, 2 and 4 s less a random part, at most 30 s each (`CLASSIFICATION_MODEL_MAX_RETRY_SECONDS`), then the page fails with `model_unavailable` and the case with it. If the limit is hit, lower `model_max_concurrent_calls` or `classifier_max_concurrent_runs`, or raise `model_max_retries`, in that file; the eval runner's 20 cases (story 3.4) will hit it sooner. Also check `CLASSIFICATION_MODEL_MAX_COMPLETION_TOKENS` (2000): if the model reasons before it answers, its reasoning counts against it, and an answer cut short fails the page with `invalid_model_output`.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-page-classification-with-a-confidence-and-a-reason.md`
  summary: Final Azure test session: run the database bootstrap for `classification` (`infra/bootstrap/README.md`, section 6) and add its migration `0001` to the manual database step.
  evidence: Section 6 is written from sections 4 and 5 and has not been run. The deploy still has no migration step (story 1.5's item); `classification` reports not ready until its schema is at `0001`, and until then a started case fails about five minutes after its redaction, when `workflow`'s retries of the classify command are spent.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-page-classification-with-a-confidence-and-a-reason.md`
  summary: Final Azure test session: prove the classify command through real Dapr sidecars in Container Apps, with every page of a case commanded at once.
  evidence: Proven locally with the real Dapr runtime for one case; in the tests a transport stands where the sidecars would be. Unverified in Azure: `workflow` to `classification` and `classification` to `intake` by app id, the thumbnail's content type through two sidecars, and that one trace spans the start, the classify command, the reads of `intake` and the model calls.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-page-classification-with-a-confidence-and-a-reason.md`
  summary: Sweep classification key rows that stay `running` when no repeat of the command ever comes.
  evidence: As for redaction (story 1.7's item): a stale row is settled only by a repeat of the command. Once `workflow`'s retries are spent nothing repeats it and the row stays `running`. It holds no result and no page is shown as classified, so only the table is untidy. Needs a start-up or timed sweep in `classification`.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-page-classification-with-a-confidence-and-a-reason.md`
  summary: DONE in the review of story 1.8 for a fault that passes: when `intake` cannot be reached for the page (`upstream_unavailable`), or the request is cancelled, the key row is released and the command sent again classifies the page. Decide whether a page that `intake` cannot hand over after the key row is written should fail the case.
  evidence: `classification` asks `intake` for the case's page list before it writes the key row (an unknown page is 404 and leaves nothing), and reads the text and thumbnail after it. Still stored as failed with `stage_failed`, which fails the case: a page that `intake` listed and then answers 404 for, and any other error of the read that is not `upstream_unavailable`.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-page-classification-with-a-confidence-and-a-reason.md`
  summary: Let the classifier's reason be checked for copied page content.
  evidence: The reason is the model's own sentence and is stored, returned by `GET /cases/{case_id}/classifications` and later shown in the triage queue (story 1.11). The prompt tells the model not to copy names or numbers into it, and the page it reads is already redacted, but nothing checks the reason. Pick up with the triage queue (story 1.11) or the eval runner's redaction check (story 3.4).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-page-classification-with-a-confidence-and-a-reason.md`
  summary: Final Azure test session: try one model request with `n` equal to the number of runs instead of one request per run.
  evidence: Unverified; medium if the token rate limit is hit. Each run resends the prompt, the page text and the image. Whether the deployment takes `n` together with structured output and an image is not known.
