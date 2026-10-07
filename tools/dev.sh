#!/usr/bin/env bash
# Starts the whole application on this machine:
#   1. PostgreSQL, the blob emulator and the Durable Task Scheduler emulator,
#      in containers;
#   2. the database migrations and the blob containers (tools/migrate-local.sh);
#   3. the built SPA;
#   4. the stand-in for Azure AI Language (packages/synthdata), which redacts
#      documents while the Azure environment is down;
#   5. each service in dapr.yaml with its Dapr sidecar (so far: web, intake,
#      workflow).
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

# The stand-in for Azure AI Language's document redaction, on loopback. It is
# a dev tool: no service image holds it, and Azure uses the real service.
# Its port is the one dapr.yaml gives intake (INTAKE_LANGUAGE_ENDPOINT).
language_port=5100
# Something else on the port would be taken for the stand-in below.
if (exec 3<>"/dev/tcp/127.0.0.1/${language_port}") 2>/dev/null; then
  echo "Port ${language_port} is in use; the Language stand-in needs it." >&2
  exit 1
fi
uv run python -m synthdata.language_standin --port "$language_port" &
language_standin=$!
dapr_run=""

# Stops Dapr (and with it the services) and the stand-in, however the script
# ends: at its end, on Ctrl+C, or when it is told to stop.
stop_all() {
  trap - EXIT INT TERM
  if [ -n "$dapr_run" ]; then
    kill "$dapr_run" 2>/dev/null || true
    wait "$dapr_run" 2>/dev/null || true
  fi
  kill "$language_standin" 2>/dev/null || true
}
trap stop_all EXIT
trap 'stop_all; exit 130' INT
trap 'stop_all; exit 143' TERM

# Without the stand-in every case would fail at redaction: wait until it
# listens, and stop here if it does not.
for _ in $(seq 1 60); do
  if ! kill -0 "$language_standin" 2>/dev/null; then
    echo "The Language stand-in stopped at start-up (is port ${language_port} in use?)." >&2
    exit 1
  fi
  if (exec 3<>"/dev/tcp/127.0.0.1/${language_port}") 2>/dev/null; then
    listening=yes
    break
  fi
  sleep 0.5
done
if [ "${listening:-no}" != yes ]; then
  echo "The Language stand-in did not start listening on port ${language_port}." >&2
  exit 1
fi

# In the background and waited for, not `exec`: a signal to this script then
# runs the traps above, which pass it on.
dapr run --run-file dapr.yaml &
dapr_run=$!
wait "$dapr_run"
