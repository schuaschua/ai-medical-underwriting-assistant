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

output "image_tag" {
  description = "Image tag the services run."
  value       = var.image_tag
}
