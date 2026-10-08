# The bake-off runner

One command with two bake-offs (architecture spine AD-17). It scores the retrieval ladder rows `r1`
to `r6` on the synthetic cases (story 3.4), or, with `--bake-off classification`, the two classifier
contenders on the scored page set (story 4.3, [below](#the-classification-bake-off-story-43)). It
drives the running system through `web`'s API, as a user would, and writes the scoreboard files. It is a workspace member and a dev tool: it is in no image, no service imports
it, and it is the only code besides the generator's tests that reads `data/answer-key/`. The package
is imported as `bakeoff`.

## What it measures

| Figure | How |
|---|---|
| Rule recall | For every expected fact of every case that meets at least one rule: one search per row, with the query `contracts.query.build_fact_query` makes from the fact's statement, `top_k` 5. A hit is one of the fact's expected rule ids among the answered items' rule ids. Recall is hits over searches. A search that fails is a miss and is listed. |
| Latency | The search's own `latency_ms`, as `retrieval` reports it: median and 95th percentile of the searches that answered. |
| Verdict accuracy | Each case is uploaded once and started once with every row that answered and the run's `eval_run_id`, so every row judges the same extracted facts. A run is right when its verdict is the expected one and, for a loaded case, its loading too. Accuracy is right runs over cases. A case that failed, or was not final within its deadline, is wrong for every row and is listed. |
| Cost and effort | Not measured: stated in `static-metrics.yaml`, each figure with its source. A figure nobody has stated is `null`. |
| Winner | Highest verdict accuracy, then rule recall, then lower latency, among the measured rows. |

Recall and accuracy are measured apart on purpose: recall with a fixed query says how good a row's
search is, accuracy how good the whole agent is with that row. The runner never scores a row on any
other query.

The human waits are answered from the answer key's page labels and nothing else: a page that waits
for the customer is kept if its expected label is medical and discarded if not; a page in triage is
accepted if medical and denied if not. A waiting page the key has no label for fails its case.

**Redaction check**, in the same run: the text of every page of every case is read through `web` and
normalised with `contracts.text.normalise`. A planted identifier found in it is a leak, and so is any
part of a planted name standing as a word of its own. A leak makes the command exit with status 1;
`redaction.json` names the case, the page and the category, never the value.

The same check counts over-redaction (spine AD-17): every quote of every expected fact of a checked
case (`places` in the answer key) is looked for in the stored text of its own page with the
contracts' quote finder (`contracts.text.QuoteFinder`, the rule of the quote check of story 2.4).
`redaction.json` holds how many were looked for, how many were not found, and each of those by case,
page and fact number, never the quote. A quote that is not found is a figure: it does not fail the
command and does not make the report unclean.

A row that answers `retriever_not_available` is recorded as not measured, with no numbers, and the
run goes on.

## Run it locally

Start the system (`./tools/dev.sh`, see the root README), then from the repository root:

```sh
uv run python -m bakeoff                          # every case, every row
uv run python -m bakeoff --cases case-001 case-003 --rows r3
```

The files go to `.work/scoreboards/retrieval.json` and `.work/scoreboards/redaction.json`, with
`"stand_ins": true`. **These figures are not results.** The local stand-in's vectors only count
shared words and its agent is scripted: a local run proves that the runner and the system fit
together, nothing about the retrievers. The command refuses to write `data/scoreboards/` unless it
is told the run is against the deployed environment, and no local file is ever committed there.

The command prints the `eval_run_id` of the run. If it is stopped half-way, start it again with that
id and no case is uploaded a second time:

```sh
uv run python -m bakeoff --eval-run-id <the id it printed>
```

Which case was uploaded as which case id is kept in `.work/evals/<eval_run_id>.json`.

## The classification bake-off (story 4.3)

A second mode of the same command, and a run of its own with its own `eval_run_id`:

```sh
uv run python -m bakeoff --bake-off classification                      # every file, both contenders
uv run python -m bakeoff --bake-off classification --cases case-002 case-003 --contenders llm
```

Each file of the scored page set (`data/answer-key/page-set.json`: the 22 case files, 94 pages) is
uploaded once per contender and started with that contender, the run's `eval_run_id` and
`stop_after` `gate`. The pages are redacted, classified and routed and nothing more: no extraction
and no verdict runs, no page waits for a person, and the cases are in neither of the underwriter's
lists. When a case has come to rest the runner reads its progress (where the gate put each page) and
its stored classifications through `web`, and holds them against the page set's labels. It labels,
routes and decides nothing itself.

| Figure | How |
|---|---|
| Accuracy | Pages whose stored `is_medical` is the expected one, over every page of the set. The page type is not scored. |
| Calibration | Of the pages whose stored confidence is 0.90 or more, the share labelled correctly. None when no page was scored that high. |
| Queue rate | Pages the gate left in `awaiting_triage`, over every page of the set. |
| Cost per page | Not measured: stated in `static-metrics.yaml` under `classifiers`, with its source. `null` until somebody states it. |
| Winner | The more accurate contender among those whose calibration is at least 0.90 over at least 10 pages scored 0.90 or more, then the one with the lower queue rate; all three compared on the counts, not on the rounded figures. A contender with fewer than 10 such pages, or with no calibration, cannot win (the floor is `CALIBRATION_FLOOR_PAGES` in `contracts.models.web`; owner's decision of 2026-10-08). No winner when none qualifies. |

- A page with a failed result, or none, is wrong, is in no calibration count, and is listed by case
  and page (`unclassified_pages`).
- A file whose case fails, or is not final within `EVALS_CASE_DEADLINE_SECONDS`, counts every one of
  its pages as wrong for that contender, whatever was stored for them, and is listed
  (`unscored_cases`). Of its pages only those that failed are listed by page as well.
- **A contender that cannot be run here is not measured.** The first file of the set goes alone.
  The contender is taken as not runnable only when that file's case shows what the system shows
  through `web` when `classification` refuses the contender's commands: the case failed with the
  case-level `stage_failed`, redaction is done, every page is still `uploaded` with no error, **and**
  `classification` itself answers the read of the case's classifications and holds none. Then the
  contender has no numbers, no other file is uploaded for it, the run goes on, and `not_run` in the
  file names that first case (its key, its case id, what it ended with) so that it can be looked at.
  It is never taken for `llm`, which every instance runs. A first file that failed in any other way
  (a page failed, redaction failed, `classification` does not answer) is a file that is not scored,
  and the other files follow. One thing looks the same and cannot be told apart through `web`:
  `doc-intelligence` configured but not yet trained, whose commands are sent again until
  `workflow`'s retries run out. That too is "cannot be run here", but its remedy is the training job.
- A run in which no contender is measured is incomplete (status 3) and writes no file.
- **The reasons check**, in the same run: every stored `reason` that was read, of every contender, is
  looked through for the planted identifiers of its case, by the rule of the redaction check (the
  normalised value, and each part of a planted name as a word of its own). A reason that holds one
  makes the command exit with status 1; `classification.json` names the contender, the case, the page
  and the category, never the value. Reasons that were not read are not reasons that hold nothing: a
  file whose case was not final, or whose classifications could not be read, is listed under
  `reasons_not_checked`, the printout then says "not clean" and the run is incomplete, as a case
  the redaction check could not read is.
- The contenders run one after the other, the files of one contender `EVALS_CASE_CONCURRENCY` at a
  time. With five model runs a page the `llm` contender makes about 470 calls for the whole set.
- A resumed run (`--eval-run-id`) uploads no file a second time for a contender. A case that failed
  stays failed: start a new run to score it again. The run's id cannot be used for a retrieval
  bake-off, nor the other way round.

The file goes to `.work/scoreboards/classification.json` with `"stand_ins": true`, or to
`data/scoreboards/` with `--deployed`, by the same rules as the other two; a run narrowed with
`--cases` or `--contenders` never writes `data/scoreboards/`. **Local figures are not results**: the
chat stand-in and the classifier stand-in both tell page types apart by the headings the generator
prints, so both are right on nearly every page. The exit statuses are in the table below. A
contender that is not measured does not make a run incomplete while another is measured.

Before the run in Azure the classifier must be trained (the next section and
`infra/bootstrap/README.md`, section 10); otherwise `doc-intelligence` is not measured or its cases
fail.

## Prepare the classifier's training pages (story 4.2)

A second command of this package, beside the runner. The Document Intelligence classifier is trained
on pages that passed the same redaction as case pages, and only the pipeline redacts. So each page of
`data/classifier-training/` is uploaded through `web` as a case of an eval run, started with
`stop_after` `gate`, and the redacted file `web` serves is fetched and written under its page type:

```sh
uv run python -m bakeoff.training_pages                                   # the local start
uv run python -m bakeoff.training_pages --web-address https://<web's address>   # the deployed environment
```

It writes `.work/classifier-training/<page_type>/<file>.pdf` and one list beside them,
`redacted-pages.json`, which names every page with its label, its case id, its document id and the
MD5 of the redacted file. The list is written last, when every page is there; the training job
trains only on a container that holds exactly the listed files with that content. A page whose case
fails stops the command (exit status 1), naming the page; `--eval-run-id <the id it logged>`
resumes without uploading a page twice, but for the page whose case failed, which is uploaded again
as a new case. PDFs an earlier run left in the folder are removed. It never writes into `data/`. An operator uploads the folder to the `classifier-training` container
(`infra/bootstrap/README.md`, section 10); locally `./tools/train-local.sh` does it. The pages run
through the gate, so the chat model classifies each of them too: 46 pages, five runs each.

## Run it against the deployed environment

In the final Azure test session only (the environment is down while coding), with every service
deployed from one build, the manual ingested and the search index loaded:

```sh
uv run python -m bakeoff --deployed --web-address https://<the web app's address>
```

Then, once the classifier is trained, the classification bake-off, in a run of its own:

```sh
uv run python -m bakeoff --bake-off classification --deployed --web-address https://<the web app's address>
```

`--deployed` says that the real AI services answered: the files are written to `data/scoreboards/`
with `"stand_ins": false`, to be committed. Nothing can be read from `web` that proves it, so the
operator says it; the command refuses `--deployed` for an address on this machine, and refuses to
send the case documents over plain HTTP to any other machine.

Before that run, the rows `workflow` and `verdict` may run with (`available_retriever_configs` in
`infra/demo/app/terraform.tfvars`) must be the rows `retrieval` answers. The runner starts each case
with the rows whose search answered. If `workflow` refuses one of them, every start fails and every
case is listed as not scored; run with `--rows` then, or fix the setting.

Each case costs model calls for every page and one agent run per row, against one shared token
limit. That is why only 2 cases are under way at once by default, and why a run can be resumed.

## Settings

Environment variables with the prefix `EVALS_`. Where a setting has a command line option it is named beside it, and the option wins.

| Setting | Default | |
|---|---|---|
| `EVALS_WEB_ADDRESS` (`--web-address`) | `http://localhost:8000` | Where `web` is. |
| `EVALS_DEPLOYED` (`--deployed`) | off | The run is against the deployed environment. |
| `EVALS_EVAL_RUN_ID` (`--eval-run-id`) | a new id | Give it to resume a run. |
| `EVALS_CASES` (`--cases`) | every case | As a JSON list in the environment. |
| `EVALS_ROWS` (`--rows`) | every row | As a JSON list in the environment. |
| `EVALS_BAKE_OFF` (`--bake-off`) | `retrieval` | `retrieval` or `classification`. |
| `EVALS_CONTENDERS` (`--contenders`) | both | The classification bake-off only. As a JSON list in the environment. |
| `EVALS_OUTPUT_DIR` (`--output-dir`) | `.work/scoreboards`, or `data/scoreboards` with `--deployed` | Where the run's files are written: `retrieval.json` and `redaction.json`, or `classification.json`. |
| `EVALS_CASE_CONCURRENCY` (`--case-concurrency`) | 2 | Cases under way at once (1 to 8). |
| `EVALS_SEARCH_CONCURRENCY` | 2 | Eval searches under way at once (1 to 4). |
| `EVALS_CASE_DEADLINE_SECONDS` | 1800 | From a case's start to its final status. |
| `EVALS_POLL_SECONDS` | 2 | How often a case's progress is read. |
| `EVALS_REQUEST_TIMEOUT_SECONDS` | 30 | One call to `web`. |
| `EVALS_UPLOAD_TIMEOUT_SECONDS` | 150 | The upload of one case. |
| `EVALS_REQUEST_RETRIES`, `EVALS_RETRY_SECONDS` | 2, 2 | A call with no answer, or a 429, 502, 503 or 504, is sent again. Every call the runner makes is safe to repeat. |
| `EVALS_STATE_DIR` | `.work/evals` | Where a run remembers its uploads. |
| `EVALS_DATA_DIR`, `EVALS_STATIC_METRICS_FILE` | `data`, `evals/static-metrics.yaml` | |

Exit status:

| Status | Meaning |
|---|---|
| 0 | The run was made and is whole. Retrieval: every case was scored and checked, and no planted identifier was found. Classification: a contender was measured, every file of every measured contender was scored, every page has a result, every file's reasons were read, and none holds a planted identifier. |
| 1 | A planted identifier was found, in a page text (retrieval) or in a stored reason (classification), whatever else happened. |
| 2 | The run was refused: its settings, a case the key or the page set does not hold, a case document or `static-metrics.yaml` that is missing or malformed, `web` does not answer, or a resume that is not the same run (another address, other rows, the other bake-off, or the id of a training-pages run). This command uploaded no case. |
| 3 | Incomplete: no leak, but the run is not whole. Retrieval: a case was not scored (it failed, hung, or a call to `web` failed) or a case's page texts were not checked; both files are written. Classification: no contender was measured (no file is written), or a file was not scored, a page has no result, or a file's reasons were not read (the file is written). The printout says how many. |
| 4 | The run broke off after it began, or the run's state file could not be written; no file was written. Start it again with its `eval_run_id`. |

`redaction.json` says `"clean": true` only when no leak was found **and** every case's pages were
read: a case in `cases_not_checked` makes it false.

A run is resumed against the same `web` address and with the same rows as its first start: a case is
started once, so other rows would have no run. The command refuses a resume that differs. A run
narrowed with `--cases` or `--rows` never writes `data/scoreboards/`; give it `--output-dir`.

## The files

All are contract models (`contracts.models.web`), so `web` and the SPA can show them (stories 3.5
and 4.3):

- `retrieval.json` (`RetrievalScoreboard`): `run` (the `eval_run_id`, when, the `web` address,
  `stand_ins`), `top_k`, one entry per ladder row (store, chunk set, method, `measured`, rule recall
  with `recall_hits` and `recall_searches`, verdict accuracy with `right_runs` and `cases`,
  `failed_runs` (the runs of scored cases that failed or are missing: not right, and no wrong
  verdict either), latency with `latency_searches`, cost, effort), the `winner`, the searches that
  failed and the cases that were not scored, each with its reason.
- `redaction.json` (`RedactionScoreboard`): the same `run`, `clean`, the cases and pages checked, the
  identifiers looked for, every leak by case, page and category, how many of the answer key's
  "may also be redacted" strings are in no page text any more, the cases none of whose pages
  could be read, and the expected-fact quotes: `quotes_checked`, `quotes_not_found` and each quote
  not found by case, page and fact number (`quotes_not_found_at`). A file written before the quote
  count was built (2026-10-08) has none of the three and does not fit the model: run the bake-off
  again.
- `classification.json` (`ClassificationScoreboard`, story 4.3): its own `run`, one entry per
  contender (`measured`, accuracy with `right_pages` and `pages`, calibration with
  `confident_right_pages` and `confident_pages`, queue rate with `queued_pages`,
  `pages_not_classified`, `cost_per_page`), the `winner`, `not_run` (for a contender that could
  not be run: its first file's case and what it ended with), the files that were not scored and the
  pages without a result, each with its contender, `reasons_checked`, every leak in a reason by
  contender, case, page and category, and `reasons_not_checked` (the files whose reasons were not
  read).

## Tests

`uv run pytest evals`: the pure parts, runs against a stand-in for `web`
(`tests/support/bakeoff_fakes.py`), and whole-path runs against the real services with the stand-ins
behind them (needs `docker compose up --detach --wait`): one of each bake-off over two synthetic
cases, and the training pages' tool. The budget is 15 test cases (`CLAUDE.md`), and the suite is at
15.
