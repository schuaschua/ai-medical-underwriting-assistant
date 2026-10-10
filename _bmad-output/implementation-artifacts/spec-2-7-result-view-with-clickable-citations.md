---
title: 'Story 2.7: Result view with clickable citations'
type: 'feature'
created: '2026-10-08'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '12a74c8bc9e8f5d34a6518d8e405534587b4f40c'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A case now ends with facts and a suggested verdict, but an underwriter can only read them as API answers: there is no screen on which to see the suggestion beside the document and check each claim against its source.

**Approach:** Add the result view for the underwriter: the redacted PDF on the left; on the right the verdict, then the reasons, then the facts. Selecting a fact's citation scrolls the PDF to its page and highlights the quote with the word boxes `intake` holds for the fact's offsets; selecting a reason's rule opens the manual rule's text beside it. `web` gains the read routes the screen needs and holds no rule of its own.

## Boundaries & Constraints

**Always:**
- Spine AD-10, AD-14, AD-19, AD-9, AD-21; FR13. The screen is for the underwriter role only and is reached from the case list and from a case's audit trail. It shows the exact label "AI suggestion, not a decision", taken from the run's payload, wherever a verdict is shown.
- Order on the right: the verdict (with its loading when loaded, its system reasons in plain words when it refers, its confidence as a percentage), then the reasons (rule, effect, debit, the facts each cites), then the facts in page order. Nothing is shown as a finding without its citation: a reason shows its rule id and facts; a fact shows its page number and quote.
- The highlight is drawn from boxes `intake` returns for the fact's `quote_start` and `quote_end`, scaled to the rendered page; the SPA never searches the browser's own text layer for the quote. A verified fact's citation scrolls to its page and draws the boxes; an unverified fact is visibly flagged as not found on the page, has no highlight and offers none.
- The document shown is the redacted PDF from `intake`, read through `web` by the API client (every `/api` call carries the demo role, so no plain element fetches it). No route serves an original.
- Selecting a rule id opens the rule's text, impairment and manual page, read from `retrieval` through `web`, beside the reasons. The rule text and every model-written string (reasons' wording if any, fact statements, quotes) are rendered as text, never as HTML.
- `web` adds read routes only, each a pass-through of one owner's operation with the role check: the case's facts (`extraction`), its verdict runs (`verdict`), a rule (`retrieval`), the document file and the page list (`intake`), and a page's boxes for an offset range (`intake`). Bytes are passed through with their media type and the security headers; refusals of the caller's own request (404, 409 `not_redacted`, 422) are passed on, anything else is `upstream_unavailable`.
- A case with several runs (one per retriever configuration) shows which run is on screen and lets the underwriter pick another; a case with no run yet, a failed run, or a case that failed says so plainly. The view reads by polling while the case is not final and stops when it is.
- Wording is in the strings module; plain default styling; usable with the keyboard (citations and rule ids are buttons or links, the highlight is not the only sign of where the quote is: the page number is stated).

**Never:**
- No decision, approval or override control anywhere on the screen: the verdict is a suggestion and the screen offers nothing that would make it more.
- No Compare view of two rows side by side (story 3.6), no agent log (story 2.8), no editing of facts.
- No business rule in `web` or the SPA: no verdict, loading, confidence floor or verification is worked out in the browser.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Open a finished case | Underwriter opens the result of a completed case with one run | PDF on the left; verdict with the label, reasons and facts on the right | N/A |
| Loaded | Verdict `loaded`, loading 75 | "Loaded premium, +75 %" with the label; each reason with its rule, debit and facts | N/A |
| Refer | Verdict `refer` with system reasons | "Refer to underwriter" and each system reason in plain words | N/A |
| Cite a verified fact | The citation of a fact on page 3 is selected | The PDF shows page 3 and the quote's words are highlighted by boxes from `intake` | If the boxes cannot be read, the page is still shown and a plain note says the highlight failed |
| Unverified fact | A fact with `quote_verified` false | Flagged "quote not found on the page"; no citation control, no highlight | N/A |
| Open a rule | A reason's rule id is selected | The rule's text, impairment and manual page beside it | 404 shown as "this rule is not in the manual" |
| Several runs | A case with runs for two configurations | The configuration on screen is named; the other can be picked | N/A |
| No run yet | A case still running or waiting for a person | The document and the facts so far; "no suggestion yet"; polling goes on | N/A |
| Failed run or case | A run with an error code, or a failed case | Says so with the code in plain words; shows what exists | N/A |
| Not redacted yet | The document is asked for before redaction is done | "The document is not ready yet" | 409 `not_redacted` passed on |
| Unknown case | A case id never started | "No such case" | 404 passed on |
| Customer | Customer role calls any of the new routes | Refused | 403 `role_not_allowed` |
| Malformed answer | A service answers something that is not the contract's shape | The screen shows a fault for that part, not a broken page | Other parts still shown |
| Long document | A PDF of many pages | Pages are rendered as they come into view, not all at once | N/A |

