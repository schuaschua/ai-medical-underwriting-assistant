# Web verification review — Architecture Spine, AI Medical Underwriting Assistant POC

- Reviewed: `../ARCHITECTURE-SPINE.md` and `../.memlog.md`
- Date of checks: 2026-10-06. Every claim below was checked against a live web source today (Microsoft Learn pages fetched as markdown, PyPI and npm registry JSON, GitHub raw files, the Azure retail price API). Nothing is asserted from memory.
- Status key: **CONFIRMED**, **WRONG** (correction given), **PARTLY** (true with a condition the spine does not state), **NOT VERIFIED**.
- The spine was not edited.

## Verdict

The stack is real and current: all 24 pinned package versions exist and are the latest stable releases, and the core platform choices (pgvector at 3,072 dimensions, Azure AI Search Basic, Durable Task Scheduler with managed identity, Agent Framework, Document Intelligence classifier, Dapr invocation over HTTP) hold. Five points need a change to the spine; the most serious are that `r6` cannot do real agentic retrieval on the pinned stable SDK, and that TypeScript 7.0.2 cannot be used with the `typescript-eslint` the project's own coding standard requires.

## Findings that require a change to the spine

### F1 — HIGH — AD-11, AD-16, Stack: `r6` (agentic retrieval) is not agentic on `azure-search-documents` 12.0.0

- The stable API (`2026-04-01`), which is what `azure-search-documents` 12.0.0 targets, supports only "minimal, extractive retrieval". LLM query planning, non-minimal reasoning effort and answer synthesis are **preview only** (`2026-08-01-preview`). With `minimal` effort the LLM step is skipped and the query goes straight to the knowledge source with semantic reranking, which is close to what `r5` already does. The bake-off row would not test what it claims to.
  - "Use the `2026-04-01` REST API for production workloads that use generally available knowledge source types with minimal, extractive retrieval. Use the `2026-08-01-preview` REST API for ... LLM-based query planning, answer synthesis, non-minimal retrieval reasoning effort" — https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview (ms.date 2026-09-16)
  - "The `2026-04-01` API version only supports the `intents` input and minimal, extractive retrieval." — https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-retrieve (ms.date 2026-09-04)
  - "an LLM is optional in the `2026-08-01-preview` API version and unsupported in the `2026-04-01` API version"; preview needs `pip install --pre azure-search-documents` — https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-create-knowledge-base (ms.date 2026-08-12)
- `gpt-5.4` is a supported knowledge-base model, but only in `2026-05-01-preview` and `2026-08-01-preview` (same page, "Supported models" table).
- The preview SDK is `azure-search-documents` 12.1.0b2 (2026-08-28), default API `2026-08-01-preview`, requires Python 3.10+ — https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/search/azure-search-documents/CHANGELOG.md and https://pypi.org/project/azure-search-documents/#history
- **Correction:** either pin `azure-search-documents` 12.1.0b2 (a beta, with breaking changes between betas) for `r6` and record that `r6` runs on a preview API with no SLA, or keep 12.0.0 and state that `r6` is "knowledge base, minimal effort" (no LLM planning).
- Two infrastructure items follow if the LLM is used, and neither is in the spine:
  - The **search service's own managed identity** needs **Cognitive Services User** on the Foundry resource (same knowledge-base page). The deployment diagram has no `search --> foundry` edge and the `app`/`foundation` stacks list no such role assignment. AD-16 ("all services use the same deployment, reached with managed identity") should name Azure AI Search as a caller.
  - Vector subqueries in agentic retrieval need a **vectorizer** on the index so the search service embeds subqueries itself: "having a vectorizer defined in the vector search configuration is critical. It determines whether your vector field is used during query execution" — https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-create-index (ms.date 2026-06-02). Without it `r6` is text plus rerank only. The index also needs a semantic configuration (required).
- AD-11 result shape: retrieve returns references with an optional `rerankerScore`; when a source bypasses reranking the score is omitted (retrieve page above). `rank` and `score` for `r6` must be derived from the references; that mapping is not automatic.

### F2 — HIGH — Stack: TypeScript 7.0.2 conflicts with the project's lint standard

