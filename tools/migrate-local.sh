#!/usr/bin/env bash
# Brings the local containers of compose.yaml to what the services expect:
#   1. every service's database schema at its newest migration;
#   2. the blob containers, in the emulator;
#   3. the database role `workflow` runs as.
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

# workflow (schema `workflow`). The migrations run as the container's own user
# and grant the service's role its rights; the role is made first. The service
# then signs in as that role (dapr.yaml), so it cannot update or delete an
# audit event here either.
export WORKFLOW_DATABASE_HOST=127.0.0.1
export WORKFLOW_DATABASE_PORT=5432
export WORKFLOW_DATABASE_NAME=aiuw
export WORKFLOW_DATABASE_USER=aiuw
export WORKFLOW_DATABASE_SERVICE_ROLE=workflow
export WORKFLOW_DATABASE_ENTRA_AUTH=false

uv run python -m workflow.local_setup
uv run alembic -c services/workflow/alembic.ini upgrade head
