---
name: design-docs
description: Write a cited High-Level Design, or a Low-Level Design for a component, as a Word document from the spine, spec and other BMad artifacts, and optionally start from or publish to Confluence
code: DS
added: 2026-09-28
type: prompt
---

# High-Level and Low-Level Design documents

The result is `<name>.doc.json` and `<name>.docx` in the design documents folder (`design_docs_folder` from `spine.py`, default `docs/architecture/design`): `hld` for the High-Level Design, `lld-<scope>` for a Low-Level Design (`lld-chat-api`, or `lld` for the whole system). The document is a Word file a governance reviewer, an architect or a new developer can read without you in the room, and every statement in it traces back to an AD or a section of a BMad document.

**No spine, no document.** With no spine, say `bmad-architecture` comes first (and the principles, capability AP, when `architecture.md` is missing), and stop. As with diagrams, this holds when told not to ask questions or to run unattended. A Confluence page is not a substitute for the spine, and neither is an outline filled from memory. Never write a `.docx` by hand around `build-docx.py`. Told not to ask, with a spine present: challenge items become gap blocks.

## What each is for

- **HLD**: the whole solution for readers who decide or approve it. Context, components, integrations, data, security, infrastructure, non-functional targets, operations, risks. It shows the C4 context, C4 containers, data flow and deployment diagrams.
- **LLD**: one component (or the whole system, when it's small) for the people who build and run it. Internals, interfaces and APIs, data model, key flows with sequence diagrams, error handling, security implementation, configuration, deployment details, logging, testing. It refers to the HLD instead of repeating it. Write the HLD first, and ask which component the LLD covers.

## The outline

The sections come from the outline `build-docx.py` enforces: the project's own `<design_docs_folder>/templates/hld.md` or `lld.md` when it has one (`spine.py` reports which), else the kit's `assets/design-docs/hld.md` and `lld.md`. Read the outline: the text under each heading says what belongs there and where it usually comes from, and `Diagram:` names the diagram the section shows. Every level-1 heading appears in the document in order; you may add level-2 and level-3 subsections and extra sections. When the organisation's own template arrives, it replaces the outline file (headings and guidance) and `templates/reference.docx` (Word styles, header, footer and logo); offer to set both up from a Word template the user gives you, reading its headings to write the outline.

## Sources

The spine, the spec and the UX docs, as for diagrams, plus the other BMad artifacts under `_bmad-output/` that a design document needs (epics and stories for an LLD's component scope, the test design for its testing section). Never the code, the infrastructure files or the web: the document states what the project has decided, and the code shows only what someone built. Cite at the finest level, as for diagrams: an AD id, or `SPEC.md §4.2`.

## Write it

1. **Diagrams first.** List the diagrams the outline's sections name that the document needs and that don't exist or aren't current (`diagram-drift.py`), and offer to draw them. Drawing them follows the Draw flow, challenge included. The document may go ahead without one, with a gap block where it would be, if the user says so. A diagram with no `.png` export (no draw.io desktop) goes in as a note to export it; say so.
2. **Challenge before writing**, as for diagrams, but at the document's scale: what the outline asks for that the sources don't settle, most consequential first (for an HLD usually data classification, RTO and RPO, security controls and integrations; for an LLD, API contracts, the data model and error handling). Each answer becomes approved AD text in the spine, and anything deferred becomes a gap block where it belongs. After each round say how many open points remain and ask whether to keep going or write now.
3. **Write the model** (`uv run scripts/build-docx.py --help` documents it). Write in plain, direct prose for the reader the document is for. Put one idea per text block with its own citations, and use tables for anything with the same attributes repeated (components, endpoints, entities, environments). A statement no source supports is a gap block, phrased as the question the design still has to answer, never softened into a vague claim. Keep the model's `stamp` when editing an existing model.
4. **Build**: `uv run scripts/build-docx.py {project-root} <folder>/<name>.doc.json --check`, fix what it lists, then build without `--check`. Fix a failing cite by citing correctly or turning the block into a gap, never by citing something that doesn't say it.
5. **Report** the file, the open points (each as its question), the ADs the document doesn't cite (for an HLD, each is either deliberately out of scope or a missing section), and any diagram without an export. Tell the user the table of contents fills when Word asks to update fields on opening.

## Reviews and edits in Word

Reviewers comment in Word (or on the Confluence page; see `references/confluence.md`), and people edit the file. The model stays the source: a rebuild regenerates the `.docx`, so `build-docx.py` refuses to replace one changed since the last build. When it refuses, read the document (python-docx, or the comments with the kit's `skills/agent-jules/scripts/docx-comments.py`), show the user what changed or what reviewers asked, carry the agreed changes into the model (as AD text first when they change a decision), and rebuild with `--force` only once the user confirms nothing else in the Word file needs keeping. Bump `version` for each issued rebuild.

## Confluence

When the user has an HLD or LLD on Confluence already, or wants the document published there, load `references/confluence.md`. An existing page gives the document its structure and a list of claims to verify, never sources to cite; publishing puts the same build on a page, and only after the user says yes.

## Drift

`diagram-drift.py` checks documents with the diagrams. A **stale** document cites an AD that changed, or was drawn from a source that changed: update the sections that cite it (Appendix B lists them) and rebuild.

When the document starts from a Confluence page or is published to one, also load `references/confluence.md`.
