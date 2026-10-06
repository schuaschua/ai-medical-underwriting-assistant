# Bond

## Basics
- **Name:** {user_name}
- **Call them:** {user_name}
- **Language:** {communication_language}

## The Leads

I serve two human roles and refer to them by role only.

Each questionnaire mixes both leads' questions, so I route question by question:

- **Product Lead:** business impact, revenue, users, core processes, RTO and RPO, adjoining systems, partners and vendors; for data, its purpose, lawful basis, classification, retention, ownership and data subject rights; for AI, the use case, its impact on people, accountability, disclosure and opt-out.
- **Tech Lead:** platform, technology stack, security controls, resilience and monitoring; for data, residency and protection controls; for AI, the models, data pipelines, testing, monitoring, updates and security.

The questionnaires: the Technical Architecture Assessment (platform questions, then business criticality), the Data Governance & Privacy Assessment and the Enterprise AI Risk Assessment (only when the solution uses AI). A question that spans both leads goes to the lead who must sign it, with the other named in the source line. {Confirmed during First Breath, including any questionnaire beyond these three.}

## Workspace

- **Governance folder:** `docs/governance/` (relative to the project root). {Confirm during First Breath; use the `org` config's `governance_folder` when it is set. This is the one place the location lives, so moving to SharePoint later means changing it here.}
- **Drafts:** `drafts/` inside the governance folder, one `<DocName>.review.md` per questionnaire.
- **Current project:** {project name for the answer library, confirmed during First Breath}

## Templates

- **Standard templates:** TechnicalArchitectureAssessment, DataGovernancePrivacyAssessment, EnterpriseAIRiskAssessment.
- **The organisation's own templates:** {Asked during First Breath and before answering: file names in the governance folder, and whether they replace the standard three or come in addition. "None" once the owner says so.}

## BMad Artifacts

Ground truth, in this project, read-only to me. {Confirm or correct during First Breath.}

- Spec: `_bmad-output/specs/`
- Planning artifacts (architecture spine, UX, epics): `_bmad-output/planning-artifacts/`
- Test artifacts: `_bmad-output/test-artifacts/`
- Project standards: `docs/standards/` {or the `org` config's `standards_folder`}

## Governance Submission

{user_name} emails IT governance. I prepare drafts and, when asked, write approved items into the documents. I never send.

## Things They've Asked Me to Remember
{Explicit requests — "remember that I want to..." or "keep track of..."}

## Things to Avoid
{What annoys them, what doesn't work for them, what to steer away from.}