- TypeScript 7.0.2 is a real, stable release (npm `latest`, published 2026-07-08) and is the native (Go) compiler — https://registry.npmjs.org/typescript
- It ships the `tsc` binary but no importable compiler API; the upstream status table lists "API: not ready" — https://github.com/microsoft/typescript-go/blob/main/README.md
- `typescript-eslint` 8.71.1 (latest) declares peer `typescript: >=4.8.4 <6.1.0` — https://registry.npmjs.org/typescript-eslint/latest . `docs/standards/coding-style.md` line 19 requires ESLint with `typescript-eslint` recommended rules, so the standard cannot be met on TypeScript 7.0.2 (npm refuses the peer range; forcing it crashes ESLint). Secondary reports: https://mergify.com/blog/native-typescript-compiler-faster-typecheck , https://dev.to/the-modern-web/why-angular-vue-and-eslint-cant-upgrade-to-typescript-70-yet-and-why-ts-71-changes-441g
- Vite 8.3.3 itself has no TypeScript peer dependency and does not use `tsc` to transpile, so "usable with vite 8.3.3" is true for building; the conflict is with linting.
- **Correction:** pin TypeScript **6.0.3** (latest 6.x, published 2026-04-16, inside the `typescript-eslint` range), or keep 7.0.2 and record an exception to the coding standard's ESLint rule. Revisit at TypeScript 7.1.

### F3 — MEDIUM — Deployment section: `workflow` does not need a permanent replica

- The spine says "`workflow` keeps 1 replica so it stays connected to the scheduler". A KEDA scaler exists for exactly this: "Autoscaling is supported for apps built using the Durable Task SDKs and hosted in Azure Container Apps. This feature uses the `azure-durabletask-scheduler` KEDA scaler." and "Setting `minReplicas` to `0` enables scale-to-zero, which saves costs when idle but introduces cold-start latency" — https://learn.microsoft.com/en-us/azure/durable-task/sdks/durable-task-scheduler-auto-scaling (ms.date 2026-04-30). Also "Worker container apps scale down to zero replicas when there are no pending workflow tasks" — https://learn.microsoft.com/en-us/azure/container-apps/workflows-overview (ms.date 2026-07-29).
- The scale rule authenticates with a managed identity (user-assigned supported) and takes `endpoint`, `taskhubName`, `workItemType` (`Orchestration`, `Activity`, `Entity`) and `maxConcurrentWorkItemsCount`.
- **Correction:** the stated reason is wrong. Either keep `min_replicas = 1` as a latency choice and say so, or add `azure-durabletask-scheduler` scale rules (one for `Orchestration`, one for `Activity`) to `workflow` in the `app` stack and let it scale to zero like the others.

### F4 — MEDIUM — AD-13, AD-4, deployment: the Document Intelligence classifier needs a training container that nothing owns

- Training reads from Blob Storage: the build request takes `azureBlobSource` (folder per class) or `azureBlobFileListSource` (JSONL file list) — https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/train/custom-classifier (ms.date 2026-08-15).
- Each training document also needs its Layout result stored beside it: "When using the API or SDK to train a classifier, you need to add the layout results to the folders containing the individual documents" (`*.ocr.json`) — https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/how-to-guides/build-a-custom-classifier (ms.date 2026-05-21).
- The spine has no blob container for classifier training data in AD-4's ownership table, no `classification --> blob` or `di --> blob` edge in the deployment diagram, and no role assignment giving the Document Intelligence resource's managed identity read access to Storage (AD-18 disables keys, so a SAS URL is not an option).
- Default split behaviour changed in v4.0: "The default for `splitMode` is `none`" — the service returns one class for the whole file unless `splitMode` is `perPage` or `auto` (classifier page above). `classification` works per page, so it must send single-page input or set `splitMode=perPage`. AD-13 should say which.
- **Correction:** add a training container (owner `classification`), the Storage Blob Data Reader assignment for the Document Intelligence identity, the Layout pre-processing step, and the split mode.

### F5 — LOW/MEDIUM — Open question on Durable Task Scheduler price: sources conflict, plan on the higher reading

