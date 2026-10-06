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
  evidence: The SPA must use the same field names and enums; nothing generates or checks them. Pick up in story 1.3.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-shared-contracts-package.md`
  summary: Add dependency vulnerability scans to CI.
  evidence: `security.md` rule 28 requires them on every pull request; the story's CI runs four checks and no scanner. Pick up in story 1.3.
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
