variable "state_resource_group_name" {
  description = "Resource group of the central Terraform state account."
  type        = string
}

variable "state_storage_account_name" {
  description = "Name of the central Terraform state account."
  type        = string
}

variable "state_container_name" {
  description = "Blob container that holds this project's state files."
  type        = string
}

variable "foundation_state_key" {
  description = "State file of the foundation stack, whose outputs this stack reads."
  type        = string
}

variable "image_tag" {
  description = "Tag of the service images to run: the commit the deploy workflow built. Set it with -var or TF_VAR_image_tag; it is not committed."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.image_tag)) && var.image_tag != "latest"
    error_message = "image_tag must be a valid image tag that names one build, not \"latest\"."
  }
}

variable "min_replicas" {
  description = "Fewest replicas of each scale-to-zero service. 0 lets them scale to zero; set 1 to keep them warm during a demo."
  type        = number
  default     = 0

  validation {
    condition     = contains([0, 1, 2], var.min_replicas)
    error_message = "min_replicas must be 0, 1 or 2: the compute ceiling is 2 replicas."
  }
}

variable "role_propagation_wait" {
  description = "How long to wait after a role assignment before a Container App that relies on it is created."
  type        = string
}

variable "web_port" {
  description = "Port the web container listens on."
  type        = number
}

variable "web_health_path" {
  description = "Path of the web service's health route, the target of its probes."
  type        = string
}

variable "intake_port" {
  description = "Port the intake container listens on."
  type        = number
}

variable "intake_health_path" {
  description = "Path of the intake service's health route, the target of its startup and liveness probes."
  type        = string
}

variable "intake_ready_path" {
  description = "Path of the intake service's readiness route, which fails until the database schema is at the migration head bundled in the image."
  type        = string
}

variable "workflow_port" {
  description = "Port the workflow container listens on."
  type        = number
}

variable "workflow_health_path" {
  description = "Path of the workflow service's health route, the target of its startup and liveness probes."
  type        = string
}

variable "workflow_ready_path" {
  description = "Path of the workflow service's readiness route, which fails until the database schema is at the migration head bundled in the image."
  type        = string
}

variable "otel_sampling_ratio" {
  description = "Share of requests each service traces, from 0 to 1."
  type        = number

  validation {
    condition     = var.otel_sampling_ratio >= 0 && var.otel_sampling_ratio <= 1
    error_message = "otel_sampling_ratio must be between 0 and 1."
  }
}

variable "dapr_http_max_request_size_mb" {
  description = "Largest request body the web and intake sidecars accept, in MB. An upload is at most 10 MB (spine AD-3)."
  type        = number

  validation {
    condition     = var.dapr_http_max_request_size_mb == floor(var.dapr_http_max_request_size_mb) && var.dapr_http_max_request_size_mb >= 10
    error_message = "dapr_http_max_request_size_mb must be a whole number of at least 10: an upload is up to 10 MB."
  }
}

variable "dapr_http_port" {
  description = "Port of the Dapr sidecar's HTTP API beside each container, on loopback. Container Apps uses 3500."
  type        = number
}

variable "language_api_version" {
  description = "API version of Azure AI Language's document PII redaction that intake calls (spine AD-21)."
  type        = string
}

variable "redaction_categories" {
  description = "The PII categories redaction removes, by Azure AI Language's category names (spine AD-21). Dates, ages and medical terms are kept by not being listed."
  type        = list(string)

  validation {
    condition     = length(var.redaction_categories) > 0 && alltrue([for name in var.redaction_categories : can(regex("^[A-Za-z0-9]+$", name))])
    error_message = "redaction_categories must name at least one category, each of letters and digits only."
  }
}
