locals {
  foundation = data.terraform_remote_state.foundation.outputs

  # The one naming block of this stack, built on the foundation's suffix
  # (workload, environment, region code).
  names = {
    web = "ca-${local.foundation.name_suffix}-web"
  }

  # The six required tags, as the foundation stack built them.
  tags = local.foundation.tags

  web_identity = local.foundation.runtime_identities["web"]

  # Compute ceilings (spine, Deployment): raising one is an architecture change.
  container_cpu    = 0.5
  container_memory = "1Gi"
  max_replicas     = 2
  workload_profile = "Consumption"

  # Name of the Container Apps secret that holds the Application Insights
  # connection string.
  appi_secret_name = "appi-connection-string"

  image_repositories = {
    web = "${local.foundation.container_registry_login_server}/web"
  }
}
