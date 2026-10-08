# AI Medical Underwriting Assistant

An AI assistant to support medical underwriting.

## Setup

This repository uses the [BMad Method](https://github.com/bmad-code-org/BMAD-METHOD) v6.12.1 with the
[Org Kit](https://github.com/schuaschua/bmad-org-kit) v1.14.0 module, configured for Claude Code.

- `_bmad/` holds the BMad configuration and the Org Kit overrides (`_bmad/custom/`).
- `.claude/skills/` holds the BMad and Org Kit skills.
- `docs/standards/` holds the org standards baselines and `docs/governance/` the blank governance questionnaires.

Open the folder in Claude Code and run the `bmad-help` skill to see what to do next.

## Code

The Python code is one [uv](https://docs.astral.sh/uv/) workspace (Python 3.13). The root `pyproject.toml`
holds the ruff, mypy and pytest settings for every member.

- `packages/contracts/` is the only shared code: the payload models for every service operation, the
  audit record, enums, the error catalogue, the `rule_id` patterns, the page type mapping, the eval
  query builder and text normalisation. It imports only the standard library and pydantic. A change to
  it is one pull request that updates every affected service.
- `services/` holds the seven services. `services/web/` is the FastAPI service
  that serves the React app in `services/web/spa/` and every `/api` route from one origin.
  `services/intake/` owns cases, documents and the stored PDFs: database schema `intake` and the blob
  containers `originals` and `cases`. `services/workflow/` owns the case lifecycle: one orchestration
  per case on Azure Durable Task Scheduler, case and page status, the decisions people made and the
  append-only audit trail (database schema `workflow`). `services/classification/` says what each page is, with a confidence
  and a reason (database schema `classification`); its prompt is in
  `services/classification/src/classification/prompts/`. `web` calls `intake` and `workflow` through
  its Dapr sidecar: it asks `intake` to create a case from an upload, then asks `workflow` to start
  it. It passes a person's decision about a page on to `workflow`, reads the classifications from
  `classification`, and composes the underwriter's triage queue from `workflow`'s queue and those
  classifications; a page's thumbnail it reads from `intake`. `workflow` commands `intake`, `classification`, `extraction` and `verdict`, and `classification` reads
  each page from `intake`. `services/extraction/` reads the medical facts of each page that reaches
  extraction, each with a verbatim quote that code checks against the page text `intake` stores
  (database schema `extraction`); its prompt is in `services/extraction/src/extraction/prompts/`. `services/retrieval/` owns the underwriting manual's rules as chunks with
  vectors (database schema `retrieval`, blob container `manual`) and the one-off job that ingests the
  manual; its prompt is in `services/retrieval/src/retrieval/prompts/`. It searches those chunks
  (`POST /searches`) and reads a rule by its id (`GET /rules/<rule_id>`), and calls no other service. `services/verdict/` suggests a verdict for a case,
  with cited reasons: its agent reads the case's facts from `extraction` and searches and reads the
  manual's rules at `retrieval`, both through its Dapr sidecar, and every tool call it makes is kept
  in an append-only step log (database schema `verdict`); its prompt is in
  `services/verdict/src/verdict/prompts/`. No service imports another service's code.
- `evals/` is the bake-off runner, a dev tool that is in no image: one command that drives the running
  system through `web`, as a user would, over the synthetic cases and their answer key, scores the
  retrieval rows or the two classifiers, and writes the scoreboard files (see "The bake-off" below and `evals/README.md`). It is the only code besides the
  generator's tests that reads `data/answer-key/`.

### Install and check

Install uv 0.11.8 and Node.js 24.21.0, then from the repository root:

```sh
uv sync
uv run ruff format --check . && uv run ruff check .
uv run mypy packages services evals
docker compose up --detach --wait                    # for the integration tests
uv run pytest --cov

npm --prefix services/web/spa ci
npm --prefix services/web/spa run lint
npm --prefix services/web/spa run typecheck
npm --prefix services/web/spa test -- --run
npm --prefix services/web/spa run build
```

`uv sync` creates `.venv/` and installs the exact versions in `uv.lock`. `pytest --cov` takes its test
paths, the measured packages and the 80% coverage threshold from the root `pyproject.toml`. The tests
marked `integration` use a real PostgreSQL, the blob emulator and the Durable Task Scheduler emulator
from `compose.yaml` and fail with a message saying so if those containers are not running; each makes a
database of its own (and `workflow`'s use a task hub of their own, `aiuw-test`), so your local data is
left alone, and none calls Azure. `uv run pytest -m "not integration"` leaves them out. The SPA's
60% threshold is in `services/web/spa/vite.config.ts`. The same checks, plus dependency scans
(`pip-audit`, `npm audit`), run on every pull request and on every push to `main`
(`.github/workflows/ci.yml`). Fix a failing check in the code; do not loosen the settings.

### Run locally

You need Docker, the [Dapr CLI](https://docs.dapr.io/getting-started/install-dapr-cli/) 1.18 with its
runtime installed once (`dapr init`), uv and Node.js.
One command starts everything:

```sh
./tools/dev.sh
```

It starts PostgreSQL with pgvector, the Azurite blob emulator and the Durable Task Scheduler emulator in
containers (`compose.yaml`), applies the database migrations, builds the SPA, starts a stand-in for
Azure AI Language, one for the Foundry model deployments, one for Document Intelligence's layout
model and one for Azure AI Search (see below), ingests the underwriting manual, loads the search
stand-in's index from it and has it hold a knowledge base over that index, and runs the `web`, `intake`, `workflow`,
`classification`, `extraction`, `retrieval` and `verdict` services, each with its Dapr sidecar (`dapr.yaml`; each later service
is added to that file).
If the Dapr runtime is missing it stops and says so. Then open <http://localhost:8000/>. The app and
its API share that one address: `/api/health` answers without a role, and every other `/api` route
needs the `X-Demo-Role` header the role switcher sends. As the customer, "Upload a document" takes a
PDF of up to 10 MB (try one from `data/cases/`), starts its case and lists it with its status, which
the screen reads again every few seconds. If the case cannot be started, it is listed as received but
not started, with a button to try again; the document is not sent a second time.

A started case is redacted first: `workflow` commands `intake`, which has Azure AI Language mask
person names, addresses, phone numbers, email addresses, and identity and policy numbers with tokens
such as `[Person]` (dates, ages and medical terms are kept), stores the redacted PDF as the document
of record and splits it into pages, each with its text, a box per word and a thumbnail. From then on
only the redacted PDF is read; no route serves the original. If redaction fails or takes longer than
180 seconds the case is shown as failed, with a message asking for the document to be uploaded again.

Once a case is redacted, every page is classified: `workflow` sends `classification` one command per
page, all at once, with the classifier the case was started with (`llm`; a case started with
`doc-intelligence` fails, because that classifier is not built yet). `classification` reads the page's
text and thumbnail from `intake`, runs the chat model on it five times
(`CLASSIFICATION_CLASSIFIER_RUNS`), and stores the page type most runs named, whether that type is
medical (from the one mapping in the contracts package, never from the model), the share of runs that
agreed as the confidence, and one of the agreeing runs' one-line reasons. A tie goes to the type that
comes first in the contracts' list of page types. The page then shows as `classified`, and the audit
trail holds a `page.classified` event naming the service and the model deployment. If the model's
answer is not what was asked for, or the model cannot be had after three retries, or 180 seconds pass,
that page and its case are shown as failed. `GET /cases/<case_id>/classifications` on the service
lists what was stored; no screen shows it yet.

Then the gate routes every classified page. It is one rule in `workflow` and nowhere else: a medical
page classified with a confidence of 0.90 or more goes on to extraction (`extracting`), a non-medical
page at 0.90 or more goes back to the customer (`awaiting_customer`), and any page under 0.90 goes to
the underwriter's triage (`awaiting_triage`); exactly 0.90 counts as "or more". The threshold is the
setting `WORKFLOW_GATE_THRESHOLD` (default `0.90`, refused at start-up outside 0 to 1); only
`workflow` is given it, and no other service, screen or prompt works out a route. A case keeps the
value in force when its lifecycle confirmed it, so a change applies to cases started after it.
Each route is written with a `page.routed` event by `workflow:gate`, which names the classification it
came from, the route and the threshold used. After the gate a case with a page that waits for a person
shows as `awaiting_human`; a case whose pages all went to extraction stays `running` until they are
extracted (see below); a case started with `stop_after: gate` ends as `completed` and none of its
pages is extracted.

A page that waits for a person is decided by a person and by nothing else. There is one operation for
it, `POST /cases/<case_id>/pages/<page_id>/decisions` on `workflow`, and no other way a page is kept,
discarded, accepted or denied. Who may decide what is one mapping in the contracts package
(`contracts/decisions.py`), enforced in `workflow`'s domain code:

| Decision | Role | The page must be | It becomes | Audit action |
| --- | --- | --- | --- | --- |
| `discard` | customer | `awaiting_customer` | `discarded` | `page.discarded` |
| `keep` | customer | `awaiting_customer` | `awaiting_triage` | `page.kept` |
| `accept` | underwriter | `awaiting_triage` | `extracting` | `page.accepted` |
| `deny` | underwriter | `awaiting_triage` | `denied` | `page.denied` |

An actor that is not one of the two demo roles is refused with `actor_not_human` (403), a role asking
for the other role's decision with `role_not_allowed` (403), and a page that does not wait for that
decision, or whose case has failed, is completed or was started with `stop_after: gate`, with
`not_awaiting_decision` (409). The decision (table `workflow.human_decision`), the page's new status,
the case status and the audit event (actor kind `human`, the demo role as actor, the decision's id as
reference) are written in one transaction. The same decision sent again writes nothing and is
answered with the stored one.

The case status follows the pages, in that same transaction: `awaiting_human` while any page waits
for a person, else `running` while any page is still in work, else `completed`. The step after the
gate uses the same rule on the stored pages, so it cannot undo a decision however late it runs.

The lifecycle no longer ends at the gate when a page waits. Its orchestration waits for each
decision as an external event (never by polling or a timer) and goes on page by page as decisions
arrive; it ends when no page waits. The decision is stored by the HTTP call, and the event only wakes
the orchestration: if the event cannot be raised the call answers 502, and sending the same decision
again raises it, as long as the case's orchestration is still alive. For an orchestration that has
completed nothing is raised and nothing is said; for one that is missing, failed or terminated
nothing is raised and `workflow` logs a warning (`decision not told`), because the decision is stored
and no lifecycle will go on with it.
Because a waiting case keeps its orchestration alive, the orchestration's code may not be changed
freely once a deployed case is in flight: see the note at the top of
`services/workflow/src/workflow/adapters/orchestration.py`.

**Facts with checked quotes (story 2.4).** Every page that reaches `extracting`, by the gate or by an
underwriter's accept, gets one command from `workflow` to `extraction` (`POST /fact-sets`, keyed on
case and page). The waits for people and the extractions run side by side: an accepted page is
extracted when it is accepted, not when every page is decided. `extraction` reads the page's number
and its stored text from `intake` (the redacted reading, and nothing else of the page), asks the chat
model once for the page's medical facts, each a one-line statement and a verbatim quote, and then
checks every quote in code. The check is one function in the contracts package
(`contracts.text.find_quote`): a quote is found when it occurs in the page text once both are
normalised (case, spacing, line breaks, ligatures and a PDF's invisible characters do not count) and
stands there whole, not inside a word or a number (`5.6` is not found in `15.6`, nor `4 %` in
`7.4 %`). The answer is where it sits in the text as stored: `quote_start` and `quote_end`, counted
in Unicode code points like `intake`'s word boxes (`QUOTE_OFFSET_UNIT`), the first place if the words
occur twice. Only a found quote makes a fact `quote_verified`. A fact whose quote is not on the page is
stored and flagged, with no offsets; it is never dropped and never shown as verified. A masked value
such as `[Person]` is never a fact: a proposal whose statement holds a mask token, or whose quote is
nothing but mask tokens, is left out and counted in the log. The model decides none of this: ids,
page numbers, verification and offsets are set by code. A page with nothing medical is a done result
with no facts. A single proposal that cannot be a fact (no quote, another field) is left out and
counted in the log; an answer that is not the shape asked for, or that was cut off at the token
limit (`reason=answer_cut_off` in the log), fails the page with `invalid_model_output`.
`GET /cases/<case_id>/facts` on the service lists the stored facts in page order; the underwriter's
result view shows them (story 2.7, below).

`workflow` records each result: a done one moves the page to `extracted` with a `facts.extracted`
event naming the service and the model deployment, a failed one fails the page and the case with
`stage.failed`. In that same recording the case status follows the pages, so the last page to become
final (`extracted`, `discarded` or `denied`) completes the case, with `case.completed` as the trail's
last event, and the orchestration ends. A case whose extraction fails while another page waits for a
person is `failed`, its orchestration ends, and the waiting page takes no decision.

A waiting case no longer depends on the browser. A decision is stored first and the orchestration
told after; each decision that was told is marked (table `workflow.decision_told`). Every
`WORKFLOW_DECISION_TELL_INTERVAL_SECONDS` (15) `workflow` itself looks for stored decisions older
than `WORKFLOW_DECISION_TELL_GRACE_SECONDS` (30) that carry no mark, and raises their events again.
It never gives one up: after a look in which the scheduler could not be reached, the wait before the
next look doubles, up to `WORKFLOW_DECISION_TELL_MAX_INTERVAL_SECONDS` (300), with a log line each
time. If a decision's case turns out to have no orchestration, or a failed or terminated one, the
decision is not marked as told and the case is failed with a case-level `stage.failed` event, so it
does not stay `running` with a page nothing will extract. That is a timer of the service; the
orchestration still neither polls nor has a timer.

`web` passes a decision on (`POST /api/cases/<case_id>/pages/<page_id>/decisions`, body
`{"decision": ...}`) with the request's demo role as the actor, and adds no rule of its own. It also
reads what the classifier said (`GET /api/cases/<case_id>/classifications`, from `classification`).

The upload screen shows each case's pages under its status, one badge per page with the page number
and the status the server gave it, and updates them as it reads the progress again every few seconds.
Under a page that waits for the customer it asks, for example, "This looks like an invoice or bill
(100%). Discard or keep?", with the predicted type and the confidence the server gave, and the two
buttons. After an answer it reads the case again. If the call fails the prompt stays, with the error.
The customer sees the same message whether or not the answer was saved: after a call that got no
answer, or a fault of the server, the answer may be stored, so only the same answer can be sent again
("Try again"), also once the page shows a new status. After a refusal nothing was saved, and both
answers are offered again. To see it locally, upload
`data/cases/case-002.pdf`: three of its pages go back to the customer.

**The triage queue (story 1.11).** `GET /pages?status=<status>` on `workflow` is the queue across
cases: the pages in that status, the one that has waited longest first. The status must be one a
page waits for a person in (`awaiting_triage` or `awaiting_customer`); a missing or unknown status,
or any other, is refused with `validation_failed` (422). Left out are the pages of a case that
belongs to an eval run, and of a case that takes no decision (failed, completed, or started with
`stop_after: gate`), so nothing is listed that would be refused. Each page names the classifier its
case runs with and, in triage, how it got there (`queued_by`: `gate`, or `customer` for a page the
customer kept). One read lists at most `WORKFLOW_PAGE_QUEUE_LIMIT` pages (default 100) and says with
`has_more` when more wait.

`web` serves the underwriter's queue at `GET /api/triage` (the customer role is refused with
`role_not_allowed`). It reads the triage queue from `workflow` and, once per case with a waiting
page, the classifications from `classification` (at most `WEB_TRIAGE_MAX_CONCURRENT_READS` at a
time, default 8, all within `WEB_LIFECYCLE_TIMEOUT_SECONDS`), and joins them into one payload per
page: case, page, page number, `thumbnail_path`, and the `page_type`, `is_medical`, `confidence` and
`reason` of the classifier the case runs with. A page whose classification cannot be read is listed
without those four and can still be decided. `web` holds no rule about the queue and keeps nothing.
`GET /api/pages/<page_id>/thumbnail` serves, to either role, the PNG `intake` made of the redacted
page; no route serves anything of an original.

The underwriter's "Triage queue" screen lists those pages in the server's order, each with its
thumbnail, the predicted type in plain words, the confidence as a percentage, the reason (shown as
text) and how the page got there, with Accept and Deny. It reads the queue again every few seconds,
and at once after a decision; an empty queue says "Nothing is waiting." A decision goes through the
decision route above, as the underwriter. A failure is shown on the row: after a refusal both
buttons are offered again, and after a call that may have been stored only "Try again", which sends
the same decision, and whose row stays until that call succeeds even if the server no longer lists
the page. A page that was decided in another tab (`not_awaiting_decision`) gets a plain note and
leaves at the next read. The thumbnail is read by the API client, with the role header every call
carries, and drawn on a canvas: an image element could not send the header, and the content security
policy allows no `blob:` or `data:` picture. To see it locally, run
`FOUNDRY_STANDIN_MODE=mixed ./tools/dev.sh`, upload `data/cases/case-002.pdf` as the customer and
keep a page, then switch to the underwriter.
The progress of a case, and of a page, also carries the error code of the stage that failed, if one
did (`error_code`); the screen does not show it yet.

**The audit trail (story 1.12).** `GET /cases/<case_id>/audit` on `workflow` answers a case's events
in the order `workflow` wrote them: by `audit_event_seq`, a number the database gives each event at
its insert (migration `0004`), and by nothing else. A case's events are inserted one after the other
under the lock on its row, so the number is the order of writing. The trail is not ordered by
`occurred_at`, which the clock of the service that did the work sets, nor by the record time, so a
page's classification always comes before its route and its route before a decision about it. Each
event still carries its `occurred_at`. A `stage.failed` event carries the `error_code` stored with
it (the audit record has that field now; it is null for every other action, and a stage's own
record may leave it out). One read lists the first `WORKFLOW_AUDIT_TRAIL_LIMIT` events (default 500)
and says with `has_more` when the case has more. Because it is the first events that are listed, a
trail longer than the limit never shows its last ones: the `case.completed` event of story 1.13 is
then not on the screen, and the case's status says whether it was completed. A stored row whose code is outside the catalogue is
answered as `stage_failed`, and a code on a row that is no failure is left out; both are logged with
the case id and the event id. Nothing new writes to the audit table. Migration `0004` numbers the
events already there in the order they were read in before (record time, then id), with the
append-only trigger set aside for that one statement and put back in the same transaction, and
replaces the index on `(case_id, occurred_at)` by one on `(case_id, audit_event_seq)`. `web` passes the trail on at `GET /api/cases/<case_id>/audit`, for the
underwriter only (the customer role is refused with `role_not_allowed`).

The underwriter's "Audit trail" screen (`/underwriter/audit?case=<case_id>`) has a field for a case
id and a link from every row of the triage queue. Text that is not a case id is refused in the field
and no call is made; a case that was never started reads "No such case." The table lists the events
in the server's order with the time (the viewer's local time; the UTC time is the title of the
cell), the actor, the action with the page number, the page and the detail. A person is shown as
the demo role; an AI step as the service and what did the work in it, both parts, for example
"Classification service, model deployment chat-main" or "Workflow service, rule gate"; an actor the
screen does not know shows its two parts with no name for the second. The detail is the redaction's
counts per category, the gate's route and the confidence it asked for exactly as recorded, or the
reason a step failed in plain words (a code the screen has no words for is shown as it is) with the
event's trace id as a reference. The screen
reads the case's progress first (for the page numbers and the case status) and the trail after it,
every few seconds while the tab is visible, and stops once the case is completed or failed, once
the trail holds more events than one read lists (it then says that only the first are shown), or
once the server refuses the read (a 4xx other than 429); "Check again" reads once more. The field
and its refusal follow the address, so back and forward move between trails. It
orders nothing and works out no status or actor. To see it locally, run
`FOUNDRY_STANDIN_MODE=mixed ./tools/dev.sh`, upload `data/cases/case-002.pdf` as the customer,
discard a page and keep one, then switch to the underwriter, accept the kept page in the triage
queue and follow "Audit trail" on a row (or copy the case id from the upload screen into the field).

**Case events and the case list (story 1.13).** A trail now says who started its case and when the
case was completed. Two audit actions were added to the contracts' catalogue: `case.started` (actor
kind `human`, the actor is the demo role that asked for the start) and `case.completed` (actor kind
`ai`, the actor is `workflow:case-lifecycle`). Both are about the whole case: no page, no detail, and
the case id as their reference. `workflow` writes `case.started` in the transaction that stores the
case, and `case.completed` in the transaction that moves the case to `completed` (the last decision
that leaves every page final, or the settle after the gate of a case started with
`stop_after: gate`), after the decision's own event, so it is the trail's last. A repeated start, by
the same role or the other, adds no event and the first actor stands; a repeated decision or settle
adds no second completion. A failed case keeps its `stage.failed` event and gets no `case.completed`.
Nothing is recorded when a case begins to wait for a person or runs again. Migration `0005` adds two
partial unique indexes on `workflow.audit_event`, so the database itself takes one `case.started` and
one `case.completed` row per case, whatever reference a row names. A recording of a stage result
that asked to complete a case is refused in the store: a case is completed only where the event is
written with the status.

The start request `workflow` takes carries an `actor`. `web` sets it from `X-Demo-Role`, as it does
for a decision. The body the browser sends to `POST /api/cases/<case_id>/start` is the start options
only (`StartCaseOptions`): it has no `actor`, and a body that names one is refused with 422.
`workflow` refuses a start whose actor is missing, blank or not a demo role with 403
`actor_not_human`, and stores nothing; so anything that calls `workflow`'s start directly must name
a role in the body, for example `{"actor": "underwriter"}`.

`GET /cases` on `workflow` lists the cases newest first: case id, case status, when it was started
(`started_at`), how many pages it has (`page_count`) and how many of them wait for a person
(`waiting_page_count`, which is 0 for a case that takes no decision any more: failed, completed, or
told to stop after the gate). Cases of an eval run are left out. One read lists at most
`WORKFLOW_CASE_LIST_LIMIT` cases (default 100) and says with `has_more` when more exist; there is no
filter, search or paging. `web` passes the list on at `GET /api/cases`, for the underwriter only (the
customer role is refused with `role_not_allowed`). The underwriter's "Cases" screen
(`/underwriter/cases`) shows the list in the server's order and reads it again every few seconds
while the tab is visible; each row links to the case's audit trail, which is how a finished case is
found. Once the server refuses the read itself (a 4xx other than 429) the screen shows the server's
message and reads no more until "Check again" is chosen; the triage queue now does the same. An empty list reads "No cases yet." The audit trail screen words the two new actions as "Case
started" and "Case completed". To see it locally, run `FOUNDRY_STANDIN_MODE=disagree ./tools/dev.sh`,
upload `data/cases/case-001.pdf` as the customer, switch to the underwriter, deny every page in the
triage queue, then open "Cases" and follow "Audit trail" on the case's row: the trail starts with
"Case started" by the customer and ends with "Case completed".

**The result view (story 2.7).** The underwriter reads a case's suggested verdict beside its
document at `/underwriter/result?case=<case_id>`, reached from "Result" on a row of the "Cases"
screen and from "Result of this case" on a case's audit trail (the customer has no such screen). On
the left is the redacted PDF; on the right the verdict, then the reasons, then the facts in the
order `extraction` lists them (page order). Wherever a verdict is shown, so is the label "AI
suggestion, not a decision", taken from the run's payload, and the screen has no control that
decides, approves or overrides anything. A loaded verdict reads, for example, "Loaded premium,
+50 %"; a referral reads "Refer to underwriter" with each system reason in plain words; the
confidence is shown as a percentage. A reason shows its rule, its effect and debit, and the facts it
cites; a fact shows its statement, its quote and its page.

`web` gains six read routes for it, for the underwriter only (the customer role is refused with
`role_not_allowed`, and no service is asked). Each passes one operation of the owning service on,
under the same path, and adds no rule:

| `web` route | Owner |
|---|---|
| `GET /api/cases/<case_id>/facts` | `extraction` |
| `GET /api/cases/<case_id>/verdict-runs` | `verdict` |
| `GET /api/rules/<rule_id>` | `retrieval` (the rule's one-rule chunk) |
| `GET /api/cases/<case_id>/pages` | `intake` |
| `GET /api/pages/<page_id>/boxes?quote_start=&quote_end=` | `intake` |
| `GET /api/documents/<document_id>/file` | `intake` (the redacted PDF, `application/pdf`) |

A refusal of the caller's own request is passed on (404 `not_found`, 422 `validation_failed`, and
409 `not_redacted` for a document that has no redacted file yet); anything else, an answer that is
not the contract's shape, or a file that is not a PDF or is empty, is 502 `upstream_unavailable`.
The file is the only document any route returns, and it is the redacted one: `intake` has no route
for an original.

Following the citation of a fact whose quote was found ("Page 3") scrolls the document to that page
and highlights the quote. The highlight is drawn from the word boxes `intake` returns for the
fact's own `quote_start` and `quote_end`, each placed as a share of the page's width and height, so
it sits on its words at any drawn size; the browser never looks for the quote in any text, and the
PDF's text layer is not drawn at all. The page is also named in words above the document ("Page 3:
the quote is highlighted."), and if the boxes cannot be read the page is still shown and the line
reads "Page 3: the highlight could not be shown." A
fact whose quote was not found reads "Page 3: quote not found on the page" and has no citation
control. Choosing a reason's rule ("Rule UW-DM-002") opens the manual's text of it, with its
impairment and manual page, beside the reasons; a rule the manual does not hold reads "This rule is
not in the manual." The rule text, statements and quotes are rendered as text, never as HTML.

A case with several runs (one per retriever configuration) names the row of the run on screen and
has a picker for the others. A case with no run yet reads "No suggestion yet." and shows the
document and the facts so far; a failed run, and a failed case, say so with the reason in plain
words. While the document is not redacted the screen reads "The document is not ready yet."; a case
that was never started reads "No such case." The screen reads the case's status, pages, facts and
runs again every 3 s while the tab is visible, and stops once the case is final (and no listed run
is still running); "Check again" reads at once. An answer that is not the contract's shape is a
fault of that part only ("The facts could not be read."), and the other parts are still shown.

The PDF is drawn by `react-pdf` 11.0.0 (`pdf.js`). The file is read by the API client, with the
role header every call carries, and handed to the renderer as bytes; pages are drawn as they come
into view, as wide as the document's pane (never under 240 CSS pixels), and drawn again when the
pane's width changes. The result view is a chunk of its own, so the PDF library is loaded only when
that screen is opened, not with the other screens. The renderer's worker is built as a file of its own
(`dist/assets/pdf.worker.min-<hash>.mjs`) and served by `web` like every other script, so the
content security policy is unchanged: scripts and workers from this origin only, no inline script,
no `blob:` and no WebAssembly (`useWasm` is off). To see it locally, run `./tools/dev.sh`, upload
`data/cases/case-001.pdf` as the customer, switch to the underwriter, open "Cases" and follow
"Result" on the case's row. The tests stand in for the renderer (jsdom has no canvas and no
worker), so that the real PDF draws under the policy, and that a highlight sits on its words, still
needs a look in a real browser (`_bmad-output/implementation-artifacts/deferred-work.md`).

**The agent's log (story 2.8).** The underwriter can see which searches and rule reads led to a
suggestion. On a case's audit trail, a "Verdict suggested" row names the retrieval row the run was
made with and has the button "Show the agent's steps"; on the result view the run on screen has
"How was this reached?". Both open the same table under what was chosen: every tool call of that
run in order, with its step number, tool, arguments, the fact it was about, the rules returned or
read, its outcome, how long it took and when. A call that was refused or failed is listed like any
other, with the reason in plain words ("Refused. A rule was asked for that had not been found
first."). Arguments, queries and rule ids come from a model and are rendered as text, never as
HTML; a value of more than 80 characters is cut and opens whole. The steps can be narrowed by tool
and by rule id ("Show matching steps"); the narrowing is done by `verdict`, not in the browser, and
a rule id of another form is refused before any call. When a run has more steps than one answer
holds, "Show more steps" reads the ones after the last step shown. "Read the steps again" reads from
the start, which is how the steps of a run that is still running are followed. Nothing on the screen
edits, removes or runs a step again, and the customer has no such screen.

`web` gains two read routes for it, for the underwriter only, each one operation of `verdict`
passed on under the same path:

| `web` route | What it lists |
|---|---|
| `GET /api/verdict-runs/<verdict_run_id>/steps?tool=&rule_id=&after_step_no=` | One run's steps by step number. 404 `not_found` for a run that is not stored. |
| `GET /api/cases/<case_id>/agent-steps?tool=&rule_id=&after_verdict_run_id=&after_step_no=` | The steps of every run of a case, in the order they were logged. |

Every query parameter is optional. `tool` is `list_facts`, `search_rules` or `read_rule`; `rule_id`
keeps the steps that returned or read that rule. A value that is not a tool, a rule id that is not
of the form `UW-DM-002`, half a cursor, or any other parameter is 422 `validation_failed`, and
`verdict` is not asked. Both answers are bounded (`VERDICT_STEP_LIST_LIMIT`, 500) and say with
`has_more` that more steps exist; the rest is read by asking again with the last step listed as the
cursor: its step number for a run (`after_step_no`), and its run and step number for a case
(`after_verdict_run_id` with `after_step_no`, because a step number is one run's own). The screen
uses the read by run; the read by case is there for the API.

**Compare (story 3.6).** On the result of a completed case the underwriter has the button "Compare
two retrieval rows". Turned on, it shows two verdict runs of that case side by side, each made with
another retrieval row, in place of the one run; turned off, the result view is as it was. Each pane
shows what the result view shows of a run (the row, the label, the verdict with its loading, the
confidence, the system reasons, the reasons with rule and effect, "How was this reached?") and the
rules the run retrieved: the rule ids of its agent steps, each once, in the order first seen. What
differs is marked in words, not by colour: "Differs from the other run" beside a verdict or loading
that is not the other run's, and "Only in this run" beside a reason whose rule the other run does
not cite and beside a retrieved rule the other run did not retrieve. Nothing is marked unless both
runs are done, and neither run is marked as right: the browser compares ids and values the services
answered and holds no rule and no expected answer. A run that is still being made says so, and the
screen reads again every 3 s (further apart while reads fail) until both runs are final; a failed
run shows its failure.

Which two rows are shown is a setting of `web`: `WEB_COMPARE_PAIR` (default `["r4","r5"]`) and
`WEB_COMPARE_FALLBACK_PAIR` (default `["r3","r5"]`), each a JSON list of two different rows. `web`
does not know which rows are built. The screen asks for a run with each row of the default pair
that the case has no run for; when `workflow` refuses a row as not available (409
`retriever_not_available`), it does the same with the fallback pair, and when a row of that pair is
refused too it reads "Compare is not available here" with the rows. So where `r4` is not available
the pair shown is `r3` and `r5`, and where the three services list `r4` (the local start and the
deploy, since story 3.7) the default pair is shown with no change here. A run that exists, whatever its state, is shown as it is, and the screen sends one
request for a row for as long as it is open (Compare turned off and on again asks for nothing
more), because a repeat can make `workflow` schedule a run again. A run that was asked for and is
not listed after about five minutes of reading (100 reads) is said not to have appeared; "Check
again" reads again.

`web` gains two routes for it, for the underwriter only:

| `web` route | What it does |
|---|---|
| `GET /api/compare-pairs` | The two pairs, from `web`'s settings. No service is asked. |
| `POST /api/cases/<case_id>/verdict-runs` with `{"retriever_config": "r5"}` | `workflow`'s request for one more verdict run, passed on. Idempotent on the case and the row, with one exception: a run that ended without a stored result is scheduled again by a repeat. The run is an orchestration of its own on the facts already extracted and never changes the case's status. Answers the run's state (`running`, `done` with its `verdict_run_id`, or `failed` with an `error_code`); the run itself is read from `GET /api/cases/<case_id>/verdict-runs`. |

`workflow`'s refusals are passed on as they are: 404 `not_found`, 422 `validation_failed`, 409
`pages_not_terminal` for a case that is not completed, and 409 `retriever_not_available`; anything
else is 502 `upstream_unavailable`. To see it locally a second row must be switched on in
`workflow`, `verdict` and `retrieval`, and only `r3` has a local store: `r5` needs Azure AI Search,
so Compare on a real case is a check of the final Azure session
(`_bmad-output/implementation-artifacts/deferred-work.md`).

The Azure environment is down while the stories are built, so locally Azure AI Language is a stand-in:
`uv run python -m synthdata.language_standin` (started by `./tools/dev.sh` on port 5100). It speaks
the service's REST job routes, reads the original from the blob emulator and writes the redacted PDF
and a result file back. It finds email addresses, phone numbers, identity numbers and policy numbers
by their shape, and the names and addresses of the synthetic cases; it is not a recogniser. It is part
of the dev-only `synthdata` package, so no service image holds it, and `intake` refuses a plain-HTTP
Language endpoint that is not on this machine. Start it with `--mode fail` or `--mode hang` to see a
failed case. What the real service does is checked in the final Azure test session
(`_bmad-output/implementation-artifacts/deferred-work.md`).

The chat model is a stand-in too: `uv run python -m synthdata.foundry_standin` (started by
`./tools/dev.sh` on port 5101). It answers the one route the model gateway calls
(`POST /openai/v1/chat/completions`) in the shape of a chat completion. It is not a model: it tells
the page types apart by the headings on the synthetic pages and does not look at the picture, so its
runs always agree and every confidence is 1.0. Start it with `--mode disagree` (three of five runs
agree, so 0.6, which sends every page to triage), `--mode mixed` (its runs disagree only on pages it
takes for a laboratory report or an identity document, so one case shows all three routes of the
gate: with `data/cases/case-002.pdf` two pages go to extraction, one to triage and three back to the
customer), `--mode invalid` (an answer that is not the JSON asked for) or `--mode throttled`
(every call answered 429) to see the other outcomes. For extraction it reads the labelled values and
the table rows the generator prints on the medical pages, and nothing else; `--mode
quote_not_on_page` adds to every page a fact whose quote is on no page (stored as unverified), and
`--mode masked_value` adds a masked value proposed as a fact (never stored). For the verdict agent it
plays one fixed conversation, worked out from the messages it is sent: it lists the facts, searches
the manual for each reading, reads the rule whose band the fact meets, follows a reference the facts
meet, and answers with one reason per rule. Five modes show what `verdict`'s own code then does:
`--mode endless_loop` (never answers: the run stops at the step limit and the case is referred),
`--mode unseen_rule` (also cites a rule and a fact the run never saw: not stored), `--mode
wrong_effect` (debits the rules do not say: not stored, and the case is referred), `--mode
low_confidence` (confidence 0.6: referred) and `--mode invalid_answer` (a final answer that is not
the JSON asked for: the run and the case fail). For the reranker of retrieval row `r4` it answers
Cohere's rerank route (`POST /providers/cohere/v2/rerank`) and scores each document by the share of
the query's words it holds; `--mode rerank_incomplete` (one document left out) and `--mode
rerank_slow` (an answer after 30 seconds) show a search with `r4` failing, as `--mode invalid`
does. With `./tools/dev.sh` the mode is the variable `FOUNDRY_STANDIN_MODE`
(`FOUNDRY_STANDIN_MODE=mixed ./tools/dev.sh`). It is part of the same dev-only package, and
`classification`, `extraction` and `verdict` refuse a plain-HTTP model endpoint that is not on this machine.
Locally the audit trail names the model as `local-stand-in` (`CLASSIFICATION_CHAT_DEPLOYMENT`,
`EXTRACTION_CHAT_DEPLOYMENT` and `VERDICT_CHAT_DEPLOYMENT` in `dapr.yaml`); in Azure those settings
are the name of the real deployment.

**The manual's rules as chunks (story 2.2).** `retrieval` holds the underwriting manual
(`data/manual/underwriting-manual.pdf`) as one `smart` chunk per rule, in table `retrieval.chunk`. A
one-off job on the service's own package fills it, `python -m retrieval.ingest`; locally:

```sh
./tools/ingest-local.sh
```

`./tools/dev.sh` runs it for you after `./tools/migrate-local.sh`, which creates schema `retrieval`
(with the `vector` extension) and uploads the manual to the emulator's `manual` container. The job
calls no other service of ours. It reads the PDF from the container, has Document Intelligence's
layout model parse it, and cuts the parsed text into chunks of exactly one rule each: a definition is
found by the contracts' marker (`Rule <rule_id>:`), its section by the number printed with the
heading above it, and page headers, footers and page numbers are left out (by the layout model's
roles, or because they repeat on most pages). Each chunk has the rule's definition as its text, the
part of the manual it is printed in (`section_id`, for example `2.4`), the section's heading as its
`impairment`, the page of the definition, and the rules its text refers to (`reference_rule_ids`);
its `rule_ids` are only the rule it defines. `chunk_id` is `smart-<rule_id>`, so it is the same on
every run and in any other store. The chat model then writes one context line per chunk that says
where the rule sits in the manual, and the embedding model (`text-embedding-3-large`, 3,072
dimensions, named only in `RETRIEVAL_EMBEDDING_DEPLOYMENT`) embeds the context line followed by the
chunk text. There is no approximate index on the vectors: search will be exact. The chunk text also
has a stored full-text column with a GIN index (`text_search`, configuration `english`), in which
every rule id is one word (`UW-DM-001` is indexed as `uwdm001`), so story 2.3 needs no second
migration for hybrid search.

The job is safe to run again. Each chunk is stored with a hash of what its context line and vector
were made from (the rule as the chat model is shown it, the prompt and the two deployment names); a
run calls a model only for a chunk whose hash differs. The index also notes what it was last built
from, in one row of `retrieval.ingest_run`: the manual's SHA-256, the prompt's digest and the
deployment names. A second run over the same manual finds them unchanged and ends there
(`skipped=yes` in its last log line): it changes nothing, calls no model and does not send the manual
to Document Intelligence again. Before it spends anything a run reads what is stored and checks
that the schema is at the migration it ships with, and before the context lines it makes one small
embedding call. A run after which more than a tenth of the rules a chunk set defines would be
defined by none of its chunks (`RETRIEVAL_INGEST_MAX_REMOVED_SHARE`) is refused and its log names
them; set `RETRIEVAL_INGEST_ALLOW_LARGE_REMOVAL` to the chunk set it is meant for (`'["smart"]'`,
`'["fixed"]'` or both) for the one run of a manual that really lost those rules. Two runs at once cannot undo each other: the second to store finds the index changed and
writes nothing. After a change to the manual it rewrites the chunks that changed, removes
the chunks whose rule is gone, and writes everything in one transaction at its end. It fails loudly:
a rule id defined twice, a definition with no text or cut short, a page with no text, no rule at all,
a heading whose number is taken, skipped or of another section, rules of one section with two id
codes, a rule that is referred to and defined nowhere, a context
line that is empty, longer than one line or over `RETRIEVAL_CONTEXT_LINE_MAX_CHARS` (300), a vector
of another size, a missing manual, a layout analysis that fails or does not end, or a model that is
not available after its retries each end the job with exit status 1 and one log line,
`ingestion failed: code=<error code> reason=<what exactly>`, and leave the index as it was. Its logs
hold ids, counts and timings, never text of the manual. Nothing in `retrieval` reads `data/answer-key/`:
the job learns the rules from the manual alone, and only a test outside `services/` compares its
chunks with the rule table.

**The `fixed` chunk set, the baseline (story 3.2).** The same job also writes a second chunk set
from the same parsed manual, for row `r1` of the retrieval ladder: the manual's body text in reading
order, page furniture left out, in runs of 350 words of which 35 are shared with the run before
(`RETRIEVAL_FIXED_CHUNK_WORDS`, `RETRIEVAL_FIXED_CHUNK_OVERLAP_WORDS`; words, not model tokens, since
the baseline only has to be fixed and stated). The project's manual gives 150 such chunks. A `fixed`
chunk has no context line and costs no chat call: what is embedded is its own text, with the same
embedding deployment. Its `chunk_id` is the set and its position (`fixed-0001`, `fixed-0002`, ...).
Its `rule_ids` are the rules whose definition marker (`Rule <rule_id>:`) lies inside its text, which
may be none, one or several, and a rule in an overlap is in two chunks; rules it only mentions are
its `reference_rule_ids`. In its text the words of a paragraph are a space apart and two paragraphs
a line break, so a rule's own definition can be told from what is printed around it. Its section,
impairment and page are those of where it starts; text before the first section's own heading
stands under "Front matter". A definition is not kept
whole: one that a cut falls in is in two chunks, part in each. That is what the baseline is there to
show.

Which sets the job writes is `RETRIEVAL_INGEST_CHUNK_SETS` (default `["smart", "fixed"]`). The
manual is read and parsed once (a parse that failed or was stopped is not asked for again), and one
deadline covers all sets (`RETRIEVAL_INGEST_DEADLINE_SECONDS`). Each set is then a run of its own,
with its own checks, removal guard, transaction and row in `retrieval.ingest_run`, and its own last
log line (`chunk_set=smart`, `chunk_set=fixed`). A set whose run fails, in whatever way, is left as
it was and the other is run and reported all the same; the job then ends with status 1. If the
stored sets then stand on different manuals (one set took a changed manual and the other failed, or
was not among the sets of that job), the job says so in a line of its own,
`reason=chunk_sets_built_from_different_manuals` with both sets named, and ends with 1: the rows
are compared with each other. For the `fixed` set the run record holds a digest of the size and the
overlap where the `smart` set's holds the prompt's digest, and no chat deployment: a changed size or
overlap cuts the set again on the next run and leaves the `smart` set untouched. The removal guard
counts rules, not chunks, so a recut is never refused for its other number of chunks, whatever else
changed with it. The overlap is at least one word and at most half the size. The `fixed` cut checks
itself as well: the same page and heading checks, a rule defined twice, page furniture inside a
paragraph, a rule that is referred to and defined nowhere, a marker that only the joining of two
paragraphs forms, more chunks than the ids number (9,999), and a rule whose marker ended up in no
chunk.

Locally Document Intelligence is a stand-in, `uv run python -m synthdata.layout_standin` (port 5102):
it reads the PDF it is sent with PyMuPDF and answers in the shape of the service's layout result
(pages, lines, words, and paragraphs with a page and a role). It is not a layout model; start it with
`--no-roles` to leave the roles out, or with `--mode fail`, `reject`, `throttled` or `hang` to see a
failed job. The model stand-in (port 5101) answers the job as well: a context line built from the
headings it is shown, and on `POST /openai/v1/embeddings` vectors that count words, so that the same
text always gets the same vector and texts that share words are close. They know nothing of meaning.
`./tools/ingest-local.sh` uses the stand-ins `./tools/dev.sh` started, or starts its own.

**A second classifier: Document Intelligence (story 4.2).** `classification` can run two
contenders behind the one classify command: `llm` (the chat model, as above) and `doc-intelligence`,
a custom classification model of Azure AI Document Intelligence. A case is started with one of them
(`classifier_contender` in the start options, `llm` when it says nothing) and the gate routes on
that contender's result only. Both store the same fields. For `doc-intelligence` the page is sent as
a one-page PDF, which `intake` cuts from the redacted file for `classification` alone
(`GET /documents/<document_id>/pages/<page_number>/file`); the confidence is the service's own for
the document type it names, and the reason is one fixed sentence. A document type that is no page
type, or an answer without one, is a failed result (`invalid_model_output`). The contender is
available where the service is given `CLASSIFICATION_DOC_INTELLIGENCE_ENDPOINT` and
`CLASSIFICATION_DOC_INTELLIGENCE_CLASSIFIER_ID`; without both a command that names it is refused
(422 `validation_failed`) and `llm` works as before. When a page has a result of each contender, the
triage queue and the customer's prompt show the one the case was started with (the case's progress
names it, `classifier_contender`).

The classifier is trained once, by a job on the service's own package,
`python -m classification.train`, from the `classifier-training` container. The pages it is trained
on pass the same redaction as case pages first, through the pipeline itself. Locally, with
`./tools/dev.sh` running:

```sh
uv run python -m bakeoff.training_pages   # every page of data/classifier-training/ through web, redacted, into .work/classifier-training/
./tools/train-local.sh                    # that folder into the emulator's container, then the job
```

The first command uploads each of the 46 training pages as a case of an eval run, started with
`stop_after` `gate` (so it is redacted as any case page, in neither of the underwriter's lists, and
never extracted), fetches the redacted file `web` serves and writes it under its page type, with one
list, `redacted-pages.json`, that names every page with its label and its case. A page whose case
fails stops it, naming the page; start it again with `--eval-run-id <the id it logged>` and no page
is uploaded twice, but for the one whose case failed, which is uploaded again as a new case. The
list also names the MD5 of every redacted file. The job then trains only when the container holds
exactly the list and the listed pages, each once and with the listed content: a blob the list does
not name, a listed page that is missing, or a page with other content (the unredacted sources have
the same file names) refuses the training, as does a page type with fewer than five pages or a page
outside its type's folder. It ends with `training done: classifier_id=... trained=yes
pages=46 page_types=6`, or `training failed: code=... reason=...` and status 1. Run again it trains
nothing (`trained=no`). To start a case with the classifier:

```sh
curl -s -X POST http://localhost:8000/api/cases/<case_id>/start \
  -H 'X-Demo-Role: underwriter' -H 'content-type: application/json' \
  -d '{"classifier_contender": "doc-intelligence"}'
```

Locally the classifier is a stand-in, served by the Document Intelligence stand-in on port 5102
(`packages/synthdata`, `classifier_standin.py`). It learns nothing: a build counts the PDFs under
each type's folder and fails under five, and a page is then told apart by the heading the generator
prints, as the chat stand-in tells it, at a confidence of 0.97. It keeps the classifier in memory, so
run `./tools/train-local.sh` after every start of the application; until then a classify command
with `doc-intelligence` answers 502 `upstream_unavailable` and stores nothing, so the case fails
once `workflow` has sent it as often as it does. `CLASSIFIER_STANDIN_MODE=unsure ./tools/dev.sh` answers
every page at 0.55 (all to triage); `unknown_type`, `no_document`, `fail`, `throttled` and `hang`
show the failures. What the real service answers is checked in the Azure session
(`_bmad-output/implementation-artifacts/deferred-work.md`).

**Search the manual for rules (story 2.3).** `retrieval` has two reads, and neither stores anything.
With `./tools/dev.sh` running:

```sh
curl -s -X POST http://localhost:8004/searches -H 'content-type: application/json' \
  -d '{"query": "Type 2 diabetes mellitus: HbA1c from 8.0 to below 9.0 %", "retriever_config": "r3", "top_k": 5}'
curl -s http://localhost:8004/rules/UW-DM-003
```

`POST /searches` is the one search operation for every row of the retrieval ladder: a query, a
`retriever_config` and `top_k` (default 5, at most 50) in; `retriever_config`, `latency_ms` (the time
`retrieval` spent, the embedding call included) and ranked items out, each with `chunk_id`, `rule_ids`,
`rank`, `score`, `text`, `manual_page` and `impairment`. All six rows are built (the two baselines,
`r5`, `r4` and `r6` are below). The steps of `r3`, each a function of its own in
`services/retrieval/src/retrieval/`:

1. The query is embedded once, exactly as it was asked, through the model gateway on the one embedding
   deployment, the same the chunks were embedded with (`domain/search.py`, `embed_query`).
2. The vector side: exact cosine nearest-neighbour over the `smart` chunks, with no approximate index
   (`adapters/index.py`, `nearest_statement`).
3. The full-text side: PostgreSQL full-text search over the stored column `text_search`
   (`matching_statement`), in the same configuration (`english`, the constant `TEXT_SEARCH_CONFIG`) and
   with every rule id written as one word, as at ingestion, so `UW-DM-001` in a query is found whole.
   The query's words are read with `plainto_tsquery` and joined with "or", so a chunk that holds some
   of them matches: a query is a sentence about a fact, not a keyword list. Matches are ranked with
   `ts_rank`, divided by the logarithm of the chunk's length, and a chunk that defines a rule the query
   names by its id comes before the chunks that only refer to it.
4. The two ranked lists are fused with reciprocal rank fusion (`domain/fusion.py`): a chunk's score is
   the sum of `1 / (60 + rank)` over the sides that found it, so a chunk only one side found is still
   ranked, and the largest score there is, is 2/61. Each side hands the fusion its best 50 chunks
   (`RETRIEVAL_SEARCH_CANDIDATE_DEPTH`), or twice `top_k` when that is more. Both lists are read on
   one connection in one read-only, repeatable-read transaction, so they see the same index.
5. The best `top_k` become the items, ranked from 1 (`rank_items`). Equal scores come in the order of
   their `chunk_id`, on each side and in the fusion, so the same query on the same index always gives
   the same answer.

**The baseline rows `r1` and `r2` (story 3.2).** Both go through the same operation and answer the
same shape; only the row differs. `r1` is exact cosine nearest-neighbour over the `fixed` chunks,
`r2` the same over the `smart` chunks (`domain/search.py`, `vector_search`): steps 1 and 2 above and
nothing else, so no full-text search and no fusion. A query that matches a chunk only by a rare word
gets no help from that word, which is what `r3` adds. Their `score` is the cosine similarity moved
onto 0 to 1 (`(1 + cos) / 2`: 1 for the same direction, 0.5 for nothing in common), larger is
better, and chunks equally near come in the order of their `chunk_id`. A `fixed` chunk's `rule_ids`
may name several rules or none. Both rows keep the search's short budget, the check of the embedding
deployment against the set's own run record, and the read-only view of the index. So `r1` to `r2`
changes only the chunking, and `r2` to `r3` only adds full-text search.

```sh
curl -s -X POST http://localhost:8004/searches -H 'content-type: application/json' \
  -d '{"query": "Type 2 diabetes mellitus: HbA1c from 8.0 to below 9.0 %", "retriever_config": "r1"}'
curl -s 'http://localhost:8004/rules/UW-DM-003?retriever_config=r1'
```

A case may be started with any of the available rows, or with all of them
(`"retriever_configs": ["r1", "r2", "r3", "r4", "r5", "r6"]` in the start request): it gets one verdict run per row on
the same extracted facts and completes when each has its `verdict.suggested` event. The rows a case
may run with are named in three places, which a test outside `services/` holds equal without any
container (`packages/synthdata/tests/test_foundry_standin.py`): `retrieval`'s row table
(`domain/rows.py`, `available_rows`: the built rows, less `r5` and `r6` when the service has no search
endpoint, less `r4` when it has no reranker deployment and less `r6` when it has no chat deployment), `verdict`'s setting `VERDICT_AVAILABLE_RETRIEVER_CONFIGS` and `workflow`'s setting
`WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS`. Both settings default to `r1`, `r2` and `r3`; `dapr.yaml`
and the deploy (the one list of `infra/demo/app/terraform.tfvars`) add `r4`, `r5` and `r6`, because there
`retrieval` is given the reranker deployment, the chat deployment and a search service. A verdict run on `r1` reads its rules from the `fixed`
set. A reason's effect is then read from that rule's own definition inside the chunk, from its
marker to the end of its paragraph; a definition the chunk cuts off before its rating bears out no
debit and no decline, so that reason is dropped and the run refers. That is the baseline's honest
weakness and nothing works around it.

**Row `r5` on Azure AI Search (story 3.3).** The same chunks in another store, so that the comparison
can say what a managed search service does with them. The store is the one thing that differs:

- *The index.* The ingestion job's last step (`domain/index_load.py`), after pgvector is written,
  loads the index `manual-smart` (`RETRIEVAL_SEARCH_SERVICE_INDEX_NAME`) from the `smart` chunk
  records the chunk table holds: one document per chunk with the same `chunk_id`, text, context line,
  rule ids, references, section, impairment, manual page and vector. Nothing is cut again and no model
  is asked. It creates the index if the service has none (`adapters/search_index.py`,
  `index_definition`: a 3,072-dimension vector field searched exhaustively, `exhaustiveKnn` with
  cosine, so exact like pgvector, and one semantic configuration), uploads the documents that are new
  or changed (each carries a hash of its fields), deletes those whose chunk is gone, and then compares
  what the index says it holds, its count and its ids, with pgvector. A difference fails the job
  (`index load failed: code=stage_failed reason=search_index_differs`), as does a service that gives
  no answer (`code=upstream_unavailable reason=search_...`); either way pgvector stays as the runs
  before it wrote it, and the next run of the job brings the index up to date. A run that changes
  nothing uploads nothing and still compares. Its own log line is `index load done: documents=...`.
- *The search* (`domain/search.py`, `ai_search_hybrid`). The query is embedded once, as for the other
  rows, and sent to the service as one hybrid query: the text (with every operator of the query
  syntax escaped) and the vector (`exhaustive`), fused by the service and ordered by its semantic
  ranker. The items are the service's documents in the service's order, in the common shape. `score`
  is the ranker's score, which runs from 0 to 4 and is no probability, divided by 4: so 0 to 1 and
  larger is better, comparable within the row only. A request the ranker could not serve fails
  (`semanticErrorHandling: fail`): there is no partial answer, and a service that is down or slower
  than the search's deadline is `upstream_unavailable` (502). The search keeps the guard on the
  embedding deployment, against pgvector's run record and against each answered document's own, and
  leaves out (and counts in its log line, `left_out=`) a document whose id pgvector does not hold,
  or holds with another content hash, which is an index behind the chunk table. The vector side is
  asked for as many candidates as `r3` hands its fusion (50), and the largest raw ranker score of a
  search is in its log line and on its span. A rule read for `r5` answers the `smart` chunk from
  pgvector.
- *Availability.* The service is reached with `retrieval`'s identity, never a key
  (`RETRIEVAL_SEARCH_SERVICE_ENDPOINT`, `..._ENTRA_AUTH`, `..._API_VERSION`, default `2024-07-01`).
  With no endpoint set, `r5` is refused with `retriever_not_available` (409), the job loads no index
  and the other rows work as before.
- *The client* is REST over `httpx2`, like the Document Intelligence client, not the
  `azure-search-documents` SDK: the spine pins that pre-release for row `r6` alone, and `r5` needs
  five plain calls of the stable API.

Locally Azure AI Search is a stand-in, `uv run python -m synthdata.search_standin` (port 5103,
started by `./tools/dev.sh`): the REST routes the client calls, with its indexes and its knowledge
base in memory, so the job loads it again at every start. It answers a hybrid query from the vectors and from shared words,
fused by rank and then ordered by a made-up "reranker score" from 0 to 4; it is not a search engine
and not a language model. `SEARCH_STANDIN_MODE=unavailable ./tools/dev.sh` and `=slow` show a search
with `r5` or `r6` failing. It is part of the dev-only package: no service image holds it, and `retrieval`
refuses a plain-HTTP search endpoint that is not on loopback.

```sh
curl -s -X POST http://localhost:8004/searches -H 'content-type: application/json' \
  -d '{"query": "Type 2 diabetes mellitus: HbA1c from 8.0 to below 9.0 %", "retriever_config": "r5"}'
```

**Row `r4` with a reranker (story 3.7).** Row `r3` and one more step, so that the comparison can say
what reranking alone is worth on pgvector. The order is the one thing that differs:

- *The candidates* are `r3`'s, found by the same function (`domain/search.py`, `fused_candidates`):
  the same embedding call, the same two searches to the same depth, the same fusion. `r3` itself
  answers as before.
- *The reranker* is Cohere Rerank on Foundry: the model `Cohere-rerank-v4.0-fast` (the owner's choice
  of 2026-10-08, when Azure's catalogue listed it for West US 3), a deployment of its own on the one
  Foundry account (`infra/demo/foundation`: exact version, no auto-upgrade, Global Standard, the
  default content filter). Its name reaches `retrieval` as `RETRIEVAL_RERANK_DEPLOYMENT`, and it is
  reached with the service identity, without a key. A search makes one call to it, through the same
  model gateway and under its cap on concurrent calls: Cohere's rerank API at
  `/providers/cohere/v2/rerank` below the account's endpoint, or at the whole address
  `RETRIEVAL_RERANK_URL` names (a query string included), signed in with a token of the scope
  `RETRIEVAL_RERANK_TOKEN_SCOPE`; both are settings, and variables of `infra/demo/app`, because
  neither is proven in Azure yet. The request holds the deployment as the model, the query, and the 20 best fused candidates as documents
  (`RETRIEVAL_SEARCH_RERANK_DEPTH`; a search that asks for more items has that many scored), each
  the candidate's impairment and its text. Nothing else is sent, so every document is scored. The
  answer holds a relevance score from 0 to 1 for each document, by its place in the request. The
  items are the candidates by that score, largest first, and candidates of equal score in the fused
  order; `score` is the reranker's score, comparable within the row only. There is one reranker and
  no switch between two: the LLM reranker the row was first built with is gone (its prompt, its
  chat call and its settings), and `r4` no longer needs the chat deployment.
- *No answer is no answer* (`domain/rerank.py`). An answer that is not the object expected, leaves a
  candidate out, names a place it was not given or one twice, or holds a score that is no number
  from 0 to 1, fails the search with `model_unavailable` (503), with the reason in the log
  (`rerank answer invalid: reason=...`). A call the service refuses (a 4xx) is
  `upstream_unavailable` (502). The fused order is never answered in its place: that would be
  `r3`'s answer under `r4`'s name.
- *Its own budget.* The call has 15 seconds (`RETRIEVAL_SEARCH_RERANK_TIMEOUT_SECONDS`) and a search
  with `r4` 20 in all (`RETRIEVAL_SEARCH_RERANK_DEADLINE_SECONDS`), not the other rows' 8. Past that
  the search is `model_unavailable` and its log line says `waited_for=reranker` and how long
  (`rerank_ms=`). A rerank call that timed out is not sent again: a second one could not be answered
  inside the deadline. The settings are set in `dapr.yaml` and are variables of `infra/demo/app`.
  Rerank calls leave one slot of the gateway's cap on concurrent calls free
  (`RETRIEVAL_MODEL_MAX_CONCURRENT_CALLS`), so the other rows' query embeddings do not wait behind
  them. Every caller of a search waits longer: `verdict` 25 seconds for one tool call
  (`VERDICT_UPSTREAM_TIMEOUT_SECONDS`), `web` 30 and the bake-off runner 30; a test holds that. The
  search's log line and span carry the number of candidates scored and the reranker's time
  (`reranked=`, `rerank_ms=`), never the query or a chunk's text. Cohere bills by search unit, not
  by token: the gateway's line for the call says `search_units=`, as the answer states them.
- *Availability.* A service that is told of no reranker deployment refuses `r4` with
  `retriever_not_available` (409), and the other rows work as before, `r6` included.
- *If Cohere Rerank cannot be deployed* in the Azure session, the environment runs without `r4`:
  the `rerank` entry is left out of the foundation stack's `model_deployments` and `r4` out of
  `available_retriever_configs` of the `app` stack (the steps are in `infra/bootstrap/README.md`,
  section 2; locally the same is `RETRIEVAL_RERANK_DEPLOYMENT` and the two lists of rows in
  `dapr.yaml`). `retrieval` then refuses the row, Compare shows its fallback pair, `r3` and `r5`,
  and the scoreboard shows `r4` as "not measured". There is no other reranker to fall back to.
- *Not proven in Azure.* The address and the shapes of the call are written from documentation and
  proven against the local stand-in only; the checks for the Azure session are in
  `_bmad-output/implementation-artifacts/deferred-work.md`.

```sh
curl -s -X POST http://localhost:8004/searches -H 'content-type: application/json' \
  -d '{"query": "Type 2 diabetes mellitus: HbA1c from 8.0 to below 9.0 %", "retriever_config": "r4"}'
```

Locally the reranker is the model stand-in, which counts shared words: `r4`'s figures on a local
scoreboard prove the plumbing and say nothing of reranking.

**Row `r6` with agentic retrieval (story 3.8).** The managed pipeline of Azure AI Search itself, over
the index `r5` already uses, so that the comparison can say what the service's own query planning
does against the same need.

- *The knowledge base.* A knowledge source names the index `manual-smart`, and a knowledge base names
  that source and the chat deployment as its planning model, with no answer synthesis
  (`adapters/knowledge_base.py`). The ingestion job creates both after the index is loaded and
  checked, each only where the service has none of that name: `knowledge base done: source=...
  base=... source_created=yes base_created=yes`, and `no` twice on every later run. One that is
  there is compared with what would be created (the source's index; the base's source and its
  planning model): a difference fails the step (`reason=search_knowledge_source_differs`,
  `search_knowledge_base_differs`), so that a leftover from another index name is never asked
  unnoticed. A failure has its own line (`knowledge base failed: code=... reason=search_...`), ends
  the job with 1 and leaves pgvector and the index as they are. Nothing is chunked or embedded again: the same documents and
  the same vectors as `r5`.
- *The vectorizer.* The index definition now names the embedding deployment as the vectorizer of
  its vector field, so that the search service can embed the queries it plans; `r5` never uses it.
  An index is created once and not changed afterwards: over one that names no vectorizer the
  knowledge base is not made (`reason=search_index_without_vectorizer`), and the index must be
  made again under a new name, with new names for the knowledge source and base (none exists in
  Azure).
- *The search* sends the query exactly as it was asked in one retrieve request and takes the
  references that return: the documents the service found, in its order. The service's model plans
  queries of its own, the service runs them and reranks. No answer is asked for (`outputMode:
  extractiveData`) and a `response` in the answer is never read. `retrieval` embeds nothing for
  this row.
- *The items.* Of two references to one chunk (two of the service's queries found it) the first
  is kept. A reference whose chunk pgvector does not hold, or holds with another content hash,
  is left out and counted, as for `r5`. The search's log line says which score rule the answer
  used (`score_rule=reranker` or `rank`). `rank` is the service's order. `score`, by one rule for a
  whole answer: where every reference kept carries the semantic ranker's score, that score divided
  by 4, as for `r5`; where one of them carries none, one over the item's rank for all of them (the
  service says a reference may come without a score).
- *Its own budget.* The retrieve request has 15 seconds (`RETRIEVAL_SEARCH_AGENTIC_TIMEOUT_SECONDS`)
  and is never sent again, whatever became of it: the planning is paid for every time. A search
  with `r6` has 20 in all (`RETRIEVAL_SEARCH_AGENTIC_DEADLINE_SECONDS`), under every caller's wait,
  as for `r4`. The service down, slow, or answering something that is not a whole list of
  references of the index: `upstream_unavailable` (502) and no partial answer. A service that
  holds no such knowledge base (the job has not run) answers `retriever_not_available` (409), as
  a chunk set that was never ingested does.
- *Only these calls use the preview REST version* (`RETRIEVAL_SEARCH_AGENTIC_API_VERSION`, default
  `2026-08-01-preview`); the index and `r5` stay on the stable one. The calls are REST over `httpx2`
  like the index's. The pre-release SDK the spine pins for this row, `azure-search-documents`
  12.1.0b2, installs, and its models are where the request shapes were read from; but its async
  client sends through azure-core's own `aiohttp` transport, takes neither the `httpx2` transport
  the tests stand a service in with nor a plain-HTTP loopback endpoint with its bearer policy, and
  brings its own retries and HTTP logging. So it is not a dependency.
- *Who calls the model.* The search service does, with its own identity: the `app` stack gives that
  identity Cognitive Services User on the Foundry account, and no key is named anywhere.
- *Availability.* `r6` needs the search endpoint and the chat deployment
  (`RETRIEVAL_CHAT_DEPLOYMENT`); without either it is refused with `retriever_not_available` (409)
  and the other rows work as before.
- *A verdict run on `r6`* does not run the agent's loop: a model that could search again on top of
  the service's own planning would be a second agent, and the row would measure both. `verdict`
  lists the facts and makes one search per fact itself, with the query the contracts' query builder
  makes from the fact's statement, each logged as a step like a tool call; then the model is asked
  once, with another prompt (`prompts/compose_verdict.md`) and no tool, to compose its proposal
  from the facts and the rules those searches returned. Every check on a proposal is the same as on
  the other rows; a rule counts as read when a search of the run returned its chunk, and an effect
  is checked against that text. A case with more facts than the run has steps left is referred as
  at the step limit as soon as the facts are listed, before any search is paid for. A search that
  fails ends the run as on every row, and a search the toolbox refuses fails it (`stage_failed`):
  nothing is composed over a search that was not made.

Locally the knowledge base is the search stand-in's: its "plan" is the query and each of its parts
between commas, colons and semicolons, each run as a hybrid query over the index. It is no model:
`r6`'s figures on a local scoreboard prove the plumbing only.

```sh
curl -s -X POST http://localhost:8004/searches -H 'content-type: application/json' \
  -d '{"query": "Type 2 diabetes mellitus: HbA1c from 8.0 to below 9.0 %", "retriever_config": "r6"}'
```

On rows `r1` to `r5` there is no query rewriting by a model, and no row has a cache. A
`retriever_config` that is no row at all is `validation_failed` (422), as are a blank query and a
`top_k` outside 1 to 50 or a query over 2,000 characters. A search has a short budget of its own,
apart from the ingestion job's model settings: 3 seconds for the query's embedding call
(`RETRIEVAL_SEARCH_EMBEDDING_TIMEOUT_SECONDS`), one retry (`RETRIEVAL_SEARCH_EMBEDDING_MAX_RETRIES`)
and 8 seconds for the whole search (`RETRIEVAL_SEARCH_DEADLINE_SECONDS`). When the embedding model
fails after its retries, or the deadline passes while it is awaited, a search is `model_unavailable`
(503); when the database cannot be read, ends a statement for its time limit, or holds the search
past its deadline, it is `upstream_unavailable` (502). There is never a partial result. A search is
also refused (`model_unavailable`) when the last ingest run recorded another embedding deployment
than the service is set to (`RETRIEVAL_EMBEDDING_DEPLOYMENT`): vectors of two models are not
comparable. The record is read with every search, in the search's own transaction, so a new
ingestion is seen at once. `GET /rules/<rule_id>` answers the `smart` chunk
that defines the rule: its text, manual page, impairment, chunk id and chunk set, and the rules its
text refers to (`reference_rule_ids`). An unknown rule is `not_found` (404) and a malformed id 422.
With `?retriever_config=`, every row on the `smart` set answers the same chunk, and `r1` answers
from the `fixed` set: the chunk that holds the rule's definition marker (of two that hold it, the
later one, in which the definition goes on), with the rules that rule's own definition refers to
(from its marker to the end of its paragraph, also when they are defined in the same chunk, and
never what a neighbouring rule or a worked example mentions); a rule no `fixed` chunk defines is
404. A search or a rule read on a row whose chunk set has no run record, because the job never
wrote it, is `retriever_not_available` (409), never an empty answer. Logs name the row, counts and timings, never
the query. The service needs `RETRIEVAL_MODEL_ENDPOINT` and `RETRIEVAL_EMBEDDING_DEPLOYMENT` to search
(`dapr.yaml` names the stand-in); without them it says once at start-up that searches are off, still
answers its probes and rule reads, and tells a search that it is not configured to search. A test
outside `services/` searches the ingested manual for every rule of the rule table by its impairment
and threshold and prints how many came first, in the top 3 and in the top 5
(`uv run pytest packages/synthdata/tests/test_manual_search_end_to_end.py -s`). The stand-in's vectors
only count shared words, so those numbers say little about the real embedding model.

The services never run migrations when they start, here or in Azure, and `intake`, `workflow`,
`classification`, `extraction`, `retrieval` and `verdict` each report "not ready" (`/ready`) until their schema is at the
newest migration they ship with. Locally, one script stands in
for the pipeline's migration step. `./tools/dev.sh` runs it for you; run it yourself after pulling a
change that adds a migration:

```sh
./tools/migrate-local.sh
```

It starts the two containers if they are not running, applies `intake`'s migrations to the local
database (`uv run alembic -c services/intake/alembic.ini upgrade head`, pointed at `localhost`) and
creates the blob containers `originals` and `cases` in the emulator. For `workflow` it creates the
database role `workflow` and applies that service's migrations
(`uv run alembic -c services/workflow/alembic.ini upgrade head`, with
`WORKFLOW_DATABASE_SERVICE_ROLE=workflow`), which grant the role its rights. For `classification` it
applies that service's migrations (`uv run alembic -c services/classification/alembic.ini upgrade head`),
and for `extraction` likewise (`uv run alembic -c services/extraction/alembic.ini upgrade head`).
For `retrieval` it applies that service's migrations
(`uv run alembic -c services/retrieval/alembic.ini upgrade head`) and uploads the manual to the
emulator's `manual` container (`uv run python -m retrieval.local_setup data/manual/underwriting-manual.pdf`).
For `verdict` it creates the database role `verdict` (`uv run python -m verdict.local_setup`) and
applies that service's migrations (`uv run alembic -c services/verdict/alembic.ini upgrade head`, with
`VERDICT_DATABASE_SERVICE_ROLE=verdict`), which grant the role its rights, as for `workflow`.
It can be run again safely.

`workflow` runs as that role, not as the database's own user, so the rule that the audit trail is
append-only holds on your machine as it does in Azure: the role may read `workflow.audit_event` and
add to it, and the database refuses it an `UPDATE` or a `DELETE`. `verdict` runs as its own role
in the same way: it may read `verdict.agent_step`, the agent's step log, and add to it, and nothing
more.

| What | Where |
| --- | --- |
| The app and its API (`web`) | <http://localhost:8000/> |
| `web`'s Dapr sidecar | `http://localhost:3500` |
| `intake` (`/health`, `/ready`, `POST /cases`, `POST /cases/<case_id>/redaction`, `GET /cases/<case_id>/pages`, `GET /pages/<page_id>/text`, `/boxes` and `/thumbnail`, `GET /documents/<document_id>/file`), and its Dapr sidecar | `http://localhost:8001`, `http://localhost:3501` |
| Stand-in for Azure AI Language (this machine only) | `http://localhost:5100` |
| `workflow` (`/health`, `/ready`, `POST /cases/<case_id>/start`, `GET /cases`, `GET /cases/<case_id>/progress`, `GET /cases/<case_id>/audit`, `POST /cases/<case_id>/pages/<page_id>/decisions`, `GET /pages?status=<status>`), and its Dapr sidecar | `http://localhost:8002`, `http://localhost:3502` |
| `classification` (`/health`, `/ready`, `POST /classifications`, `GET /cases/<case_id>/classifications`), and its Dapr sidecar | `http://localhost:8003`, `http://localhost:3503` |
| Stand-in for the Foundry chat and embedding deployments (this machine only) | `http://localhost:5101` |
| `retrieval` (`/health`, `/ready`, `POST /searches`, `GET /rules/<rule_id>`), and its Dapr sidecar | `http://localhost:8004`, `http://localhost:3504` |
| `extraction` (`/health`, `/ready`, `POST /fact-sets`, `GET /cases/<case_id>/facts`), and its Dapr sidecar | `http://localhost:8005`, `http://localhost:3505` |
| `verdict` (`/health`, `/ready`, `POST /verdict-runs`, `GET /cases/<case_id>/verdict-runs`, `GET /verdict-runs/<verdict_run_id>/steps?tool=&rule_id=&after_step_no=`, `GET /cases/<case_id>/agent-steps?tool=&rule_id=&after_verdict_run_id=&after_step_no=`), and its Dapr sidecar | `http://localhost:8006`, `http://localhost:3506` |
| Stand-in for Document Intelligence's layout model (this machine only) | `http://localhost:5102` |
| Stand-in for Azure AI Search (this machine only; its index is kept in memory) | `http://localhost:5103` |
| PostgreSQL (database and user `aiuw`, and the roles `workflow` and `verdict`; no password, this machine only) | `localhost:5432` |
| Azurite blob emulator (its built-in account `devstoreaccount1`, this machine only) | `localhost:10000` |
| Durable Task Scheduler emulator (task hubs `default` and, for tests, `aiuw-test`), and its dashboard | `localhost:8080`, <http://localhost:8082/> |

Stop with Ctrl+C, then `docker compose down` (add `-v` to delete the local database and blobs).

Locally `intake` reaches the emulator with `INTAKE_BLOB_CONNECTION_STRING=UseDevelopmentStorage=true`
(set in `dapr.yaml`), which names the emulator's built-in account and holds no secret. In Azure that
variable is never set: the service signs in to Blob Storage and PostgreSQL with its managed identity,
and to Azure AI Language as well (`INTAKE_LANGUAGE_ENDPOINT` is the account's endpoint there, with
`INTAKE_LANGUAGE_ENTRA_AUTH=true`; there is no key). Language reads the original and writes the
redacted PDF with its own identity. `workflow` commands `intake` through its own Dapr sidecar
(`WORKFLOW_DAPR_HTTP_PORT`).
`classification` reads pages from `intake` through its own sidecar (`CLASSIFICATION_DAPR_HTTP_PORT`);
in Azure it signs in to PostgreSQL and to the chat deployment with its managed identity
(`CLASSIFICATION_MODEL_ENDPOINT` is the Foundry account's endpoint there, with
`CLASSIFICATION_MODEL_ENTRA_AUTH=true`; there is no key). `extraction` reads page text from `intake`
through its own sidecar (`EXTRACTION_DAPR_HTTP_PORT`) and signs in the same way
(`EXTRACTION_MODEL_ENDPOINT`, `EXTRACTION_MODEL_ENTRA_AUTH=true`). `verdict` reads a case's facts
from `extraction` and the manual's rules from `retrieval` through its own sidecar
(`VERDICT_DAPR_HTTP_PORT`) and signs in the same way (`VERDICT_MODEL_ENDPOINT`,
`VERDICT_MODEL_ENTRA_AUTH=true`). A case's suggested verdicts are on the underwriter's result view
(story 2.7), and at `http://localhost:8006/cases/<case_id>/verdict-runs`; the steps of one run are
on the audit trail and the result view (story 2.8), and at
`http://localhost:8006/verdict-runs/<verdict_run_id>/steps`, and a case's steps across its runs at
`http://localhost:8006/cases/<case_id>/agent-steps`; both take `?tool=` and `?rule_id=` to narrow
them and a cursor for the steps beyond one answer.
`workflow` reaches the scheduler emulator without a credential; in Azure it signs in to the Durable Task
Scheduler and PostgreSQL with its managed identity. The emulator keeps its state in memory, so
orchestrations are gone after `docker compose stop`, while case status and the audit trail stay in
PostgreSQL; starting such a case again gives it a new orchestration.

To work on the SPA with hot reload, keep the above running and start `npm --prefix services/web/spa run dev`;
its dev server passes `/api` calls on to port 8000.

To run the container images instead (`web` alone serves the app; an upload needs both services and
their sidecars, which is what `./tools/dev.sh` is for):

```sh
docker build -f services/web/Dockerfile -t aiuw-web:dev .
docker run --rm -p 8000:8000 aiuw-web:dev

docker build -f services/intake/Dockerfile -t aiuw-intake:dev .
docker build -f services/workflow/Dockerfile -t aiuw-workflow:dev .
docker build -f services/classification/Dockerfile -t aiuw-classification:dev .   # the service, and the job: python -m classification.train
docker build -f services/extraction/Dockerfile -t aiuw-extraction:dev .
docker build -f services/verdict/Dockerfile -t aiuw-verdict:dev .
docker build -f services/retrieval/Dockerfile -t aiuw-retrieval:dev .   # the service, and the job: python -m retrieval.ingest
```

### The bake-off

The retrieval rows are scored by one runner (`evals/`, story 3.4). With the local start running
(`./tools/dev.sh`), from the repository root:

```sh
uv run python -m bakeoff
```

It talks to `web` only, with a demo role on every call, and measures two things apart. **Rule
recall**: for every expected fact of the answer key that meets a rule, one search per row with the
query the contracts' query builder makes from the fact's statement; a hit is an expected rule among
the top 5. **Verdict accuracy**: every case is uploaded once and started once with every row that
answers and one `eval_run_id`; the runner answers the human waits from the answer key's page labels
and compares each row's suggested verdict (and loading) with the expected one. In the same run it
reads every page's text and fails (exit status 1) if a planted identifier, or a part of a planted
name, is left in it; it also counts the quotes of the expected facts that are no longer found in
the stored text of their page (over-redaction: a figure, which fails nothing). It writes
`retrieval.json` (one line per ladder row with store, chunk set, method, rule recall, verdict
accuracy, latency, the counts behind them, the stated cost and effort from
`evals/static-metrics.yaml`, and the winner) and `redaction.json`. Both shapes are contract models
(`RetrievalScoreboard`, `RedactionScoreboard`).

A row that answers "not available" is recorded as not measured; a case that fails or does not finish
counts as wrong for every row and is listed. Figures from a local run are **not results**: the
stand-in's vectors count shared words and its agent is scripted, so they prove the plumbing only.
Such a run writes under `.work/scoreboards/` and its files say `"stand_ins": true`. Only a run
against the deployed environment (`--deployed`) writes `data/scoreboards/`. For this, `web` has two
routes for the underwriter that no screen uses yet: `POST /api/searches` (the search operation of
`retrieval`, passed through) and `GET /api/pages/<page_id>/text` (the redacted page's stored text).
Cases the runner starts are in neither the triage queue nor the case list.

The underwriter's **Scoreboard** screen shows the result: one line per row `r1` to `r6` with its
store, chunk set and method, rule recall and verdict accuracy as a percentage with the counts
behind them (`38 of 39`), the verdict runs that failed or are missing (counted apart, so that a
failure of the system is not read as a wrong verdict), latency (median and 95th percentile), and
the stated cost and effort with their source. The winner is the row the file names, marked with
the word "Winner". A row that was not measured says so and shows no number. Above the table the
screen says when and against which address the run was made and, for a run with stand-ins, that
the figures are not results; below it, how many cases were not scored, how many searches failed,
and one line of the redaction check, which ends with how many of the expected-fact quotes are no
longer found in their page's text. The scores are files: `web` reads `retrieval.json` and
`redaction.json` from one folder (`WEB_SCOREBOARDS_DIR`), checks each against its contract model
and answers it as it is on `GET /api/scoreboards/retrieval` and `GET /api/scoreboards/redaction`,
for the underwriter only. Until the file is there the screen says that the bake-off has not been
run (404 `not_found`); a file that does not fit its model is an error (500), never half a table.
No route takes a score. The folder is `data/scoreboards/` in a checkout and the image's own copy
of it in Azure (the only folder of `data/` in any image); `dapr.yaml` points the local `web` at
`.work/scoreboards/`, so after `./tools/dev.sh` and a local `uv run python -m bakeoff` the screen
shows the local stand-in figures. The screen reads the files once: use "Read again" after a new
run. A file written before story 3.5 has no `failed_runs` and is refused: run the bake-off again.
So is a `redaction.json` written before the count of expected-fact quotes was built (2026-10-08).

**The classifier bake-off** (story 4.3) is a second mode of the same command, and a run of its own:

```sh
uv run python -m bakeoff --bake-off classification
```

It uploads each file of the scored page set (`data/answer-key/page-set.json`: the 22 case files, 94
pages) once per classifier contender, started with that contender, the run's `eval_run_id` and
`stop_after` `gate`, so the pages are redacted, classified and routed and nothing else runs: no
extraction, no verdict, no page waiting for a person. Then it reads each case's progress and stored
classifications through `web` and compares every page with its expected label. Per contender, over
every page: **accuracy** (the stored `is_medical` is the expected one), **calibration** (of the
pages scored 0.90 or more, the share labelled correctly), **queue rate** (the share the gate sent to
triage) and the stated **cost per page** (`evals/static-metrics.yaml`). The winner is the more
accurate contender among those calibrated at 0.90 or more over at least 10 pages scored that high,
then the one with the lower queue rate; a contender with fewer such pages cannot win, and there is
no winner when no contender qualifies. A page without a result is wrong and listed; a file whose
case fails or does not finish counts all its pages as wrong and is listed; a contender whose
commands `classification` refuses (`doc-intelligence` without a classifier) is recorded as not
measured, with the case that showed it, and the run goes on. Every stored `reason` is checked for
the planted identifiers of its case, and one that holds any fails the command like a leak (exit
status 1); a file whose reasons could not be read is listed and makes the run incomplete (3), as
does a run that measured no contender, which writes no file. The run writes `classification.json`
(`ClassificationScoreboard`), under `.work/scoreboards/` locally and in `data/scoreboards/` only
with `--deployed`. Local figures are not results here either: both stand-ins read the generator's
headings.

`web` serves that file as the other two, on `GET /api/scoreboards/classification`, and the
Scoreboard screen shows it in a second table under the retrieval one: a line per classifier with
accuracy, calibration and queue rate as percentages with their counts, the pages not classified,
the stated cost per page, the winner marked with the word "Winner", and "Not measured" for a
contender without figures. Under the table, each line names the classifier it is about: one that
could not be run, files that failed, reasons that leaked or were not read. Each table stands alone:
where one file is missing, that table says that its bake-off (named) has not been run and the other
is shown all the same.

### Contract types

The SPA's TypeScript types for API payloads are generated from `packages/contracts`, never written by
hand. After changing a contract model, regenerate both files and commit them:

```sh
uv run python -m contracts.export_schema services/web/spa/src/api/contracts.schema.json
npm --prefix services/web/spa run contracts:generate
```

Two checks fail until that is done: a test in `uv run pytest` compares the schema file with the models,
and `npm --prefix services/web/spa run contracts:check` compares the TypeScript file with the schema.

### Deploy

The demo environment is two Terraform stacks, applied in order: `infra/demo/foundation` (see
`infra/bootstrap/README.md`) and `infra/demo/app`, which so far holds four Container Apps: `web`, the
only one reachable from the internet, and `intake`, `workflow` and `classification`, with internal
ingress only. `workflow` is held at one replica and holds Durable Task Data Contributor on the task
hub. For redaction `intake` holds Cognitive Services User on Azure AI Language, and Language's own
identity may read the `originals` container and write the `cases` container. `classification` is held
at one replica and holds Foundry User on the Foundry project, for the chat deployment.

Story 2.2 adds a fifth Container App, `retrieval` (internal ingress, one replica), and a Container
Apps job, `caj-aiuw-demo-wus3-ingest`, on the same image and identity with the command
`python -m retrieval.ingest`. That identity holds Foundry User on the Foundry project, Cognitive
Services User on Document Intelligence and Storage Blob Data Reader on the `manual` container;
Document Intelligence is sent the manual's bytes and holds no role on storage. The deploy builds the `retrieval`
image, fails unless `retrieval`'s latest revision runs it, and says whether it is ready. It does not
start the job: the manual is uploaded, the database role and migrations are done, and the job is
started by an operator (`infra/bootstrap/README.md`, section 7).

Story 2.4 adds a sixth Container App, `extraction` (internal ingress, one replica, so that its own
cap on model calls is the cap for the environment). Its identity holds Foundry User on the Foundry
project, for the chat deployment it shares with `classification`, and no role on storage: it reads
pages from `intake` only. The deploy builds the `extraction` image, fails unless `extraction`'s
latest revision runs it, and says whether it is ready. Its database role and migration are a manual
step (`infra/bootstrap/README.md`, section 8); until it is done, a case fails once a page reaches
extraction.

Stories 2.5 and 2.6 add a seventh Container App, `verdict` (internal ingress, one replica, so that
its own cap on model calls is the cap for the environment). Its identity holds Foundry User on the
Foundry project, for the chat deployment it shares with `classification` and `extraction`, and no
role on storage: it reads facts from `extraction` and rules from `retrieval` only. The `app` stack
sets the cap on its model calls, the most tool calls one run may make and the confidence under which
a run refers its case (`verdict_model_max_concurrent_calls`, `verdict_step_limit` and
`verdict_confidence_floor` in `infra/demo/app/terraform.tfvars`). The deploy builds the `verdict`
image, fails unless `verdict`'s latest revision runs it, and says whether it is ready. Its database
role and migration are a manual step (`infra/bootstrap/README.md`, section 9); until it is done, a
case fails once all its pages are final and its verdict runs are commanded.

The `deploy` workflow (`.github/workflows/deploy.yml`) is started by hand on `main` and deploys only
the commit `main` is at. It builds the `web`, `intake`, `workflow` and `classification` images in the
registry, plans `app`, refuses a plan that destroys or replaces a resource, applies it, waits until the
new `web` revision is the one serving, fails unless the latest revisions of `intake`, `workflow` and
`classification` run the same commit's image, checks `/api/health` and the SPA's page, and ends by
saying whether `intake`, `workflow` and `classification` are ready. It does not run database migrations
yet: the database roles and migrations of `intake`, `workflow` and `classification` are a manual step
(`infra/bootstrap/README.md`, sections 4, 5 and 6). Until it is done for `intake`, that service stays
"not ready" and an upload is answered with 502; until it is done for `workflow`, an uploaded case is
shown as received but not started; until it is done for `classification`, a started case fails once
its document is redacted. It needs:

| What | Set by |
| --- | --- |
| Repository variables `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID` (ids for OIDC sign-in, not secrets) | `infra/bootstrap/state-backend.sh` |
| GitHub Environment `demo`, deploying only from `main` | `infra/bootstrap/state-backend.sh` |
| The deployment identity `id-aiuw-demo-wus3-deploy`, trusted for that environment | `infra/bootstrap/state-backend.sh` |
| The `foundation` stack applied: the workflow reads the registry and the rest from its state | `infra/bootstrap/README.md`, section 2 |

No secret is stored in GitHub. Pull requests that touch `infra/` get a format check and `validate` of
`app`; it is not planned there, because its plan needs the `foundation` state.

### Still to do

The Org Kit's Jira sync needs this project's Jira site and project key. When they are known, run
**"setup Org Kit"** in Claude Code with those values. It adds the `bmad-create-epics-and-stories`
override (Jira sync, story structure, token budgets) and Scrooge's personal ledger hooks.
