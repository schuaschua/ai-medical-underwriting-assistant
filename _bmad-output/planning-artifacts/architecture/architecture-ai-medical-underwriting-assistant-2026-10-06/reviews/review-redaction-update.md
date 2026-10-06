# Review — redaction update (CAP-12, AD-21)

- **Reviewed:** `ARCHITECTURE-SPINE.md` (status final, updated 2026-10-06) against `SPEC.md`, `flows.md`, `synthetic-data.md`, `bake-offs.md`, and `docs/standards/security.md` rule 2.
- **Scope:** only the redaction change and what it touches. The owner's three choices (redaction inside `intake` as the first workflow-commanded stage; original kept in `originals` and never served or read by another stage; dates including date of birth kept) are taken as fixed and are not re-argued.
- **Method:** read-through only. Nothing was run. Mermaid was checked by reading the source; no renderer is installed in the project. Claims about how Azure AI Language behaves come from memory and are marked "confirm at build"; no web source was consulted.

## Verdict

The change is coherent in its main line: no arrow, operation, role or owner still reads or serves the original PDF, and the spec and spine agree. It is not yet build-safe, because the hand-offs around the new stage are under-specified: two operations do not connect, the audit record has no place for the counts AD-21 requires, and the failure path and the progress screen before pages exist are undefined.

Severity: **High** = two builders will build incompatibly, or a rule cannot be met as written. **Medium** = likely divergence or an untestable claim. **Low** = wording.

## 1. Internal consistency with AD-21

### What agrees

- **No path to the original.** `POST /cases` "stores the original only"; the only file operation, `GET /documents/<document_id>/file`, is defined as the redacted PDF; no reader arrow touches `originals`.
- **Roles.** `intake` has Contributor on `originals` and `cases`; Azure AI Language's identity has Reader on `originals` and Contributor on `cases`; Document Intelligence's identity has no right on either. The deployment diagram's `intake --> lang` and `lang --> blob` match.
- **Counts.** "Its four containers" matches `originals`, `cases`, `classifier-training`, `manual`. Seven services is unchanged.
- **AD-4 owner table, AD-8 action list, AD-14, AD-18, Stack, accepted exceptions, capability map row CAP-12:** all present and consistent with AD-21.
- **Call graph.** `workflow -->|redaction command, page list| intake` covers both calls `workflow` needs.

### Findings

**F1 — High — Operations table, `POST /cases/<case_id>/start` and `POST /documents/<document_id>/redaction`: `workflow` is never given the `document_id`.**
`web` receives `case_id` and `document_id` from `intake`, but the start operation carries only `case_id` plus four optional fields. `workflow` must then command redaction by `document_id`, and there is no operation that lists a case's documents (`GET /cases/<case_id>/pages` is empty before redaction). One builder will add `document_id` to the start body, another will add a documents read, a third will key by case.
*Smallest fix:* change the operation to `POST /cases/<case_id>/redaction`, key `case_id`. The POC has one document per upload, and the sequence diagram already reads "redact document".

**F2 — High — AD-8 field table against AD-21's last-but-one sentence: the audit record cannot hold the per-category counts.**
AD-21 says `workflow` writes `document.redacted` "with the count of items redacted per category". The AD-8 record has nine fields and none carries a payload. `ref` is listed as "classification, fact set, verdict run, human decision", so it does not name a redaction record either.
*Smallest fix (choose one, state it in AD-8):* (a) add a row `detail` — "small JSON summary; for `document.redacted`, a map of category to count; otherwise null"; or (b) keep the record as is, add "redaction result" to the `ref` list, and say the counts live on `intake`'s redaction record, read through `intake`. Option (a) keeps the trail self-contained, which CAP-10 needs.

**F3 — Medium — AD-8, `actor` row: redaction has no model deployment.**
`actor` for AI is "service app id plus model deployment name". Redaction calls no Foundry deployment.
*Smallest fix:* append "(for redaction: `intake` plus `azure-ai-language` and the API version)".

**F4 — Medium — AD-21, sentence "The original is the only place PII exists, so it is the only store marked sensitive": contradicts the same AD.**
Dates of birth, ages and medical content are kept by design, so they exist in `cases`, in `intake`'s page text and in `extraction`'s facts. `security.md` rule 2 names date of birth and health answers as sensitive fields even when synthetic. A builder could read this sentence as licence to log page text from the redacted copy. This does not question keeping dates; it only corrects the claim.
*Smallest fix:* "The original is the only place the redacted categories exist, so it is the only store marked as holding identifiers. Redacted text still holds dates of birth and health data and stays under `security.md` rule 2."

**F5 — Medium — AD-13 and the AD-4 row for `classification`: training pages bypass redaction.**
The training job reads `data/classifier-training/` straight into container `classifier-training`. Scored pages reach both contenders redacted (with `[Person]` tokens); training pages are not said to be redacted. So the Document Intelligence contender is trained on one kind of page and scored on another, and if training pages carry identifiers, a second container holds them, which AD-21 says cannot happen.
*Smallest fix:* add to AD-13: "Training pages are redacted with the same settings before training" or, cheaper, add to `synthetic-data.md` and AD-13: "Training pages are generated already masked, in the same form redaction produces."

