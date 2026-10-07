#!/usr/bin/env bash
# Brings the local containers of compose.yaml to what the services expect:
#   1. every service's database schema at its newest migration;
#   2. the blob containers, in the emulator;
#   3. the database roles `workflow` and `verdict` run as;
#   4. the underwriting manual, in the emulator's `manual` container.
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

# classification (schema `classification`). Like intake it runs as the
# container's own user locally; in Azure it has a role of its own
# (infra/bootstrap/README.md, section 6).
export CLASSIFICATION_DATABASE_HOST=127.0.0.1
export CLASSIFICATION_DATABASE_PORT=5432
export CLASSIFICATION_DATABASE_NAME=aiuw
export CLASSIFICATION_DATABASE_USER=aiuw
export CLASSIFICATION_DATABASE_ENTRA_AUTH=false

uv run alembic -c services/classification/alembic.ini upgrade head

# extraction (schema `extraction`). Like classification it runs as the
# container's own user locally; in Azure it has a role of its own
# (infra/bootstrap/README.md, section 8).
export EXTRACTION_DATABASE_HOST=127.0.0.1
export EXTRACTION_DATABASE_PORT=5432
export EXTRACTION_DATABASE_NAME=aiuw
export EXTRACTION_DATABASE_USER=aiuw
export EXTRACTION_DATABASE_ENTRA_AUTH=false

uv run alembic -c services/extraction/alembic.ini upgrade head

# retrieval (schema `retrieval`, with the `vector` extension; container
# `manual`). Like intake it runs as the container's own user locally; in Azure
# it has a role of its own (infra/bootstrap/README.md, section 7). The manual
# is uploaded here so that the ingestion job finds it: tools/ingest-local.sh
# runs the job.
export RETRIEVAL_DATABASE_HOST=127.0.0.1
export RETRIEVAL_DATABASE_PORT=5432
export RETRIEVAL_DATABASE_NAME=aiuw
export RETRIEVAL_DATABASE_USER=aiuw
export RETRIEVAL_DATABASE_ENTRA_AUTH=false
export RETRIEVAL_BLOB_CONNECTION_STRING="UseDevelopmentStorage=true"
unset RETRIEVAL_BLOB_ACCOUNT_URL

uv run alembic -c services/retrieval/alembic.ini upgrade head
uv run python -m retrieval.local_setup data/manual/underwriting-manual.pdf

# verdict (schema `verdict`). As for workflow: the migrations run as the
# container's own user and grant the service's role its rights; the role is
# made first. The service then signs in as that role (dapr.yaml), so it cannot
# update or delete a step of the agent's log here either. In Azure the role is
# the one mapped to the service identity (infra/bootstrap/README.md,
# section 9).
export VERDICT_DATABASE_HOST=127.0.0.1
export VERDICT_DATABASE_PORT=5432
export VERDICT_DATABASE_NAME=aiuw
export VERDICT_DATABASE_USER=aiuw
export VERDICT_DATABASE_SERVICE_ROLE=verdict
export VERDICT_DATABASE_ENTRA_AUTH=false

uv run python -m verdict.local_setup
uv run alembic -c services/verdict/alembic.ini upgrade head
