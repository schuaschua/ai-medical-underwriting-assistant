---
title: 'Story 1.2: Azure foundation for the demo environment'
type: 'feature'
created: '2026-10-06'
status: 'in-review'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '138587fa76006529339c2b2a18ee34ebb251bed8'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/docs/standards/azure.md'
  - '{project-root}/docs/standards/terraform.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** No Azure resource exists for the POC. The seven services need a registry, a Container Apps environment, a database, storage, the AI services and a workflow scheduler before any of them can be deployed.

**Approach:** Write the bootstrap script and the `foundation` Terraform stack for the one `demo` environment in West US 3, with a pull-request check for infrastructure code, then run the bootstrap and apply the stack so the resources exist.

## Boundaries & Constraints

**Always:**
- Follow `docs/standards/azure.md` and `docs/standards/terraform.md` in full: layout `infra/bootstrap`, `infra/modules`, `infra/demo/foundation`; Azure Verified Modules first, pinned exactly; `azapi` only where `azurerm` has no resource; one naming block; the six required tags on everything.
- Names use workload `aiuw`, environment `demo`, region `westus3`, region code `wus3`. Tags: `workload=aiuw`, `env=demo`, `owner=darrel`, `managedby=terraform`, `datatype=synthetic`, `repo=https://github.com/schuaschua/ai-medical-underwriting-assistant`.
- Managed identity only: disable key, password, SAS shared-key and admin-user access wherever Azure allows; PostgreSQL is Entra-only.
- Terraform state goes to the existing central account `stdjtfstatesea` (resource group `rg-tfstate-sea`), container `aiuw`, key `demo/foundation.tfstate`, with Entra auth.
- Cheapest tier that works: Container Apps Consumption; PostgreSQL Burstable B1ms with the smallest storage; registry Basic; Azure AI Search Basic; Document Intelligence S0; Azure AI Language S; Durable Task Scheduler Consumption; Log Analytics with a 0.5 GB daily cap and 30-day retention.
- Model deployments are Global Standard, pinned with auto-upgrade off and the default content filter: `gpt-5.4` version `2026-03-05` and `text-embedding-3-large` version `1`, each at low capacity.
- Every command that changes Azure is recorded in `infra/bootstrap/README.md`, with what it did.

**Never:**
- No Container Apps, jobs or runtime role assignments (those belong to the `app` stack in later stories), except the seven runtime identities, which this stack creates.
- No private endpoints, VNet, Key Vault, second environment or high availability (deferred in the spine).
- No resource outside West US 3, other than the existing central state account.
- Never destroy or replace an existing resource group or anything outside `rg-aiuw-demo-wus3` and the `aiuw` state container. The subscription holds other projects.
- No secrets, keys or subscription ids in committed files.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Bootstrap, first run | Operator with Owner | Creates `rg-aiuw-demo-wus3` with tags, the `aiuw` state container, `id-aiuw-demo-wus3-deploy` with federated credentials for the repo's `pull_request` and `environment:demo` subjects, and its three scoped roles | Stops on first error |
| Bootstrap, second run | Already bootstrapped | No change, exit 0 | N/A |
| Plan after apply | Stack applied | `terraform plan` reports no changes | N/A |
| A model or tier unavailable in West US 3 | Apply fails for one resource | Stop, record the exact error in the spec's Implementation Notes, leave the rest applied | Do not substitute another region or model |
| Pull request touching `infra/` | CI runs | `fmt -check`, `validate` and `plan` run with OIDC and the plan is posted as a comment | Fails the check on any error |

</frozen-after-approval>

## Code Map

Greenfield: no `infra/` exists. Verified facts to build on (2026-10-06):

