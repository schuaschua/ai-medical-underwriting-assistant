#!/usr/bin/env bash
# Starts the whole application on this machine:
#   1. PostgreSQL and the Durable Task Scheduler emulator, in containers;
#   2. the built SPA;
#   3. each service in dapr.yaml with its Dapr sidecar (so far: web).
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

spa=services/web/spa
if [ ! -d "$spa/node_modules" ] || [ "$spa/package-lock.json" -nt "$spa/node_modules" ]; then
  npm --prefix "$spa" ci
fi
npm --prefix "$spa" run build

exec dapr run --run-file dapr.yaml
