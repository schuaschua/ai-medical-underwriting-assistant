#!/usr/bin/env bash
# Runs retrieval's ingestion job on this machine: the manual in the blob
# emulator is parsed, cut into one chunk per rule and stored, with its
# vectors, in schema `retrieval` of the local PostgreSQL.
#
# The Azure environment is down while the stories are built, so Document
# Intelligence and the Foundry deployments are the stand-ins of
# packages/synthdata. If they already listen on their ports (./tools/dev.sh
# starts them) they are used; otherwise this script starts them and stops
# them again when the job has ended.
#
# Safe to run again: a second run changes nothing and calls no model. It only
# ever touches this machine: the settings below point at loopback, whatever
# the environment says. Run ./tools/migrate-local.sh first (it creates the
# schema and uploads the manual); ./tools/dev.sh does both.
set -euo pipefail

cd "$(dirname "$0")/.."

model_port=5101
layout_port=5102
started=()

stop_standins() {
  trap - EXIT INT TERM
  for standin in "${started[@]:-}"; do
    if [ -n "$standin" ]; then
      kill "$standin" 2>/dev/null || true
    fi
  done
}
trap stop_standins EXIT
trap 'stop_standins; exit 130' INT
trap 'stop_standins; exit 143' TERM

listening() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

# Whether the stand-in itself answers on a port: each is asked something only
# it answers the way it does. A port that is merely open proves nothing, and
# the job would send the manual to whatever listens there.
is_standin() {
  local name="$1" port="$2"
  case "$name" in
    model)
      # An embedding request without texts: the stand-in refuses it in its own words.
      curl --silent --max-time 3 --request POST \
        --header 'content-type: application/json' --data '{}' \
        "http://127.0.0.1:${port}/openai/v1/embeddings" 2>/dev/null |
        grep --quiet 'Not an embedding request.'
      ;;
    layout)
      # A result that does not exist: likewise.
      curl --silent --max-time 3 \
        "http://127.0.0.1:${port}/documentintelligence/documentModels/prebuilt-layout/analyzeResults/none" 2>/dev/null |
        grep --quiet 'No such analysis.'
      ;;
    *) return 1 ;;
  esac
}

# Use the stand-in that answers on its port, or start one there and wait for
# it. Anything else on the port stops the script.
ensure_standin() {
  local name="$1" module="$2" port="$3" pid
  if is_standin "$name" "$port"; then
    return 0
  fi
  if listening "$port"; then
    echo "Port ${port} is in use, but not by the ${name} stand-in, which this script expects there (./tools/dev.sh starts it) or starts itself on a free port. Stop what listens on port ${port} and run again." >&2
    return 1
  fi
  uv run python -m "$module" --port "$port" &
  pid=$!
  started+=("$pid")
  for _ in $(seq 1 60); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "The ${name} stand-in stopped at start-up; it was to listen on port ${port}." >&2
      return 1
    fi
    if is_standin "$name" "$port"; then
      return 0
    fi
    sleep 0.5
  done
  echo "The ${name} stand-in did not answer on port ${port}." >&2
  return 1
}

if ! command -v curl >/dev/null 2>&1; then
  echo "Missing tool: curl (it asks each stand-in whether it is one)." >&2
  exit 1
fi

ensure_standin "model" synthdata.foundry_standin "$model_port"
ensure_standin "layout" synthdata.layout_standin "$layout_port"

export RETRIEVAL_DATABASE_HOST=127.0.0.1
export RETRIEVAL_DATABASE_PORT=5432
export RETRIEVAL_DATABASE_NAME=aiuw
export RETRIEVAL_DATABASE_USER=aiuw
export RETRIEVAL_DATABASE_ENTRA_AUTH=false
# The blob emulator's built-in account: it is no secret.
export RETRIEVAL_BLOB_CONNECTION_STRING="UseDevelopmentStorage=true"
unset RETRIEVAL_BLOB_ACCOUNT_URL
# The stand-ins take no credential, and exist only on a developer machine.
export RETRIEVAL_LAYOUT_ENDPOINT="http://127.0.0.1:${layout_port}"
export RETRIEVAL_LAYOUT_ENTRA_AUTH=false
export RETRIEVAL_MODEL_ENDPOINT="http://127.0.0.1:${model_port}"
export RETRIEVAL_MODEL_ENTRA_AUTH=false
# Local names for the two deployments; they are stored with every chunk.
export RETRIEVAL_CHAT_DEPLOYMENT="local-stand-in"
export RETRIEVAL_EMBEDDING_DEPLOYMENT="local-stand-in-embedding"
# Both chunk sets (spine AD-11): `smart` for rows r2 and r3, and `fixed`,
# row r1's baseline, at its default size and overlap in words.
export RETRIEVAL_INGEST_CHUNK_SETS='["smart", "fixed"]'
export RETRIEVAL_FIXED_CHUNK_WORDS=350
export RETRIEVAL_FIXED_CHUNK_OVERLAP_WORDS=35

uv run python -m retrieval.ingest
