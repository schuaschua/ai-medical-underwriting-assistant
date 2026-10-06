---
name: principles
description: Before bmad-architecture begins, settle the architecture questions (where it runs and connects; rules and priorities; solution shape and stack; data and AI; DevOps build and deploy; DevOps environments and release) with the user, or pick or skip them at their choice, asking only what the project's documents leave open, confirm a summary, and write docs/architecture/architecture.md plus one file per cloud provider; or revise them
code: AP
added: 2026-09-28
type: prompt
---

# Architecture principles

The outcome is a set of files in the architecture folder, whose paths `spine.py` reports under `architecture`:

- **`architecture.md`**: the cloud-neutral answers to the questions below, and the guardrails {user_name} sets, written before the architecture is designed.
- **One file per cloud provider** they name, such as `azure.md`, `aws.md` or `gcp.md`. A single provider still gets its own file. A solution with no cloud has none.

**Who reads them:** `bmad-architecture`, which loads every `.md` in the architecture folder as standing facts. Every AD it writes must fit the answers and honour each principle, or name the principle it departs from and say why. The files are also read by:

- you, when you challenge a spine before drawing;
- Jules, when a governance answer asks what the architecture was bound by;
- Scrooge, when he prices the environments.

**The bar:** someone who wasn't in the room can hold an AD against the files and say whether it complies.

## Check the documents before asking

Read everything the project itself says:

- the product brief or intent, the spec (`_bmad-output/specs/`) and the PRD and other planning artifacts (`_bmad-output/planning-artifacts/`);
- an existing `architecture.md` and provider files;
- any policies or guidelines the organisation has placed in the architecture folder (`architecture.other_docs`);
- the region's regulator documents: `uv run ../agent-jules/scripts/region-sources.py {project-root}`.

The kit's org standards (the `standards_folder`, such as its `azure.md`) are not a source for these answers. `bmad-architecture` loads them separately, and naming and tags are asked here like any other question.

A question the project's documents already answer is **not asked**. Record the answer with its source (`SPEC.md §3`, `<policy file> §2`) instead. Ask only the parts still open.

The one exception is **DevOps** (questions 5 and 6): always ask them, unless {user_name} chose to have you pick or skip. Where the documents say something about them, offer that as the default and have {user_name} confirm it, since pipelines are rarely written down accurately.

## Open the session

Read the documents first, then open with these words, in {communication_language}:

> Bonjournio, I am the architecture design guardian & artist. I have been tasked with gathering some data about your architecture as a pre-requisite to building architecture through BMad. I have rummaged through your files and found:

Then give one short list of what you found. Each item is an answer and where it came from, for example "Cloud only, on Microsoft Azure (SPEC.md §3)". If you found nothing, say so in place of the list. Invite {user_name} to correct anything you read wrong. This opening replaces your usual greeting whenever the session is for the architecture principles, including a first meeting.

## How to proceed

Before the first question, ask {user_name} how to go on, as one multiple-choice question:

- **Answer the questions:** the six questions below, asking only what the documents leave open.
- **Pick everything for me:** you answer every open part yourself. Use the documents first, then the safest sensible default for what the solution must do: the most common managed choice on their cloud, the stricter option when unsure. Mark each pick as yours. DevOps is picked too, since they chose not to be asked.
- **Skip the questions:** record only what the documents say. Every open part, DevOps included, becomes an open question, never a guess.

Every path ends at the same summary to confirm (below). With picks, the summary is where {user_name} corrects them.

Unattended, or told not to ask questions, take **Skip the questions** without asking. Write the files without the confirmation, and add a line under the title: "Not yet confirmed with the owner: answers come from the documents only."

## The questions

Ask them in this order, one question at a time, with its open parts together in one message. Where a part is a choice, offer the choices. Where it's open, propose a sensible default from what you read. Take the answer as given, but say so when an answer looks risky for what the solution must do. {user_name} can skip any part: it becomes an open question, never a guess.

1. **Where it runs and how it connects**
   - a. **Environment:** cloud, hybrid (cloud plus on-premises) or on-premises?
   - b. **Cloud platforms:** which ones (Azure, AWS, GCP, others)? This decides the provider files.
   - c. **Service model:** mainly IaaS, mainly PaaS, or a mix (and SaaS where it fits)? With several providers, ask per provider.
   - d. **Regions and availability:** single-region or multi-region? Within a region, high availability across availability zones or a single datacentre? Name the regions per provider.
   - e. **On-premises link** (hybrid only): how much integration is there, and how strong is the network link (for example Azure ExpressRoute or AWS Direct Connect, and its bandwidth)?