- Retail price API, `westus3`: meter "Consumption Action", `unitPrice` 0.003 USD, `unitOfMeasure` "1" — https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20'Durable%20Task%20Scheduler'
- Pricing page: "Consumption Plan — Meter: 1 action dispatched — Price: $- per action" (price loads per region) — https://azure.microsoft.com/en-us/pricing/details/functions/
- Billing doc formula: "`(monthly actions ÷ 1,000,000) × regional price per million actions`" — https://learn.microsoft.com/en-us/azure/durable-task/scheduler/durable-task-scheduler-billing (ms.date 2026-05-01)
- Two first-party sources say per action (USD 0.003 each, USD 3,000 per million); one says per million. **NOT VERIFIED** which is right. What an action is, is confirmed: a message the scheduler dispatches — orchestration start, activity start, activity result, timer, external event; one activity call costs two actions (billing doc).
- **Correction:** replace the open question with "USD 0.003 per action per the price API and pricing page; the billing doc's 'per million' wording conflicts; budget on per action (about 2 actions per stage call plus 1 per human event, so roughly USD 0.02–0.05 per page) and confirm on the first invoice."

### F6 — LOW — Stack notes worth recording (no decision is wrong)

- **`agent-framework` 1.20.0 is a meta-package** that installs `agent-framework-core[all]==1.20.0`, every optional integration — https://pypi.org/pypi/agent-framework/1.20.0/json . The Azure path the docs now recommend is `pip install agent-framework-openai` (1.15.0, requires `agent-framework-core>=1.20.0`, `openai>=2.25.0,<4`) — https://learn.microsoft.com/en-us/agent-framework/integrations/by-component/model-providers/openai (ms.date 2026-10-02). Pinning `agent-framework-core` 1.20.0 + `agent-framework-openai` 1.15.0 gives a much smaller image.
- **`openai` 3.x uses HTTPX2, not httpx** (`httpx2>=2.12.0`) — https://pypi.org/pypi/openai/3.24.0/json and https://github.com/openai/openai-python/blob/v3.24.0/README.md . `azure-monitor-opentelemetry` 1.8.10 bundles instrumentation for `httpx`, `fastapi`, `psycopg2`, `requests`, `urllib3` — not `httpx2` and not `psycopg` 3 — https://pypi.org/pypi/azure-monitor-opentelemetry/1.8.10/json . Dapr calls (httpx) and FastAPI are traced automatically; model calls and database calls are not, so the "OpenTelemetry in every service" convention needs explicit instrumentation for those two. Test stubs for the model must mock HTTPX2.
- **PyMuPDF is AGPL-3.0 or commercial** ("Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License") — https://pypi.org/pypi/pymupdf/1.28.2/json . Fine for an internal synthetic-data demo; a licence decision is needed before anything is distributed or offered as a service.
- **Node version:** `vitest` 5.0.3 requires Node `^22.12.0 || ^24.0.0 || >=26.0.0`; `vite` 8.3.3 requires `^20.19.0 || >=22.12.0` — https://registry.npmjs.org/vitest , https://registry.npmjs.org/vite . The stack table pins no Node version; it must be 22.12+ or 24.

## Claim-by-claim results

### 1. AD-12 — vectors