</frozen-after-approval>

## Code Map

- `packages/contracts/src/contracts/operations.py` -- `list_facts`, `list_verdict_runs`, `read_rule`, `read_document_file`, `list_pages`, `read_page_boxes` and their callers; `models/extraction.py` (`Fact`, `FactList`), `models/verdict.py` (`VerdictRun`, `Reason`, `SUGGESTION_LABEL`, `VerdictRunList`), `models/retrieval.py` (`RuleText`), `models/intake.py` (`PageList`, `PageBoxes`, `WordBox`, `PageBoxesQuery`); `text.py` (`QUOTE_OFFSET_UNIT`)
- `services/web/src/web/adapters/dapr.py` -- `ServiceClient`: `_call` for JSON, `read_page_thumbnail` as the model of a bytes pass-through with the PNG check; `adapters/http/api.py` -- `role_checked`, `underwriter_only`, the thumbnail and triage routes; `adapters/http/triage.py`; `adapters/http/middleware.py` (the content security policy the PDF renderer must live with: scripts and workers from this origin only)
- `services/intake/src/intake/adapters/http/page_routes.py`, `domain/pages.py` -- the file, page list and boxes routes as they answer (boxes are in PDF points with the page's size)
- `services/web/spa/src/` -- `navigation.tsx`; `screens/CaseList.tsx` and `screens/AuditTrail.tsx` (where the links go); `components/PageThumbnail.tsx` (bytes read through the client and drawn, because an element cannot send the role header); `polling/polled.ts`; `api/client.ts` (how each answer is checked against its contract shape); `strings.ts` (page types, verdict wording to add); `package.json` (the spine pins `react-pdf` 11.0.0)
- `packages/synthdata/tests/` -- the cross-service stack; after stories 2.4 and 2.5 a case run through it has facts and a run to read

## Tasks & Acceptance

**Execution:**
- [x] `services/web/` -- the six read routes and their client calls (JSON and bytes), underwriter only; tests for each: pass-through, refusals passed on, the customer refused, a wrong media type or empty body refused
- [x] `services/web/spa/` -- the result screen: the PDF pane (the file read through the client and handed to the renderer, pages rendered as they come into view, the worker loaded from this origin), the highlight layer drawn from boxes, the verdict, reasons and facts panes, the rule panel, the run picker, the states of the matrix; client functions with shape checks; strings; links from the case list and the audit trail; tests
- [x] `packages/synthdata/tests/` -- one cross-service test through `web`: a case run to its verdict, then every route read as the underwriter, a verified fact's boxes lying inside its page, and the customer refused
- [x] `README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` -- what changed; what a browser check must still confirm

**Acceptance Criteria:**
- Given the local stack and a case run to its verdict, when the underwriter opens its result, then the document, the verdict with the exact label, the reasons and the facts are on screen, a verified fact's citation scrolls to its page and highlights its quote, and a reason's rule opens the manual's text.
- Given the SPA's source, when it is searched, then nothing computes a verdict, a loading or a verification, and no model-written text reaches the page as HTML.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

**Decisions made (the spec left room)**
- The screen is `/underwriter/result?case=<case_id>`, named in the address like the audit trail. It is not a navigation entry (it needs a case); each row of "Cases" and each case's trail link to it, and it links back to the trail.
- `GET /api/rules/{rule_id}` passes no `retriever_config` on: the rule shown is always the `smart` one-rule chunk.
- The run on screen is the first the server lists unless another is picked. Facts are shown in the order `extraction` answers (page order); the browser sorts nothing.
- One read is progress (`workflow`), then pages (`intake`), facts and runs side by side, every 3 s. A part that fails is a fault of that part; what it showed before stays, with a note. Reading also goes on while a listed run is `running`, a little beyond "stops when it is final".
- The document's `document_id` comes from the page list; the file is read once per visit and handed to the renderer as bytes. The PDF's text layer and annotation layer are not drawn.
- The content security policy is unchanged: the worker is a file of the build (`assets/pdf.worker.min-<hash>.mjs`), served as `text/javascript`; `useWasm` is off. `pdf.js` 6.3.289 (the version `react-pdf` 11.0.0 pins) has no `isEvalSupported` option and evaluates no code from text, so there is no eval switch to pass; the comment at `OPTIONS` in `PdfDocument.tsx` says so.
- `usePolled` gained an optional `judge` of an answer (`following`, `failing`, `settled`), used by this screen only; the other screens behave as before.
- The SPA's tests replace `react-pdf` with a stand-in (`spa/src/test/pdfStandIn.tsx`), mocked for every test file in `spa/src/test/setup.ts`.

**What earlier tests changed**
- `web` (48 to 47 tests, budget 45), no assertion dropped: the two contract-type tests are one; the case list's read and its refusal are one; the audit trail's role check (1.6) joined its pass-through (1.12); the thumbnail's pass-through and its "not a PNG" case are one. `test_story_1_5_no_route_returns_a_document_file` is now `..._no_route_returns_an_original` and lists the six new routes; `/api/documents/{id}/file` left the paths that must be 404 and is asserted to refuse the customer (403). The SPA host test also asserts the `.mjs` worker's media type.
- `synthdata` (49, one added and two merged; budget 45): the two story 1.4 generator tests are one. `LocalRetrieval.app()` was added to the test support.
- SPA: the case list test expects the new "Result" column.

**What the browser check must confirm** (listed in `deferred-work.md`)
- The worker loads and the console shows no policy violation; the pages draw with readable text (no standard fonts, character maps or WebAssembly decoders are shipped).
- A citation scrolls the document's pane, not the window, to the highlight, and the boxes sit on the quote's words, also after the window is resized (the page is as wide as the pane).
- A page far down a long document is drawn only when scrolled to; the result view's code and the PDF library are fetched only when the screen is opened.
- In Azure: `web` to `extraction`, `verdict` and `retrieval` by Dapr app id, a PDF of several MB through the sidecar, and the real redacted PDF.

**Review round 1 (2026-10-08)**
- The line that says where a cited quote is now follows what is on screen: "the quote is highlighted" only when the boxes are drawn on a drawn page; "the highlight could not be shown" when the boxes cannot be read, are for another page number, lie beside the page or have no whole-number offsets (checked in `getPageBoxes`), the page is beyond the PDF's last, the page or the PDF fails to draw, or no document is on screen.
- The pages scroll in a pane of their own (`.result-document-pages`), under the heading and the status line; a citation scrolls that pane to the page and then to the highlight itself, never the window.
- A failing part counts toward the back-off; on a final case the screen gives up after 3 failed reads in a row and says so, with "Check again"; a wrong-shaped answer is not read again by itself; "The case is finished, so this screen does not read again." is shown only when reading has stopped with nothing left to mend.
- When the facts cannot be read a reason says that, once, and no longer that each fact "is not in the list below"; a cited fact shows its quote and page with the reason.
- The page is drawn as wide as its pane (`ResizeObserver`, 720 px where there is none); the two panes stack under 60rem.
- Focus goes to the rule panel's heading when it opens and back to the rule's button when it closes.
- List keys carry the position (reasons, cited facts, system reasons, fact rows, boxes).
- The redacted PDF has its own deadline: `WEB_DOCUMENT_TIMEOUT_SECONDS` 60 in `web`, `DOCUMENT_TIMEOUT_MS` 90 000 in the browser. The setting is not in `dapr.yaml` or the `app` stack: the default applies.
- The result view is a chunk of its own, loaded when the screen is opened: the main bundle is 310 kB (97 kB gzipped) and holds no `pdf.js`; `ResultView-<hash>.js` is 645 kB.
- `_UNKNOWN_PAGE` and `_UNKNOWN_RESOURCE` in `web`'s client are one set.
- Not changed in this round, by the coordinator's limit on what to touch: `README.md` and `deferred-work.md` still say the page is 720 px wide, that the bundle is one chunk of 950 kB, and quote the old wording of the highlight fault.

## Spec Change Log

## Review Triage Log

Two layers ran (blind, edge-case). The verification-gap layer was left out for this story: the owner's rule of 2026-10-08 keeps the suite small, and the two layers were told to report a missing test only where it guards a real risk.

| # | Finding (reviewer) | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | The screen says "the quote is highlighted" when nothing is: the document absent or failed, the page not drawn, the fact's page beyond the PDF's last page; and boxes are drawn on the fact's page without checking they are that page's boxes (blind x2, edge x3) | high | `ResultView.tsx` sets the citation to drawn when the boxes are read; `PdfDocument.tsx` draws only on a drawn page; `getPageBoxes` never compares page numbers. A citation said to be shown that is not is the one thing this screen must not get wrong | patch |
| 2 | Following a citation scrolls to the top of the page, so a quote low on the page is out of view (blind) | medium | `scrollIntoView({ block: "start" })` on the sheet | patch |
| 3 | A part that keeps failing is read every 3 s for ever with no back-off, and the screen says it reads no more while it still polls (blind, edge x2) | medium | `result.ts`: the read succeeds when progress does, so the failure count resets | patch |
| 4 | When the facts cannot be read, every reason says its fact "is not in the list below"; and a fact cited in a reason does not show its quote (blind x2) | medium | `VerdictPane` is handed an empty list; `ReasonItem` shows statement and citation only. The intent: a fact shows its page number and quote | patch |
| 5 | The page is 720 px wide in a pane half the window, so the document scrolls sideways under about 1,500 px (blind) | medium | `PAGE_WIDTH_PX` with `flex: 1 1 50%`; a laptop screen is the likely demo screen | patch |
| 6 | Focus is not moved to the rule panel when it opens nor back when it closes (blind) | low | `RulePanel`; small and direct | patch |
| 7 | React keys can collide (two reasons on one rule, a fact id twice), a box without its offsets passes the check, and a box outside its page is clipped in silence (blind, edge x3) | low | `ResultView.tsx`, `client.ts` `isBox`; direct corrections | patch |
| 8 | The renderer is not told that `eval` is unavailable, which the content security policy would report (blind) | maybe-false | Depends on the pinned `pdfjs-dist`; the option is harmless to set | patch |
| 9 | The redacted PDF is read under the same deadline as a JSON call, in the browser (30 s) and in `web` (20 s) (blind, edge) | medium | `client.ts` `file()`, `dapr.py` `_read_file`; a 10 MB document on a slow link fails | patch |
| 10 | The PDF library is in the one bundle every screen loads, the customer's too (implementer) | medium | About 290 kB compressed; the result view can be loaded on demand | patch |
| 11 | `_UNKNOWN_RESOURCE` repeats `_UNKNOWN_PAGE`; the spec's Implementation Notes are empty (blind) | low | Direct corrections | patch |
| 12 | Runs beyond the list of 50 cannot be reached, and the picker cannot tell two runs of one row apart (blind) | low | A case has one run per row | reject |
| 13 | The rule id pattern is repeated in the SPA (blind) | low | It would fail loudly, as a part that cannot be read | reject |
| 14 | An empty audit trail passing through `web` is no longer tested (edge, deletion) | low | The same pass-through is tested with events | reject |

## Design Notes

- **The PDF renderer and the security headers.** `react-pdf` runs `pdf.js` with a worker. The service's content security policy allows scripts from this origin only and no inline script, so the worker must be bundled and served by `web`, not loaded from a CDN or a blob address. If the policy must name `worker-src`, change it in the one middleware and say why; do not loosen `script-src`.
- **Scaling boxes.** Boxes are in PDF points from the page's top-left corner, with the page's width and height in the answer. Draw them as positioned elements over the rendered page, scaled by rendered width over page width; a rotated page keeps `intake`'s orientation.
- **Unproven without a browser.** The tests run in jsdom, where the renderer and the canvas are stand-ins. That the real PDF renders under the policy, and that a highlight sits on its words, needs a look in a real browser against the local stack: add it to `deferred-work.md` beside the thumbnail check of story 1.11.
- Tests follow the owner's rule in `CLAUDE.md` ("Tests: keep the suite small"): a test per acceptance criterion and for the guards of real risk, whole paths before parts, and the Python budgets per package kept (`web` is at 48 of 45: merge or drop weaker tests to make room). The same restraint applies to the SPA's tests: one test per state of the matrix that a person would notice, not one per branch.
- Do not commit or push. Scratch files go in the gitignored `.work/` folder, never outside the project. Stop any containers you start; do not run `tools/dev.sh` while another local stack holds its ports.
- No architecture principles were agreed for this project; the spine and the standards are the guardrails. No UX design exists by the owner's choice: plain default styling.
- Approval: Darrel asked on 2026-10-07 for the remaining stories to be built in order in one session; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services && uv run pytest --cov`, then `docker compose stop` -- expected: all pass, coverage at least 80%
- `npm --prefix services/web/spa ci && npm --prefix services/web/spa run lint && npm --prefix services/web/spa run typecheck && npm --prefix services/web/spa run contracts:check && npm --prefix services/web/spa test -- --run && npm --prefix services/web/spa run build` -- expected: all pass, and the build holds the PDF worker as a file of its own
- `docker build -f services/web/Dockerfile -t aiuw-web:dev .` -- expected: builds