2. **Rules and priorities**
   - a. **Policies and guidelines:** are there architecture policies the solution must follow? If yes, ask them to place the documents in the architecture folder, then read them before going on. Every later part is answered from them first, citing the document, and only what they leave open is asked.
   - b. **Well-Architected priorities:** rank the six pillars from 1 (cares most) to 6: cost optimization, performance efficiency, reliability, security, operational excellence, sustainability. The ranking decides the trade-offs, so each principle says which pillar it serves.
   - c. **Security level:** Very strict, Strict, Neutral or Relaxed?
   - d. **Naming and tags:** which naming convention, per provider? Suggest `<orgname>-<region>-<appname>-<resourceType>-<number>`, with that provider's resource type abbreviations. Which tags matter most (for example owner, cost centre, environment, data classification, application)?
3. **Solution shape and stack**
   - a. **Application type:** pure web, pure mobile, or hybrid (both)? For mobile or hybrid: iOS, Android or both, and native apps per platform or one cross-platform framework (for example React Native, Flutter or .NET MAUI)?
   - b. **Architecture style:** mainly event-driven, microservices, a modular monolith or a monolith?
   - c. **Migration:** is this a migration or modernisation? If yes, how comfortable are they with AI carrying out the migration, and what must a person review?
   - d. **Approved stack:** what their IT teams know best for the front end, the back end and the database.
4. **Data and AI**
   - a. **AI:** will the project involve much AI? If yes: how familiar is the organisation with AI; how high are the data quality standards for data the AI consumes; are we building our own AI/ML models (training or fine-tuning) or only using existing ones; and which AI platform?
   - b. **Analytics:** any heavy analytics workloads (large reporting, data warehousing, big batch or streaming jobs)?
5. **DevOps: build and deploy** (always asked)
   - a. **CI:** which pipeline stack builds and tests the code: GitHub Actions, Jenkins, CircleCI, Azure DevOps (ADO) Pipelines, Bitbucket Pipelines, or another?
   - b. **CD and GitOps:** how does it deploy: ArgoCD, Flux, Tekton, the CI tool's own deploy stages, or another? For Kubernetes, push (the pipeline deploys) or pull (GitOps reconciles from a repository)?
   - c. **Source control:** where the code lives (GitHub, Azure Repos, Bitbucket, GitLab), and the branching model (trunk-based, GitHub flow, GitFlow).
   - d. **Infrastructure as code:** Terraform, Bicep, ARM, CloudFormation, CDK, Pulumi, or none? Does infrastructure go through the same pipeline as the application?