- Subscription "Babaloo"; the signed-in operator is Owner. Providers registered: `Microsoft.App`, `DBforPostgreSQL`, `CognitiveServices`, `Search`, `DurableTask`, `Storage`, `ContainerRegistry`, `OperationalInsights`.
- State account `stdjtfstatesea` in `rg-tfstate-sea` (Southeast Asia) already exists and is shared by other projects: add a container, change nothing else on it.
- Quota in West US 3: `OpenAI.GlobalStandard.gpt-5.4` 1000, `OpenAI.GlobalStandard.text-embedding-3-large` 1000.
- GitHub repo `schuaschua/ai-medical-underwriting-assistant`; the `gh` CLI is signed in with admin rights. Current branch `architecture/spine-redaction-diagrams`.
- Spine sections to follow: AD-4 (containers `originals`, `cases`, `manual`, `classifier-training`), AD-16, AD-18, "Deployment" (stack resource list, runtime identities), Accepted exceptions.
- `terraform` 1.16.4 is installed locally; 1.16.5 is the latest.

## Tasks & Acceptance

**Execution:**
- [x] `infra/bootstrap/state-backend.sh` -- idempotent `az` script per `azure.md` rule 31 -- one-time setup outside Terraform
- [x] `infra/bootstrap/README.md` -- how to run it, and a dated log of every out-of-band step taken -- `terraform.md` rule 29
- [x] `infra/demo/foundation/` -- `versions.tf`, `providers.tf`, `locals.tf`, `variables.tf`, `main.tf`, `outputs.tf`, `terraform.tfvars`, committed lock file -- the stack: Log Analytics, Application Insights, registry, Container Apps environment, PostgreSQL server and database with `vector` allow-listed, Storage account and four containers, Foundry account, project and two deployments, Azure AI Search, Document Intelligence, Azure AI Language, Durable Task Scheduler and task hub, seven user-assigned identities (`web`, `intake`, `classification`, `extraction`, `retrieval`, `verdict`, `workflow`), budget with 90/100/110% actual and 110% forecast alerts, Log Analytics cap alert, diagnostic settings
- [x] `infra/modules/` -- only if a composition is reused; otherwise leave empty with a `.gitkeep`
- [x] `.github/workflows/infra-pr.yml` -- fmt, validate, plan and plan comment on pull requests touching `infra/`, OIDC login, repository variables for tenant, subscription and client ids
- [x] `docs/standards/azure.md` -- add the new resource types' abbreviations and name patterns to a project table (`srch`, `di`, `lang`, `dts`, `st`) and the runtime role list placeholder -- rule 2
- [ ] Run the bootstrap, set the GitHub repository variables and `demo` environment, then `terraform init`, `plan -out=tfplan`, `apply tfplan` for the stack

**Acceptance Criteria:**
- Given the applied stack, when `az resource list -g rg-aiuw-demo-wus3` is read, then every resource in the task list exists in West US 3 with the six tags.
- Given each data or AI resource, when its auth settings are queried with `az`, then local, key or password auth is disabled.
- Given the two model deployments, when queried, then each shows the pinned version, Global Standard and auto-upgrade off.
- Given the applied stack, when `terraform plan` runs again, then it reports no changes.

## Implementation Notes

**State on 2026-10-06: code written and validated; nothing applied.** The agent's session refused to run `infra/bootstrap/state-backend.sh` because it grants roles (the permission system's decision, not a script error). Without the bootstrap there is no resource group, state container or state access, so `terraform init`, `plan` and `apply` against the real backend were not run. Nothing was created or changed in Azure or GitHub. The last task above and all four acceptance criteria are open, and no apply evidence exists yet. The owner runs sections 1 and 2 of `infra/bootstrap/README.md` to finish.

What was verified without Azure changes:

- `terraform fmt -check -recursive` and `terraform validate` pass on `infra/demo/foundation` (Terraform 1.16.5).
- A read-only dry plan of a scratch copy (local state, no import block, a placeholder `deploy_principal_id`) against the subscription: `Plan: 103 to add, 0 to change, 0 to destroy`, no errors. It showed `disableLocalAuth = true` on the three Cognitive Services accounts, `allowSharedKeyAccess = false` on storage, `password_auth_enabled = false` on PostgreSQL, `admin_enabled = false` on the registry, local authentication off on Log Analytics, Application Insights and Azure AI Search, and `NoAutoUpgrade` with `Microsoft.DefaultV2` on both deployments. A plan does not prove that Azure accepts each resource at apply.
- `bash -n` on the bootstrap script, `actionlint` on the workflow. Neither has been executed.
- Read-only `az` checks in West US 3: `gpt-5.4` `2026-03-05` and `text-embedding-3-large` `1` are offered as Global Standard; `TextAnalytics` has SKU `S`, `FormRecognizer` `S0`, `AIServices` `S0`; PostgreSQL offers `Standard_B1ms` and versions up to 18; `Microsoft.DurableTask/schedulers` has stable API `2026-02-01`; all needed providers are registered. The repository already uses GitHub's immutable OIDC subject prefix.

Versions pinned (latest stable on 2026-10-06):

| Item | Version | Note |
| --- | --- | --- |
| Terraform | 1.16.5 | The machine has 1.16.4; a checksum-verified 1.16.5 binary is in `.work/bin/` |
| `hashicorp/azurerm` | 4.81.0 | 5.8.0 is the latest, but every Azure Verified Module caps azurerm below 5.0.0 |
| `Azure/azapi` | 2.13.0 | |
| `avm-res-operationalinsights-workspace` | 0.5.1 | |
| `avm-res-insights-component` | 0.4.0 | |
| `avm-res-containerregistry-registry` | 0.8.0 | |
| `avm-res-app-managedenvironment` | 0.5.1 | |
| `avm-res-managedidentity-userassignedidentity` | 0.5.3 | |
| `avm-res-dbforpostgresql-flexibleserver` | 0.2.3 | |
| `avm-res-storage-storageaccount` | 0.10.0 | |
| `avm-res-cognitiveservices-account` | 0.11.1 | Foundry, Document Intelligence, Azure AI Language |
| `avm-res-search-searchservice` | 0.3.0 | |
| Lock file only | `random` 3.9.1, `time` 0.14.2, `modtm` 0.4.0 | Pulled in by the modules |

Open-at-build answers:

- Azure AI Language: kind `TextAnalytics`, SKU `S` is the single-service Language resource.
- Durable Task Scheduler: no Azure Verified Module and no `azurerm` resource, so the scheduler and task hub are `azapi` resources at API `2026-02-01`.
- No Azure Verified Module exists for a budget, an action group or a scheduled query rule; those are hand-written `azurerm`. The Foundry project is `azurerm_cognitive_account_project` (the module does not create projects).

Decisions made while building, for the owner to confirm:

- **Resource group is hand-written `azurerm`, not the Azure Verified Module**, so it can carry `prevent_destroy`. Teardown removes it from state first; the `import` block adopts it again. Tested locally with a throwaway resource: import blocks are ignored by `plan -destroy`.
- **PostgreSQL Entra administrator is the deployment identity only.** Its object id comes from `TF_VAR_deploy_principal_id` (repository variable `AZURE_DEPLOY_PRINCIPAL_ID`, a fourth variable beyond the three in the task), because that identity cannot look itself up and the id should not be committed. No human is an administrator, so the database principal bootstrap in story 1.3 must run as the pipeline, or an administrator is added then.
- **Deployment identity** sits in `rg-tfstate-sea` (azure.md rule 31) with location `westus3`, tagged `managedby=bootstrap` like the existing one for another project there.
- **Action group `ag-aiuw-demo-wus3` is `global`**: action groups are not offered in West US 3. It is the one resource that will not show `westus3` in the acceptance check. Without it the Log Analytics cap alert would notify nobody.
- **Budget and alert mail go to the Owner role**, not to an address, so no email is committed to the public repository.
- **Budget amount USD 150 a month** and **capacity 100 (chat) and 50 (embedding) thousand tokens per minute** are the agent's picks; the spec gives no numbers. Both are in `terraform.tfvars`.
- **PostgreSQL 17**, 32 GB on tier P4; `azure.extensions = VECTOR`; firewall open to Azure services only.
- **Container Apps environment logs** use the `azure-monitor` destination plus a diagnostic setting, so no workspace key is used. Diagnostic settings carry logs only, no metrics, and exist on the environment, PostgreSQL and the three AI accounts.
- **Azure AI Search semantic ranker** is on the `free` plan (needed for `r5`).
- **Foundry to Application Insights connection** (azure.md rule 14) stores the connection string as the connection's key. It is the least certain resource; if apply rejects it, record the error here.
- Two modules call preview Azure API versions internally (Container Apps environment, Azure AI Search); that is the modules' choice at their pinned versions.
- Model retirement dates: `gpt-5.4` 2027-09-02, `text-embedding-3-large` 2028-02-09.