**F6 — Medium — "Local development" paragraph: Azure AI Language and Blob Storage are missing.**
The list of `demo` resources used locally names Foundry, Azure AI Search and Document Intelligence only. Redaction is now on the demo path, and the Language service reads and writes Blob with its own identity, so a local storage emulator cannot serve it.
*Smallest fix:* "...the `demo` environment's Foundry, Azure AI Search, Document Intelligence, Azure AI Language and Storage account...".

**F7 — Low — places not updated for CAP-12.**
- AD-2 Binds omits CAP-12, and write 1 says "store the PDF"; say "store the original PDF".
- AD-17 Binds omits CAP-12 although it now holds the planted identifiers and the runner check.
- Paradigm table (`intake`: "page files") and AD-3 ("page files and thumbnails") name per-page files, but the Operations table has only the whole redacted document and thumbnails. Either drop "page files" or add the operation. This predates the change; it matters now because AD-13 says both classifiers get "a one-page document" and it must come from the redacted copy.
- Capability map: CAP-1 and CAP-7 could cite AD-21 (progress and the PDF viewer both depend on it).
- AD-3: says the 10 MB limit exists but not who enforces it. Add "`web` rejects a larger upload with 413 and code `file_too_large`".

## 2. Could two builders obey the spine and still disagree?

**F8 — High — AD-21 and AD-6: the failure path of redaction is not defined, and it must fail closed.**
What the spine does say: the stage stores `failed` at 180 seconds, and a repeat with the same key returns the stored result. So a failed redaction is permanent for that document. What it does not say:
- that the case ends in status `failed` with a `stage.failed` event at case level (no page exists to carry page status `failed`);
- that nothing falls back to the original;
- what happens to the Language job, which is asynchronous and may finish after the deadline and write into `cases`.
*Smallest fix, one sentence in AD-21:* "If redaction fails or passes its deadline, `intake` cancels the Language job, creates no pages, and `workflow` ends the case as `failed` with a case-level `stage.failed` event; the original is never used instead and the customer uploads again."

**F9 — High — Operations table, `GET /cases/<case_id>/progress` and the reader row; SPEC CAP-1: the state between upload and the end of redaction is undefined.**
Pages are created inside the redaction command, so for up to 180 seconds a case has no pages. Undefined today:
- what `progress` returns (CAP-1 promises per-page badges on the upload screen, and the payload has no case-level stage field);
- what `GET /cases/<case_id>/pages` returns (empty list or an error);
- what `GET /documents/<document_id>/file` returns (it must not be the original).
*Smallest fix:* add to the reader row's note: "Before redaction is done, the page list is empty and the file returns 409 with code `not_redacted`." Add to the progress row's note: "Carries case status and the redaction stage status (`running`, `done`, `failed`) beside the page list, which is empty until redaction is done."

**F10 — Medium — who creates page records, and how `workflow` learns of them.**
The Operations note makes `intake` the creator ("redacts, then splits pages and stores text, boxes and thumbnails"), which is clear. Unclear: whether the redaction result returns the page ids or `workflow` must call the page list. The call graph says "page list"; AD-6 allows either.
*Smallest fix:* add to the redaction row: "Result: status, the redaction record id and the counts. `workflow` then reads the page list and creates one page status row per page (`uploaded`) in the same transaction as the `document.redacted` event."

**F11 — Medium — AD-4 and AD-21: no rule for where the redacted file and the result JSON sit in `cases`.**
The Language service chooses the output file names under the target location it is given, and writes a result JSON beside the redacted file. Nothing fixes the target prefix or says how `intake` finds the output. Also confirm at build whether that JSON lists the detected entities with their original text. If it does, identifiers sit in `cases`, which breaks AD-21.
*Smallest fix:* add to AD-21: "`intake` passes the target prefix `cases/<case_id>/<document_id>/redaction/` and stores the output locations the job returns on its document row; nothing derives a path. Thumbnails go under `cases/<case_id>/<document_id>/pages/`. If the result JSON holds entity text, `intake` deletes it after reading the counts."

**F12 — Medium — AD-21: what the counts are computed from.**
Two honest readings: count the entities in the service's result, or count mask tokens in the stored page text. They differ (a mask wrapped across lines, a literal bracket in the source). Category names also differ: AD-21 lists five friendly groups; the service returns many specific categories, several of them identity numbers.
*Smallest fix:* "Counts are the number of entities in the service's result, grouped by the service's category name. The categories setting holds service category names. A document with nothing redacted still gets the event, with an empty map."

