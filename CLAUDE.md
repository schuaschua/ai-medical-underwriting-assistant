# Project rules for coding agents

These rules come from the project owner (Darrel) and apply to every session in this repository.

## Azure: code everything first, bring the environment up once at the end

- Keep the Azure demo environment torn down while coding. Do not bring it up to test a single story.
- Build all the stories across all the epics first, and test everything that can be tested locally (unit tests, integration tests against the containers in `compose.yaml`, the local start with Dapr).
- Only when all the coding is done, bring the environment up and test there, in as few sessions as possible. Tear it down when that testing is finished.
- Work that can only be proven in Azure (Azure AI Language redaction, Azure AI Search, Document Intelligence, Foundry models, the deploy workflow) is written and tested against fakes or stand-ins, and its Azure check is added to the list in `_bmad-output/implementation-artifacts/deferred-work.md` for the final test session.
- Reason (owner, 2026-10-07): bringing the environment up and down per story wastes money and time. A cycle takes about 11 minutes up and about 32 minutes down.

Bring-up and tear-down steps are in `infra/bootstrap/README.md`. The resource group `rg-aiuw-demo-wus3`, the Terraform state container and the deployment identity stay in place between sessions.
