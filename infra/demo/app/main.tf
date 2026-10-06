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

# The intake identity holds these roles and no others (azure.md, "Runtime
# roles"). Its PostgreSQL role is not an Azure role: the database bootstrap
# creates it (infra/bootstrap/README.md). Cognitive Services User on Azure AI
# Language arrives with redaction (story 1.7).
resource "azurerm_role_assignment" "intake_acr_pull" {
  scope                = local.foundation.container_registry_id
  role_definition_name = "AcrPull"
  principal_id         = local.intake_identity.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "intake_metrics_publisher" {
  scope                = local.foundation.application_insights_id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = local.intake_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# Scoped to the two containers, not to the account (azure.md rule 9).
resource "azurerm_role_assignment" "intake_blob_contributor" {
  for_each = local.intake_blob_containers

  scope                = local.foundation.storage_container_ids[each.key]
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = local.intake_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# As for web: a new role assignment is not honoured at once. intake waits for
# all of its own, so its first revision can pull its image, write blobs and
# send telemetry. The wait runs again only when one of them is made again.
resource "time_sleep" "intake_role_propagation" {
  create_duration = var.role_propagation_wait

  triggers = {
    acr_pull_id          = azurerm_role_assignment.intake_acr_pull.id
    metrics_publisher_id = azurerm_role_assignment.intake_metrics_publisher.id
    blob_contributor_ids = join(",", [for name in sort(tolist(local.intake_blob_containers)) : azurerm_role_assignment.intake_blob_contributor[name].id])
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

# --- intake ------------------------------------------------------------------

# Internal ingress only (spine AD-18): reachable from inside the environment,
# and called only through Dapr service invocation (AD-3).
module "intake" {
  source  = "Azure/avm-res-app-containerapp/azurerm"
  version = "0.9.0"

  name                                  = local.names.intake
  resource_group_name                   = local.foundation.resource_group_name
  resource_group_id                     = local.foundation.resource_group_id
  location                              = local.foundation.location
  container_app_environment_resource_id = local.foundation.container_apps_environment_id
  workload_profile_name                 = local.workload_profile
  revision_mode                         = "Single"

  managed_identities = {
    user_assigned_resource_ids = [local.intake_identity.id]
  }

  registries = [{
    server   = local.foundation.container_registry_login_server
    identity = local.intake_identity.id
  }]

  secrets = {
    appi = {
      name  = local.appi_secret_name
      value = local.foundation.application_insights_connection_string
    }
  }

  ingress = {
    external_enabled           = false
    allow_insecure_connections = false
    target_port                = var.intake_port
    transport                  = "auto"
    traffic_weight = [{
      latest_revision = true
      percentage      = 100
    }]
  }

  # The upload arrives here from web's sidecar, so this sidecar's request
  # limit is raised as well.
  dapr = {
    enabled               = true
    app_id                = "intake"
    app_port              = var.intake_port
    app_protocol          = "http"
    http_max_request_size = var.dapr_http_max_request_size_mb
  }

  template = {
    min_replicas = var.min_replicas
    max_replicas = local.max_replicas

    containers = [{
      name   = "intake"
      image  = "${local.image_repositories.intake}:${var.image_tag}"
      cpu    = local.container_cpu
      memory = local.container_memory

      # No password and no storage key: the database and Blob Storage are
      # reached with the service identity (azure.md rule 7).
      env = [
        { name = "INTAKE_HOST", value = "0.0.0.0" },
        { name = "INTAKE_PORT", value = tostring(var.intake_port) },
        { name = "INTAKE_AZURE_CLIENT_ID", value = local.intake_identity.client_id },
        { name = "INTAKE_OTEL_SAMPLING_RATIO", value = tostring(var.otel_sampling_ratio) },
        { name = "INTAKE_APPLICATIONINSIGHTS_CONNECTION_STRING", secret_name = local.appi_secret_name },
        { name = "INTAKE_BLOB_ACCOUNT_URL", value = local.foundation.storage_blob_endpoint },
        { name = "INTAKE_DATABASE_HOST", value = local.foundation.postgresql_fqdn },
        { name = "INTAKE_DATABASE_NAME", value = local.foundation.postgresql_database_name },
        { name = "INTAKE_DATABASE_USER", value = local.intake_identity.name },
        { name = "INTAKE_DATABASE_ENTRA_AUTH", value = "true" },
      ]

      # azure.md rule 22. Startup and liveness ask the process; readiness
      # also asks the database, and fails unless its schema revision equals
      # the migration head bundled in the image.
      startup_probes = [{
        transport               = "HTTP"
        port                    = var.intake_port
        path                    = var.intake_health_path
        interval_seconds        = 5
        timeout                 = 2
        failure_count_threshold = 10
      }]
      readiness_probes = [{
        transport               = "HTTP"
        port                    = var.intake_port
        path                    = var.intake_ready_path
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
        success_count_threshold = 1
      }]
      liveness_probes = [{
        transport               = "HTTP"
        port                    = var.intake_port
        path                    = var.intake_health_path
        interval_seconds        = 30
        timeout                 = 2
        failure_count_threshold = 3
      }]
    }]
  }

  enable_telemetry = true
  tags             = local.tags

  # The first revision needs every one of the identity's role assignments,
  # and Azure has to have spread them.
  depends_on = [
    time_sleep.intake_role_propagation,
  ]
}
