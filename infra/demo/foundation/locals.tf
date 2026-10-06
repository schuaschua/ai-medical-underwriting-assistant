locals {
  name_suffix         = "${var.workload}-${var.environment}-${var.region_short}"
  name_suffix_compact = "${var.workload}${var.environment}${var.region_short}"

  # The one naming block: every Azure resource name is built here.
  names = {
    resource_group             = "rg-${local.name_suffix}"
    log_analytics_workspace    = "log-${local.name_suffix}"
    application_insights       = "appi-${local.name_suffix}"
    container_registry         = "cr${local.name_suffix_compact}"
    container_apps_environment = "cae-${local.name_suffix}"
    postgresql_server          = "pgsql-${local.name_suffix}"
    postgresql_database        = var.workload
    storage_account            = "st${local.name_suffix_compact}"
    foundry_account            = "aif-${local.name_suffix}"
    foundry_project            = "proj-${local.name_suffix}"
    foundry_appi_connection    = "appi-${local.name_suffix}"
    search_service             = "srch-${local.name_suffix}"
    document_intelligence      = "di-${local.name_suffix}"
    language                   = "lang-${local.name_suffix}"
    durable_task_scheduler     = "dts-${local.name_suffix}"
    durable_task_hub           = "${var.workload}-${var.environment}"
    budget                     = "budget-${var.workload}-${var.environment}"
    action_group               = "ag-${local.name_suffix}"
    action_group_short         = "${var.workload}-${var.environment}"
    alert_log_cap              = "ar-${local.name_suffix}-log-cap"
    deploy_identity            = "id-${local.name_suffix}-deploy"
    postgresql_firewall_azure  = "AllowAllAzureServicesAndResourcesWithinAzureIps"
    diagnostic_setting         = "diag-${local.name_suffix}"
  }

  runtime_identity_names = { for role in var.runtime_identity_roles : role => "id-${local.name_suffix}-${role}" }

  # The six required tags, passed to every module and resource.
  tags = {
    workload  = var.workload
    env       = var.environment
    owner     = var.owner
    managedby = "terraform"
    datatype  = var.data_classification
    repo      = var.repository_url
  }

  subscription_id   = data.azurerm_client_config.current.subscription_id
  tenant_id         = data.azurerm_client_config.current.tenant_id
  resource_group_id = "/subscriptions/${local.subscription_id}/resourceGroups/${local.names.resource_group}"

  # The single-service AI accounts. Azure AI Language document PII needs a
  # single-service Language resource, which is kind TextAnalytics.
  ai_accounts = {
    document_intelligence = {
      name     = local.names.document_intelligence
      kind     = "FormRecognizer"
      sku_name = var.document_intelligence_sku
    }
    language = {
      name     = local.names.language
      kind     = "TextAnalytics"
      sku_name = var.language_sku
    }
  }

  # Diagnostic settings carry only the log categories named here and no
  # metrics, to stay inside the workspace's daily cap.
  ai_account_log_categories = ["Audit", "RequestResponse"]

  # The built-in Owner role: budget and alert mail goes to whoever holds it,
  # so no address is written in the repository.
  owner_role_id = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"

  # Azure accepts a new budget only if it starts in the current month, so
  # without a supplied date it starts on the first day of the month of the plan.
  # The budget ignores later changes to its time period.
  budget_start_date = coalesce(var.budget_start_date, formatdate("YYYY-MM-01'T'00:00:00Z", plantimestamp()))

  # Entra administrators of PostgreSQL: the deployment identity, plus any
  # operators passed in. An extra administrator's database role is named after
  # its object id.
  postgresql_administrators = merge(
    {
      deploy = {
        tenant_id      = local.tenant_id
        object_id      = var.deploy_principal_id
        principal_name = local.names.deploy_identity
        principal_type = "ServicePrincipal"
      }
    },
    {
      for object_id in var.postgresql_extra_admin_object_ids : object_id => {
        tenant_id      = local.tenant_id
        object_id      = object_id
        principal_name = object_id
        principal_type = "User"
      }
    }
  )
}
