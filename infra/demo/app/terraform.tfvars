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
otel_sampling_ratio           = 1
dapr_http_max_request_size_mb = 16

# A new role assignment takes a while to reach every Azure region and service.
role_propagation_wait = "60s"