**F13 — Medium — AD-14: quotes and offsets around a mask token.**
The design mostly holds: extraction reads the redacted text, so a quote that spans a mask contains the literal token, the check runs against the same text, and offsets map to whatever boxes cover the range. Three things are left to chance:
- the normalisation function might strip or alter brackets, or the model might write the token differently (`[PERSON]`, `[Person 1]`), which turns a good fact into an unverified one and, by AD-15, a `refer`;
- a mask token is one word box, so highlighting cannot be finer than the token;
- nothing stops the model emitting a "fact" whose value is a mask token.
*Smallest fix, add to AD-14:* "Mask tokens such as `[Person]` are ordinary page text: normalisation keeps them unchanged, a quote may contain them verbatim, and a highlight covers the whole token's box. A fact whose value is a mask token is not stored."

**F14 — Medium — AD-17 and AD-21: the planted-identifier check has a reach but no matching rule and no place for its result.**
Reach is fine: the runner reads the answer key and gets page text through `web` (`GET /api/pages/<page_id>/text`), so the "runner only" rule on the answer key holds. Missing:
- the comparison rule (exact, case-insensitive, normalised; whole identifier or each name part; an address or name broken across lines);
- where the result goes. AD-17 says metrics are "exactly those in `bake-offs.md`" and publishing is two named files, so a builder has nowhere to put a pass or fail.
*Smallest fix, new bullet in AD-17:* "**Redaction check:** for every case in a run, the runner reads every page's text through `web` and fails the run if any planted identifier, or any single part of a planted name, appears after the contracts normalisation. It writes `data/scoreboards/redaction.json` with cases checked and leaks found." Add CAP-12 to AD-17's Binds.

**F15 — Low — AD-8 uniqueness on a null `page_id`.**
`document.redacted` is a case-level row, so `page_id` is null. In PostgreSQL a plain unique constraint treats nulls as distinct, so a retried activity would insert a second row. Case-level rows existed before, but redaction puts one on every case's first step.
*Smallest fix:* add "nulls not distinct" to the uniqueness sentence in AD-8.

## 3. Spec against spine, and CAP-12 testability

### Agreement

- `SPEC.md` CAP-12 intent and success match AD-21 sentence for sentence.
- `flows.md` puts redaction first in the pipeline, names the same categories, the same kept items and the `[Person]` mask, and says the original is never shown or read again.
- `synthetic-data.md` plants the six identifier kinds that AD-21's default categories cover and records them; AD-17 puts that record in the answer key.
- The SPEC constraint on non-standard Azure services now names Azure AI Language, matching the accepted exception.

### Gaps

**F16 — Medium — CAP-12's second clause is testable only in one direction.**
"None of the planted identifiers appears in any stored page text" is covered once F14 is fixed. Two holes remain:
- **Over-redaction is invisible.** AD-21 says medical terms are kept, but the service decides by category. Conditions named after people (Parkinson's, Crohn's, Hodgkin) can be masked as a person, and nothing in the spine checks that. A failed case would show up only as lower verdict accuracy with no cause.
  *Smallest fix:* add to the F14 bullet: "It also reports how many expected-fact quotes from the answer key are no longer found in page text."
- **Policy number.** A synthetic policy number has no fixed real-world format, so the service may not detect it, and then CAP-12 cannot pass.
  *Smallest fix:* add an open question: "Does the service mask the synthetic policy and identity numbers? If not, generate them in a format it detects, or add them as a custom pattern." Record the chosen format in `synthetic-data.md`.

**F17 — Low — CAP-12's first clause ("every later stage and every screen uses only the redacted copy") is true by construction, not by test.**
It rests on there being no operation that serves the original and on the F9 rule. Stored page text is derived from the served file, so the F14 check also covers the viewer's text. It does not cover images: a page that is a scan yields no text, passes the check with nothing to match, and still shows its identifiers in the thumbnail and to the LLM classifier. The open question on embedded images already names this; extend it to say "the check passes on such pages without testing anything".

**F18 — Low — spec wording.**
- SPEC CAP-1 success ("shows a per-page status badge within the upload screen") does not mention the wait for redaction; add "once redaction has finished".
- SPEC CAP-10 intent lists the recorded steps and omits redaction; add it, since AD-8 now records it.

## 4. Mermaid validity

Checked by reading; not rendered.

- **Deployment flowchart:** valid. `lang[Azure AI Language: PII redaction]` uses a colon inside square brackets, as the existing `cae[...]` and `foundry[...]` labels do. `intake --> lang` and `lang --> blob` use defined ids. `di` and `foundry` are referenced one line before their labelled definitions, which Mermaid allows. `cae --> obs` links from a subgraph id, which is allowed.
- **Sequence diagram:** valid. The two new lines (`F->>I: redact document, then split pages` and the `Note over I: ... (AD-21)`) use declared participants and plain text. One content point: every other stage shows a reply arrow and redaction does not; add `I-->>F: page ids, redaction counts` after the note so the diagram matches F10.
- **Call-graph flowchart (also edited):** valid. `workflow -->|redaction command, page list| intake` is a well-formed labelled edge.

## Fix order

1. F1, F2, F8, F9 — without these the stage cannot be built one way.
2. F10 to F14, F16 — pin the hand-offs and make CAP-12 measurable.
3. F4, F5, F6 — correct the claims that reach beyond redaction.
4. F3, F7, F15, F17, F18 — wording.
