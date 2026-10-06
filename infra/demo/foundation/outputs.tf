# Values the app stack reads through terraform_remote_state.

output "resource_group_name" {
  description = "Name of the environment's resource group."
  value       = azurerm_resource_group.this.name
}

output "resource_group_id" {
  description = "Resource id of the environment's resource group."
  value       = azurerm_resource_group.this.id
}

output "location" {
  description = "Azure region of the environment."
  value       = var.location
}

output "tags" {
  description = "The six required tags."
  value       = local.tags
}

output "name_suffix" {
  description = "Shared name suffix (workload, environment, region code) for names built in later stacks."
  value       = local.name_suffix
}

output "log_analytics_workspace_id" {
  description = "Resource id of the Log Analytics workspace."
  value       = module.log_analytics_workspace.resource_id
}

output "application_insights_id" {
  description = "Resource id of Application Insights."
  value       = module.application_insights.resource_id
}

output "application_insights_connection_string" {
  description = "Application Insights connection string. An address, not a credential (local authentication is off), but kept out of logs."
  value       = module.application_insights.connection_string
  sensitive   = true
}

output "container_registry_id" {
  description = "Resource id of the container registry."
  value       = module.container_registry.resource_id
}

output "container_registry_login_server" {
  description = "Login server of the container registry."
  value       = module.container_registry.login_server
}

output "container_apps_environment_id" {
  description = "Resource id of the Container Apps environment."
  value       = module.container_apps_environment.resource_id
}

output "container_apps_environment_default_domain" {
  description = "Default domain of the Container Apps environment."
  value       = module.container_apps_environment.default_domain
}

output "runtime_identities" {
  description = "The seven runtime identities by service app id: resource id, client id and principal id."
  value = {
    for role, identity in module.runtime_identity : role => {
      id           = identity.resource_id
      name         = identity.resource_name
      client_id    = identity.client_id
      principal_id = identity.principal_id
    }
  }
}

output "postgresql_server_id" {
  description = "Resource id of the PostgreSQL flexible server."
  value       = module.postgresql.resource_id
}

output "postgresql_server_name" {
  description = "Name of the PostgreSQL flexible server."
  value       = module.postgresql.name
}

output "postgresql_fqdn" {
  description = "Host name of the PostgreSQL flexible server."
  value       = module.postgresql.fqdn
}

output "postgresql_database_name" {
  description = "Name of the one application database."
  value       = local.names.postgresql_database
}

output "storage_account_id" {
  description = "Resource id of the Storage account."
  value       = module.storage_account.resource_id
}

output "storage_account_name" {
  description = "Name of the Storage account."
  value       = module.storage_account.name
}

output "storage_blob_endpoint" {
  description = "Blob endpoint of the Storage account, from the host name Azure returns."
  value       = "https://${module.storage_account.fqdn["blob"]}"
}

output "storage_container_ids" {
  description = "Resource ids of the blob containers by container name, for container-scoped role assignments."
  value       = { for name in var.storage_containers : name => "${module.storage_account.resource_id}/blobServices/default/containers/${name}" }
}

output "foundry_account_id" {
  description = "Resource id of the Foundry account."
  value       = module.foundry_account.resource_id
}

output "foundry_endpoint" {
  description = "Endpoint of the Foundry account."
  value       = module.foundry_account.endpoint
}

output "foundry_project_id" {
  description = "Resource id of the Foundry project."
  value       = azurerm_cognitive_account_project.this.id
}

output "foundry_project_endpoints" {
  description = "Endpoints of the Foundry project by API name."
  value       = azurerm_cognitive_account_project.this.endpoints
}

output "model_deployment_names" {
  description = "Model deployment names by purpose (chat, embedding)."
  value       = { for key, deployment in var.model_deployments : key => deployment.name }
}

output "search_service_id" {
  description = "Resource id of the Azure AI Search service."
  value       = module.search_service.resource_id
}

output "search_endpoint" {
  description = "Endpoint of the Azure AI Search service."
  value       = module.search_service.resource.endpoint
}

output "search_principal_id" {
  description = "Principal id of the search service's own identity."
  value       = module.search_service.resource.identity[0].principal_id
}

output "document_intelligence_id" {
  description = "Resource id of the Document Intelligence account."
  value       = module.ai_account["document_intelligence"].resource_id
}

output "document_intelligence_endpoint" {
  description = "Endpoint of the Document Intelligence account."
  value       = module.ai_account["document_intelligence"].endpoint
}

output "document_intelligence_principal_id" {
  description = "Principal id of Document Intelligence's own identity."
  value       = module.ai_account["document_intelligence"].system_assigned_mi_principal_id
}

output "language_id" {
  description = "Resource id of the Azure AI Language account."
  value       = module.ai_account["language"].resource_id
}

output "language_endpoint" {
  description = "Endpoint of the Azure AI Language account."
  value       = module.ai_account["language"].endpoint
}

output "language_principal_id" {
  description = "Principal id of Azure AI Language's own identity."
  value       = module.ai_account["language"].system_assigned_mi_principal_id
}

output "durable_task_scheduler_id" {
  description = "Resource id of the Durable Task Scheduler."
  value       = azapi_resource.durable_task_scheduler.id
}

output "durable_task_scheduler_endpoint" {
  description = "Endpoint of the Durable Task Scheduler."
  value       = azapi_resource.durable_task_scheduler.output.properties.endpoint
}

output "durable_task_hub_id" {
  description = "Resource id of the task hub, the scope of the workflow service's role."
  value       = azapi_resource.durable_task_hub.id
}

output "durable_task_hub_name" {
  description = "Name of the task hub."
  value       = azapi_resource.durable_task_hub.name
}
