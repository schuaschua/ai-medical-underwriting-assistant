data "azurerm_client_config" "current" {}

# --- Resource group ----------------------------------------------------------

# The bootstrap script creates the resource group; this stack adopts it
# (azure.md rule 31). Hand-written instead of the Azure Verified Module so it
# can carry prevent_destroy: a teardown removes everything in the group and
# keeps the group, with the deployment identity's role assignments on it.
import {
  to = azurerm_resource_group.this
  id = local.resource_group_id
}

resource "azurerm_resource_group" "this" {
  name     = local.names.resource_group
  location = var.location
  tags     = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

# --- Observability -----------------------------------------------------------

module "log_analytics_workspace" {
  source  = "Azure/avm-res-operationalinsights-workspace/azurerm"
  version = "0.5.1"

  name                                                 = local.names.log_analytics_workspace
  resource_group_name                                  = azurerm_resource_group.this.name
  location                                             = var.location
  log_analytics_workspace_sku                          = var.log_sku
  log_analytics_workspace_daily_quota_gb               = var.log_daily_cap_gb
  log_analytics_workspace_retention_in_days            = var.log_retention_days
  log_analytics_workspace_local_authentication_enabled = false
  log_analytics_workspace_internet_ingestion_enabled   = "true"
  log_analytics_workspace_internet_query_enabled       = "true"
  enable_telemetry                                     = true
  tags                                                 = local.tags
}

module "application_insights" {
  source  = "Azure/avm-res-insights-component/azurerm"
  version = "0.4.0"

  name                          = local.names.application_insights
  resource_group_name           = azurerm_resource_group.this.name
  location                      = var.location
  workspace_id                  = module.log_analytics_workspace.resource_id
  application_type              = "web"
  local_authentication_disabled = true
  daily_data_cap_in_gb          = var.log_daily_cap_gb
  retention_in_days             = var.log_retention_days
  enable_telemetry              = true
  tags                          = local.tags
}

# --- Registry and compute ----------------------------------------------------

module "container_registry" {
  source  = "Azure/avm-res-containerregistry-registry/azurerm"
  version = "0.8.0"

  name                    = local.names.container_registry
  resource_group_name     = azurerm_resource_group.this.name
  location                = var.location
  sku                     = var.container_registry_sku
  admin_enabled           = false
  anonymous_pull_enabled  = false
  zone_redundancy_enabled = false
  enable_telemetry        = true
  tags                    = local.tags
}

module "container_apps_environment" {
  source  = "Azure/avm-res-app-managedenvironment/azurerm"
  version = "0.5.1"

  name                = local.names.container_apps_environment
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location

  # Consumption only. Logs go to Azure Monitor and from there to the workspace
  # through the diagnostic setting, so no workspace key is used.
  workload_profiles = [{
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }]
  zone_redundant         = false
  public_network_access  = "Enabled"
  app_logs_configuration = { destination = "azure-monitor" }

  diagnostic_settings = {
    workspace = {
      name                  = local.names.diagnostic_setting
      workspace_resource_id = module.log_analytics_workspace.resource_id
      log_groups            = []
      log_categories        = ["ContainerAppConsoleLogs", "ContainerAppSystemLogs"]
      metric_categories     = []
    }
  }

  enable_telemetry = true
  tags             = local.tags
}

module "runtime_identity" {
  source   = "Azure/avm-res-managedidentity-userassignedidentity/azurerm"
  version  = "0.5.3"
  for_each = local.runtime_identity_names

  name                = each.value
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  enable_telemetry    = true
  tags                = local.tags
}

# --- Data --------------------------------------------------------------------

module "postgresql" {
  source  = "Azure/avm-res-dbforpostgresql-flexibleserver/azurerm"
  version = "0.2.3"

  name                = local.names.postgresql_server
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location

  server_version               = var.postgresql_version
  sku_name                     = var.postgresql_sku
  storage_mb                   = var.postgresql_storage_mb
  storage_tier                 = var.postgresql_storage_tier
  auto_grow_enabled            = false
  backup_retention_days        = var.postgresql_backup_days
  geo_redundant_backup_enabled = false
  high_availability            = null

  # Entra-only: no password, no administrator login.
  authentication = {
    active_directory_auth_enabled = true
    password_auth_enabled         = false
    tenant_id                     = local.tenant_id
  }
  ad_administrator = local.postgresql_administrators

  # Public endpoint, open to Azure services only (azure.md rule 13).
  public_network_access_enabled = true
  firewall_rules = {
    azure_services = {
      name             = local.names.postgresql_firewall_azure
      start_ip_address = "0.0.0.0"
      end_ip_address   = "0.0.0.0"
    }
  }

  server_configuration = {
    extensions = {
      name   = "azure.extensions"
      config = "VECTOR"
    }
    connection_throttle = {
      name   = "connection_throttle.enable"
      config = "on"
    }
  }

  databases = {
    main = {
      name      = local.names.postgresql_database
      charset   = "UTF8"
      collation = "en_US.utf8"
    }
  }

  diagnostic_settings = {
    workspace = {
      name                  = local.names.diagnostic_setting
      workspace_resource_id = module.log_analytics_workspace.resource_id
      log_groups            = []
      log_categories        = ["PostgreSQLLogs"]
      metric_categories     = []
    }
  }

  enable_telemetry = true
  tags             = local.tags
}

module "storage_account" {
  source  = "Azure/avm-res-storage-storageaccount/azurerm"
  version = "0.10.0"

  name             = local.names.storage_account
  parent_id        = azurerm_resource_group.this.id
  location         = var.location
  account_kind     = "StorageV2"
  account_sku_name = var.storage_account_sku

  # Entra only: no account key, no SAS signed with it, no public blobs.
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false
  local_user_enabled              = false
  sftp_enabled                    = false

  # Public endpoint (private networking is deferred in the spine).
  public_network_access_enabled = true
  network_rules = {
    default_action = "Allow"
    bypass         = ["AzureServices"]
  }

  containers = { for name in var.storage_containers : name => { name = name } }

  enable_telemetry = true
  tags             = local.tags
}

# --- AI services -------------------------------------------------------------

module "foundry_account" {
  source  = "Azure/avm-res-cognitiveservices-account/azurerm"
  version = "0.11.1"

  name                          = local.names.foundry_account
  parent_id                     = azurerm_resource_group.this.id
  location                      = var.location
  kind                          = "AIServices"
  sku_name                      = var.foundry_sku
  custom_subdomain_name         = local.names.foundry_account
  local_auth_enabled            = false
  public_network_access_enabled = true
  allow_project_management      = true
  # The project is created below; naming it here keeps the account from
  # planning to drop the association on every later run.
  associated_projects = [local.names.foundry_project]
  managed_identities  = { system_assigned = true }

  cognitive_deployments = {
    for key, deployment in var.model_deployments : key => {
      name = deployment.name
      # The stack's content filter unless the deployment names its own; an
      # empty text sends no name, and the service applies its default.
      rai_policy_name        = deployment.content_filter == null ? var.model_content_filter : (deployment.content_filter == "" ? null : deployment.content_filter)
      version_upgrade_option = deployment.version_upgrade_option
      model = {
        format  = deployment.model_format
        name    = deployment.model_name
        version = deployment.model_version
      }
      scale = {
        type     = coalesce(deployment.sku, var.model_deployment_sku)
        capacity = deployment.capacity
      }
    }
  }


  enable_telemetry = true
  tags             = local.tags
}

resource "azurerm_cognitive_account_project" "this" {
  name                 = local.names.foundry_project
  cognitive_account_id = module.foundry_account.resource_id
  location             = var.location
  tags                 = local.tags

  identity {
    type = "SystemAssigned"
  }

  # Wait for the whole account module, not only the account id: the project is
  # refused with 409 while the model deployments are still being created.
  depends_on = [module.foundry_account]
}

# Connects the Foundry account, and so its project, to the one Application
# Insights instance (azure.md rule 14). The connection string is an address,
# not a credential: local authentication is off on Application Insights.
# A connection is a child resource and takes no tags.
resource "azurerm_cognitive_account_connection_api_key" "application_insights" {
  name                 = local.names.foundry_appi_connection
  cognitive_account_id = module.foundry_account.resource_id
  category             = "AppInsights"
  target               = module.application_insights.resource_id
  api_key              = module.application_insights.connection_string
  metadata = {
    ApiType    = "Azure"
    ResourceId = module.application_insights.resource_id
  }

  depends_on = [azurerm_cognitive_account_project.this]
}

# Document Intelligence and Azure AI Language.
module "ai_account" {
  source   = "Azure/avm-res-cognitiveservices-account/azurerm"
  version  = "0.11.1"
  for_each = local.ai_accounts

  name                          = each.value.name
  parent_id                     = azurerm_resource_group.this.id
  location                      = var.location
  kind                          = each.value.kind
  sku_name                      = each.value.sku_name
  custom_subdomain_name         = each.value.name
  local_auth_enabled            = false
  public_network_access_enabled = true
  managed_identities            = { system_assigned = true }


  enable_telemetry = true
  tags             = local.tags
}

module "search_service" {
  source  = "Azure/avm-res-search-searchservice/azurerm"
  version = "0.3.0"

  name                          = local.names.search_service
  resource_group_name           = azurerm_resource_group.this.name
  location                      = var.location
  sku                           = var.search_sku
  replica_count                 = 1
  partition_count               = 1
  semantic_search_sku           = var.search_semantic_sku
  local_authentication_enabled  = false
  public_network_access_enabled = true
  managed_identities            = { system_assigned = true }
  enable_telemetry              = true
  tags                          = local.tags
}

# --- Workflow engine ---------------------------------------------------------

# azapi: azurerm has no resource for Microsoft.DurableTask/schedulers.
resource "azapi_resource" "durable_task_scheduler" {
  type      = "Microsoft.DurableTask/schedulers@2026-02-01"
  name      = local.names.durable_task_scheduler
  parent_id = azurerm_resource_group.this.id
  location  = var.location
  body = {
    properties = {
      # Public endpoint; access is by Entra role only (the service has no keys).
      ipAllowlist = ["0.0.0.0/0"]
      sku = {
        name = var.durable_task_sku
      }
    }
  }
  response_export_values = ["properties.endpoint"]
  tags                   = local.tags
}

# azapi: azurerm has no resource for Microsoft.DurableTask/schedulers/taskHubs.
# A task hub is a child resource and takes no tags.
resource "azapi_resource" "durable_task_hub" {
  type      = "Microsoft.DurableTask/schedulers/taskHubs@2026-02-01"
  name      = local.names.durable_task_hub
  parent_id = azapi_resource.durable_task_scheduler.id
  body = {
    properties = {}
  }
}

# --- Cost guards -------------------------------------------------------------

# Hand-written: no Azure Verified Module is available for a consumption budget.
# A budget takes no tags.
resource "azurerm_consumption_budget_resource_group" "this" {
  name              = local.names.budget
  resource_group_id = azurerm_resource_group.this.id
  amount            = var.budget_amount
  time_grain        = var.budget_time_grain

  time_period {
    start_date = local.budget_start_date
  }

  dynamic "notification" {
    for_each = {
      actual_90    = { threshold = 90, threshold_type = "Actual" }
      actual_100   = { threshold = 100, threshold_type = "Actual" }
      actual_110   = { threshold = 110, threshold_type = "Actual" }
      forecast_110 = { threshold = 110, threshold_type = "Forecasted" }
    }

    content {
      operator       = "GreaterThanOrEqualTo"
      threshold      = notification.value.threshold
      threshold_type = notification.value.threshold_type
      contact_roles  = ["Owner"]
      contact_emails = var.alert_email_addresses
    }
  }

  lifecycle {
    # The start is set once, at creation (see locals), and Azure fills in the
    # end date; neither is changed afterwards.
    ignore_changes = [time_period]
  }
}

# Hand-written: no Azure Verified Module is available for an action group.
# Action groups are a global Azure Monitor resource; this one is the only
# resource of the stack that is not regional.
resource "azurerm_monitor_action_group" "this" {
  name                = local.names.action_group
  resource_group_name = azurerm_resource_group.this.name
  short_name          = local.names.action_group_short

  arm_role_receiver {
    name                    = "owner"
    role_id                 = local.owner_role_id
    use_common_alert_schema = true
  }

  dynamic "email_receiver" {
    for_each = { for index, address in var.alert_email_addresses : "email-${index + 1}" => address }

    content {
      name                    = email_receiver.key
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }

  tags = local.tags
}

# Hand-written: no Azure Verified Module is available for a scheduled query rule.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "log_cap" {
  name                 = local.names.alert_log_cap
  resource_group_name  = azurerm_resource_group.this.name
  location             = var.location
  display_name         = local.names.alert_log_cap
  description          = "Billable ingestion over the last 24 hours has reached ${var.log_cap_alert_ratio * 100}% of the ${var.log_daily_cap_gb} GB daily cap of the Log Analytics workspace."
  severity             = 2
  evaluation_frequency = "PT1H"
  window_duration      = "P1D"
  scopes               = [module.log_analytics_workspace.resource_id]

  criteria {
    # Usage.Quantity is in MB and the cap counts 1 GB as 1,000 MB.
    query                   = <<-QUERY
      Usage
      | where TimeGenerated > ago(24h) and IsBillable == true
      | summarize ingested_gb = sum(Quantity) / 1000.0
    QUERY
    time_aggregation_method = "Maximum"
    metric_measure_column   = "ingested_gb"
    operator                = "GreaterThanOrEqual"
    threshold               = var.log_daily_cap_gb * var.log_cap_alert_ratio

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.this.id]
  }

  tags = local.tags
}

# Diagnostic settings for the three AI accounts are written here, not through
# the modules: the modules always send a Log Analytics destination type, which
# these account kinds never return, so every plan would show a change.
resource "azurerm_monitor_diagnostic_setting" "ai_accounts" {
  for_each = merge(
    { foundry = module.foundry_account.resource_id },
    { for key, account in module.ai_account : key => account.resource_id },
  )

  name                       = local.names.diagnostic_setting
  target_resource_id         = each.value
  log_analytics_workspace_id = module.log_analytics_workspace.resource_id

  dynamic "enabled_log" {
    for_each = local.ai_account_log_categories

    content {
      category = enabled_log.value
    }
  }
}

# Original PDFs are the only place the redacted identifiers exist (AD-21).
# The owner keeps them for 30 days (decision of 2026-10-07); Azure deletes
# each one that many days after it was written. A lifecycle policy is a child
# of the storage account and takes no tags.
resource "azurerm_storage_management_policy" "originals_retention" {
  storage_account_id = module.storage_account.resource_id

  rule {
    name    = "delete-originals-after-retention"
    enabled = true

    filters {
      prefix_match = ["originals/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        delete_after_days_since_creation_greater_than = var.originals_retention_days
      }
    }
  }
}
