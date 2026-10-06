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

variable "otel_sampling_ratio" {
  description = "Share of requests each service traces, from 0 to 1."
  type        = number

  validation {
    condition     = var.otel_sampling_ratio >= 0 && var.otel_sampling_ratio <= 1
    error_message = "otel_sampling_ratio must be between 0 and 1."
  }
}

variable "dapr_http_max_request_size_mb" {
  description = "Largest request body the web sidecar accepts, in MB. An upload is at most 10 MB (spine AD-3)."
  type        = number

  validation {
    condition     = var.dapr_http_max_request_size_mb == floor(var.dapr_http_max_request_size_mb) && var.dapr_http_max_request_size_mb >= 10
    error_message = "dapr_http_max_request_size_mb must be a whole number of at least 10: an upload is up to 10 MB."
  }
}
