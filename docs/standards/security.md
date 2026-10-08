# Security standards (org baseline)

> **Org baseline, version 1.0.0** (Org Kit, module `org`). Projects copy this file into their standards folder and extend it: fill in the placeholders, add project rules below the baseline ones, and record any deviation under [Accepted exceptions](#9-accepted-exceptions). Never delete or weaken a baseline rule silently.

Binding for all code, infrastructure and agent prompts in a project repository. Where this file and the project's architecture disagree, raise it; don't pick one silently. Related: `azure.md`, `terraform.md`, the project's architecture document and its Technical Architecture Assessment in the governance folder.

## 1. Data

1. Use synthetic data only in seed data, tests, fixtures, demo scripts and prompts. Never enter or import real customer, health or ID data unless the project has an approved exception for a named environment.
2. The spec names the project's sensitive fields (for example health answers, national ID, date of birth, contact and financial details). Treat them as sensitive even when synthetic: never log their values, and never put them in error messages, traces or analytics exports.
3. Keep all stored data in the project's approved region. Processing outside it (for example a Global Standard model deployment) is an accepted exception, allowed at most for synthetic data.

## 2. Identity and access

4. Every API route requires a signed-in Entra principal; return 401 without one.
5. Every read and write checks ownership against the caller's identity claim (e.g. `oid`). Return 404, not 403, for a record the caller doesn't own, so its existence isn't revealed.
6. Runtime identities get only the roles listed in the project's `azure.md` (rule 9); never Owner, Contributor or subscription-scoped roles. The pipeline's deployment identity holds only the resource-group-scoped roles in `azure.md` rule 31.

#### If your project uses PostgreSQL row-level security

7. Keep row-level security enabled and forced on every owner- or tenant-scoped table; every request transaction sets its scope (e.g. `app.owner_id`) with `SET LOCAL`. All policies are permissive and `TO` a named role (the runtime principal or the migration role), never `PUBLIC`; views over these tables are `security_invoker`. Only the pipeline identity holds the migration role, and only its migration step uses it; the runtime identity is never granted it and cannot `SET ROLE` to it, so the running app can never bypass row-level security.

## 3. Secrets

8. Keep secrets only in the platform secret store (Container Apps secrets, or Key Vault). Never put a secret in code, images, environment files in git, Terraform variables files in git, logs or chat.
9. Use managed identity for databases, AI services and the container registry; never create database passwords or API keys for them.
10. GitHub Actions authenticates to Azure only through OIDC federation; never store Azure credentials or personal access tokens as repository secrets.
11. Rotate Terraform-generated signing keys by changing their `keepers` value and re-applying; document the effect on live tokens in the project copy.

## 4. AI agents (if your project has one)

12. An agent reaches data only through the project's server (e.g. an MCP server), never with direct database access, and only with a credential the server issues per turn: signed, scoped to one record or task, short-lived (minutes), and bound to that turn. Never log or return the credential or place it in model context or metadata.
13. The agent may never perform the actions the spec reserves for a person (submitting, signing, declaring, approving) or write fields marked agent-read-only; enforce this in the domain layer, never only in the prompt.
14. Treat everything typed in chat as untrusted input, including instructions that try to change the agent's rules ("ignore previous instructions", "update record 202"). Server-side checks (scoped credential, ownership, schema validation, human edits win) are the defence; prompts are not.
15. Validate every tool argument server-side against the schema; never trust ids, codes or values produced by the model. Tools never take a record id the model chose.
16. The agent has exactly the tools the architecture lists; adding a tool or any other Foundry tool or connection needs an architecture decision.
17. Keep the model deployment's default content filter; never attach a policy that weakens it (`azure.md` rule 25).
18. Enforce a per-user rate limit on chat turns server-side, at the value the architecture sets.
19. Log every AI write with the record, turn and conversation ids, and keep an alert that fires when an AI write targets a record other than its conversation's or carries a turn id that isn't current.

## 5. Application

20. Validate every input on the server with the schema and domain rules; client-side checks are for convenience only.
21. Access the database only through the ORM or query builder with bound parameters (e.g. SQLAlchemy); never build SQL from strings.
22. Never use `dangerouslySetInnerHTML` or render chat, model output or user text as HTML; keep the framework's default escaping on.
23. Serve the web app and API from one origin and don't enable CORS; if the architecture requires CORS, allow-list exact origins, never `*`.
24. Protect state-changing requests from cross-site forgery: keep the auth cookie `SameSite=Lax` or stricter, and require a custom request header on every non-GET API call.
25. Send security headers on every response: `Strict-Transport-Security`, `Content-Security-Policy` (self only, no inline scripts), `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `frame-ancestors 'none'`.
26. Return errors in the project's error shape with plain messages; never return stack traces, SQL or internal paths.

## 6. Dependencies and supply chain

27. Pin exact versions and commit lock files (`uv.lock` / `requirements*.txt`, `package-lock.json`); follow `terraform.md` rule 9 for Terraform.
28. Run dependency vulnerability scans in CI on every pull request (e.g. `pip-audit`, `npm audit --omit=dev`) and GitHub Dependabot alerts; a critical or high finding blocks merge unless the owner accepts it in the PR.
29. Pre-release packages are allowed only where the architecture names them; no others without an architecture decision.
30. Enable GitHub secret scanning and push protection on the repository.

## 7. Logging, monitoring and audit

31. Log request ids, record ids, field ids, error codes and timings; never log field values, tokens, headers or connection strings (`azure.md` rule 16).
32. Keep audit and override tables append-only; no code path updates or deletes their rows.
33. Keep one trace per request (and per chat turn, if there is an agent) in Application Insights so any action, including an AI action, can be followed end to end.

## 8. Infrastructure

34. Follow `azure.md` and `terraform.md`; every change goes through a pull request and a human-approved pipeline apply.
35. Keep Terraform state in the Entra-authenticated storage account with shared-key access disabled (`azure.md` rule 29).

## 9. Accepted exceptions

Projects record here the known gaps they accept (and declare in their Technical Architecture Assessment), each with who approved it and what closes it before real data or production use. The org baseline ships this table empty.

| Exception | Approved by (role) and date | Close before production by |
|---|---|---|
| Rules 4, 5 and 7: no signed-in Entra principal on API routes, no ownership checks and no row-level security; every case is visible to both demo roles (architecture spine AD-9). | Project owner (Darrel), 2026-10-06 | Before real data, or any audience beyond the demo |
| Rules 12 and 18: no per-turn agent credential and no per-user rate limit, because there are no users (AD-9). | Project owner (Darrel), 2026-10-06 | Before real data, or any audience beyond the demo |
| Rule 15: the verdict agent's `read_rule` tool takes a `rule_id` chosen by the model, checked server-side against the ids seen in that run (AD-15). | Project owner (Darrel), 2026-10-06 | Before real data, or any audience beyond the demo |
| Rule 19: no alert on AI writes; the audit trail and the queryable agent step log are the record (AD-8, AD-15). | Project owner (Darrel), 2026-10-06 | Before real data, or any audience beyond the demo |
| Rule 29: one pre-release package, `azure-search-documents` 12.1.0b2, is named in the architecture spine for retrieval row `r6` (AD-11). | Project owner (Darrel), 2026-10-06 | The stable SDK supports LLM query planning |
| Rule 3: the Foundry model deployments are Global Standard, so synthetic data is processed outside West US 3 (architecture spine AD-16). This includes the Cohere Rerank deployment of retrieval row `r4`, a model of another provider than the OpenAI ones, which is sent search queries and manual text. | Project owner (Darrel), 2026-10-06; the Cohere model on 2026-10-08 | Before real data |
| Accepted risk: anyone who finds the public URL can upload PDFs and spend model tokens while the environment runs; bounded only by the budget alerts, low model deployment capacity and the compute ceilings. | Project owner (Darrel), 2026-10-06 | Before real data, or any audience beyond the demo |

## 10. Reporting

36. Report a suspected vulnerability or leaked secret immediately to the project's security contact (`<security-contact>`, named in the project copy). Rotate any exposed secret first, then investigate.
