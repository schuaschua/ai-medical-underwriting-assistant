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

### Install and check

Install uv 0.11.8 and Node.js 24.21.0, then from the repository root:

```sh
uv sync
uv run ruff format --check . && uv run ruff check .
uv run mypy packages services
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
Azure AI Language, one for the Foundry model deployments and one for Document Intelligence's layout
model (see below), ingests the underwriting manual, and runs the `web`, `intake`, `workflow`,
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
`GET /cases/<case_id>/facts` on the service lists the stored facts in page order; no screen shows
them yet.

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
the JSON asked for: the run and the case fail). With `./tools/dev.sh` the mode is the variable `FOUNDRY_STANDIN_MODE`
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
embedding call. A run that would remove more than a tenth of the stored chunks
(`RETRIEVAL_INGEST_MAX_REMOVED_SHARE`) is refused and its log names them; set
`RETRIEVAL_INGEST_ALLOW_LARGE_REMOVAL=true` for the one run of a manual that really lost those
rules. Two runs at once cannot undo each other: the second to store finds the index changed and
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

Locally Document Intelligence is a stand-in, `uv run python -m synthdata.layout_standin` (port 5102):
it reads the PDF it is sent with PyMuPDF and answers in the shape of the service's layout result
(pages, lines, words, and paragraphs with a page and a role). It is not a layout model; start it with
`--no-roles` to leave the roles out, or with `--mode fail`, `reject`, `throttled` or `hang` to see a
failed job. The model stand-in (port 5101) answers the job as well: a context line built from the
headings it is shown, and on `POST /openai/v1/embeddings` vectors that count words, so that the same
text always gets the same vector and texts that share words are close. They know nothing of meaning.
`./tools/ingest-local.sh` uses the stand-ins `./tools/dev.sh` started, or starts its own.

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
`rank`, `score`, `text`, `manual_page` and `impairment`. Only row `r3` is built. Its steps, each a
function of its own in `services/retrieval/src/retrieval/`:

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

There is no reranker, no query rewriting by a model and no cache. The other rows are named in one
table (`domain/rows.py`) and refused with `retriever_not_available` (409) until Epic 3 builds them; a
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
With `?retriever_config=`, every row on the `smart` set answers the same chunk, and `r1` is
`retriever_not_available` until the `fixed` set exists. Logs name the row, counts and timings, never
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
| `verdict` (`/health`, `/ready`, `POST /verdict-runs`, `GET /cases/<case_id>/verdict-runs`, `GET /verdict-runs/<verdict_run_id>/steps`, `GET /cases/<case_id>/agent-steps?tool=&rule_id=`), and its Dapr sidecar | `http://localhost:8006`, `http://localhost:3506` |
| Stand-in for Document Intelligence's layout model (this machine only) | `http://localhost:5102` |
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
`VERDICT_MODEL_ENTRA_AUTH=true`). To look at a case's suggested verdicts on this machine, read
`http://localhost:8006/cases/<case_id>/verdict-runs`; the steps of one run are at
`http://localhost:8006/verdict-runs/<verdict_run_id>/steps`, and a case's steps across its runs at
`http://localhost:8006/cases/<case_id>/agent-steps`, which takes `?tool=` and `?rule_id=` to narrow
them.
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
docker build -f services/classification/Dockerfile -t aiuw-classification:dev .
docker build -f services/extraction/Dockerfile -t aiuw-extraction:dev .
docker build -f services/verdict/Dockerfile -t aiuw-verdict:dev .
docker build -f services/retrieval/Dockerfile -t aiuw-retrieval:dev .   # the service, and the job: python -m retrieval.ingest
```

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
