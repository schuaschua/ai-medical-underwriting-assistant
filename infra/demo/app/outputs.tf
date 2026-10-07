output "web_url" {
  description = "Public HTTPS address of the web app, the one way into the environment."
  value       = module.web.fqdn_url
}

output "web_container_app_name" {
  description = "Name of the web Container App."
  value       = module.web.name
}

output "web_container_app_id" {
  description = "Resource id of the web Container App, for the deploy workflow's revision check."
  value       = module.web.resource_id
}

output "intake_container_app_name" {
  description = "Name of the intake Container App."
  value       = module.intake.name
}

output "intake_container_app_id" {
  description = "Resource id of the intake Container App."
  value       = module.intake.resource_id
}

output "intake_database_role" {
  description = "Name of the PostgreSQL role the intake service signs in as; the database bootstrap creates it (infra/bootstrap/README.md)."
  value       = local.intake_identity.name
}

output "workflow_container_app_name" {
  description = "Name of the workflow Container App."
  value       = module.workflow.name
}

output "workflow_container_app_id" {
  description = "Resource id of the workflow Container App."
  value       = module.workflow.resource_id
}

output "workflow_database_role" {
  description = "Name of the PostgreSQL role the workflow service signs in as; the database bootstrap creates it (infra/bootstrap/README.md)."
  value       = local.workflow_identity.name
}

output "classification_container_app_name" {
  description = "Name of the classification Container App."
  value       = module.classification.name
}

output "classification_container_app_id" {
  description = "Resource id of the classification Container App."
  value       = module.classification.resource_id
}

output "classification_database_role" {
  description = "Name of the PostgreSQL role the classification service signs in as; the database bootstrap creates it (infra/bootstrap/README.md)."
  value       = local.classification_identity.name
}

output "image_tag" {
  description = "Image tag the services run."
  value       = var.image_tag
}
