# --- Runtime roles -----------------------------------------------------------

# The web identity holds exactly these two roles (azure.md, "Runtime roles").
# Hand-written: a role assignment on a resource another stack owns has no
# Azure Verified Module of its own to go through.
resource "azurerm_role_assignment" "web_acr_pull" {
  scope                = local.foundation.container_registry_id
  role_definition_name = "AcrPull"
  principal_id         = local.web_identity.principal_id
  # Stated, not looked up: the deployment identity may assign roles to service
  # principals only, and has no directory rights to look the type up.
  principal_type = "ServicePrincipal"
}

resource "azurerm_role_assignment" "web_metrics_publisher" {
  scope                = local.foundation.application_insights_id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = local.web_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# A new role assignment is not honoured at once. Without this wait the first
# revision can be refused its image and fail to start. It runs again only when
# the assignment itself is made again.
resource "time_sleep" "web_acr_pull_propagation" {
  create_duration = var.role_propagation_wait

  triggers = {
    role_assignment_id = azurerm_role_assignment.web_acr_pull.id
  }
}

# --- web ---------------------------------------------------------------------

# The only service reachable from the internet (spine AD-18). There is no
# sign-in at the edge: no authentication is configured on the app (AD-9).
module "web" {
  source  = "Azure/avm-res-app-containerapp/azurerm"
  version = "0.9.0"

  name                                  = local.names.web
  resource_group_name                   = local.foundation.resource_group_name
  resource_group_id                     = local.foundation.resource_group_id
  location                              = local.foundation.location
  container_app_environment_resource_id = local.foundation.container_apps_environment_id
  workload_profile_name                 = local.workload_profile
  revision_mode                         = "Single"

  managed_identities = {
    user_assigned_resource_ids = [local.web_identity.id]
  }

  # Images are pulled with the service identity; the registry has no admin user.
  registries = [{
    server   = local.foundation.container_registry_login_server
    identity = local.web_identity.id
  }]

  # An address, not a credential (Application Insights accepts Entra tokens
  # only), but it is kept out of the template and the logs all the same.
  secrets = {
    appi = {
      name  = local.appi_secret_name
      value = local.foundation.application_insights_connection_string
    }
  }

  ingress = {
    external_enabled           = true
    allow_insecure_connections = false
    target_port                = var.web_port
    transport                  = "auto"
    traffic_weight = [{
      latest_revision = true
      percentage      = 100
    }]
  }

  # Service invocation only (AD-3). The sidecar's request limit is raised from
  # its 4 MB default so a 10 MB upload passes.
  dapr = {
    enabled               = true
    app_id                = "web"
    app_port              = var.web_port
    app_protocol          = "http"
    http_max_request_size = var.dapr_http_max_request_size_mb
  }

  template = {
    min_replicas = var.min_replicas
    max_replicas = local.max_replicas

    containers = [{
      name   = "web"
      image  = "${local.image_repositories.web}:${var.image_tag}"
      cpu    = local.container_cpu
      memory = local.container_memory

      env = [
        { name = "WEB_HOST", value = "0.0.0.0" },
        { name = "WEB_PORT", value = tostring(var.web_port) },
        { name = "WEB_AZURE_CLIENT_ID", value = local.web_identity.client_id },
        { name = "WEB_OTEL_SAMPLING_RATIO", value = tostring(var.otel_sampling_ratio) },
        { name = "WEB_APPLICATIONINSIGHTS_CONNECTION_STRING", secret_name = local.appi_secret_name },
      ]

      # azure.md rule 22. web has no database, so all three probes ask the
      # process itself.
      startup_probes = [{
        transport               = "HTTP"
        port                    = var.web_port
        path                    = var.web_health_path
        interval_seconds        = 5
        timeout                 = 2
        failure_count_threshold = 10
      }]
      readiness_probes = [{
        transport               = "HTTP"
        port                    = var.web_port
        path                    = var.web_health_path
        interval_seconds        = 10
        timeout                 = 2
        failure_count_threshold = 3
        success_count_threshold = 1
      }]
      liveness_probes = [{
        transport               = "HTTP"
        port                    = var.web_port
        path                    = var.web_health_path
        interval_seconds        = 30
        timeout                 = 2
        failure_count_threshold = 3
      }]
    }]
  }

  enable_telemetry = true
  tags             = local.tags

  # The first revision cannot pull its image until the identity holds AcrPull
  # and Azure has spread that assignment.
  depends_on = [
    time_sleep.web_acr_pull_propagation,
    azurerm_role_assignment.web_metrics_publisher,
  ]
}
