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
verdict_port                  = 8006
verdict_health_path           = "/health"
verdict_ready_path            = "/ready"
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
# The retrieval ladder rows built so far (spine AD-11): the two baselines,
# the hybrid row, r4 with a reranker, which retrieval answers because it is
# given the chat deployment, and r5 on Azure AI Search, which it answers
# because it is given the search service's endpoint. A new row is added
# here when retrieval and verdict can answer it.
available_retriever_configs = ["r1", "r2", "r3", "r4", "r5"]

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

# Verdict (spine AD-15): the agent's runs, on the same chat deployment; each
# turn of a run is one model call. Lower the first value if the deployment's
# token rate limit is hit. A run stops at the step limit, and a run whose
# agent is less confident than the floor refers its case. The first real
# numbers come from the final Azure test session (deferred-work.md).
verdict_model_max_concurrent_calls = 5
verdict_step_limit                 = 30
verdict_confidence_floor           = 0.70

# Retrieval (spine AD-12). The ingestion job reads this blob from the `manual`
# container, has Document Intelligence's layout model parse it, and stores one
# chunk per rule. The API version is the generally available one the adapter
# was written for; it and the result's shape wait for the final Azure test
# session (deferred-work.md).
manual_blob_name   = "underwriting-manual.pdf"
layout_api_version = "2024-11-30"
# Row r5 (spine AD-11): the index of the manual's smart chunks on Azure AI
# Search, and the REST version retrieval was written for. Unverified: the
# version and the request shapes wait for the final Azure test session
# (deferred-work.md).
search_index_name  = "manual-smart"
search_api_version = "2024-07-01"
# The job makes one chat call per rule (about a hundred) on the deployment the
# other services share: lower this if its token rate limit is hit.
retrieval_model_max_concurrent_calls = 5
# The job writes both chunk sets from the one parsed manual. The fixed set is
# row r1's plain baseline: 350 words a chunk, 35 of them shared with the
# chunk before, about 150 chunks, embedded with the same deployment and with
# no chat call.
ingest_chunk_sets         = ["smart", "fixed"]
fixed_chunk_words         = 350
fixed_chunk_overlap_words = 35
# Row r3 (spine AD-11): how many chunks the vector search and the full-text
# search each hand to the rank fusion. The manual has about a hundred rules.
search_candidate_depth = 50
# A search's own short budget (the ingestion job keeps the model settings
# above): seconds for the query's embedding call, its retries, and the
# deadline over the whole search.
search_embedding_timeout_seconds = 3
search_embedding_max_retries     = 1
search_deadline_seconds          = 8
# Row r4 (spine AD-11): an LLM reranker on the chat deployment. The 20 best
# fused candidates are rated in one chat call of up to 15 s, and a search
# with r4 has 20 s in all, which every caller of a search outlasts
# (verdict 25 s, web 30 s, the bake-off runner 30 s). Its answer may take
# 4,000 tokens: an entry per candidate, and room for a model that reasons.
search_rerank_depth                 = 20
search_rerank_timeout_seconds       = 15
search_rerank_deadline_seconds      = 20
search_rerank_max_completion_tokens = 4000
# The job's own deadline, and the platform's limit on one run of it, which
# must be the longer by eight minutes or more: five for the search index
# load that follows (its own deadline) and room for storing the result.
ingest_deadline_seconds = 1800
ingest_timeout_seconds  = 2400

# A new role assignment takes a while to reach every Azure region and service.
role_propagation_wait = "60s"
