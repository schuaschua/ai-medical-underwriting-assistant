# AI Medical Underwriting Assistant

An AI assistant to support medical underwriting.

## Setup

This repository uses the [BMad Method](https://github.com/bmad-code-org/BMAD-METHOD) v6.12.1 with the
[Org Kit](https://github.com/schuaschua/bmad-org-kit) v1.14.0 module, configured for Claude Code.

- `_bmad/` holds the BMad configuration and the Org Kit overrides (`_bmad/custom/`).
- `.claude/skills/` holds the BMad and Org Kit skills.
- `docs/standards/` holds the org standards baselines and `docs/governance/` the blank governance questionnaires.

Open the folder in Claude Code and run the `bmad-help` skill to see what to do next.

### Still to do

The Org Kit's Jira sync needs this project's Jira site and project key. When they are known, run
**"setup Org Kit"** in Claude Code with those values. It adds the `bmad-create-epics-and-stories`
override (Jira sync, story structure, token budgets) and Scrooge's personal ledger hooks.