Added on the owner's instruction of 2026-10-06 (relayed by the main session): `infra/bootstrap/README.md` has "Bring up" and "Tear down" sections with exact commands, including purging the three soft-deleted Cognitive Services accounts. The teardown was not run. A local `terraform destroy` departs from `terraform.md` ("destroy only through the pipeline's destroy workflow") and azure.md rule 21; neither exception table was edited.

Changed after review on 2026-10-06 (still nothing run against Azure; only `bash -n`, `shellcheck`, `terraform fmt`, `terraform validate` and `actionlint` were re-run), superseding the matching points above:

- The bootstrap script stops before any change unless the subscription is named "Babaloo" (`EXPECTED_SUBSCRIPTION_NAME` overrides), the state account exists and an existing resource group is in `westus3`. It updates a differing RBAC condition in place and stops if it finds more than one such assignment.
- The budget starts on the first day of the month of its creation unless `budget_start_date` is given.
- Two optional inputs, set through `TF_VAR` and never committed: `alert_email_addresses` (budget and action group, beside the Owner role) and `postgresql_extra_admin_object_ids` (extra PostgreSQL Entra administrators; the database role is named after the object id). A run without them plans to remove them, and the pull-request plan does not set them.
- The log-cap alert reads a rolling 24 hours of billable usage and counts 1 GB as 1,000 MB.
- The provider deletes the Log Analytics workspace for good on destroy.
- The pull-request workflow skips fork and Dependabot pull requests, plans without the state lock, keeps one comment per pull request, and adds a shellcheck step.

