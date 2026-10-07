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
# creates it (infra/bootstrap/README.md).
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

# Redaction (spine AD-21): intake submits the job to Azure AI Language with its
# own identity. There is no key.
resource "azurerm_role_assignment" "intake_language_user" {
  scope                = local.foundation.language_id
  role_definition_name = "Cognitive Services User"
  principal_id         = local.intake_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# Azure AI Language reads the original and writes the redacted PDF and its
# result file itself, with its own identity: it may read `originals` and write
# `cases`, each scoped to the one container (azure.md rule 9). It is the only
# reader of `originals`.
resource "azurerm_role_assignment" "language_originals_reader" {
  scope                = local.foundation.storage_container_ids["originals"]
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = local.foundation.language_principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "language_cases_contributor" {
  scope                = local.foundation.storage_container_ids["cases"]
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = local.foundation.language_principal_id
  principal_type       = "ServicePrincipal"
}

# As for web: a new role assignment is not honoured at once. intake waits for
# all of its own and for Language's two, so its first revision can pull its
# image, write blobs, send telemetry and have a document redacted. The wait
# runs again only when one of them is made again.
resource "time_sleep" "intake_role_propagation" {
  create_duration = var.role_propagation_wait

  triggers = {
    acr_pull_id                   = azurerm_role_assignment.intake_acr_pull.id
    metrics_publisher_id          = azurerm_role_assignment.intake_metrics_publisher.id
    blob_contributor_ids          = join(",", [for name in sort(tolist(local.intake_blob_containers)) : azurerm_role_assignment.intake_blob_contributor[name].id])
    language_user_id              = azurerm_role_assignment.intake_language_user.id
    language_originals_reader_id  = azurerm_role_assignment.language_originals_reader.id
    language_cases_contributor_id = azurerm_role_assignment.language_cases_contributor.id
  }
}

# The workflow identity holds these roles and no others (azure.md, "Runtime
# roles"). Its PostgreSQL role is not an Azure role: the database bootstrap
# creates it (infra/bootstrap/README.md).
resource "azurerm_role_assignment" "workflow_acr_pull" {
  scope                = local.foundation.container_registry_id
  role_definition_name = "AcrPull"
  principal_id         = local.workflow_identity.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "workflow_metrics_publisher" {
  scope                = local.foundation.application_insights_id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = local.workflow_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# Scoped to the one task hub, not to the scheduler (azure.md rule 9): the
# service starts its orchestrations there and its worker runs them (spine AD-5).
resource "azurerm_role_assignment" "workflow_durable_task_contributor" {
  scope                = local.foundation.durable_task_hub_id
  role_definition_name = "Durable Task Data Contributor"
  principal_id         = local.workflow_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# As for intake: workflow waits for all of its own role assignments, so its
# first revision can pull its image, reach the task hub and send telemetry.
resource "time_sleep" "workflow_role_propagation" {
  create_duration = var.role_propagation_wait

  triggers = {
    acr_pull_id                 = azurerm_role_assignment.workflow_acr_pull.id
    metrics_publisher_id        = azurerm_role_assignment.workflow_metrics_publisher.id
    durable_task_contributor_id = azurerm_role_assignment.workflow_durable_task_contributor.id
  }
}

# The classification identity holds these roles and no others (azure.md,
# "Runtime roles"). Its PostgreSQL role is not an Azure role: the database
# bootstrap creates it (infra/bootstrap/README.md).
resource "azurerm_role_assignment" "classification_acr_pull" {
  scope                = local.foundation.container_registry_id
  role_definition_name = "AcrPull"
  principal_id         = local.classification_identity.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "classification_metrics_publisher" {
  scope                = local.foundation.application_insights_id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = local.classification_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# Scoped to the Foundry project, not to the account (azure.md rule 9): the
# service calls the shared chat deployment with its own identity (spine
# AD-16). There is no key.
resource "azurerm_role_assignment" "classification_foundry_user" {
  scope                = local.foundation.foundry_project_id
  role_definition_name = "Foundry User"
  principal_id         = local.classification_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# As for intake: classification waits for all of its own role assignments, so
# its first revision can pull its image, call the model and send telemetry.
resource "time_sleep" "classification_role_propagation" {
  create_duration = var.role_propagation_wait

  triggers = {
    acr_pull_id          = azurerm_role_assignment.classification_acr_pull.id
    metrics_publisher_id = azurerm_role_assignment.classification_metrics_publisher.id
    foundry_user_id      = azurerm_role_assignment.classification_foundry_user.id
  }
}

# The extraction identity holds these roles and no others (azure.md,
# "Runtime roles"). It reads pages from intake through Dapr, so it holds no
# role on storage. Its PostgreSQL role is not an Azure role: the database
# bootstrap creates it (infra/bootstrap/README.md, section 8).
resource "azurerm_role_assignment" "extraction_acr_pull" {
  scope                = local.foundation.container_registry_id
  role_definition_name = "AcrPull"
  principal_id         = local.extraction_identity.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "extraction_metrics_publisher" {
  scope                = local.foundation.application_insights_id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = local.extraction_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# Scoped to the Foundry project, not to the account (azure.md rule 9): the
# service calls the shared chat deployment with its own identity (spine
# AD-16). There is no key.
resource "azurerm_role_assignment" "extraction_foundry_user" {
  scope                = local.foundation.foundry_project_id
  role_definition_name = "Foundry User"
  principal_id         = local.extraction_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# As for intake: extraction waits for all of its own role assignments, so its
# first revision can pull its image, call the model and send telemetry.
resource "time_sleep" "extraction_role_propagation" {
  create_duration = var.role_propagation_wait

  triggers = {
    acr_pull_id          = azurerm_role_assignment.extraction_acr_pull.id
    metrics_publisher_id = azurerm_role_assignment.extraction_metrics_publisher.id
    foundry_user_id      = azurerm_role_assignment.extraction_foundry_user.id
  }
}

# The verdict identity holds these roles and no others (azure.md, "Runtime
# roles"). It reads facts from extraction and rules from retrieval through
# Dapr, so it holds no role on storage or on a search service. Its PostgreSQL
# role is not an Azure role: the database bootstrap creates it
# (infra/bootstrap/README.md, section 9).
resource "azurerm_role_assignment" "verdict_acr_pull" {
  scope                = local.foundation.container_registry_id
  role_definition_name = "AcrPull"
  principal_id         = local.verdict_identity.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "verdict_metrics_publisher" {
  scope                = local.foundation.application_insights_id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = local.verdict_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# Scoped to the Foundry project, not to the account (azure.md rule 9): the
# agent runs on the shared chat deployment with the service's own identity
# (spine AD-16). There is no key.
resource "azurerm_role_assignment" "verdict_foundry_user" {
  scope                = local.foundation.foundry_project_id
  role_definition_name = "Foundry User"
  principal_id         = local.verdict_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# As for intake: verdict waits for all of its own role assignments, so its
# first revision can pull its image, call the model and send telemetry.
resource "time_sleep" "verdict_role_propagation" {
  create_duration = var.role_propagation_wait

  triggers = {
    acr_pull_id          = azurerm_role_assignment.verdict_acr_pull.id
    metrics_publisher_id = azurerm_role_assignment.verdict_metrics_publisher.id
    foundry_user_id      = azurerm_role_assignment.verdict_foundry_user.id
  }
}

# The retrieval identity holds these roles and no others (azure.md, "Runtime
# roles"); the service and its ingestion job share it (spine AD-12). Its
# PostgreSQL role is not an Azure role: the database bootstrap creates it
# (infra/bootstrap/README.md). The two Azure AI Search roles are below
# (story 3.3, retrieval row r5).
resource "azurerm_role_assignment" "retrieval_acr_pull" {
  scope                = local.foundation.container_registry_id
  role_definition_name = "AcrPull"
  principal_id         = local.retrieval_identity.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "retrieval_metrics_publisher" {
  scope                = local.foundation.application_insights_id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = local.retrieval_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# Scoped to the Foundry project, not to the account (azure.md rule 9): the
# chat deployment writes each chunk's context line and the embedding
# deployment its vector (spine AD-12, AD-16). There is no key.
resource "azurerm_role_assignment" "retrieval_foundry_user" {
  scope                = local.foundation.foundry_project_id
  role_definition_name = "Foundry User"
  principal_id         = local.retrieval_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# The ingestion job has Document Intelligence's layout model parse the manual,
# with the service identity. There is no key.
resource "azurerm_role_assignment" "retrieval_document_intelligence_user" {
  scope                = local.foundation.document_intelligence_id
  role_definition_name = "Cognitive Services User"
  principal_id         = local.retrieval_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# Scoped to the one container, not to the account (azure.md rule 9), and
# read-only: the job reads the manual PDF there and nothing of ours writes to
# it in Azure (an operator uploads the manual). Document Intelligence's own
# identity holds no role on the container: the job sends it the manual's bytes.
resource "azurerm_role_assignment" "retrieval_manual_reader" {
  scope                = local.foundation.storage_container_ids[local.retrieval_manual_container]
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = local.retrieval_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# Azure AI Search (spine AD-11, row r5). The ingestion job creates the index
# of the manual's chunks if it is missing (Search Service Contributor: index
# definitions) and uploads, lists and deletes its documents; the service
# queries them (Search Index Data Contributor: documents). Scoped to the one
# search service (azure.md rule 9). The service has key access off: there is
# no admin key and no query key.
resource "azurerm_role_assignment" "retrieval_search_service_contributor" {
  scope                = local.foundation.search_service_id
  role_definition_name = "Search Service Contributor"
  principal_id         = local.retrieval_identity.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "retrieval_search_index_data_contributor" {
  scope                = local.foundation.search_service_id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = local.retrieval_identity.principal_id
  principal_type       = "ServicePrincipal"
}

# Agentic retrieval (spine AD-11 and AD-16, row r6; azure.md, "Runtime
# roles"). The search service itself calls the Foundry deployments: the chat
# deployment plans the queries of a retrieve request, and the embedding
# deployment is the index's vectorizer for the vector side of those queries.
# It signs in with its own identity, which the foundation stack gives it;
# there is no key. The role is the one Azure AI Search asks for, on the
# account that holds both deployments. retrieval's own two roles above let
# its job create the knowledge source and the knowledge base and its service
# send the retrieve requests.
resource "azurerm_role_assignment" "search_foundry_user" {
  scope                = local.foundation.foundry_account_id
  role_definition_name = "Cognitive Services User"
  principal_id         = local.foundation.search_principal_id
  principal_type       = "ServicePrincipal"
}

# As for intake: retrieval and its job wait for all of these, so the first
# revision can pull its image and send telemetry, and the first run of the
# job can read the manual, have it parsed, call the models, load the search
# index and make the knowledge base, which the search service can then plan
# with.
resource "time_sleep" "retrieval_role_propagation" {
  create_duration = var.role_propagation_wait

  triggers = {
    acr_pull_id                   = azurerm_role_assignment.retrieval_acr_pull.id
    metrics_publisher_id          = azurerm_role_assignment.retrieval_metrics_publisher.id
    foundry_user_id               = azurerm_role_assignment.retrieval_foundry_user.id
    document_intelligence_user_id = azurerm_role_assignment.retrieval_document_intelligence_user.id
    manual_reader_id              = azurerm_role_assignment.retrieval_manual_reader.id
    search_service_contributor_id = azurerm_role_assignment.retrieval_search_service_contributor.id
    search_index_data_id          = azurerm_role_assignment.retrieval_search_index_data_contributor.id
    search_foundry_user_id        = azurerm_role_assignment.search_foundry_user.id
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

      # No password, no storage key and no Language key: the database, Blob
      # Storage and Azure AI Language are reached with the service identity
      # (azure.md rule 7). The Language endpoint is the real account's; the
      # local stand-in exists only on a developer machine.
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
        { name = "INTAKE_LANGUAGE_ENDPOINT", value = local.foundation.language_endpoint },
        { name = "INTAKE_LANGUAGE_ENTRA_AUTH", value = "true" },
        { name = "INTAKE_LANGUAGE_API_VERSION", value = var.language_api_version },
        { name = "INTAKE_REDACTION_CATEGORIES", value = jsonencode(var.redaction_categories) },
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

# --- workflow ----------------------------------------------------------------

# Internal ingress only (spine AD-18): reachable from inside the environment,
# and called only by web through Dapr service invocation (AD-3). It is held at
# exactly one replica, whatever min_replicas says for the others. It commands
# the stage services through its own sidecar (AD-3), redaction on intake
# first (AD-21).
module "workflow" {
  source  = "Azure/avm-res-app-containerapp/azurerm"
  version = "0.9.0"

  name                                  = local.names.workflow
  resource_group_name                   = local.foundation.resource_group_name
  resource_group_id                     = local.foundation.resource_group_id
  location                              = local.foundation.location
  container_app_environment_resource_id = local.foundation.container_apps_environment_id
  workload_profile_name                 = local.workload_profile
  revision_mode                         = "Single"

  managed_identities = {
    user_assigned_resource_ids = [local.workflow_identity.id]
  }

  registries = [{
    server   = local.foundation.container_registry_login_server
    identity = local.workflow_identity.id
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
    target_port                = var.workflow_port
    transport                  = "auto"
    traffic_weight = [{
      latest_revision = true
      percentage      = 100
    }]
  }

  # Service invocation only (AD-3). Its calls carry ids and small JSON
  # bodies, so the sidecar keeps its default request limit.
  dapr = {
    enabled      = true
    app_id       = "workflow"
    app_port     = var.workflow_port
    app_protocol = "http"
  }

  template = {
    min_replicas = local.workflow_replicas
    max_replicas = local.workflow_replicas

    containers = [{
      name   = "workflow"
      image  = "${local.image_repositories.workflow}:${var.image_tag}"
      cpu    = local.container_cpu
      memory = local.container_memory

      # No password and no key: the database and the Durable Task Scheduler
      # are reached with the service identity (azure.md rule 7).
      env = [
        { name = "WORKFLOW_HOST", value = "0.0.0.0" },
        { name = "WORKFLOW_PORT", value = tostring(var.workflow_port) },
        { name = "WORKFLOW_AZURE_CLIENT_ID", value = local.workflow_identity.client_id },
        { name = "WORKFLOW_OTEL_SAMPLING_RATIO", value = tostring(var.otel_sampling_ratio) },
        { name = "WORKFLOW_APPLICATIONINSIGHTS_CONNECTION_STRING", secret_name = local.appi_secret_name },
        { name = "WORKFLOW_DATABASE_HOST", value = local.foundation.postgresql_fqdn },
        { name = "WORKFLOW_DATABASE_NAME", value = local.foundation.postgresql_database_name },
        { name = "WORKFLOW_DATABASE_USER", value = local.workflow_identity.name },
        { name = "WORKFLOW_DATABASE_ENTRA_AUTH", value = "true" },
        { name = "WORKFLOW_SCHEDULER_ENDPOINT", value = local.foundation.durable_task_scheduler_endpoint },
        { name = "WORKFLOW_SCHEDULER_TASK_HUB", value = local.foundation.durable_task_hub_name },
        { name = "WORKFLOW_SCHEDULER_ENTRA_AUTH", value = "true" },
        { name = "WORKFLOW_DAPR_HTTP_PORT", value = tostring(var.dapr_http_port) },
        # Spine AD-7: only workflow gets the gate's threshold.
        { name = "WORKFLOW_GATE_THRESHOLD", value = tostring(var.gate_threshold) },
        # Spine AD-11: the ladder rows a case may run with. `verdict` is
        # given the same list, and `retrieval` answers r5 because it is
        # given the search service's endpoint, and r6 because it is given
        # that endpoint and the chat deployment (local.retrieval_env).
        { name = "WORKFLOW_AVAILABLE_RETRIEVER_CONFIGS", value = jsonencode(var.available_retriever_configs) },
      ]

      # azure.md rule 22. Startup and liveness ask the process; readiness
      # also asks the database, and fails unless its schema revision equals
      # the migration head bundled in the image.
      startup_probes = [{
        transport               = "HTTP"
        port                    = var.workflow_port
        path                    = var.workflow_health_path
        interval_seconds        = 5
        timeout                 = 2
        failure_count_threshold = 10
      }]
      readiness_probes = [{
        transport               = "HTTP"
        port                    = var.workflow_port
        path                    = var.workflow_ready_path
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
        success_count_threshold = 1
      }]
      liveness_probes = [{
        transport               = "HTTP"
        port                    = var.workflow_port
        path                    = var.workflow_health_path
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
    time_sleep.workflow_role_propagation,
  ]
}

# --- classification ------------------------------------------------------------

# Internal ingress only (spine AD-18): reachable from inside the environment,
# and called only through Dapr service invocation (AD-3), by workflow (the
# classify command) and by web (reads). It reads each page from intake
# through its own sidecar, and is held at exactly one replica.
module "classification" {
  source  = "Azure/avm-res-app-containerapp/azurerm"
  version = "0.9.0"

  name                                  = local.names.classification
  resource_group_name                   = local.foundation.resource_group_name
  resource_group_id                     = local.foundation.resource_group_id
  location                              = local.foundation.location
  container_app_environment_resource_id = local.foundation.container_apps_environment_id
  workload_profile_name                 = local.workload_profile
  revision_mode                         = "Single"

  managed_identities = {
    user_assigned_resource_ids = [local.classification_identity.id]
  }

  registries = [{
    server   = local.foundation.container_registry_login_server
    identity = local.classification_identity.id
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
    target_port                = var.classification_port
    transport                  = "auto"
    traffic_weight = [{
      latest_revision = true
      percentage      = 100
    }]
  }

  # Service invocation only (AD-3). Commands carry ids, and a page's text and
  # thumbnail are well under the sidecar's default request limit.
  dapr = {
    enabled      = true
    app_id       = "classification"
    app_port     = var.classification_port
    app_protocol = "http"
  }

  template = {
    min_replicas = local.classification_replicas
    max_replicas = local.classification_replicas

    containers = [{
      name   = "classification"
      image  = "${local.image_repositories.classification}:${var.image_tag}"
      cpu    = local.container_cpu
      memory = local.container_memory

      # No password and no model key: the database and the chat deployment
      # are reached with the service identity (azure.md rule 7). The model
      # endpoint is the Foundry account's and the deployment name comes from
      # the foundation stack (spine AD-16); the local stand-in exists only on
      # a developer machine.
      env = [
        { name = "CLASSIFICATION_HOST", value = "0.0.0.0" },
        { name = "CLASSIFICATION_PORT", value = tostring(var.classification_port) },
        { name = "CLASSIFICATION_AZURE_CLIENT_ID", value = local.classification_identity.client_id },
        { name = "CLASSIFICATION_OTEL_SAMPLING_RATIO", value = tostring(var.otel_sampling_ratio) },
        { name = "CLASSIFICATION_APPLICATIONINSIGHTS_CONNECTION_STRING", secret_name = local.appi_secret_name },
        { name = "CLASSIFICATION_DATABASE_HOST", value = local.foundation.postgresql_fqdn },
        { name = "CLASSIFICATION_DATABASE_NAME", value = local.foundation.postgresql_database_name },
        { name = "CLASSIFICATION_DATABASE_USER", value = local.classification_identity.name },
        { name = "CLASSIFICATION_DATABASE_ENTRA_AUTH", value = "true" },
        { name = "CLASSIFICATION_DAPR_HTTP_PORT", value = tostring(var.dapr_http_port) },
        { name = "CLASSIFICATION_MODEL_ENDPOINT", value = local.foundation.foundry_endpoint },
        { name = "CLASSIFICATION_MODEL_ENTRA_AUTH", value = "true" },
        { name = "CLASSIFICATION_CHAT_DEPLOYMENT", value = local.foundation.model_deployment_names["chat"] },
        { name = "CLASSIFICATION_CLASSIFIER_RUNS", value = tostring(var.classifier_runs) },
        { name = "CLASSIFICATION_CLASSIFIER_MAX_CONCURRENT_RUNS", value = tostring(var.classifier_max_concurrent_runs) },
        { name = "CLASSIFICATION_MODEL_MAX_CONCURRENT_CALLS", value = tostring(var.model_max_concurrent_calls) },
        { name = "CLASSIFICATION_MODEL_MAX_RETRIES", value = tostring(var.model_max_retries) },
      ]

      # azure.md rule 22. Startup and liveness ask the process; readiness
      # also asks the database, and fails unless its schema revision equals
      # the migration head bundled in the image.
      startup_probes = [{
        transport               = "HTTP"
        port                    = var.classification_port
        path                    = var.classification_health_path
        interval_seconds        = 5
        timeout                 = 2
        failure_count_threshold = 10
      }]
      readiness_probes = [{
        transport               = "HTTP"
        port                    = var.classification_port
        path                    = var.classification_ready_path
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
        success_count_threshold = 1
      }]
      liveness_probes = [{
        transport               = "HTTP"
        port                    = var.classification_port
        path                    = var.classification_health_path
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
    time_sleep.classification_role_propagation,
  ]
}

# --- extraction ------------------------------------------------------------------

# Internal ingress only (spine AD-18): reachable from inside the environment,
# and called only through Dapr service invocation (AD-3), by workflow (the
# extract command), by verdict (reads) and, later, by web. It reads each
# page's text from intake through its own sidecar, and is held at exactly one
# replica.
module "extraction" {
  source  = "Azure/avm-res-app-containerapp/azurerm"
  version = "0.9.0"

  name                                  = local.names.extraction
  resource_group_name                   = local.foundation.resource_group_name
  resource_group_id                     = local.foundation.resource_group_id
  location                              = local.foundation.location
  container_app_environment_resource_id = local.foundation.container_apps_environment_id
  workload_profile_name                 = local.workload_profile
  revision_mode                         = "Single"

  managed_identities = {
    user_assigned_resource_ids = [local.extraction_identity.id]
  }

  registries = [{
    server   = local.foundation.container_registry_login_server
    identity = local.extraction_identity.id
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
    target_port                = var.extraction_port
    transport                  = "auto"
    traffic_weight = [{
      latest_revision = true
      percentage      = 100
    }]
  }

  # Service invocation only (AD-3). Commands carry ids, and a page's text is
  # well under the sidecar's default request limit.
  dapr = {
    enabled      = true
    app_id       = "extraction"
    app_port     = var.extraction_port
    app_protocol = "http"
  }

  template = {
    min_replicas = local.extraction_replicas
    max_replicas = local.extraction_replicas

    containers = [{
      name   = "extraction"
      image  = "${local.image_repositories.extraction}:${var.image_tag}"
      cpu    = local.container_cpu
      memory = local.container_memory

      # No password and no model key: the database and the chat deployment
      # are reached with the service identity (azure.md rule 7). The model
      # endpoint is the Foundry account's and the deployment name comes from
      # the foundation stack (spine AD-16); the local stand-in exists only on
      # a developer machine.
      env = [
        { name = "EXTRACTION_HOST", value = "0.0.0.0" },
        { name = "EXTRACTION_PORT", value = tostring(var.extraction_port) },
        { name = "EXTRACTION_AZURE_CLIENT_ID", value = local.extraction_identity.client_id },
        { name = "EXTRACTION_OTEL_SAMPLING_RATIO", value = tostring(var.otel_sampling_ratio) },
        { name = "EXTRACTION_APPLICATIONINSIGHTS_CONNECTION_STRING", secret_name = local.appi_secret_name },
        { name = "EXTRACTION_DATABASE_HOST", value = local.foundation.postgresql_fqdn },
        { name = "EXTRACTION_DATABASE_NAME", value = local.foundation.postgresql_database_name },
        { name = "EXTRACTION_DATABASE_USER", value = local.extraction_identity.name },
        { name = "EXTRACTION_DATABASE_ENTRA_AUTH", value = "true" },
        { name = "EXTRACTION_DAPR_HTTP_PORT", value = tostring(var.dapr_http_port) },
        { name = "EXTRACTION_MODEL_ENDPOINT", value = local.foundation.foundry_endpoint },
        { name = "EXTRACTION_MODEL_ENTRA_AUTH", value = "true" },
        { name = "EXTRACTION_CHAT_DEPLOYMENT", value = local.foundation.model_deployment_names["chat"] },
        { name = "EXTRACTION_MODEL_MAX_CONCURRENT_CALLS", value = tostring(var.extraction_model_max_concurrent_calls) },
        { name = "EXTRACTION_MODEL_MAX_RETRIES", value = tostring(var.model_max_retries) },
      ]

      # azure.md rule 22. Startup and liveness ask the process; readiness
      # also asks the database, and fails unless its schema revision equals
      # the migration head bundled in the image.
      startup_probes = [{
        transport               = "HTTP"
        port                    = var.extraction_port
        path                    = var.extraction_health_path
        interval_seconds        = 5
        timeout                 = 2
        failure_count_threshold = 10
      }]
      readiness_probes = [{
        transport               = "HTTP"
        port                    = var.extraction_port
        path                    = var.extraction_ready_path
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
        success_count_threshold = 1
      }]
      liveness_probes = [{
        transport               = "HTTP"
        port                    = var.extraction_port
        path                    = var.extraction_health_path
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
    time_sleep.extraction_role_propagation,
  ]
}

# --- verdict ---------------------------------------------------------------------

# Internal ingress only (spine AD-18): reachable from inside the environment,
# and called only through Dapr service invocation (AD-3), by workflow (the
# verdict run command) and, later, by web (reads). It reads the case's facts
# from extraction, and searches and reads the manual's rules at retrieval,
# through its own sidecar, and is held at exactly one replica.
module "verdict" {
  source  = "Azure/avm-res-app-containerapp/azurerm"
  version = "0.9.0"

  name                                  = local.names.verdict
  resource_group_name                   = local.foundation.resource_group_name
  resource_group_id                     = local.foundation.resource_group_id
  location                              = local.foundation.location
  container_app_environment_resource_id = local.foundation.container_apps_environment_id
  workload_profile_name                 = local.workload_profile
  revision_mode                         = "Single"

  managed_identities = {
    user_assigned_resource_ids = [local.verdict_identity.id]
  }

  registries = [{
    server   = local.foundation.container_registry_login_server
    identity = local.verdict_identity.id
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
    target_port                = var.verdict_port
    transport                  = "auto"
    traffic_weight = [{
      latest_revision = true
      percentage      = 100
    }]
  }

  # Service invocation only (AD-3). Commands carry ids, and a case's facts
  # and a search's rules are well under the sidecar's default request limit.
  dapr = {
    enabled      = true
    app_id       = "verdict"
    app_port     = var.verdict_port
    app_protocol = "http"
  }

  template = {
    min_replicas = local.verdict_replicas
    max_replicas = local.verdict_replicas

    containers = [{
      name   = "verdict"
      image  = "${local.image_repositories.verdict}:${var.image_tag}"
      cpu    = local.container_cpu
      memory = local.container_memory

      # The settings are in locals.tf (local.verdict_env): no password and no
      # model key.
      env = local.verdict_env

      # azure.md rule 22. Startup and liveness ask the process; readiness
      # also asks the database, and fails unless its schema revision equals
      # the migration head bundled in the image.
      startup_probes = [{
        transport               = "HTTP"
        port                    = var.verdict_port
        path                    = var.verdict_health_path
        interval_seconds        = 5
        timeout                 = 2
        failure_count_threshold = 10
      }]
      readiness_probes = [{
        transport               = "HTTP"
        port                    = var.verdict_port
        path                    = var.verdict_ready_path
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
        success_count_threshold = 1
      }]
      liveness_probes = [{
        transport               = "HTTP"
        port                    = var.verdict_port
        path                    = var.verdict_health_path
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
    time_sleep.verdict_role_propagation,
  ]
}

# --- retrieval -----------------------------------------------------------------

# Internal ingress only (spine AD-18): reachable from inside the environment,
# and called only through Dapr service invocation (AD-3). For now it has its
# probes only; the search and the rule read come with story 2.3. It is held
# at exactly one replica.
module "retrieval" {
  source  = "Azure/avm-res-app-containerapp/azurerm"
  version = "0.9.0"

  name                                  = local.names.retrieval
  resource_group_name                   = local.foundation.resource_group_name
  resource_group_id                     = local.foundation.resource_group_id
  location                              = local.foundation.location
  container_app_environment_resource_id = local.foundation.container_apps_environment_id
  workload_profile_name                 = local.workload_profile
  revision_mode                         = "Single"

  managed_identities = {
    user_assigned_resource_ids = [local.retrieval_identity.id]
  }

  registries = [{
    server   = local.foundation.container_registry_login_server
    identity = local.retrieval_identity.id
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
    target_port                = var.retrieval_port
    transport                  = "auto"
    traffic_weight = [{
      latest_revision = true
      percentage      = 100
    }]
  }

  # Service invocation only (AD-3). A search carries a query and answers a
  # few chunks: well under the sidecar's default request limit.
  dapr = {
    enabled      = true
    app_id       = "retrieval"
    app_port     = var.retrieval_port
    app_protocol = "http"
  }

  template = {
    min_replicas = local.retrieval_replicas
    max_replicas = local.retrieval_replicas

    containers = [{
      name   = "retrieval"
      image  = "${local.image_repositories.retrieval}:${var.image_tag}"
      cpu    = local.container_cpu
      memory = local.container_memory

      env = concat(
        [
          { name = "RETRIEVAL_HOST", value = "0.0.0.0" },
          { name = "RETRIEVAL_PORT", value = tostring(var.retrieval_port) },
        ],
        local.retrieval_env,
      )

      # azure.md rule 22. Startup and liveness ask the process; readiness
      # also asks the database, and fails unless its schema revision equals
      # the migration head bundled in the image.
      startup_probes = [{
        transport               = "HTTP"
        port                    = var.retrieval_port
        path                    = var.retrieval_health_path
        interval_seconds        = 5
        timeout                 = 2
        failure_count_threshold = 10
      }]
      readiness_probes = [{
        transport               = "HTTP"
        port                    = var.retrieval_port
        path                    = var.retrieval_ready_path
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
        success_count_threshold = 1
      }]
      liveness_probes = [{
        transport               = "HTTP"
        port                    = var.retrieval_port
        path                    = var.retrieval_health_path
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
    time_sleep.retrieval_role_propagation,
  ]
}

# The ingestion job (spine AD-12): the same image and the same identity as the
# service, with another command. It has no ingress and no Dapr sidecar,
# because it calls no service of ours. It never starts by itself: an operator
# or the pipeline starts one execution, after the manual is in its container
# and the database is migrated (infra/bootstrap/README.md, section 7). A run
# over an unchanged manual changes nothing and calls no model, so starting it
# again is safe. A failed execution is not tried again by the platform: the
# job leaves the index as it was and its log says why.
module "retrieval_ingest" {
  source  = "Azure/avm-res-app-job/azurerm"
  version = "0.2.2"

  name                                  = local.names.retrieval_ingest
  resource_group_name                   = local.foundation.resource_group_name
  location                              = local.foundation.location
  container_app_environment_resource_id = local.foundation.container_apps_environment_id
  workload_profile_name                 = local.workload_profile

  managed_identities = {
    user_assigned_resource_ids = [local.retrieval_identity.id]
  }

  registries = [{
    server   = local.foundation.container_registry_login_server
    identity = local.retrieval_identity.id
  }]

  secrets = [{
    name  = local.appi_secret_name
    value = local.foundation.application_insights_connection_string
  }]

  trigger_config = {
    manual_trigger_config = {
      parallelism              = 1
      replica_completion_count = 1
    }
  }
  replica_retry_limit        = 0
  replica_timeout_in_seconds = var.ingest_timeout_seconds

  template = {
    container = {
      name    = "ingest"
      image   = "${local.image_repositories.retrieval}:${var.image_tag}"
      cpu     = local.container_cpu
      memory  = local.container_memory
      command = ["python", "-m", "retrieval.ingest"]
      env     = local.retrieval_env
    }
  }

  enable_telemetry = true
  tags             = local.tags

  depends_on = [
    time_sleep.retrieval_role_propagation,
  ]
}
