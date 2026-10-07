# Project rules for coding agents

These rules come from the project owner (Darrel) and apply to every session in this repository.

## Azure: code everything first, bring the environment up once at the end

- Keep the Azure demo environment torn down while coding. Do not bring it up to test a single story.
- Build all the stories across all the epics first, and test everything that can be tested locally (unit tests, integration tests against the containers in `compose.yaml`, the local start with Dapr).
- Only when all the coding is done, bring the environment up and test there, in as few sessions as possible. Tear it down when that testing is finished.
- Work that can only be proven in Azure (Azure AI Language redaction, Azure AI Search, Document Intelligence, Foundry models, the deploy workflow) is written and tested against fakes or stand-ins, and its Azure check is added to the list in `_bmad-output/implementation-artifacts/deferred-work.md` for the final test session.
- Reason (owner, 2026-10-07): bringing the environment up and down per story wastes money and time. A cycle takes about 11 minutes up and about 32 minutes down.

Bring-up and tear-down steps are in `infra/bootstrap/README.md`. The resource group `rg-aiuw-demo-wus3`, the Terraform state container and the deployment identity stay in place between sessions.

## Tests: keep the suite small

- The whole Python suite is kept at about 500 test cases (as `pytest` counts them, parametrised cases included), not thousands.
- Write a test for each acceptance criterion of a story and for the few edge cases that guard a real risk (a security or privacy rule, money or a verdict, data that cannot be corrected afterwards). Do not write a test for every setting, every wording, every refusal code or every combination.
- Prefer one test that runs a whole path over many tests of its parts. Use `parametrize` sparingly: a handful of cases, not a table of every input.
- When a story adds tests, it stays inside the budget: remove or merge weaker tests of the same package if needed.
- The coverage threshold in `pyproject.toml` (80%) still holds. If a cut would take coverage under it, keep the broad tests and cut the narrow ones.
- Budget per package, to be kept roughly: `contracts` 60, `synthdata` 45, `intake` 50, `web` 45, `workflow` 110, `classification` 40, `extraction` 40, `retrieval` 55, `verdict` 55.
- Reason (owner, 2026-10-08): 3,490 tests for a proof of concept is far too many; they slow every run and every change.