6. **DevOps: environments and release** (always asked)
   - a. **Environments:** how many (for example Dev, Test, UAT, Prod), and how strictly separated (separate subscriptions or accounts, production data kept out of lower environments)?
   - b. **Approval gates and release strategy:** what must pass before production (tests, security scans, a named approver)? How does it release (all at once, blue-green, canary, feature flags)?
   - c. **Secrets and pipeline security:** where pipeline secrets live (the platform's secret store or a vault; federated identity such as OIDC rather than stored keys), and which scans run (SAST, DAST, dependency, container image, IaC)?

Drop the parts that no longer apply: 1e unless the solution is hybrid, the mobile follow-ups to 3a for a pure web application, 3c's follow-ups unless it is a migration, 4a's follow-ups when there's no AI, and 5b's push-or-pull when nothing runs on Kubernetes. Questions 5 and 6 are never dropped.

Then offer to add any further guardrails the answers suggest, such as a cost ceiling, a recovery target (RTO and RPO) or data residency. Stop when {user_name} says so.

## Summarise and confirm

Before writing anything, give {user_name} a summary to confirm. The summary is:

- every part's answer on one line, marked *asked*, *from <document>* or *picked by Da Vinci*;
- the principles those answers lead to, each with its pillar;
- the open questions;
- the files you'll write (`architecture.md`, plus one file per provider).

Ask them to confirm or correct it. Apply their corrections and show the changed lines again. Write only once they confirm. A pick they confirm is recorded as "Da Vinci's pick, confirmed by <who>, <date>".

## The files

Where an answer goes:

| Answer | `architecture.md` | `<provider>.md` |
| --- | --- | --- |
| 1a environment | yes | |
| 1b cloud platforms | the list, linking each provider file | |
| 1c service model | the overall stance | that provider's IaaS and PaaS choices |
| 1d regions and availability | single or multi-region, HA or single datacentre | the named regions and zones |
| 1e on-premises link | whether there is one | the connection (ExpressRoute, Direct Connect, VPN) and its bandwidth |
| 2a policies, 2b pillar ranking, 2c security level | yes | |
| 2d naming and tags | | each provider's convention and tag keys |
| 3a application type, 3b style, 3c migration | yes | |
| 3d approved stack | front end, back end and database, where they don't depend on a provider | that provider's managed services |
| 4a AI, 4b analytics | yes | that provider's AI platform |
| 5 and 6 DevOps | a **DevOps** section: CI, CD/GitOps, source control and branching, IaC, environments, gates and release strategy, pipeline security | that provider's deploy targets, pipeline identity (for example workload identity federation or an IAM role) and secret store |

Principles follow the same split: cloud-neutral ones go in `architecture.md`, provider-specific ones in that provider's file. Number them `P-1`, `P-2` and so on across all the files, so each number is unique and an AD can cite `P-7` without naming a file. Never reuse a number: a dropped principle keeps its number in **Changes**.

`architecture.md`:

```markdown
# Architecture: <project>

Agreed with <user_name> on <date>. `bmad-architecture` loads this file and the provider files beside it as
standing facts. Every AD fits the answers below and honours the principles, or names the principle it departs
from and why.

## Context

| # | Question | Answer | Source |
| --- | --- | --- | --- |
| 1a | Environment | Hybrid | owner: <who, date> |
| 1b | Cloud platforms | Azure ([azure.md](azure.md)) | owner: <who, date> |
| 2b | Well-Architected priorities | 1 security, 2 reliability, 3 cost optimization, 4 operational excellence, 5 performance efficiency, 6 sustainability | Da Vinci's pick, confirmed by <who, date> |
| ... | | | |

## Policies and guidelines
- <file in the architecture folder>: <what it governs>

## DevOps

| # | Item | Answer | Source |
| --- | --- | --- | --- |
| 5a | CI | GitHub Actions | owner: <who, date> |
| 5b | CD / GitOps | ArgoCD, pull-based from the `deploy` repository | owner: <who, date> |
| 5c | Source control and branching | GitHub, trunk-based | |
| 5d | Infrastructure as code | Terraform, through the same pipeline | |
| 6a | Environments | Dev, Test, Prod; separate subscriptions; no production data below Prod | |
| 6b | Approval gates and release strategy | tests, SAST and a named approver before Prod; canary | |
| 6c | Secrets and pipeline security | OIDC federation, no stored cloud keys; SAST, DAST, dependency and image scans | |

## Principles

### P-1 <short name>
- **Rule:** <one testable sentence: must, must not, at least, only>
- **Why:** <the risk or goal it serves, and the Well-Architected pillar>
- **Source:** <context row, owner, policy or regulator document>
- **Applies to:** <the whole solution, or a named part>

## Open questions
- <question part (for example 1d) and what still needs deciding, and who decides>

## Changes
| Date | Item | Change | Why |
```

Every answer row, in `architecture.md` and the provider files, starts with its question part (`1b`, `3d`, `5a` ...). The build checks the project against them with `scripts/stack-check.py`, which finds the rows by that key and matches the names in the Answer cells. So:
- keep each Answer cell to what was chosen, because anything named there counts as agreed, even when it's written as "not Bicep". Put exclusions in a principle;
- name regions as the provider does (`UAE North`, `us-east-1`);
- list tag keys after `tags:`;
- name environments plainly (Dev, Test, UAT, Prod).

A provider file (`azure.md`, `aws.md` ...) has the same shape:

- a title, `# <Provider>: <project>`, and a line pointing back to `architecture.md`;
- a **Context** table for that provider's answers;
- its **Principles**, numbered on from the shared sequence;
- its **Open questions** and **Changes**.

Turn the answers that constrain the design into principles an AD can comply with or break. "PaaS first" becomes "P-3: use a managed PaaS service unless an AD shows why none fits". "Very strict" security becomes concrete rules. The DevOps answers usually give at least:
- "every change reaches production through the pipeline, never by hand";
- "infrastructure is changed only through <IaC tool>";
- "pipelines authenticate to the cloud by federated identity, never stored keys".

Each of these is a principle (operational excellence or security).

A later session edits the files in place: update an answer, add a principle, move an open question up once it's answered, and log each change. The summary and confirmation apply to a revision too, showing only what changes.

Never overwrite a document you didn't write. If a policy the organisation placed in the folder already has a provider's file name (for example their own `azure.md`), ask what to call yours.

## After writing

- **The build follows the answers.** `bmad-build`, `bmad-build-auto` and `bmad-code-review` run `scripts/stack-check.py`. It flags code, pipelines and infrastructure that use something not agreed here: another cloud; a different front end, back end, database or AI platform; mobile code in a pure web app; Bicep when Terraform was agreed; a GitHub Actions workflow when Jenkins was; an agreed scan missing from the pipelines; stored cloud keys when federated identity was agreed. Regions, tags and environments not named here are warnings. What files can't show goes to the reviewer as a checklist. Tell {user_name} in one line.
- **No spine yet:** say `bmad-architecture` can start now and will load the files. Suggest a fresh session (`/clear`).
- **A spine exists:** a changed answer or principle may break ADs already written. Run the drift check's principles pass (`references/drift-check.md`), then list each AD that no longer complies and what would fix it. The spine changes only through `bmad-architecture`, or through an amendment {user_name} approves word for word.

Note in MEMORY.md what {user_name} held firm on, what they skipped and how they ranked the pillars. That shapes the next project's first proposals.
