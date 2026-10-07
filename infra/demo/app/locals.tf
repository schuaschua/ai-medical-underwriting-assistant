locals {
  foundation = data.terraform_remote_state.foundation.outputs

  # The one naming block of this stack, built on the foundation's suffix
  # (workload, environment, region code).
  names = {
    web      = "ca-${local.foundation.name_suffix}-web"
    intake   = "ca-${local.foundation.name_suffix}-intake"
    workflow = "ca-${local.foundation.name_suffix}-workflow"

    classification = "ca-${local.foundation.name_suffix}-classification"
    extraction     = "ca-${local.foundation.name_suffix}-extraction"
    retrieval      = "ca-${local.foundation.name_suffix}-retrieval"
    verdict        = "ca-${local.foundation.name_suffix}-verdict"
    # The one-off job that ingests the manual (spine AD-12); `caj` is the
    # CAF abbreviation for a Container Apps job (azure.md, project names).
    retrieval_ingest = "caj-${local.foundation.name_suffix}-ingest"
  }

  # The six required tags, as the foundation stack built them.
  tags = local.foundation.tags

  web_identity      = local.foundation.runtime_identities["web"]
  intake_identity   = local.foundation.runtime_identities["intake"]
  workflow_identity = local.foundation.runtime_identities["workflow"]

  classification_identity = local.foundation.runtime_identities["classification"]
  extraction_identity     = local.foundation.runtime_identities["extraction"]
  retrieval_identity      = local.foundation.runtime_identities["retrieval"]
  verdict_identity        = local.foundation.runtime_identities["verdict"]

  # The blob containers intake owns (spine AD-4): the uploaded originals, and
  # the redacted PDFs and thumbnails.
  intake_blob_containers = toset(["originals", "cases"])
  # The blob container retrieval owns (spine AD-4): the manual PDF.
  retrieval_manual_container = "manual"

  # Compute ceilings (spine, Deployment): raising one is an architecture change.
  container_cpu    = 0.5
  container_memory = "1Gi"
  max_replicas     = 2
  # workflow is held at exactly 1 replica (spine AD-18, Deployment): a case
  # starts without a cold start, and one worker runs the orchestrations.
  workflow_replicas = 1
  # classification is held at exactly 1 replica as well, so that the limit on
  # model calls under way at once (var.model_max_concurrent_calls, which the
  # service keeps for its whole process) is the limit for the environment.
  # The replica count itself bounds nothing: every page of a case is
  # classified at once, several model runs each.
  classification_replicas = 1
  # extraction is held at exactly 1 replica for the same reason: its own
  # limit on model calls (var.extraction_model_max_concurrent_calls) is then
  # the limit for the environment, on the chat deployment it shares with
  # classification and the verdict agent (spine AD-16).
  extraction_replicas = 1
  # verdict is held at exactly 1 replica for the same reason: its own limit
  # on model calls (var.verdict_model_max_concurrent_calls) is then the limit
  # for the environment, on the same chat deployment.
  verdict_replicas = 1
  # retrieval is held at exactly 1 replica for the same reason: from story 2.3
  # on it embeds every search query on the one embedding deployment.
  retrieval_replicas = 1
  workload_profile   = "Consumption"

  # Name of the Container Apps secret that holds the Application Insights
  # connection string.
  appi_secret_name = "appi-connection-string"

  image_repositories = {
    web      = "${local.foundation.container_registry_login_server}/web"
    intake   = "${local.foundation.container_registry_login_server}/intake"
    workflow = "${local.foundation.container_registry_login_server}/workflow"

    classification = "${local.foundation.container_registry_login_server}/classification"
    extraction     = "${local.foundation.container_registry_login_server}/extraction"
    retrieval      = "${local.foundation.container_registry_login_server}/retrieval"
    verdict        = "${local.foundation.container_registry_login_server}/verdict"
  }

  # What the retrieval service and its ingestion job are both given: one
  # image, one identity, one set of settings (spine AD-12). No password, no
  # storage key and no model key: the database, Blob Storage, Document
  # Intelligence and the Foundry deployments are reached with the service
  # identity (azure.md rule 7). The endpoints are the real accounts'; the
  # local stand-ins exist only on a developer machine.
  retrieval_env = [
    { name = "RETRIEVAL_AZURE_CLIENT_ID", value = local.retrieval_identity.client_id },
    { name = "RETRIEVAL_OTEL_SAMPLING_RATIO", value = tostring(var.otel_sampling_ratio) },
    { name = "RETRIEVAL_APPLICATIONINSIGHTS_CONNECTION_STRING", secret_name = local.appi_secret_name },
    { name = "RETRIEVAL_DATABASE_HOST", value = local.foundation.postgresql_fqdn },
    { name = "RETRIEVAL_DATABASE_NAME", value = local.foundation.postgresql_database_name },
    { name = "RETRIEVAL_DATABASE_USER", value = local.retrieval_identity.name },
    { name = "RETRIEVAL_DATABASE_ENTRA_AUTH", value = "true" },
    { name = "RETRIEVAL_BLOB_ACCOUNT_URL", value = local.foundation.storage_blob_endpoint },
    { name = "RETRIEVAL_MANUAL_CONTAINER", value = local.retrieval_manual_container },
    { name = "RETRIEVAL_MANUAL_BLOB_NAME", value = var.manual_blob_name },
    { name = "RETRIEVAL_LAYOUT_ENDPOINT", value = local.foundation.document_intelligence_endpoint },
    { name = "RETRIEVAL_LAYOUT_ENTRA_AUTH", value = "true" },
    { name = "RETRIEVAL_LAYOUT_API_VERSION", value = var.layout_api_version },
    { name = "RETRIEVAL_MODEL_ENDPOINT", value = local.foundation.foundry_endpoint },
    { name = "RETRIEVAL_MODEL_ENTRA_AUTH", value = "true" },
    # Spine AD-16: the deployment names come from the foundation stack.
    { name = "RETRIEVAL_CHAT_DEPLOYMENT", value = local.foundation.model_deployment_names["chat"] },
    { name = "RETRIEVAL_EMBEDDING_DEPLOYMENT", value = local.foundation.model_deployment_names["embedding"] },
    { name = "RETRIEVAL_MODEL_MAX_CONCURRENT_CALLS", value = tostring(var.retrieval_model_max_concurrent_calls) },
    { name = "RETRIEVAL_MODEL_MAX_RETRIES", value = tostring(var.model_max_retries) },
    # Spine AD-11, row r3: how many chunks each side of the hybrid search
    # hands to the rank fusion.
    { name = "RETRIEVAL_SEARCH_CANDIDATE_DEPTH", value = tostring(var.search_candidate_depth) },
    # A search's own short budget, apart from the ingestion job's model settings.
    { name = "RETRIEVAL_SEARCH_EMBEDDING_TIMEOUT_SECONDS", value = tostring(var.search_embedding_timeout_seconds) },
    { name = "RETRIEVAL_SEARCH_EMBEDDING_MAX_RETRIES", value = tostring(var.search_embedding_max_retries) },
    { name = "RETRIEVAL_SEARCH_DEADLINE_SECONDS", value = tostring(var.search_deadline_seconds) },
    # The job's own deadline: under the platform's limit on one execution
    # (var.ingest_timeout_seconds), so that the job ends itself and says why.
    { name = "RETRIEVAL_INGEST_DEADLINE_SECONDS", value = tostring(var.ingest_deadline_seconds) },
  ]

  # What the verdict service is given. No password and no model key: the
  # database and the chat deployment are reached with the service identity
  # (azure.md rule 7). The model endpoint is the Foundry account's and the
  # deployment name comes from the foundation stack (spine AD-16); the local
  # stand-in exists only on a developer machine. The service is given no
  # address of `extraction` or `retrieval`: it calls them by app id through
  # its own sidecar (AD-3).
  verdict_env = [
    { name = "VERDICT_HOST", value = "0.0.0.0" },
    { name = "VERDICT_PORT", value = tostring(var.verdict_port) },
    { name = "VERDICT_AZURE_CLIENT_ID", value = local.verdict_identity.client_id },
    { name = "VERDICT_OTEL_SAMPLING_RATIO", value = tostring(var.otel_sampling_ratio) },
    { name = "VERDICT_APPLICATIONINSIGHTS_CONNECTION_STRING", secret_name = local.appi_secret_name },
    { name = "VERDICT_DATABASE_HOST", value = local.foundation.postgresql_fqdn },
    { name = "VERDICT_DATABASE_NAME", value = local.foundation.postgresql_database_name },
    { name = "VERDICT_DATABASE_USER", value = local.verdict_identity.name },
    { name = "VERDICT_DATABASE_ENTRA_AUTH", value = "true" },
    { name = "VERDICT_DAPR_HTTP_PORT", value = tostring(var.dapr_http_port) },
    { name = "VERDICT_MODEL_ENDPOINT", value = local.foundation.foundry_endpoint },
    { name = "VERDICT_MODEL_ENTRA_AUTH", value = "true" },
    { name = "VERDICT_CHAT_DEPLOYMENT", value = local.foundation.model_deployment_names["chat"] },
    { name = "VERDICT_MODEL_MAX_CONCURRENT_CALLS", value = tostring(var.verdict_model_max_concurrent_calls) },
    { name = "VERDICT_MODEL_MAX_RETRIES", value = tostring(var.model_max_retries) },
    # Spine AD-15: the most tool calls one run of the agent may make, and
    # the confidence under which a run refers its case.
    { name = "VERDICT_STEP_LIMIT", value = tostring(var.verdict_step_limit) },
    { name = "VERDICT_CONFIDENCE_FLOOR", value = tostring(var.verdict_confidence_floor) },
  ]
}
