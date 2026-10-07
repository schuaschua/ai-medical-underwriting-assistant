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

# A new role assignment takes a while to reach every Azure region and service.
role_propagation_wait = "60s"
