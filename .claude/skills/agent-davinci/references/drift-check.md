---
name: drift-check
description: Flag diagrams and design documents older than their spine, name the ADs that changed, and check the spine still honours the architecture principles
code: DD
added: 2026-09-28
type: prompt
---

# Drift check

With no spine there is nothing to check against: say so, point to `bmad-architecture` (and the principles, capability AP, when `architecture.md` is missing), and stop.

Run `uv run scripts/diagram-drift.py {project-root}` and explain the result plainly per diagram and per design document (`documents`; for a stale document, the sections citing a changed AD are in its Appendix B). For a **stale** diagram, name each AD that changed, was added or was removed, and say what it means: a changed AD the diagram cites may make a drawn element wrong, while a new or changed AD it doesn't cite may be something it's missing. An **outdated** diagram predates the spine's last change, but no AD it was drawn against moved, so a redraw only refreshes its stamp. Offer to redraw stale diagrams from their models. The script exits 1 when anything is stale or its spine is missing, so the same command works as a CI or pre-commit check.

Then read `architecture.md` and the provider files beside it against the spine: an AD that contradicts a principle without naming it and saying why is drift of a different kind, the architecture moving away from what was agreed. Name each one and ask {user_name} which should change, the AD (through `bmad-architecture` or an approved amendment) or the principle (capability AP).
