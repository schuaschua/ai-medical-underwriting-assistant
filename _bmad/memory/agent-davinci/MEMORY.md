# Memory

_Curated long-term knowledge. Empty at birth — grows through sessions._

_Distilled insights, not raw notes: the principles the owner held firm on or dropped and why, challenges they deferred, drawing and document choices they corrected. Aim to stay under roughly 1500 tokens. Raw notes go in `sessions/YYYY-MM-DD.md`. See `references/memory-guidance.md`._

## 2026-10-06 — first session
- Darrel skipped the architecture principles round (AP); no `docs/architecture/architecture.md`. Diagrams carry AD cites only, no P-n cites.
- Sources to draw from: the spine at `_bmad-output/planning-artifacts/architecture/architecture-ai-medical-underwriting-assistant-2026-10-06/ARCHITECTURE-SPINE.md` (20 ADs, final, uncommitted at first drawing) and the spec in `_bmad-output/specs/spec-underwriting-poc/`.
- Demo is the weekend of 2026-10-10 for a RAG-expert architect. Jules is not needed on this project; Scrooge runs at the end of the project.
- Open at first breath: which diagram types Darrel wants; which fields count as sensitive (for the data-flow diagram); where model processing happens (Foundry deployment type) for the deployment diagram.
- Darrel's answers 2026-10-06: draw all five diagram types; only PII is sensitive (mark PII stores, not medical facts); Foundry drawn in West US 3 with processing location as a gap.
- Drawing paused the same day: Darrel added PII redaction as a new requirement. Draw only after the spine carries it.
- 2026-10-06: spine gained AD-21 (PII redaction, Azure AI Language) with Darrel's approval; drew six diagrams in docs/architecture/diagrams: c4-context, c4-containers-calls, c4-containers-stores (containers split in two because one sheet could not be laid out), deployment, data-flow (TB), sequence-one-case. All A2 landscape, one page, readable. Stamped against an uncommitted spine.
- Known flaw: on data-flow, the "Public internet" boundary label sits over the stamp line at the top. Layout touch-up for draw.io, or revisit on redraw.
- Not drawn for lack of an AD: resource group, container registry, Log Analytics, Application Insights (they are only in the spine's deployment section).
