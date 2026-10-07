#!/usr/bin/env bash
# Trains the Document Intelligence classifier on this machine (story 4.2):
# the prepared training pages are put into the blob emulator's
# `classifier-training` container, and classification's training job has the
# classifier of the id in dapr.yaml built from them.
#
# The Azure environment is down while the stories are built, so Document
# Intelligence is the stand-in of packages/synthdata that ./tools/dev.sh
# starts on port 5102. It keeps the classifier in memory: the job is run
# against the running application, and run again after every start of it.
# This script starts no stand-in of its own, since a classifier built in one
# would be gone when the script ends.
#
# Before it, with the application running, prepare the pages:
#   uv run python -m bakeoff.training_pages
# which redacts every page of data/classifier-training/ through web and
# writes .work/classifier-training/. Another folder can be given as the one
# argument.
#
# Safe to run again: once the classifier exists the job trains nothing. It
# only ever touches this machine: the settings below point at loopback,
# whatever the environment says.
set -euo pipefail

cd "$(dirname "$0")/.."

pages="${1:-.work/classifier-training}"
port=5102

if ! command -v curl >/dev/null 2>&1; then
  echo "Missing tool: curl (it asks the stand-in whether it is one)." >&2
  exit 1
fi
# A classifier that does not exist: the stand-in refuses it in its own words.
# A port that is merely open proves nothing.
if ! curl --silent --max-time 3 \
  "http://127.0.0.1:${port}/documentintelligence/documentClassifiers/none?api-version=2024-11-30" 2>/dev/null |
  grep --quiet 'No such classifier.'; then
  echo "The Document Intelligence stand-in does not answer on port ${port}. Start the application first (./tools/dev.sh); the classifier lives in that stand-in." >&2
  exit 1
fi
if [ ! -f "${pages}/redacted-pages.json" ]; then
  echo "${pages} holds no redacted-pages.json. Prepare the pages first: uv run python -m bakeoff.training_pages" >&2
  exit 1
fi

# The blob emulator's built-in account: it is no secret.
export CLASSIFICATION_BLOB_CONNECTION_STRING="UseDevelopmentStorage=true"
unset CLASSIFICATION_BLOB_ACCOUNT_URL
export CLASSIFICATION_TRAINING_CONTAINER="classifier-training"
# The stand-in takes no credential, and exists only on a developer machine.
export CLASSIFICATION_DOC_INTELLIGENCE_ENDPOINT="http://127.0.0.1:${port}"
export CLASSIFICATION_DOC_INTELLIGENCE_ENTRA_AUTH=false
# The id dapr.yaml tells the service to ask.
export CLASSIFICATION_DOC_INTELLIGENCE_CLASSIFIER_ID="page-types-local"
export CLASSIFICATION_TRAINING_POLL_SECONDS=1
unset CLASSIFICATION_APPLICATIONINSIGHTS_CONNECTION_STRING
unset CLASSIFICATION_AZURE_CLIENT_ID

uv run python -m classification.local_setup "$pages"
uv run python -m classification.train
