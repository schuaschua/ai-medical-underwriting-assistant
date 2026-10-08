# Azure standards (org baseline)

> **Org baseline, version 1.0.0** (Org Kit, module `org`). Projects copy this file into their standards folder and extend it: fill in the placeholders, add project rules below the baseline ones, and record any deviation under [Accepted exceptions](#accepted-exceptions). Never delete or weaken a baseline rule silently.

These rules apply to everything in `infra/` and to any code that talks to Azure. The project's architecture document is binding. If a rule here seems to disagree with it, raise it and fix one of them; don't pick one silently.

Placeholders: `<project>` is the workload short name, `<env>` the environment (e.g. `demo`, `dev`, `prod`), `<region>` the Azure region name (e.g. `southeastasia`), `<rgn>` the project's short region code (e.g. `sea`), `<org>` the organisation's short code.

Citations: "WAF p. n" and "WAF-AI p. n" are pages in the Azure Well-Architected Framework PDFs listed under [Where to look deeper](#where-to-look-deeper). CAF links go to Microsoft Learn.

## Naming pattern

Most names follow `<abbr>-<project>-<env>-<rgn>[-<role>]`. Names that can't contain hyphens (registries and storage accounts) drop the hyphens. CAF leaves region codes to each project; record yours in the project copy. Abbreviations come from [CAF resource abbreviations](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-abbreviations), and the component order comes from [CAF naming](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-naming).

| Resource | Abbr | Name pattern |
| --- | --- | --- |
| Resource group | `rg` | `rg-<project>-<env>-<rgn>` |
| Resource group (Terraform state, central for all projects) | `rg` | `rg-tfstate-<rgn>` |
| Storage account (Terraform state, central; one container per project) | `st` | `st<org>tfstate<rgn>` |
| Container registry | `cr` | `cr<project><env><rgn>` |
| Container Apps environment | `cae` | `cae-<project>-<env>-<rgn>` |
| Container app | `ca` | `ca-<project>-<env>-<rgn>[-<role>]` |
| PostgreSQL flexible server | `pgsql` | `pgsql-<project>-<env>-<rgn>` |
| Log Analytics workspace | `log` | `log-<project>-<env>-<rgn>` |
| Application Insights | `appi` | `appi-<project>-<env>-<rgn>` |
| Foundry account (AIServices) | `aif` | `aif-<project>-<env>-<rgn>` (also its custom subdomain) |
| Foundry project | `proj` | `proj-<project>-<env>-<rgn>` |
| User-assigned managed identity (runtime) | `id` | `id-<project>-<env>-<rgn>-<role>` |
| User-assigned managed identity (pipeline deployment) | `id` | `id-<project>-<env>-<rgn>-deploy` |
| Budget | none in CAF | `budget-<project>-<env>` |
| Azure Monitor action group | `ag` | `ag-<project>-<env>-<rgn>` |
| Azure Monitor alert rule (scheduled query) | `ar` | `ar-<project>-<env>-<rgn>-<role>`, e.g. `ar-<project>-<env>-<rgn>-log-cap` |

**Required tags** go on the resource group and on every resource that supports tags. Keys are lowercase, and values are lowercase and exact ([CAF tagging](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-tagging)):

| Key | Value |
| --- | --- |
| `workload` | `<project>` |
| `env` | `<env>` |
| `owner` | the responsible person's alias (never a password or other personal data) |
| `managedby` | `terraform` |
| `datatype` | the data classification, e.g. `synthetic` for a POC |
| `repo` | the repository URL |

## Rules

### Naming & tagging

1. Build every name in one Terraform `locals` block (or a naming module) from `workload`, `env` and `region_short`. Never hand-type a resource name inside a module call. ([CAF naming](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-naming))
2. Use only the abbreviations in the table above. Any new resource type takes its abbreviation from the [CAF list](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-abbreviations), and is added to the project's table in the same PR.
3. Pass a single `local.tags` map containing the six required tags to every AVM module and resource. Code review rejects any taggable resource that doesn't carry it. (WAF p. 866, WAF p. 550)
4. Never put secrets, personal data or real customer data in names or tags. ([CAF tagging](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-tagging))

### Region & subscription

5. Deploy every resource to the project's approved region `<region>`, including AI accounts, the Log Analytics workspace and Application Insights. `location` must come from a variable whose default is `<region>`. (WAF p. 816, WAF p. 1126)
6. Deploy only to the subscription approved for the project. Take the subscription ID from `ARM_SUBSCRIPTION_ID`, and never hard-code it in committed code. A new subscription means re-checking model access and quotas first.

### Identity & secrets

7. Services authenticate with managed identity only. Each runtime component that reaches data or AI services uses a user-assigned identity (e.g. for PostgreSQL with Entra auth, for a Foundry endpoint). (WAF p. 861)
8. Turn off key and password auth wherever Azure lets you: for example Foundry `disableLocalAuth = true`, PostgreSQL Entra-only authentication, the ACR admin user disabled, Application Insights local authentication off. (WAF p. 862, WAF p. 943)
9. Grant runtime identities only the data-plane roles they need, at the narrowest scope (for example **Foundry User** on the Foundry project, **Monitoring Metrics Publisher** on Application Insights, **AcrPull** for each identity that pulls images), and list them in the project copy. Runtime identities never get Owner, Contributor, or roles scoped to the subscription. The pipeline's deployment identity is the one exception, with the resource-group roles in rule 31. (WAF p. 258)
10. Keep application secrets to the minimum and store them only in the platform secret store (Container Apps secrets, or Key Vault once the project has one). Never put a secret in images, git, `.tfvars`, logs or model context. (WAF p. 307)

### Networking

11. Every endpoint uses TLS; container app ingress is HTTPS-only (`allowInsecure: false`). Public endpoints versus private networking is an architecture decision; public endpoints on anything that holds real data are an accepted exception at most. (WAF p. 861)
12. Enforce sign-in both at the edge (Container Apps authentication with Entra) and in the application: every API route returns 401 without a signed-in principal. Allowing unauthenticated requests at the edge (`AllowAnonymous`, e.g. for a signed-out landing page) is flagged by Azure Policy and must be recorded as an accepted exception. (WAF p. 869)

#### If your project uses PostgreSQL Flexible Server

13. The firewall allows only Azure services, plus two kinds of temporary rule: an operator IP rule for the `pgaadauth_create_principal` bootstrap, deleted as soon as the bootstrap is done, and the pipeline's runner-IP rule for the migration step, which a cleanup step always removes, even on failure (terraform.md rule 36). Keep `require_secure_transport` on and connection throttling enabled. Opening the firewall wider is an accepted exception with an end date. (WAF p. 944, WAF p. 948)

### Observability

14. Use exactly one Log Analytics workspace and one workspace-based Application Insights instance per environment, in the same region. Connect any Foundry project to that Application Insights instance. (WAF p. 814, WAF p. 816)
15. Send diagnostic settings for the Container Apps environment, databases and AI accounts to that workspace, with only the log categories you need. Don't add duplicate settings. (WAF p. 1126, WAF p. 1131)
16. Configure OpenTelemetry sampling in every service, and never log auth or session tokens, `Authorization` headers or connection strings. (WAF p. 815, WAF p. 861)

### Cost

17. Create a resource-group budget in the stack that owns the resource group, with actual-cost alerts at 90%, 100% and 110%, plus a forecast alert at 110%, all sent to the owner. (WAF p. 386-387)
18. Give the Log Analytics workspace a daily cap (start at 0.5 GB/day for non-production) with an alert when usage reaches 90% of the cap, and set retention to the 30-day minimum unless the project needs longer. (WAF p. 816, WAF p. 1133, WAF p. 430)
19. Use the cheapest tier that works: for example Container Apps on the Consumption profile, PostgreSQL on Burstable with the smallest storage (storage can't be scaled down), ACR Basic, and model deployments on pay-per-token with low deployment capacity (TPM). Any higher tier needs a PR note saying why. (WAF p. 863, WAF p. 944-945)
20. Record the project's compute ceilings (replicas, CPU and memory, idle timeouts, per-user rate limits) in the architecture and keep them in code; raising one is an architecture change.
21. Never destroy an environment automatically or on an agent's initiative. The owner decides when to destroy it; when they do, destroy the stacks in reverse apply order, keeping the state storage. (WAF p. 431, WAF-AI p. 112)

### Reliability

22. Configure startup, readiness and liveness probes on every container app. If your project uses database migrations, the app never runs them at startup; readiness fails unless the database's schema revision equals the migration head bundled in the image. (WAF p. 858-859)
23. Use the platform's default backups as the minimum (PostgreSQL Flexible Server: 7-day point-in-time restore). High availability, zone redundancy and longer retention follow the project's agreed RTO and RPO. (WAF p. 945)

### If your project uses Microsoft Foundry (AI)

24. Pin every model deployment to an exact model version with auto-upgrade off (`NoAutoUpgrade`). The deployment name reaches code only as configuration. Record the model's retirement date and plan the swap before it. (WAF-AI p. 35, WAF-AI p. 114)
25. Keep the default content filter (RAI policy) on every deployment, and never attach a custom policy that weakens it. (WAF-AI p. 45)
26. An agent has only the tools its architecture lists, with no other Foundry tools or connections; adding one needs an architecture decision. Consequential actions (submitting, approving, paying, declaring) stay human. (WAF-AI p. 27-28)
27. Keep agent prompts under version control, together with a scenario evaluation set that checks tool-call accuracy. Run the set before changing the prompt or the model. (WAF-AI p. 35, WAF-AI p. 118-119)

### Terraform-on-Azure interplay

28. Use Azure Verified Modules (`Azure/avm-res-*`, versions pinned) first, then hand-written `azurerm`, and `azapi` only where `azurerm` doesn't model the resource (see terraform.md rule 12). Never use provisioners.
29. Keep the state backend in the central account `st<org>tfstate<rgn>` (container `<project>`), using Entra auth (`use_azuread_auth = true`), shared-key access disabled, and blob versioning on. Stacks share values only through remote-state outputs.
30. Apply stacks in the order the architecture defines, and a human approves every plan. Any manual step (such as a database principal bootstrap) is recorded in `infra/README`. (WAF p. 865)
31. A bootstrap script, run once by an operator who holds Owner, creates `rg-<project>-<env>-<rgn>` with the six required tags, registers the resource providers the stacks use, and creates the deployment identity `id-<project>-<env>-<rgn>-deploy` in the state resource group with federated credentials for the repository's `pull_request` and `environment:<env>` subjects, using the repo's GitHub OIDC subject prefix read from GitHub's API (the immutable `repo:<owner>@<id>/<repo>@<id>` form), never built from the repo name. That identity holds exactly **Contributor** on the project resource group; **Role Based Access Control Administrator** on that resource group, with a condition that it can assign or remove only the runtime roles in rule 9, and only for service principals; and **Storage Blob Data Contributor** on the project's state container. It has nothing at subscription scope, no Owner and no directory rights. The first stack adopts the resource group with an `import` block, and the `azurerm` provider sets `resource_provider_registrations = "none"`.

## Project additions (aiuw)

Project values: `<project>` is `aiuw`, `<env>` is `demo`, `<region>` is `westus3`, `<rgn>` is `wus3`. The central state account `stdjtfstatesea` in `rg-tfstate-sea` keeps its own region code.

### Project resource names (rule 2)

Resource types this project adds to the baseline table. Abbreviations are from the CAF list.

| Resource | Abbr | Name pattern |
| --- | --- | --- |
| Storage account (application data) | `st` | `st<project><env><rgn>` |
| Azure AI Search service | `srch` | `srch-<project>-<env>-<rgn>` |
| Document Intelligence account | `di` | `di-<project>-<env>-<rgn>` (also its custom subdomain) |
| Azure AI Language account | `lang` | `lang-<project>-<env>-<rgn>` (also its custom subdomain) |
| Durable Task Scheduler | `dts` | `dts-<project>-<env>-<rgn>` |
| Durable Task Scheduler task hub (child of the scheduler) | none in CAF | `<project>-<env>` |
| Diagnostic setting (child of the resource it reads) | `diag` | `diag-<project>-<env>-<rgn>` |
| PostgreSQL database (child of the server) | none | `<project>` |
| Container Apps job | `caj` | `caj-<project>-<env>-<rgn>-<role>` |

Three things belonging to the environment are not `westus3` resources in `rg-aiuw-demo-wus3`: the action group `ag-aiuw-demo-wus3` (Azure Monitor action groups are global, location `global`); the budget `budget-aiuw-demo` (a budget has no region); and the deployment identity `id-aiuw-demo-wus3-deploy`, which has location `westus3` but sits in the central state resource group `rg-tfstate-sea` (rule 31).

### Runtime roles (rule 9)

Placeholder until the `app` stack assigns them (story 1.3); the source is the architecture spine, section "Deployment". The `foundation` stack creates the identities and assigns no role. The deployment identity's condition in `infra/bootstrap/state-backend.sh` allows exactly these roles, for service principals only; change both lists together.

| Role | Scope | Held by |
| --- | --- | --- |
| AcrPull | Container registry | all seven runtime identities |
| Monitoring Metrics Publisher | Application Insights | all seven runtime identities |
| Storage Blob Data Contributor | One blob container | `intake` (`originals`, `cases`), Azure AI Language's own identity (`cases`) |
| Storage Blob Data Reader | One blob container | `retrieval` (`manual`), `classification` (`classifier-training`), Azure AI Language's own identity (`originals`), Document Intelligence's own identity (`classifier-training`) |
| Cognitive Services User | Azure AI Language, Document Intelligence or the Foundry account | `intake` (Language), `classification` and `retrieval` (Document Intelligence), Azure AI Search's own identity (Foundry account) |
| Foundry User | Foundry project | `classification`, `extraction`, `retrieval`, `verdict` |
| Search Index Data Contributor, Search Service Contributor | Azure AI Search | `retrieval` |
| Durable Task Data Contributor | Task hub | `workflow` |

PostgreSQL access is not an Azure role: each service identity except `web` gets its own database role (spine AD-4).

## Commonly deferred (revisit triggers)

Projects that defer any of these list them in their copy with their own trigger.

| Often deferred in a POC | Revisit when |
| --- | --- |
| Availability zones, 3+ replicas | Real users or an uptime target (SLO) are agreed (WAF p. 857) |
| Private endpoints, VNet integration, NSGs, egress control | Real data, or compliance comes into scope (WAF p. 860) |
| Key Vault for secrets and keys | More than one secret, or a key rotation requirement (WAF p. 862) |
| AI gateway (API Management) with per-user rate limits and token quotas | A second client of the same agent or model (WAF-AI p. 39) |
| Database high availability, Azure Backup vault | Real data, or a recovery target is set (WAF p. 942) |
| Azure Policy tag enforcement, Defender for Cloud | A second environment, or a shared subscription owner asks for it (WAF p. 866) |

## Accepted exceptions

Projects record their own deviations from this baseline here, each with who approved it and what closes it. The org baseline ships this table empty.

| Rule | Exception | Approved by (role) and date | Close by |
| --- | --- | --- | --- |
| 12 | No sign-in at the edge or in the application: the demo is open, with a two-role switcher (architecture spine AD-9). | Project owner (Darrel), 2026-10-06 | Before real data, or any audience beyond the demo |

## Where to look deeper

- The Azure Well-Architected Framework PDF (`azure-well-architected.pdf`; projects may keep a local copy in their standards folder under `azure/`) has these service guides: Container Apps p. 856, PostgreSQL p. 941, Application Insights p. 810, Log Analytics p. 1124, and cost alerts p. 386.
- The AI workloads PDF (`azure-well-architected-ai.pdf`) covers the AI architecture pattern (p. 21), application design (p. 30), operations (p. 110) and testing and evaluation (p. 117).
- PDFs go out of date. Check the online versions before acting: [Well-Architected Framework](https://learn.microsoft.com/azure/well-architected/), [AI workloads](https://learn.microsoft.com/azure/well-architected/ai/), and [service guides](https://learn.microsoft.com/azure/well-architected/service-guides/).
- CAF: [abbreviations](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-abbreviations), [naming](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-naming), [tagging](https://learn.microsoft.com/azure/cloud-adoption-framework/ready/azure-best-practices/resource-tagging).
