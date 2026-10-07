# Values for the one environment, `demo`. No secrets and no ids here.
# `image_tag` is set by the deploy workflow.

state_resource_group_name  = "rg-tfstate-sea"
state_storage_account_name = "stdjtfstatesea"
state_container_name       = "aiuw"
foundation_state_key       = "demo/foundation.tfstate"

# Scale to zero outside a demo; set to 1 to hold the services warm.
# One replica each while the demo environment is up: a cold start takes about
# 30 seconds (observed 2026-10-07). Owner's decision, 2026-10-07.
min_replicas = 1

web_port                      = 8000
web_health_path               = "/api/health"
intake_port                   = 8001
intake_health_path            = "/health"
intake_ready_path             = "/ready"
workflow_port                 = 8002
workflow_health_path          = "/health"
workflow_ready_path           = "/ready"
classification_port           = 8003
classification_health_path    = "/health"
classification_ready_path     = "/ready"
retrieval_port                = 8004
retrieval_health_path         = "/health"
retrieval_ready_path          = "/ready"
extraction_port               = 8005
extraction_health_path        = "/health"
extraction_ready_path         = "/ready"
otel_sampling_ratio           = 1
dapr_http_max_request_size_mb = 16
dapr_http_port                = 3500

# Redaction (spine AD-21). Both values wait for the final Azure test session
# (deferred-work.md): the API version is the one the spine names as generally
# available, and `PolicyNumber` is this project's own name for a category the
# service may not know. If the service refuses it, take it out here.
language_api_version = "2026-05-01"
redaction_categories = [
  "Person",
  "Address",
  "PhoneNumber",
  "Email",
  "USSocialSecurityNumber",
  "PolicyNumber",
]

# The gate (spine AD-7): medical at this confidence or more goes to
# extraction, non-medical at it or more back to the customer, anything under
# it to triage. Only workflow is given it.
gate_threshold = 0.90

# Classification (spine AD-13): the LLM classifier's confidence is the share
# of this many runs that agree. Whether the runs differ at all on the real
# deployment waits for the final Azure test session (deferred-work.md).
classifier_runs = 5
# What to tune there if the deployment's token rate limit is hit: how many of
# a page's runs go at once, how many model calls the service has under way in
# all, and how often a throttled call is sent again (spine AD-16: three).
classifier_max_concurrent_runs = 5
model_max_concurrent_calls     = 10
model_max_retries              = 3

# Extraction (spine AD-14): one model call per page that reaches extraction,
# on the chat deployment classification shares. Lower this if its token rate
# limit is hit; the first real numbers come from the final Azure test session
# (deferred-work.md).
extraction_model_max_concurrent_calls = 5

# Retrieval (spine AD-12). The ingestion job reads this blob from the `manual`
# container, has Document Intelligence's layout model parse it, and stores one
# chunk per rule. The API version is the generally available one the adapter
# was written for; it and the result's shape wait for the final Azure test
# session (deferred-work.md).
manual_blob_name   = "underwriting-manual.pdf"
layout_api_version = "2024-11-30"
# The job makes one chat call per rule (about a hundred) on the deployment the
# other services share: lower this if its token rate limit is hit.
retrieval_model_max_concurrent_calls = 5
# Row r3 (spine AD-11): how many chunks the vector search and the full-text
# search each hand to the rank fusion. The manual has about a hundred rules.
search_candidate_depth = 50
# A search's own short budget (the ingestion job keeps the model settings
# above): seconds for the query's embedding call, its retries, and the
# deadline over the whole search.
search_embedding_timeout_seconds = 3
search_embedding_max_retries     = 1
search_deadline_seconds          = 8
# The job's own deadline, and the platform's limit on one run of it, which
# must be the longer by two minutes or more.
ingest_deadline_seconds = 1800
ingest_timeout_seconds  = 2100

# A new role assignment takes a while to reach every Azure region and service.
role_propagation_wait = "60s"
