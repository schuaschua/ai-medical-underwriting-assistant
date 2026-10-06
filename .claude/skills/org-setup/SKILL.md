---
name: "org-setup"
description: Sets up Org Kit module in a project. Use when the user requests to 'install org module', 'configure Org Kit', or 'setup Org Kit'.
---

# Org Kit Setup

## Overview

Adds the Org Kit's project files to a BMad 6.12 project. It doesn't support BMad 6.13, which replaces `bmad-create-epics-and-stories` and `bmad-sprint-planning` with `bmad-ticket` and moves the planning documents, and Org Kit 2.0 will support it. The **BMad installer** owns module registration: installing this kit as a module (from its GitHub repository) writes the `[modules.org]` section of `{project-root}/_bmad/config.toml`, registers the agents (Jules, Scrooge McDuck, Da Vinci) in the manifests and adds the capabilities to `bmad-help`. This skill never writes `_bmad/config.toml`, `_bmad/config.user.toml`, `_bmad/_config/` or any installer-managed folder, and never deletes anything under `_bmad/`.

What it does add (see **Org Kit Extensions**): the org standards baselines, the blank governance questionnaires, the BMad customization overrides in `_bmad/custom/`, the skill links, Scrooge's ledger refresh hooks and `.gitignore` entry, and a check of the tooling Jules and the Jira sync need.

`{project-root}` is a literal token in config values. Resolve it to the real project root only in the filesystem path arguments passed to scripts.

## On Activation

1. Read `./assets/module.yaml` for the variable definitions (`jira_site`, `jira_project_key`, `confluence_wiki_url`, `governance_folder`, `standards_folder`, `token_usage_folder`, `cost_estimates_folder`, `architecture_folder`, `diagrams_folder`, `design_docs_folder`, `region`).
2. Read the values from `{project-root}/_bmad/config.toml`, section `[modules.org]`, then apply any overrides from `{project-root}/_bmad/custom/config.toml` and `{project-root}/_bmad/custom/config.user.toml` (same section; later files win).
3. If `[modules.org]` is missing, the kit was not installed through the BMad installer. Tell the user that registration (Jules in the roster and `bmad-help`) needs the installer, and offer to continue with the project files only: ask for the values (show the `module.yaml` defaults and examples, offer only the `single-select` choices for `region`, re-ask on a failed `regex`, store a declined optional value as an empty string) and use them for this run. Do not write them to any config file.

If the user passes arguments (e.g. `accept all defaults`, `--headless`, inline values), use them in place of prompting, and still show the confirmation summary.

## Org Kit Extensions

These run in this order. They never overwrite anything the project already has: an existing file is the project's own version and wins.

### 1. Standards, questionnaires, overrides and skills

Run the installer once with the values resolved in On Activation (keep the `{project-root}` token in the folder values; the script resolves it). Resolve `{project-root}` in `--project-root` only. Run it first with `--dry-run`, show the user what it would copy, write and link, then run it for real.

```bash
uv run ./scripts/install-org-assets.py --project-root "{project-root}" \
  --standards-folder "{standards_folder}" --governance-folder "{governance_folder}" \
  --jira-site "{jira_site}" --jira-project-key "{jira_project_key}" \
  --architecture-folder "{architecture_folder}" --token-usage-folder "{token_usage_folder}" [--no-hooks] [--skills-mode link|copy|skip] [--dry-run]
```

What it does, each reported in its JSON output:

- **Standards** (`standards`): copies the org baselines in `./assets/standards/` (`azure.md`, `terraform.md`, `security.md`, `coding-style.md`) into `standards_folder`. Each file says it is org baseline 1.0.0 and that the project copies and extends it. Report every file under `skipped` as "already present, left unchanged"; if the user wants to compare, offer to diff the project copy against the kit's baseline, never to replace it.
- **Questionnaires** (`governance`): copies the blank `TechnicalArchitectureAssessment.docx`, `DataGovernancePrivacyAssessment.docx` and `EnterpriseAIRiskAssessment.docx` from `./assets/governance/` into `governance_folder`, skipping existing files the same way.
- **BMad overrides** (`overrides`): renders `./assets/custom/*.toml` into `{project-root}/_bmad/custom/`, writing a file only when it does not exist yet. The files add `persistent_facts = ["file:<standards_folder relative to the project root>/*.md"]` to `bmad-agent-dev` (`[agent]`) and to `bmad-architecture`, `bmad-build`, `bmad-build-auto`, `bmad-code-review` and `bmad-testarch-test-design` (`[workflow]`); `bmad-architecture`, `bmad-agent-dev`, `bmad-build`, `bmad-build-auto` and `bmad-code-review` also load the architecture principles (`file:<architecture_folder>/*.md`) as a persistent fact (every `.md` in the folder: `architecture.md`, the per-cloud-provider files and any Markdown policies); `bmad-spec` and `bmad-build` get an `activation_steps_prepend` that stops when there are no principles yet and offers Da Vinci's guardrails round first; `bmad-build`, `bmad-build-auto` and `bmad-code-review` run Da Vinci's `stack-check.py` once done, which holds the code, pipelines and infrastructure against every architecture answer (cloud, stack, AI and analytics platforms, CI, CD, source host, IaC, scans and pipeline credentials; regions, tags and environments as warnings; the rest as a checklist to judge). The build won't call off-stack work finished, the unattended loop reports it, and the review raises it as findings; `bmad-architecture` gets an `activation_steps_prepend` that stops before the architecture begins when there are no principles yet and sends the user to Da Vinci to agree them, and an `on_complete` that offers Da Vinci (the architecture diagrams), Jules (the three governance assessments) and Scrooge McDuck (the cost estimate); `bmad-build` gets one that asks the end-of-epic AI feedback (a 1 to 5 rating of the AI, and what went wrong when it was poor) for every finished epic nobody has rated, then offers Scrooge's project close-out once every epic and story in the sprint status is done; `bmad-build-auto` runs unattended, so it only mentions epics waiting to be rated before the same close-out offer; `bmad-retrospective` asks the same AI feedback once the retrospective is done; `bmad-create-epics-and-stories` gets the org's hybrid story-structure fact plus an `on_complete` that offers Jira sync to `{jira_project_key}` at `{jira_site}`, then Scrooge's token budgets when none are set; `bmad-sprint-planning` gets an `on_complete` that offers Scrooge's token budgets (a Claude API budget per epic, sprint and remaining planning phase), or a review that adds sprint budgets. For each entry under `overrides.existing`, show the user the `suggested` content next to their current file and let them merge it themselves (or merge it for them on their explicit go-ahead); never replace their file.
- **Skills** (`skills`): links the kit's skills (`agent-jules`, `agent-scrooge`, `agent-davinci`, `org-setup`) into `{project-root}/.claude/skills/` as relative symlinks, which is how BMad projects expose custom skills (e.g. `.claude/skills/agent-jules -> <kit>/skills/agent-jules`). Use `--skills-mode copy` when the kit lives outside the repository or the platform can't follow symlinks, and `skip` when the skills are already installed another way (for example through the plugin marketplace). An existing `.claude/skills/<name>` is left alone. When the BMad installer has already put the kit's skills in `.claude/skills` (the usual case), the result's mode is `in-place`: say the skills are installed and move on. Jules's sanctum stays at `{project-root}/_bmad/memory/agent-jules/`, so a project that already has one keeps it.

