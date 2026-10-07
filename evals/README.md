# The bake-off runner

One command that scores the retrieval ladder rows `r1` to `r6` on the synthetic cases (architecture
spine AD-17, story 3.4). It drives the running system through `web`'s API, as a user would, and writes
two scoreboard files. It is a workspace member and a dev tool: it is in no image, no service imports
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

## Run it against the deployed environment

In the final Azure test session only (the environment is down while coding), with every service
deployed from one build, the manual ingested and the search index loaded:

```sh
uv run python -m bakeoff --deployed --web-address https://<the web app's address>
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
| `EVALS_OUTPUT_DIR` (`--output-dir`) | `.work/scoreboards`, or `data/scoreboards` with `--deployed` | Where the two files are written. |
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
| 0 | The run was made, every case was scored and checked, and no planted identifier was found. |
| 1 | A planted identifier was found (whatever else happened). |
| 2 | The run was refused: its settings, a case the key does not hold, a case document or `static-metrics.yaml` that is missing or malformed, `web` does not answer, or a resume that is not the same run. This command uploaded no case. |
| 3 | Incomplete: no leak, but a case was not scored (it failed, hung, or a call to `web` failed) or a case's page texts were not checked. The printout says how many; both files are written. |
| 4 | The run broke off after it began; no file was written. Start it again with its `eval_run_id`. |

`redaction.json` says `"clean": true` only when no leak was found **and** every case's pages were
read: a case in `cases_not_checked` makes it false.

A run is resumed against the same `web` address and with the same rows as its first start: a case is
started once, so other rows would have no run. The command refuses a resume that differs. A run
narrowed with `--cases` or `--rows` never writes `data/scoreboards/`; give it `--output-dir`.

## The files

Both are contract models (`contracts.models.web`), so `web` and the SPA can show them (story 3.5):

- `retrieval.json` (`RetrievalScoreboard`): `run` (the `eval_run_id`, when, the `web` address,
  `stand_ins`), `top_k`, one entry per ladder row (store, chunk set, method, `measured`, rule recall
  with `recall_hits` and `recall_searches`, verdict accuracy with `right_runs` and `cases`,
  `failed_runs` (the runs of scored cases that failed or are missing: not right, and no wrong
  verdict either), latency with `latency_searches`, cost, effort), the `winner`, the searches that
  failed and the cases that were not scored, each with its reason.
- `redaction.json` (`RedactionScoreboard`): the same `run`, `clean`, the cases and pages checked, the
  identifiers looked for, every leak by case, page and category, how many of the answer key's
  "may also be redacted" strings are in no page text any more, and the cases none of whose pages
  could be read.

## Tests

`uv run pytest evals`: the pure parts, a run against a stand-in for `web`
(`tests/support/bakeoff_fakes.py`), and one whole-path run of two synthetic cases against the real
services with the stand-ins behind them (needs `docker compose up --detach --wait`). The budget is
15 test cases (`CLAUDE.md`).
