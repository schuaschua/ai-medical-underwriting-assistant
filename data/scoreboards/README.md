# Published scoreboards

The bake-off runner writes three files here, and only when it is run against the deployed
environment (`uv run python -m bakeoff --deployed --web-address https://<web>` for the first two,
the same with `--bake-off classification` for the third, see `evals/README.md`):

| File | Holds |
|---|---|
| `retrieval.json` | One entry per retrieval row `r1` to `r6`: what the row is, rule recall, verdict accuracy, latency, cost and effort, with the counts behind each figure, and the winner. |
| `redaction.json` | Whether redaction left a planted identifier in a stored page text: where and of what kind, never the value. |
| `classification.json` | One entry per classifier contender (`llm`, `doc-intelligence`): accuracy, calibration, queue rate and cost per page, with the counts behind each figure, the winner, and whether a stored reason held a planted identifier. |

Until a run has been made, the folder holds this note only and the Scoreboard screen says, for each
of its two tables, that the bake-off has not been run.

- `web` serves the three files read-only (`GET /api/scoreboards/retrieval`,
  `GET /api/scoreboards/redaction` and `GET /api/scoreboards/classification`) from
  `WEB_SCOREBOARDS_DIR`: this folder in a checkout, its own copy of this folder in the `web` image.
  It reads those three names and nothing else, this note included. No service works a score out, stores one or accepts one (architecture spine AD-17).
- This is the only folder of `data/` that reaches an image.
- Never commit a file with `"stand_ins": true` here. A local run writes to `.work/scoreboards/`,
  which `dapr.yaml` points the local `web` at, and the runner refuses to write here without
  `--deployed`.
