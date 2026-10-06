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
  summary: Decide whether synthetic pages keep a visible "SYNTHETIC TEST DOCUMENT" footer in their text layer.
  evidence: The footer is on every non-blank page and could cue a classifier or model in a way no real document would. Owner's call (Darrel).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-4-first-synthetic-case-documents.md`
  summary: Extend the answer key with occurrence counts per page, strings that may legitimately be redacted, and expected clinical facts.
  evidence: The key lists pages per identifier and a prose summary only. Pick up in stories 3.1 and 3.4.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-4-first-synthetic-case-documents.md`
  summary: Check whether Azure AI Language recognises the fictional identifiers (identity numbers with area 000, state ZZ, POL-SYN policy numbers) and adjust the synthetic formats if not.
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
  summary: Find and remove originals that no document row references, and decide how long originals are kept.
  evidence: A crash between the blob write and the insert leaves an unreferenced original; nothing sweeps the container and no retention rule exists. Owner decision on retention.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-5-upload-a-document-and-see-the-case.md`
  summary: Add the migration step to the deploy, run the database bootstrap for `intake`, and prove a real upload over 4 MB through Dapr in the deployed environment, including a call to a service scaled to zero.
  evidence: The deploy builds `intake` but cannot migrate; the sidecar body limit and scale-from-zero were tested only with a stand-in. Pick up in the first deploy session.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-5-upload-a-document-and-see-the-case.md`
  summary: Confirm whether the redaction service's 10 MB limit is decimal or binary and align the upload limit.
  evidence: The upload rule allows 10 x 1024 x 1024 bytes. Unverified; low if true. Pick up in story 1.7.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-case-lifecycle-and-audit-trail.md`
  summary: Let the audit record carry the error code of a failed stage, so the audit trail API and screen can show it.
  evidence: The code is stored in `workflow.audit_event.error_code`, but `AuditRecord` (spine AD-8) has no field for it, so `GET /cases/{case_id}/audit` leaves it out. Needs a contracts change. Pick up in story 1.12, with the failure reason on progress payloads (story 1.9).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-case-lifecycle-and-audit-trail.md`
  summary: Check that a case exists in `intake` before `workflow` starts it.
  evidence: `POST /cases/{case_id}/start` accepts any UUIDv7 and stores a case for it; no `intake` operation says whether a case exists. A made-up id would fail at redaction. Pick up in story 1.7.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-case-lifecycle-and-audit-trail.md`
  summary: Prove `workflow` in the deployed environment: the database bootstrap (section 5), sign-in to the Durable Task Scheduler with the service identity, the id reuse policy on the real scheduler, and a start through real Dapr sidecars.
  evidence: The environment is torn down; everything was proven against the emulator and a stand-in sidecar. The deploy still has no migration step, and when it gets one it must set `WORKFLOW_DATABASE_SERVICE_ROLE`. Pick up in the first deploy session.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-case-lifecycle-and-audit-trail.md`
  summary: Confirm the recording rules and the status transition table that later stories inherit.
  evidence: Chosen here without a stage to test them against: a failed page stage fails the whole case; a failed case takes no further result; a page number is the position in `page_ids`; a page reaches `extracted` only from `extracting`, which the gate or a decision must set first (`workflow/domain/transitions.py`). A case whose orchestration cannot go on is marked failed by the orchestration; if that last activity also fails on every retry, the case stays `running` until a repeat start finds the dead orchestration. Pick up in stories 1.7 to 1.9.