| # | Claim | Status | Evidence |
| --- | --- | --- | --- |
| 1.1 | pgvector `vector` type can store 3,072 dimensions | CONFIRMED | "Vectors can have up to 16,000 dimensions." — https://github.com/pgvector/pgvector/blob/master/README.md |
| 1.2 | Exact search works without an index at 3,072 dimensions | CONFIRMED | "By default, pgvector performs exact nearest neighbor search, which provides perfect recall." The 2,000-dimension limit applies only to HNSW/IVFFlat indexes on `vector` (same README). AD-12 uses no approximate index, so the limit is not hit. |
| 1.3 | `halfvec` | CONFIRMED (not needed) | "Half vectors can have up to 16,000 dimensions"; indexable up to 4,000. Only relevant if an index is added later: a `halfvec(3072)` HNSW index would fit, a `vector(3072)` one would not (same README). |
| 1.4 | pgvector version on Flexible Server | CONFIRMED | Extension `vector` is **0.8.2** on PostgreSQL 13–18 (0.7.0 on 12, 0.5.1 on 11) — https://learn.microsoft.com/en-us/azure/postgresql/extensions/concepts-extensions-versions (ms.date 2026-07-10). The table is not per region, so the spine's open question "in `westus3`" is answered: yes. Upstream latest is 0.8.7; nothing AD-12 needs is missing in 0.8.2 (halfvec arrived in 0.7.0). |
| 1.5 | Extension must be allow-listed first | PARTLY (not in spine) | Add `vector` to `azure.extensions` before `CREATE EXTENSION vector` — https://learn.microsoft.com/en-us/azure/postgresql/extensions/how-to-use-pgvector (ms.date 2026-07-10). A Terraform server parameter in `foundation`. |
| 1.6 | Azure AI Search vector field can hold 3,072 dimensions on Basic | CONFIRMED | "Maximum dimensions per vector field: 4096" on every tier including Basic — https://learn.microsoft.com/en-us/azure/search/search-limits-quotas-capacity (ms.date 2026-09-16). `text-embedding-3-large` "ranges from 1 to 3072" — https://learn.microsoft.com/en-us/azure/search/vector-search-how-to-create-index |
| 1.7 | Exhaustive (exact) KNN on Basic | CONFIRMED | `vectorSearch.algorithms` "is either `hnsw` or `exhaustiveKnn`"; prerequisite is a service "in any region and on any tier" (create-index page). Query-time `exhaustive: true` also forces exact search on an HNSW field — https://learn.microsoft.com/en-us/azure/search/vector-search-how-to-query (ms.date 2026-04-27) |
| 1.8 | Basic has room for the vectors | CONFIRMED | Basic vector quota 5 GB per partition, 15 GB storage, for services created after April 2024 in West US 3 (limits page). A manual of a few thousand chunks at 3,072 float32 is tens of MB. |
| 1.9 | Semantic ranker runs on Basic | CONFIRMED | Throttling table lists Basic: 2 concurrent requests and queue of 4 per search unit (limits page); "Semantic ranker: Runs on the Free tier but not recommended for large workloads" — https://learn.microsoft.com/en-us/azure/search/search-sku-tier (ms.date 2026-08-04). Two concurrent rerank calls per unit is a real ceiling for a parallel eval run over `r5`/`r6`. |
| 1.10 | Agentic retrieval runs on Basic | CONFIRMED | Basic: 15 knowledge sources, 15 knowledge bases, 10 sources per base (limits page). Managed-identity access to models needs "Basic tier or higher" (knowledge-base page). West US 3 has agentic retrieval and semantic ranker — https://learn.microsoft.com/en-us/azure/search/search-region-support (ms.date 2026-08-24) |
| 1.11 | Agentic retrieval is usable as a ladder row on the pinned SDK | WRONG as written | See F1. |

### 2. AD-3 — Dapr service invocation

| # | Claim | Status | Evidence |
| --- | --- | --- | --- |
| 2.1 | Service invocation is a supported managed Dapr API on Container Apps | CONFIRMED | "Service-to-service invocation — GA" — https://learn.microsoft.com/en-us/azure/container-apps/dapr-overview (ms.date 2026-01-30, updated 2026-08-10) |
| 2.2 | Plain HTTP to `http://localhost:3500/v1.0/invoke/<app-id>/method/<path>` | CONFIRMED | "The Dapr sidecar runs on HTTP port 3500 and gRPC port 50001" (overview). "make a local HTTP request to the Dapr sidecar ... `http://localhost:3500/v1.0/invoke/<DAPR_APP_ID>/method/<METHOD_NAME>`" — https://learn.microsoft.com/en-us/azure/container-apps/connect-apps (ms.date 2026-03-31, updated 2026-08-31) |
| 2.3 | Works between apps with internal ingress | CONFIRMED | Works even with no ingress: "You can enable Dapr on a container app without configuring HTTP ingress ... other Dapr-enabled apps can still invoke it through Dapr service invocation" (connect-apps). mTLS between sidecars is automatic. |
| 2.4 | "Known limitations for Dapr service invocation" | PARTLY | The overview's sentence is unlinked text; the page's Limitations section lists nothing specific to service invocation (no Dapr Configuration spec, only listed sidecar settings, actor reminders need 1+ replica, no Dapr on jobs). Consequences for this design: (a) Dapr **access-control policies and resiliency specs are unavailable**, so "any call not drawn is forbidden" cannot be enforced by Dapr — it is a code convention only; (b) the Dapr version is platform-managed and cannot be pinned — https://learn.microsoft.com/en-us/azure/container-apps/faq ; (c) sidecar request body limit defaults to 4 MB (`dapr.httpMaxRequestSize`) — https://learn.microsoft.com/en-us/azure/container-apps/enable-dapr . The `web` → `intake` PDF upload goes through the sidecar and will fail above 4 MB unless `httpMaxRequestSize` is raised on both apps. |
| 2.5 | App id uniqueness | CONFIRMED | App id defaults to the container app name and must be unique in the environment (connect-apps). |