- **Ignore the ledger** (`gitignore`): adds `token_usage_folder` to `{project-root}/.gitignore` unless it's already there. The ledger is derived from local logs and never committed.
- **Scrooge's hooks** (`hooks`): adds two async Claude Code hooks (`Stop` and `SubagentStop`) to `{project-root}/.claude/settings.local.json` that refresh the ledger in the background with `python3 <skills folder>/agent-scrooge/scripts/token_report.py`. An event that already runs `token_report.py` is left alone; other settings are kept. Tell the user the hooks are personal (`settings.local.json`) and pass `--no-hooks` if they don't want them. Scrooge's project data (`stories.json`, `savings.json`) lives in `{project-root}/_bmad/memory/agent-scrooge/` and is created by Scrooge on request, not by setup.

If the script exits non-zero, surface its `message` and stop.

### 2. Tooling check

Check, and for anything missing tell the user what it is for and how to add it; missing tools never block setup.

- **`uv`** — runs every kit script. The installer reports `uv_available`; if false, point them to https://docs.astral.sh/uv/.
- **Atlassian MCP server** — used by the `bmad-create-epics-and-stories` Jira sync and for the Confluence wiki. Available when `mcp__atlassian__*` tools are in this session. If absent, suggest adding Atlassian's remote MCP server (`claude mcp add --transport http atlassian https://mcp.atlassian.com/v1/mcp`, then authenticate) and confirm `{jira_site}` is one of its accessible sites.
- **draw.io desktop** (optional) — `agent-davinci` uses its command line to export PNG and SVG beside each `.drawio` file. Without it the `.drawio` files are complete and open in draw.io or at https://app.diagrams.net; point the user to https://www.drawio.com/ if they want exports.
- **draw.io MCP server** (optional) — lets `agent-davinci` open a finished diagram in the browser editor for a preview. Available when `mcp__drawio__*` tools are in this session; if absent and wanted: `claude mcp add drawio -- npx -y @drawio/mcp`.
- **safe-docx MCP server** — Jules uses it to write tracked changes and comment replies in the questionnaires. Available when `mcp__safe-docx__*` tools are in this session. If it is configured but failed to connect, say so rather than calling it missing. Without it Jules can still read comments and write plain answer cells.

### 3. Region

The `region` value tells Jules which regulator's documents to consider (the registry is `../agent-jules/assets/regions.toml`). Show what it resolves to:

```bash
uv run ../agent-jules/scripts/region-sources.py "{project-root}" [--region "{region}"]
```

Pass `--region` only when the values were entered for this run; otherwise the script reads the configured value itself. List the region's regulator and each source's title and URL. A region the registry doesn't know exits non-zero with the known codes: surface them and ask for the region again. To change the region later, the user reconfigures the kit (or sets `region` under `[modules.org]` in `_bmad/custom/config.toml`); Jules reads the current value every time, so nothing else needs updating.

### 4. Report

Add to the Confirm summary: files copied and skipped per folder, override files written and those waiting for a manual merge, skills linked or copied, the region and its source documents, the `.gitignore` entry and hooks added or skipped, and any tooling gaps with their fix.

## Confirm

Show the Org Kit Extensions report, say whether the values came from `[modules.org]` or were entered for this run only (and in that case, that installing the kit through the BMad installer will register Jules), then display the `module_greeting` from `./assets/module.yaml`.

## Outcome

Once the user's `user_name` and `communication_language` are known (from collected input, arguments, or existing config), use them consistently for the remainder of the session: address the user by their configured name and communicate in their configured `communication_language`.
