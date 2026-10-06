# Values for the one environment, `demo`. No secrets and no ids here.

workload            = "aiuw"
environment         = "demo"
location            = "westus3"
region_short        = "wus3"
owner               = "darrel"
data_classification = "synthetic"
repository_url      = "https://github.com/schuaschua/ai-medical-underwriting-assistant"

runtime_identity_roles = ["web", "intake", "classification", "extraction", "retrieval", "verdict", "workflow"]

# Observability and cost (azure.md rules 17 and 18).
log_daily_cap_gb    = 0.5
log_retention_days  = 30
budget_amount       = 150
budget_time_grain   = "Monthly"
log_cap_alert_ratio = 0.9

# Cheapest tiers that work (azure.md rule 19).
container_registry_sku    = "Basic"
postgresql_sku            = "B_Standard_B1ms"
postgresql_version        = "17"
postgresql_storage_mb     = 32768
postgresql_storage_tier   = "P4"
postgresql_backup_days    = 7
storage_account_sku       = "Standard_LRS"
storage_containers        = ["originals", "cases", "manual", "classifier-training"]
search_sku                = "basic"
search_semantic_sku       = "free"
document_intelligence_sku = "S0"
language_sku              = "S"
foundry_sku               = "S0"
durable_task_sku          = "Consumption"

# Model deployments: Global Standard, exact version, no auto-upgrade, default
# content filter. Capacity is in thousands of tokens per minute.
# Retirement dates on 2026-10-06: gpt-5.4 2027-09-02, text-embedding-3-large 2028-02-09.
model_deployments = {
  chat = {
    name          = "gpt-5.4"
    model_name    = "gpt-5.4"
    model_version = "2026-03-05"
    capacity      = 100
  }
  embedding = {
    name          = "text-embedding-3-large"
    model_name    = "text-embedding-3-large"
    model_version = "1"
    capacity      = 50
  }
}