### 3. AD-5 and deployment — Durable Task Scheduler

| # | Claim | Status | Evidence |
| --- | --- | --- | --- |
| 3.1 | Worker on Container Apps needs one permanent replica | WRONG | See F3. |
| 3.2 | Managed-identity auth from a user-assigned identity | CONFIRMED | "You can use either a user-assigned or system-assigned managed identity. User-assigned identities are recommended." Connection string `Endpoint=...;Authentication=ManagedIdentity;ClientID=<IDENTITY_CLIENT_ID>` — https://learn.microsoft.com/en-us/azure/durable-task/scheduler/durable-task-scheduler-identity (ms.date 2026-04-30). Roles: Durable Task Worker (processing only), Durable Task Data Contributor (superset). `workflow` starts orchestrations and raises events as well as processing, so it needs **Data Contributor**; assignable at task-hub scope. |
| 3.3 | West US 3 supported | PARTLY | The retail price API has Consumption and Dedicated meters for `westus3`, and the overview says "available in most Azure regions" and points to `az provider show` for the list — https://learn.microsoft.com/en-us/azure/durable-task/scheduler/durable-task-scheduler (ms.date 2026-05-04). No published page lists regions; a price meter is strong but indirect evidence. Run `az provider show --namespace Microsoft.DurableTask --query "resourceTypes[?resourceType=='schedulers'].locations"` before the first infrastructure story. |
| 3.4 | Consumption SKU: no base fee, 30-day retention | CONFIRMED | "No upfront costs, minimum commitments, or base fees"; 500 actions/second; 30 days; 10 schedulers per region, 5 task hubs per scheduler (billing page). No high availability on Consumption. |
| 3.5 | Price unit of a "Consumption Action" | NOT VERIFIED | See F5. |
| 3.6 | 1 MB payload limit (AD-6) | CONFIRMED | Orchestrator, activity inputs/outputs and external event data: 1 MB each (scheduler overview). |
| 3.7 | `case_id` as instance id | CONFIRMED | Instance ids up to 100 printable-ASCII characters (scheduler overview); a UUIDv7 string fits. |
| 3.8 | Python SDK GA; emulator | CONFIRMED (spot check) | `durabletask-azuremanaged` GA — https://learn.microsoft.com/en-us/azure/durable-task/sdks/durable-task-overview ; 1.11.0 on PyPI, uploaded 2026-09-29, "Production/Stable". |

### 4. AD-15 — Microsoft Agent Framework

