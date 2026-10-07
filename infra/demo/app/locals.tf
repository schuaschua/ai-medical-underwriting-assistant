locals {
  foundation = data.terraform_remote_state.foundation.outputs

  # The one naming block of this stack, built on the foundation's suffix
  # (workload, environment, region code).
  names = {
    web      = "ca-${local.foundation.name_suffix}-web"
    intake   = "ca-${local.foundation.name_suffix}-intake"
    workflow = "ca-${local.foundation.name_suffix}-workflow"

    classification = "ca-${local.foundation.name_suffix}-classification"
  }

  # The six required tags, as the foundation stack built them.
  tags = local.foundation.tags

  web_identity      = local.foundation.runtime_identities["web"]
  intake_identity   = local.foundation.runtime_identities["intake"]
  workflow_identity = local.foundation.runtime_identities["workflow"]

  classification_identity = local.foundation.runtime_identities["classification"]

  # The blob containers intake owns (spine AD-4): the uploaded originals, and
  # the redacted PDFs and thumbnails.
  intake_blob_containers = toset(["originals", "cases"])

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
  workload_profile        = "Consumption"

  # Name of the Container Apps secret that holds the Application Insights
  # connection string.
  appi_secret_name = "appi-connection-string"

  image_repositories = {
    web      = "${local.foundation.container_registry_login_server}/web"
    intake   = "${local.foundation.container_registry_login_server}/intake"
    workflow = "${local.foundation.container_registry_login_server}/workflow"

    classification = "${local.foundation.container_registry_login_server}/classification"
  }
}