Not testable until there is a pull request: the `infra-pr.yml` run itself (OIDC sign-in, plan, comment).

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Bootstrap has no preflight: wrong subscription, missing state account, or a resource group in another region are not caught before changes start (blind, edge) | high | The script uses whatever subscription `az` points at; the subscription holds other projects | patch |
| 2 | RBAC Administrator condition: only the first assignment is checked, and a differing one is deleted then re-created (blind, edge, gap) | high | A second unconditioned assignment would lift the restriction; a failure between delete and create leaves no role | patch |
| 3 | Budget start date is a hard-coded month in a stack built to be destroyed and re-created (blind, edge) | medium | Azure rejects a start date in a past month on re-create | patch |
| 4 | Budget and log-cap alerts go only to the Owner role and may reach nobody (blind, edge) | medium | Role receivers mail direct subscription-scope assignees only; no fallback address | patch |
| 5 | PR workflow: no guard for fork or Dependabot PRs; plan takes the state lock and can be cancelled holding it; no comment when an earlier step fails; a new comment per push; silent truncation (blind, edge) | medium | Read from `infra-pr.yml` | patch |
| 6 | PostgreSQL's only Entra administrator is the deployment identity, so the operator running local applies cannot create database principals (blind, implementer) | medium | Stories 1.5 and 1.6 need service database roles; no workflow uses the pipeline identity yet | patch |
| 7 | Log-cap alert query divides MB by 1024 and uses UTC midnight (blind, edge) | low | Direct correction | patch |
| 8 | Almost no variable validation (ratio, name lengths, containers) (blind, edge) | low | Direct additions | patch |
| 9 | README states unrun behaviour as observed fact; teardown asks for a CLI force-delete of the Log Analytics workspace; purge commands fail when an account is not soft-deleted; cost figure omits the registry (blind, edge) | medium | README's own log says nothing was run | patch |
| 10 | `azure.md` says the action group is the only resource not in West US 3; the budget and the deployment identity are also elsewhere (blind) | low | Direct correction | patch |
| 11 | Durable Task hub and other child resources carry no tags (edge, claim) | low | True for the `azapi` task hub; tags only where the type supports them | patch |
| 12 | Hand-built endpoint outputs; no `shellcheck`; CI init does not use a read-only lock file (blind) | low | Direct corrections | patch |
| 13 | Routine `terraform state rm` in teardown has no recorded exception to `terraform.md` rule 30 (blind) | medium | True; recorded by the main session in `terraform.md` | patch |
| 14 | The PR plan runs as the full deployment identity, so a branch that can open a PR can change the job (blind) | medium | True, and prescribed by `azure.md` rule 31; fork PRs get no OIDC token, so the exposure is repository collaborators | defer (owner decision; needs a second, read-only plan identity) |
| 15 | Bootstrap script has never been executed; its two-run idempotency check is open (gap) | high | Filed with evidence; the session may not run it | defer until the owner runs the bootstrap |
| 16 | Security and cost settings are asserted only by a person reading the plan (gap) | medium | Filed with evidence; `terraform.md` defers policy tooling until the pipeline works | defer |
| 17 | The real backend, the import block and the PR workflow have never run (gap) | high | Filed with evidence | defer until the owner runs the bootstrap |
| 18 | Environment PUT could reset existing protection rules; a tag policy named `main` would pass; custom OIDC subject template (edge) | low | The environment does not exist yet and the repository uses the default immutable subject (checked by a reviewer) | reject |
| 19 | The same values are typed in several files; Search, registry and storage have no diagnostic setting (blind) | low | No drift today; `azure.md` rule 15 names only the Container Apps environment, databases and AI accounts | reject |
| 20 | Lock file missing from the diff (blind, gap) | false | It was left out of the review diff and is committed with the story, with macOS and Linux hashes | reject |
| 21 | Action group is `global`, so the location acceptance check fails as worded (edge, claim) | low | True; the criterion is read as "every regional resource" and the exception is documented in `azure.md` | reject |

## Design Notes

- First apply is run locally by the agent as operator, under the recorded exception to `terraform.md` rules 26 and 33 (Darrel, 2026-10-06). The deploy workflow that applies from `main` comes with story 1.3, when there is an `app` stack to deploy.
- The operator needs Storage Blob Data Contributor on the `aiuw` state container to use Entra auth; the bootstrap grants it and records it.
- Cost: applying starts billing of roughly USD 86 a month fixed (Azure AI Search Basic and PostgreSQL B1ms) plus small amounts for the registry, logs and usage. Darrel accepted this on 2026-10-06.
- Open at build, to verify rather than assume: the exact Azure AI Language resource kind for document PII (Microsoft requires a single-service Language resource); whether an Azure Verified Module exists for Durable Task Scheduler (hand-write with `azapi` if not); current stable versions of Terraform providers and each module, pinned and listed in this spec's Implementation Notes.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails.
- Story 1.1 is being patched in the same working tree at the same time: do not edit `packages/`, the root `pyproject.toml`, `.github/workflows/ci.yml` or `README.md`, and do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project.
- Approval: Darrel authorised unattended work, including applying to Azure, on 2026-10-06 before going offline. This spec was not reviewed by him before implementation, and it is over the 1,600-token target because the stack is one indivisible deliverable.

## Verification

**Commands:**
- `bash infra/bootstrap/state-backend.sh` (twice) -- expected: second run changes nothing
- `terraform -chdir=infra/demo/foundation fmt -check -recursive && terraform -chdir=infra/demo/foundation validate` -- expected: clean
- `terraform -chdir=infra/demo/foundation plan -detailed-exitcode` after apply -- expected: exit 0
- `az resource list -g rg-aiuw-demo-wus3 --query "[].{type:type,name:name,location:location}" -o table` -- expected: all resources, all `westus3`
