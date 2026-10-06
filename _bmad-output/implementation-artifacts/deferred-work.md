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
