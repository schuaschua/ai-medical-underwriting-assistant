#!/usr/bin/env bash
# Starts the whole application on this machine:
#   1. PostgreSQL, the blob emulator and the Durable Task Scheduler emulator,
#      in containers;
#   2. the database migrations and the blob containers (tools/migrate-local.sh);
#   3. the built SPA;
#   4. the stand-ins for Azure AI Language, for the Foundry model deployments,
#      for Document Intelligence's layout model and for Azure AI Search
#      (packages/synthdata), which redact documents, classify pages, read
#      their facts, run the verdict agent, parse the manual and answer
#      retrieval rows r5 and r6 while the Azure environment is down;
#   5. the ingestion of the underwriting manual into schema `retrieval`
#      (tools/ingest-local.sh), which does nothing when it was done before,
#      and then loads the search stand-in's index from the stored chunks
#      and has it hold row r6's knowledge base over that index (every
#      start: that stand-in keeps both in memory);
#   6. each service in dapr.yaml with its Dapr sidecar (so far: web, intake,
#      workflow, classification, extraction, retrieval, verdict).
# Stop with Ctrl+C, then `docker compose down` for the containers.
set -euo pipefail

cd "$(dirname "$0")/.."

for tool in docker dapr uv npm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing tool: $tool (see README, 'Run locally')." >&2
    exit 1
  fi
done

# The Dapr CLI alone is not enough: `dapr init` installs the runtime it starts.
if ! dapr --version 2>/dev/null | grep -q -E '^Runtime version: v?[0-9]'; then
  echo "The Dapr runtime is not installed. Run \`dapr init\` once, then start again." >&2
  exit 1
fi

docker compose up --detach --wait

uv sync --locked

# The services never migrate at start-up; locally this step stands in for the
# pipeline's migration step.
./tools/migrate-local.sh

spa=services/web/spa
if [ ! -d "$spa/node_modules" ] || [ "$spa/package-lock.json" -nt "$spa/node_modules" ]; then
  npm --prefix "$spa" ci
fi
npm --prefix "$spa" run build

# The stand-ins for Azure AI Language's document redaction, for the Foundry
# model deployments (the chat model that classifies pages, reads their facts,
# runs the verdict agent and writes the manual's context lines, and the
# embedding model) and for Document
# Intelligence's layout model, on loopback. They are dev tools: no service
# image holds them, and Azure uses the real services. Their ports are the ones
# dapr.yaml gives intake (INTAKE_LANGUAGE_ENDPOINT), classification
# (CLASSIFICATION_MODEL_ENDPOINT), extraction (EXTRACTION_MODEL_ENDPOINT),
# retrieval (RETRIEVAL_MODEL_ENDPOINT) and verdict (VERDICT_MODEL_ENDPOINT),
# and the ones tools/ingest-local.sh gives the ingestion job. The stand-in for
# Azure AI Search (retrieval row r5) is on the port dapr.yaml gives retrieval
# (RETRIEVAL_SEARCH_SERVICE_ENDPOINT).
language_port=5100
model_port=5101
layout_port=5102
search_port=5103
# Something else on a port would be taken for the stand-in below.
for port in "$language_port" "$model_port" "$layout_port" "$search_port"; do
  if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
    echo "Port ${port} is in use; a stand-in needs it." >&2
    exit 1
  fi
done
language_standin=""
model_standin=""
layout_standin=""
search_standin=""
dapr_run=""

# Stops Dapr (and with it the services) and the stand-ins, however the script
# ends: at its end, on Ctrl+C, or when it is told to stop.
stop_all() {
  trap - EXIT INT TERM
  if [ -n "$dapr_run" ]; then
    kill "$dapr_run" 2>/dev/null || true
    wait "$dapr_run" 2>/dev/null || true
  fi
  for standin in "$language_standin" "$model_standin" "$layout_standin" "$search_standin"; do
    if [ -n "$standin" ]; then
      kill "$standin" 2>/dev/null || true
    fi
  done
}
trap stop_all EXIT
trap 'stop_all; exit 130' INT
trap 'stop_all; exit 143' TERM

uv run python -m synthdata.language_standin --port "$language_port" &
language_standin=$!
# FOUNDRY_STANDIN_MODE picks what the model stand-in does (README, 'Run
# locally'): `mixed` makes one case show all three routes of the gate, and
# `quote_not_on_page` gives every page a fact whose quote cannot be verified;
# `rerank_incomplete` and `rerank_slow` show a search with r4 failing.
uv run python -m synthdata.foundry_standin --port "$model_port" \
  --mode "${FOUNDRY_STANDIN_MODE:-ok}" &
model_standin=$!
# The layout stand-in also answers for Document Intelligence's custom
# classifier (story 4.2), which tools/train-local.sh trains once the
# application runs. CLASSIFIER_STANDIN_MODE picks what it does with a page
# (README, 'Run locally'): `unsure` sends every page of a case started with
# `doc-intelligence` to triage.
uv run python -m synthdata.layout_standin --port "$layout_port" \
  --classifier-mode "${CLASSIFIER_STANDIN_MODE:-ok}" &
layout_standin=$!
# SEARCH_STANDIN_MODE picks what the search stand-in does (README, 'Run
# locally'): `unavailable` and `slow` show a search with r5 or r6 failing.
uv run python -m synthdata.search_standin --port "$search_port" \
  --mode "${SEARCH_STANDIN_MODE:-ok}" &
search_standin=$!

# Without the Language stand-in every case would fail at redaction, and
# without the model's at classification: wait until each listens, and stop
# here if one does not.
wait_for_standin() {
  local name="$1" pid="$2" port="$3"
  for _ in $(seq 1 60); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "The ${name} stand-in stopped at start-up (is port ${port} in use?)." >&2
      return 1
    fi
    if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
      return 0
    fi
    sleep 0.5
  done
  echo "The ${name} stand-in did not start listening on port ${port}." >&2
  return 1
}
wait_for_standin "Language" "$language_standin" "$language_port" || exit 1
wait_for_standin "model" "$model_standin" "$model_port" || exit 1
wait_for_standin "layout" "$layout_standin" "$layout_port" || exit 1
wait_for_standin "search" "$search_standin" "$search_port" || exit 1

# The manual's rules, as chunks with vectors in schema `retrieval`. The job is
# one-off and safe to run again: once the manual is ingested a run changes
# nothing and calls no model. A failed run leaves the index as it was, and the
# rest of the application starts all the same (a model stand-in started in a
# failure mode fails the first ingestion too).
if ! ./tools/ingest-local.sh; then
  echo "The manual was not ingested (see the line above). Run ./tools/ingest-local.sh once the cause is gone." >&2
fi

# In the background and waited for, not `exec`: a signal to this script then
# runs the traps above, which pass it on.
dapr run --run-file dapr.yaml &
dapr_run=$!
wait "$dapr_run"
