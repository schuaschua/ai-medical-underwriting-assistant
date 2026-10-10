---
title: 'Story 1.1: Shared contracts package'
type: 'feature'
created: '2026-10-06'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '138587fa76006529339c2b2a18ee34ebb251bed8'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/docs/standards/coding-style.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Seven services will be built separately and must exchange the same payloads, enums, error codes and shared functions. Nothing exists yet, so each builder would invent its own shapes.

**Approach:** Build `packages/contracts/` first as the only shared code (spine AD-20): pydantic models for every operation in the spine's Operations table, the audit record, enums, the error shape and codes, the `rule_id` patterns, the `page_type` mapping, the eval query builder and text normalisation. Set up the repository's Python workspace and checks around it.

## Boundaries & Constraints

**Always:**
- Field names are `snake_case` and identical in every model; ids are UUIDv7 strings; timestamps are ISO 8601 UTC; confidence and scores are floats from 0 to 1; loadings and debits are integer percentages.
- The package imports only the standard library and pydantic. Models are strict about unknown enum values.
- Every model, enum and function carries type hints; `mypy --strict` and `ruff` pass.
- One operation registry lists every operation in the spine's Operations table with caller, owner, method, path, request model, response model and idempotency key.

**Never:**
- No web framework, ORM, HTTP client, Azure SDK or Dapr import. No service code, routes or database tables.
- No business rules beyond the four shared functions named above (the gate threshold, verdict rules and routing stay in their services).
- No reading of `data/answer-key/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Round trip | A valid payload for any operation | Parse then dump gives the same JSON | N/A |
| Unknown enum | `verdict: "approve"` | Rejected | `ValidationError` |
| Bad `rule_id` | `uw-dm-3`, `UW-D-003`, `UW-DIABE-003` | Rejected | `ValidationError` |
| Good `rule_id` | `UW-DM-003`, `UW-HTN-120` | Accepted | N/A |
| Rule definition marker | `Rule UW-DM-003: ...` inside text | Definition pattern finds `UW-DM-003`; a bare mention of another id is not a definition | N/A |
| Normalise | `"  HbA1c\n 8.2 % "` and `"hba1c 8.2 %"` | Equal after normalisation | N/A |
| Normalise mask | `"Patient [Person] seen"` | `[person]` token preserved as one token | N/A |
| Page type mapping | `lab_report`, `attending_physician_statement`, `application_form` | `is_medical` true; `id_document`, `invoice`, `other` false | N/A |
| Confidence range | `confidence: 1.2` or `-0.1` | Rejected | `ValidationError` |
| Demo role | `X-Demo-Role: admin` | Not a member of the role enum | `ValueError` |
| Audit record | `actor_kind: human`, actor not a demo role | Rejected | `ValidationError` |
| Query builder | The same fact statement twice | The same query string | N/A |

</frozen-after-approval>

## Code Map

Greenfield: no application code exists. Sources to build from, not to change:

- `_bmad-output/planning-artifacts/architecture/architecture-ai-medical-underwriting-assistant-2026-10-06/ARCHITECTURE-SPINE.md` -- Operations table, AD-8 audit fields, AD-11 search shape, AD-13 classifier shape, AD-14 fact shape, AD-15 verdict output, Consistency Conventions (enums, errors), Stack (versions), Source tree
- `_bmad-output/implementation-artifacts/epic-1-context.md` -- distilled Epic 1 constraints
- `docs/standards/coding-style.md` -- tools (ruff rules `I`, `B`, `UP`, `S`; mypy strict for domain code), rules 3, 5, 8, 10, 12, 20-25

## Tasks & Acceptance

**Execution:**
- [x] `pyproject.toml` -- create the `uv` workspace root (members `packages/*`, `services/*`), Python 3.13, shared ruff, mypy and pytest settings -- one place for tool config
- [x] `packages/contracts/pyproject.toml` -- package `contracts`, dependency `pydantic==2.13.5` only -- AD-20
- [x] `packages/contracts/src/contracts/enums.py` -- verdict, page status, case status, stage status, decision, demo role, page type, actor kind, classifier contender, retriever config, chunk set, tool name, reason effect, system reason -- spine conventions
- [x] `packages/contracts/src/contracts/ids.py` -- UUIDv7 generation and a validated id string type -- conventions
- [x] `packages/contracts/src/contracts/errors.py` -- error code catalogue, error body, `DomainError` carrying a code -- conventions, coding-style rule 10
- [x] `packages/contracts/src/contracts/rules.py` -- `rule_id` pattern, definition marker pattern, `rule_ids_defined_in(text)`, `is_medical(page_type)` -- AD-12, AD-13
- [x] `packages/contracts/src/contracts/text.py` -- `normalise(text)` -- AD-14
- [x] `packages/contracts/src/contracts/query.py` -- `build_fact_query(statement)` -- AD-17
- [x] `packages/contracts/src/contracts/audit.py` -- audit action catalogue and `AuditRecord` -- AD-8
- [x] `packages/contracts/src/contracts/models/` -- one module per owning service (`intake`, `workflow`, `classification`, `extraction`, `retrieval`, `verdict`) with request and response models -- Operations table
- [x] `packages/contracts/src/contracts/operations.py` -- the operation registry -- lets tests and services check they cover the table
- [x] `packages/contracts/tests/` -- tests for every matrix row, a round trip per model, and a registry test that every operation has both models -- coding-style rule 20
- [x] `.github/workflows/ci.yml` -- on pull request: ruff format check, ruff check, mypy, pytest with coverage -- coding-style section 1
- [x] `README.md` -- add how to install and run the checks

**Acceptance Criteria:**
- Given the spine's Operations table, when the registry is listed, then every operation in the table appears once with a request model (or none for a plain read) and a response model.
- Given the package source, when its imports are scanned, then only the standard library and pydantic are imported.
- Given a fresh clone, when `uv sync` then the verification commands are run, then all pass.

## Implementation Notes

Choices the spine leaves open, made here and frozen with the package:

- Stage commands carry an optional `eval_run_id`, and every stage result carries `status`, `error_code` and one `audit` record (AD-8). A failed result has an `error_code` and a `stage.failed` record.
- An AI `actor` is written `<app id>:<model deployment>`, for example `intake:azure-ai-language`; `ai_actor()` builds it.
- An audit `ref` is always a UUIDv7, so classifications, fact sets and human decisions get ids (`classification_id`, `fact_set_id`, `decision_id`). Audit `detail` is required for `document.redacted` and null otherwise.
- `DecisionRequest.actor` is typed as a demo role, so a non-human actor fails validation before it reaches `workflow`'s domain.
- `stop_after` is an enum with the one value the plans name, `gate`.
- `POST /cases` takes the PDF as the raw `application/pdf` body. The two file reads have no response model; the registry records their media type instead.
- `GET /pages/{page_id}/boxes` accepts an optional `quote_start` and `quote_end`; each box carries `char_start` and `char_end` offsets and coordinates in PDF points from the top-left corner.
- The error catalogue has 19 codes; only `in_progress`, `not_redacted` and `model_unavailable` are named by the spine. `HTTP_STATUS` maps each code to one status.
- Models for parsing model output (`ClassifierOutput`, `ExtractionOutput`, `VerdictOutput`) and the two agent tool argument models were added, because the conventions require every model response to parse into a contracts model.
- Models reject unknown fields and are immutable. Ids must be lower-case canonical UUIDv7. Timestamps must carry a UTC offset.
- `normalise` applies NFKC, removes the soft hyphen and zero-width characters, case-folds and collapses whitespace. `build_fact_query` collapses whitespace and keeps case.
- After review: an audit record's four human actions need a human actor and no other action accepts one; every `page.*` action needs a `page_id`; a stage result is never `running` and a done result carries its own stage's audit action; `debit_pct` is set only for a `debit`; `loading_pct` is set only for `loaded`; `top_k` is at most 50. The coverage threshold lives in the root `pyproject.toml`, so `uv run pytest --cov` is the full test command.
- "any reader" of `intake` is recorded as the four services the call diagram lets call it: `web`, `workflow`, `classification`, `extraction`.
- CI pins `actions/checkout` v7.0.1 and `astral-sh/setup-uv` v10.2.0 by commit, and uv 0.11.8 to match the `uv_build` pin.

Not done: no dependency vulnerability scan in CI (`security.md` rule 28); this spec lists four checks and pins no scanner. Nothing was committed.

## Spec Change Log

## Review Triage Log

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Audit record accepts an AI actor on a human-reserved action and the reverse (blind, edge) | medium | `AuditRecord` checks actor against kind but not action against kind; AD-10 is the rule the record proves | patch |
| 2 | AI actor validator looser than `ai_actor()` builder (all three) | low | `"intake: "` validates; direct correction | patch |
| 3 | `page.*` audit actions accepted with null `page_id` (blind, edge) | medium | No rule ties page actions to a page; trail rows could not be tied to a page | patch |
| 4 | `Reason` has no rule tying `effect` to `debit_pct` (all three) | medium | AD-15 sums `debit_pct`; a debit with no percentage breaks the loading | patch |
| 5 | `VerdictRun` accepts `loaded` with null `loading_pct` (blind, edge) | medium | Only the reverse direction is checked | patch |
| 6 | `DomainError.to_body` can raise inside the error path; exception does not pickle or copy (all three) | medium | `ErrorDetail` validates trace id and message; `super().__init__(message)` only | patch |
| 7 | `PageBoxesQuery` allows `quote_start == quote_end` (blind, edge) | low | `Fact` requires start < end; direct correction | patch |
| 8 | `WordBox` accepts inverted boxes and ranges (blind, edge) | low | No ordering check; direct correction | patch |
| 9 | Lengths accept infinity, which dumps as null (edge) | low | `ge`/`gt` allow `inf`; direct correction | patch |
| 10 | `normalise` does no Unicode normalisation (blind, edge) | medium | PDF ligatures, soft hyphens and zero-width spaces would fail the verbatim check in story 2.4 | patch |
| 11 | A stage result with status `running` must invent an audit record (blind, edge) | medium | AD-6: a running stage answers 409 `in_progress`, so a result is only done or failed | patch |
| 12 | Stage results lack cross-field checks: wrong stage's audit action, nested classification ids differing, failed redaction with pages, counts differing from audit detail, unverified count above fact count (blind, edge) | medium | Each validates today; workflow would write a wrong audit event or fan out pages of a failed redaction | patch |
| 13 | Operation registry tests do not pin which model each operation carries (gap) | medium | Filed with evidence: swapping two response models keeps the suite green | patch |
| 14 | No rejecting test for `VerdictRunResult`, `CaseStarted`, `TraceId`, `Reason.fact_ids` (gap) | medium | Filed with evidence | patch |
| 15 | Error catalogue test pins 3 of 19 codes (gap) | medium | Filed with evidence | patch |
| 16 | `top_k` has no upper bound (blind, edge) | low | Direct correction with a named maximum | patch |
| 17 | `NonEmptyStr` and `OneLine` accept whitespace-only text (blind, edge) | low | Direct correction by pattern; page text itself may legitimately be empty and must not be stripped | patch |
| 18 | CI runs on pull request only; tests are not type-checked; pytest paths hard-coded to one package; coverage threshold duplicated (blind, gap) | low | The first service's tests would not run under `testpaths = ["packages"]` | patch |
| 19 | Models validate in lax mode, so `"3"` passes as an int and a Unix number as a timestamp (blind, edge) | low | Real, but callers are our own services emitting canonical JSON; a global strict mode is not a direct correction | reject |
| 20 | UUIDv7 ids are not monotonic within a millisecond (edge) | low | Nothing relies on intra-millisecond order | reject |
| 21 | Rule definition pattern is not anchored and has no tolerance for variants (edge) | low | The manual is generated here and story 2.1 requires exactly one `Rule <id>:` marker per rule | reject |
| 22 | Immutability is shallow; id aliases are not distinct types; no public exports (blind) | low | No named caller harmed today | reject |
| 23 | Dynamic imports could slip past the purity test (edge) | low | No dynamic import exists; guard is speculative | reject |
| 24 | Binary reads and `POST /cases` have no model in the registry (edge, claim) | false | The acceptance criterion allows none for these; media type is recorded instead | reject |
| 25 | `uv.lock` missing from the diff (blind) | false | The lock file was excluded from the review diff on purpose and is committed with the story | reject |
| 26 | `VerdictOutput` has no cross-field checks (blind) | false | It is the raw model output; AD-15 puts those rules in `verdict`'s domain code | reject |
| 27 | Workspace glob `services/*` may break `uv sync` when a service folder has no `pyproject.toml` (edge) | maybe-false | Would need a `services/x/` without a `pyproject.toml`; every service gets one in its first commit | reject (low if true) |
| 28 | Role and decision pairing is not in the contract; `ACTOR_NOT_HUMAN` is unreachable through `DecisionRequest` (blind, edge) | medium | Real; the fix adds public surface (a role-to-decision mapping) | defer to stories 1.10 and 1.11 |
| 29 | Quote offsets have no shared function mapping a normalised match back to raw page offsets; offset unit unstated (blind) | medium | Real; needs a new shared function | defer to story 2.4 |
| 30 | Progress and agent-log payloads cannot say why something failed; no list is paged; `VerdictRunRequested` has no run id (blind) | medium | Real; each needs new fields | defer to stories 1.9, 2.5 and 2.8 |
| 31 | Nothing carries the contract to the SPA (blind) | medium | Real; needs a schema export | defer to story 1.3 |
| 32 | No dependency scan in CI (implementer's own note; security rule 28) | medium | Real; not in this spec's four checks | defer to story 1.3 |

## Design Notes

- A fact needs content the spine does not fix: the model carries `statement` (one line, for example "HbA1c 8.2%") beside the fields AD-14 names. The eval query builder takes that statement, so the runner and the agent's `fact_id` logging share one notion of what a fact says.
- No architecture principles were agreed for this project (`docs/architecture/architecture.md` does not exist; the owner skipped that round). The spine and the standards are the guardrails.
- Development tools, verified current on PyPI on 2026-10-06 and pinned exactly: ruff 0.16.10, mypy 2.4.0, pytest 9.1.1, pytest-cov 7.1.0. Commit `uv.lock`. Scratch files go in the gitignored `.work/` folder, never outside the project.
- Approval: Darrel authorised unattended work on stories 1.1 to 1.6 on 2026-10-06 before going offline; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `uv sync` -- expected: resolves and installs
- `uv run ruff format --check . && uv run ruff check .` -- expected: no findings
- `uv run mypy packages/contracts/src` -- expected: no errors in strict mode
- `uv run pytest packages/contracts --cov=contracts --cov-fail-under=80` -- expected: all pass, coverage at least 80%
