---
title: 'Story 1.2: Azure foundation for the demo environment'
type: 'feature'
created: '2026-10-06'
status: 'in-progress'
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
- [ ] `infra/bootstrap/state-backend.sh` -- idempotent `az` script per `azure.md` rule 31 -- one-time setup outside Terraform
- [ ] `infra/bootstrap/README.md` -- how to run it, and a dated log of every out-of-band step taken -- `terraform.md` rule 29
- [ ] `infra/demo/foundation/` -- `versions.tf`, `providers.tf`, `locals.tf`, `variables.tf`, `main.tf`, `outputs.tf`, `terraform.tfvars`, committed lock file -- the stack: Log Analytics, Application Insights, registry, Container Apps environment, PostgreSQL server and database with `vector` allow-listed, Storage account and four containers, Foundry account, project and two deployments, Azure AI Search, Document Intelligence, Azure AI Language, Durable Task Scheduler and task hub, seven user-assigned identities (`web`, `intake`, `classification`, `extraction`, `retrieval`, `verdict`, `workflow`), budget with 90/100/110% actual and 110% forecast alerts, Log Analytics cap alert, diagnostic settings
- [ ] `infra/modules/` -- only if a composition is reused; otherwise leave empty with a `.gitkeep`
- [ ] `.github/workflows/infra-pr.yml` -- fmt, validate, plan and plan comment on pull requests touching `infra/`, OIDC login, repository variables for tenant, subscription and client ids
- [ ] `docs/standards/azure.md` -- add the new resource types' abbreviations and name patterns to a project table (`srch`, `di`, `lang`, `dts`, `st`) and the runtime role list placeholder -- rule 2
- [ ] Run the bootstrap, set the GitHub repository variables and `demo` environment, then `terraform init`, `plan -out=tfplan`, `apply tfplan` for the stack

**Acceptance Criteria:**
- Given the applied stack, when `az resource list -g rg-aiuw-demo-wus3` is read, then every resource in the task list exists in West US 3 with the six tags.
- Given each data or AI resource, when its auth settings are queried with `az`, then local, key or password auth is disabled.
- Given the two model deployments, when queried, then each shows the pinned version, Global Standard and auto-upgrade off.
- Given the applied stack, when `terraform plan` runs again, then it reports no changes.

## Implementation Notes

## Spec Change Log

## Review Triage Log

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
