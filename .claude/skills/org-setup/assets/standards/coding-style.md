# Coding style (org baseline)

> **Org baseline, version 1.0.0** (Org Kit, module `org`). Projects copy this file into their standards folder and extend it: fill in the placeholders and paths, add project rules below the baseline ones, and record any deviation under [Accepted exceptions](#accepted-exceptions). Never delete or weaken a baseline rule silently.

Formatting and most style are decided by tools with their default settings and checked in CI; this file holds only what the tools can't check. Related: `security.md`, `terraform.md`, the project's architecture document.

Placeholders: `<KEY>` is the project's Jira key (e.g. `PROJ`); `<backend>`, `<domain>` and `<web>` are the project's backend package, framework-free domain package and web app folders.

## 1. Tools (defaults, enforced in CI)

The org default toolchain for Python, TypeScript/React and Terraform projects:

| Area | Tool | Setting |
|---|---|---|
| Python formatting | `ruff format` | defaults |
| Python linting | `ruff check` | default rules plus `I` (import order), `B` (bugbear), `UP` (pyupgrade), `S` (security) |
| Python types | `mypy` | `strict` for the domain package; default elsewhere |
| TypeScript | `tsc` | `"strict": true` |
| TypeScript/React linting | ESLint | recommended + `typescript-eslint` recommended + `react-hooks` |
| TypeScript/React formatting | Prettier | defaults |
| Terraform | `terraform fmt`, `tflint` | defaults + the `azurerm` ruleset (see `terraform.md`) |

1. Never disable a rule inline (`# noqa`, `eslint-disable`, `# type: ignore`) without a comment saying why; never disable one for a whole file.
2. A failing format or lint check blocks the pull request; fix the code, don't loosen the config.

## 2. Everywhere

3. Give every domain field one canonical identifier (e.g. the schema's field or question id) and use it as the key and name everywhere: database, API, agent tools and errors. Never invent aliases for it.
4. Calculate money only with `Decimal` in Python (or an equivalent exact type); never use floats for prices. On the wire, amounts follow the architecture's contract, rounded to 2 decimals and parsed straight into `Decimal`. Only the backend calculates prices; clients display them.
5. Dates are `YYYY-MM-DD`, timestamps ISO 8601 UTC.
6. Comments explain why, not what. Reference the rule or architecture decision you're implementing where it isn't obvious, e.g. `# AD-<n>: <why this code exists>`.
7. No dead code, commented-out code or TODOs without a Jira key (`# TODO <KEY>-42: …`).

## 3. Python

8. Type-hint every function signature.
9. Keep the domain package (`<domain>`) framework-free: it never imports web frameworks, ORMs, MCP or HTTP libraries. Adapters call the domain; the domain never calls adapters.
10. Raise domain errors carrying an error code from the architecture's error catalogue; adapters map them to responses. Never return error dicts from the domain, and never use a bare `except:`.
11. Use `async` for I/O in adapters; keep domain functions synchronous and pure where possible.
12. Read configuration only through one settings object (e.g. `pydantic-settings`) built from environment variables; never read `os.environ` elsewhere.
13. Docstrings (one line is fine) on public domain functions and on every agent tool (e.g. MCP tool), since the tool docstring is what the model reads.

## 4. TypeScript / React

14. Function components and hooks only.
15. All API calls go through one client module (e.g. `<web>/src/api/`); components never call `fetch` directly.
16. Never implement business rules in the web app: no price calculation, and client-side validation is convenience only; the server decides (`security.md` rule 20).
17. Use design tokens from the project's UX design (`DESIGN.md`) as CSS variables; never hard-code colours, font sizes or spacing.
18. Put user-facing text in one strings module, worded as the UX experience spec (`EXPERIENCE.md`) sets voice and tone; never scatter UI copy through components.
19. Meet the accessibility floor in the UX experience spec (labels, focus order, keyboard use, minimum text sizes).

## 5. Tests

20. Every acceptance criterion has at least one test; name the story in the test (`test_story_1_10_<behaviour>`, or `describe("1.10 …")`).
21. Python tests live in `<backend>/tests/`, mirroring the package layout, and run with `pytest`; React tests sit next to the component as `*.test.tsx` and run with Vitest and Testing Library.
22. Test behaviour, not implementation: assert on responses, database state and what the user sees, not on private functions or component internals.
23. Unit tests never call real Azure, AI services or the network; use fakes. Integration tests use a real database in a container. Use test seams the architecture defines: never sleep in a test to wait for time-based behaviour (inject a clock and move it instead), never call a real AI agent in unit or integration tests (use a gateway stub), and never run functional tests against a shared deployed environment, where only a read-only post-deploy smoke check runs.
24. Use synthetic fixtures only, built from the project's seed content; never real data (`security.md` rule 1).
25. Each project sets coverage thresholds in CI (org suggestion: 80% backend, 60% web) and never lowers them to make a check pass; add tests instead. AI agent behaviour is checked by its evaluation set instead of coverage.

## 6. Pull requests

26. One story (or one subtask) per pull request, into the integration branch (e.g. `dev`), titled with the Jira key: `<KEY>-12: drafts list`.
27. The description links the Jira story and lists which acceptance criteria it covers; CI must be green before review.
28. Keep pull requests small enough to review in one sitting; split large stories by subtask.

## 7. Database migrations (if your project uses them)

29. Every migration (e.g. Alembic) must be backward-compatible (expand, then contract): add columns, tables and constraints first, and remove or rename only in a later release, because the old app version briefly runs against the new schema. The pipeline runs migrations before the new image goes live (`terraform.md` rule 36).

## Accepted exceptions

Projects record their own deviations from this baseline here, each with who approved it and what closes it. The org baseline ships this table empty.

| Rule | Exception | Approved by (role) and date | Close by |
|---|---|---|---|
