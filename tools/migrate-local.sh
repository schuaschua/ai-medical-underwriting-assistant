#!/usr/bin/env bash
# Brings the local containers of compose.yaml to what the services expect:
#   1. every service's database schema at its newest migration;
#   2. the blob containers, in the emulator.
# Safe to run again. It only ever touches this machine: the settings below
# point at loopback, whatever the environment says.
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose up --detach --wait postgres azurite

# intake (schema `intake`; containers `originals` and `cases`).
export INTAKE_DATABASE_HOST=127.0.0.1
export INTAKE_DATABASE_PORT=5432
export INTAKE_DATABASE_NAME=aiuw
export INTAKE_DATABASE_USER=aiuw
export INTAKE_DATABASE_ENTRA_AUTH=false
export INTAKE_BLOB_CONNECTION_STRING="UseDevelopmentStorage=true"
unset INTAKE_BLOB_ACCOUNT_URL

uv run alembic -c services/intake/alembic.ini upgrade head
uv run python -m intake.local_setup