| # | Claim | Status | Evidence |
| --- | --- | --- | --- |
| 4.1 | `agent-framework` 1.20.0 exists and is GA | CONFIRMED | PyPI 1.20.0 uploaded 2026-10-02, classifier "Production/Stable"; 1.0.0 shipped 2026-04-02; GitHub release `python-1.20.0` is not a prerelease — https://pypi.org/project/agent-framework/#history , https://github.com/microsoft/agent-framework/releases |
| 4.2 | Azure OpenAI / Foundry model deployments with managed identity | CONFIRMED | "Azure OpenAI now uses the same Python OpenAI clients ... pass explicit Azure routing inputs such as `credential` or `azure_endpoint`"; `OpenAIChatClient(azure_endpoint=..., api_version=..., credential=...)` takes any `azure-identity` credential; "`credential=` is now the preferred Azure auth surface" — https://learn.microsoft.com/en-us/agent-framework/integrations/by-component/model-providers/openai (ms.date 2026-10-02). Use `ManagedIdentityCredential(client_id=...)` rather than `DefaultAzureCredential` in production — https://learn.microsoft.com/en-us/agent-framework/integrations/by-component/model-providers/azure-openai |
| 4.3 | Custom function tools | CONFIRMED | `@tool` decorator, `tools=[...]` on the agent, explicit schema from a pydantic model, runtime-only values via `FunctionInvocationContext` (hidden from the model's schema — the mechanism for "case fixed by the server") — https://learn.microsoft.com/en-us/agent-framework/agents/tools/function-tools (ms.date 2026-09-19) |
| 4.4 | A configurable step limit | CONFIRMED | `function_invocation_configuration["max_iterations"]` (default 40), plus `max_function_calls` and `max_duration_seconds` — https://github.com/microsoft/agent-framework/blob/python-1.20.0/python/packages/core/agent_framework/_tools.py . The framework degrades gracefully at the limit; returning `refer` is the service's own code, as the spine says. |
| 4.5 | Compatible with `openai` 3.24.0 | CONFIRMED | `agent-framework-openai` 1.15.0 requires `openai<4,>=2.25.0` — https://pypi.org/pypi/agent-framework-openai/json |
| 4.6 | Package choice | PARTLY | See F6 (meta-package). |

### 5. AD-13 — Document Intelligence custom classifier

| # | Claim | Status | Evidence |
| --- | --- | --- | --- |
| 5.1 | Training data must be in Blob Storage | CONFIRMED | `azureBlobSource` / `azureBlobFileListSource`; Layout results stored with the documents. See F4. |
| 5.2 | Minimum training set | CONFIRMED | "at least two distinct classes and a minimum of five document samples per class"; max 100 samples per class, 2 GB and 25,000 pages total (classifier page; also https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits , ms.date 2026-09-08). Six `page_type` values × 5 = 30 pages minimum, kept separate from the eval set. |
| 5.3 | S0 required | PARTLY | Not strictly: the limits page gives F0 classifier limits and the how-to says "You can use the free pricing tier (F0) to try the service". But F0 processes only the first two pages and 4 MB per file. S0 is the right choice; "required" is too strong. |
| 5.4 | Returns a confidence per page | PARTLY | Confidence is per identified **document** (`"confidence": 0.97` with its page regions). It is per page only when the input is one page or `splitMode=perPage`; the v4.0 default is `none` (one class for the whole file). See F4. |
| 5.5 | An `other` class | CONFIRMED | "add a few representative samples of the document types to an `other` class" — matches AD-13's `other`. |
| 5.6 | API and SDK current | CONFIRMED | v4.0 `2024-11-30` is GA and current; `azure-ai-documentintelligence` 1.0.2 (2025-03-27) is still the latest release. Document Intelligence is now presented "as part of Azure Content Understanding capabilities" with no retirement notice for v4.0 — https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/overview (ms.date 2026-09-08) |

### 6. Stack table

Registry checks: `https://pypi.org/pypi/<name>/<version>/json` and `https://registry.npmjs.org/<name>`, fetched 2026-10-06. Every pinned version exists, is not yanked or deprecated, and equals the registry's latest stable.

| Package | Status | Note |
| --- | --- | --- |
| fastapi 0.142.2, uvicorn 0.54.0, pydantic 2.13.5, pydantic-settings 2.15.0, sqlalchemy 2.1.3, alembic 1.20.0, psycopg 3.3.6, azure-identity 1.26.0, azure-storage-blob 12.31.0, azure-monitor-opentelemetry 1.8.10, agent-framework 1.20.0, openai 3.24.0 | CONFIRMED | Latest; classifiers list Python 3.13. sqlalchemy 2.1.3 requires Python >=3.11. |
| pgvector (Python) 0.5.0, durabletask-azuremanaged 1.11.0, pymupdf 1.28.2 | CONFIRMED | No version classifiers; `requires_python >=3.10`. pymupdf ships cp313 wheels. |
| httpx 0.28.1 | CONFIRMED, stale | Latest, but released 2024-12-06 with classifiers ending at 3.12 (`requires_python >=3.8`, pure Python, installs on 3.13). A successor `httpx2` 2.13.1 exists and `openai` has moved to it. Keeping httpx for Dapr calls is fine and is the client `azure-monitor-opentelemetry` instruments. |
| azure-ai-documentintelligence 1.0.2 | CONFIRMED | Classifiers end at 3.12; `requires_python >=3.8`, pure Python. |
| azure-search-documents 12.0.0 | PARTLY | Knowledge bases are in 12.0.0 (`KnowledgeBaseRetrievalClient`, `KnowledgeBase`, `SearchIndexKnowledgeSource`, `KnowledgeRetrievalMinimalReasoningEffort`) but minimal effort only. See F1. Classifiers list 3.13. |
| openai 3.24.0 on Azure with Entra tokens | CONFIRMED | Released 2026-10-02 (3.0.0 was 2026-08-12). `AzureOpenAI(azure_endpoint, api_version, azure_ad_token_provider=...)` is still in `src/openai/lib/azure.py` — https://github.com/openai/openai-python/blob/v3.24.0/src/openai/lib/azure.py |
| Python 3.13 supported by every listed package | CONFIRMED | Strictest floor is sqlalchemy (>=3.11); nothing caps below 3.13. |
| react 19.3.0 + react-pdf 11.0.0 | CONFIRMED | react-pdf 11.0.0 peers: `react ^19.0.0`, `react-dom ^19.0.0`, `@types/react ^19.0.0`; bundles `pdfjs-dist` 6.3.289. |
| vite 8.3.3 + vitest 5.0.3 | CONFIRMED | vitest peers `vite ^6.4.0 || ^7.0.0 || ^8.0.0`. `@vitejs/plugin-react` 6.1.2 peers `vite ^8.0.0`. Node floor: see F6. |
| TypeScript 7.0.2 | PARTLY | Stable and works with Vite; breaks `typescript-eslint`. See F2. |

### 7. Azure Container Apps

| # | Claim | Status | Evidence |
| --- | --- | --- | --- |
| 7.1 | 0.5 vCPU / 1 GiB is a valid Consumption allocation | CONFIRMED | Table lists `0.5` / `1.0Gi` — https://learn.microsoft.com/en-us/azure/container-apps/containers . The combination applies to "the total CPU and memory allocated to all the containers in a container app". |
| 7.2 | Max 2 replicas, scale to zero | CONFIRMED | Min replicas 0 allowed; default rule is HTTP, 0–10 — https://learn.microsoft.com/en-us/azure/container-apps/scale-app (ms.date 2026-05-19) |
| 7.3 | A scaled-to-zero app with internal ingress wakes on a Dapr invocation | NOT VERIFIED | First-party docs imply it but never say it: Dapr invocation "routes the request through the Envoy proxy layer. This is the same infrastructure that handles FQDN-based routing" (connect-apps), and the HTTP scale rule counts requests at that layer. The only explicit statement is the opposite case: "If ingress is disabled and you don't define a `minReplicas` or a custom scale rule, your container app scales to zero and has no way of starting back up" (scale-app). So: internal ingress must stay enabled on all six internal services (the spine does this); whether the first call succeeds or times out during cold start is untested. An old issue reports Dapr timeouts on 0→1 — https://github.com/microsoft/azure-container-apps/issues/369 (2022, closed, no resolution visible). The spine's `min_replicas` variable is the right mitigation; add a retry on the Dapr client and test it in the first deployment story. |
| 7.4 | Cool-down | CONFIRMED | 300 seconds before scaling to zero (scale-app) — a demo paused for more than five minutes will hit cold starts on every stage unless `min_replicas` is 1. |

## Could not verify

- Whether a Dapr service-invocation call wakes a callee scaled to zero cleanly (7.3).
- The billing unit behind USD 0.003 for a Durable Task Scheduler Consumption action: per action or per million (F5).
- Durable Task Scheduler availability in `westus3` from a published region list (3.3) — only the price meter and "most regions".
- What "known limitations for Dapr service invocation" the overview refers to: the sentence has no link and no matching section (2.4).
- Still open in the spine and not checked here: Foundry deployment type for `gpt-5.4` in `westus3`, and Cohere Rerank availability. Note that Azure AI Search's model table now lists `gpt-5.5` and `gpt-5.6-*`, which confirms the spine's open question that newer chat models exist.

## Method note

Scratch downloads are in `.work/webverify/` (gitignored). Microsoft Learn pages were fetched with `Accept: text/markdown` and quoted from the fetched text; `ms.date` values are the page's own metadata.
