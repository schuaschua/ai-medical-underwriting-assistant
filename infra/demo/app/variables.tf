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

variable "classification_port" {
  description = "Port the classification container listens on."
  type        = number
}

variable "classification_health_path" {
  description = "Path of the classification service's health route, the target of its startup and liveness probes."
  type        = string
}

variable "classification_ready_path" {
  description = "Path of the classification service's readiness route, which fails until the database schema is at the migration head bundled in the image."
  type        = string
}

variable "extraction_port" {
  description = "Port the extraction container listens on."
  type        = number
}

variable "extraction_health_path" {
  description = "Path of the extraction service's health route, the target of its startup and liveness probes."
  type        = string
}

variable "extraction_ready_path" {
  description = "Path of the extraction service's readiness route, which fails until the database schema is at the migration head bundled in the image."
  type        = string
}

variable "verdict_port" {
  description = "Port the verdict container listens on."
  type        = number
}

variable "verdict_health_path" {
  description = "Path of the verdict service's health route, the target of its startup and liveness probes."
  type        = string
}

variable "verdict_ready_path" {
  description = "Path of the verdict service's readiness route, which fails until the database schema is at the migration head bundled in the image."
  type        = string
}

variable "retrieval_port" {
  description = "Port the retrieval container listens on."
  type        = number
}

variable "retrieval_health_path" {
  description = "Path of the retrieval service's health route, the target of its startup and liveness probes."
  type        = string
}

variable "retrieval_ready_path" {
  description = "Path of the retrieval service's readiness route, which fails until the database schema is at the migration head bundled in the image."
  type        = string
}

variable "manual_blob_name" {
  description = "Name of the underwriting manual's PDF in the `manual` container, which the ingestion job reads (spine AD-12). An operator uploads it (infra/bootstrap/README.md, section 7)."
  type        = string
}

variable "layout_api_version" {
  description = "API version of Document Intelligence's layout analysis, which parses the manual for the ingestion job."
  type        = string
}

variable "retrieval_model_max_concurrent_calls" {
  description = "The most calls to the chat and embedding deployments the retrieval service, or its ingestion job, has under way at once. The chat deployment is shared with the other services: lower it if the token rate limit is hit."
  type        = number

  validation {
    condition     = var.retrieval_model_max_concurrent_calls == floor(var.retrieval_model_max_concurrent_calls) && var.retrieval_model_max_concurrent_calls >= 1 && var.retrieval_model_max_concurrent_calls <= 100
    error_message = "retrieval_model_max_concurrent_calls must be a whole number from 1 to 100."
  }
}

variable "search_candidate_depth" {
  description = "Retrieval row r3: how many chunks the vector search and the full-text search each hand to the rank fusion. Never fewer than the most items a search may ask for (50)."
  type        = number

  validation {
    condition     = var.search_candidate_depth == floor(var.search_candidate_depth) && var.search_candidate_depth >= 50 && var.search_candidate_depth <= 1000
    error_message = "search_candidate_depth must be a whole number from 50 to 1000."
  }
}

variable "search_embedding_timeout_seconds" {
  description = "How long the one embedding call of a search's query may take, in seconds. Not longer than search_deadline_seconds."
  type        = number

  validation {
    condition     = var.search_embedding_timeout_seconds > 0
    error_message = "search_embedding_timeout_seconds must be above 0."
  }
}

variable "search_embedding_max_retries" {
  description = "How often a search sends its query's embedding call again when it is answered 429 or 5xx, before the search is model_unavailable."
  type        = number

  validation {
    condition     = var.search_embedding_max_retries == floor(var.search_embedding_max_retries) && var.search_embedding_max_retries >= 0 && var.search_embedding_max_retries <= 10
    error_message = "search_embedding_max_retries must be a whole number from 0 to 10."
  }
}

variable "search_deadline_seconds" {
  description = "The deadline over one whole search, the embedding call and both database reads included, in seconds. When it passes the search is answered model_unavailable or upstream_unavailable."
  type        = number

  validation {
    condition     = var.search_deadline_seconds >= var.search_embedding_timeout_seconds
    error_message = "search_deadline_seconds must not be shorter than search_embedding_timeout_seconds."
  }
}

variable "ingest_deadline_seconds" {
  description = "The ingestion job's own deadline: after this long it ends itself with `stage_timeout` and leaves the index as it was. The one transaction that stores a finished run is not cut off by it."
  type        = number

  validation {
    condition     = var.ingest_deadline_seconds == floor(var.ingest_deadline_seconds) && var.ingest_deadline_seconds >= 60
    error_message = "ingest_deadline_seconds must be a whole number of at least 60."
  }
}

variable "ingest_timeout_seconds" {
  description = "How long one execution of the ingestion job may run before the platform ends it. Longer than the job's own deadline (ingest_deadline_seconds), so that the job ends itself first and says why, with room left for storing its result."
  type        = number

  validation {
    condition     = var.ingest_timeout_seconds == floor(var.ingest_timeout_seconds) && var.ingest_timeout_seconds >= var.ingest_deadline_seconds + 120
    error_message = "ingest_timeout_seconds must be a whole number at least 120 above ingest_deadline_seconds, the job's own deadline."
  }
}

variable "gate_threshold" {
  description = "The gate's confidence threshold (spine AD-7): a page classified at or above it is routed by its label, one below it goes to triage. Passed to workflow only. A case keeps the value in force when its lifecycle confirmed it, so a change applies to cases started after it."
  type        = number

  validation {
    condition     = var.gate_threshold >= 0 && var.gate_threshold <= 1
    error_message = "gate_threshold must be from 0 to 1."
  }
}

variable "classifier_runs" {
  description = "How often the LLM classifier runs the model on one page; its confidence is the share of those runs that agree (spine AD-13)."
  type        = number

  validation {
    condition     = var.classifier_runs == floor(var.classifier_runs) && var.classifier_runs >= 1 && var.classifier_runs <= 25
    error_message = "classifier_runs must be a whole number from 1 to 25."
  }
}

variable "classifier_max_concurrent_runs" {
  description = "How many of one page's classifier runs are under way at once."
  type        = number

  validation {
    condition     = var.classifier_max_concurrent_runs == floor(var.classifier_max_concurrent_runs) && var.classifier_max_concurrent_runs >= 1 && var.classifier_max_concurrent_runs <= 25
    error_message = "classifier_max_concurrent_runs must be a whole number from 1 to 25."
  }
}

variable "model_max_concurrent_calls" {
  description = "The most calls to the chat deployment the classification service has under way at once, however many pages are being classified. Lower it if the deployment's token rate limit is hit."
  type        = number

  validation {
    condition     = var.model_max_concurrent_calls == floor(var.model_max_concurrent_calls) && var.model_max_concurrent_calls >= 1 && var.model_max_concurrent_calls <= 100
    error_message = "model_max_concurrent_calls must be a whole number from 1 to 100."
  }
}

variable "extraction_model_max_concurrent_calls" {
  description = "The most calls to the chat deployment the extraction service has under way at once, however many pages are being read: one call per page. Lower it if the deployment's token rate limit is hit."
  type        = number

  validation {
    condition     = var.extraction_model_max_concurrent_calls == floor(var.extraction_model_max_concurrent_calls) && var.extraction_model_max_concurrent_calls >= 1 && var.extraction_model_max_concurrent_calls <= 100
    error_message = "extraction_model_max_concurrent_calls must be a whole number from 1 to 100."
  }
}

variable "verdict_model_max_concurrent_calls" {
  description = "The most calls to the chat deployment the verdict service has under way at once, however many runs of the agent are under way: each turn of a run is one call. Lower it if the deployment's token rate limit is hit."
  type        = number

  validation {
    condition     = var.verdict_model_max_concurrent_calls == floor(var.verdict_model_max_concurrent_calls) && var.verdict_model_max_concurrent_calls >= 1 && var.verdict_model_max_concurrent_calls <= 100
    error_message = "verdict_model_max_concurrent_calls must be a whole number from 1 to 100."
  }
}

variable "verdict_step_limit" {
  description = "The most tool calls one run of the verdict agent may make. A run that would make one more is stopped and its case referred (spine AD-15)."
  type        = number

  validation {
    condition     = var.verdict_step_limit == floor(var.verdict_step_limit) && var.verdict_step_limit >= 1 && var.verdict_step_limit <= 200
    error_message = "verdict_step_limit must be a whole number from 1 to 200."
  }
}

variable "verdict_confidence_floor" {
  description = "A verdict run whose agent is less confident than this refers its case; at the floor it is not low (spine AD-15)."
  type        = number

  validation {
    condition     = var.verdict_confidence_floor >= 0 && var.verdict_confidence_floor <= 1
    error_message = "verdict_confidence_floor must be between 0 and 1."
  }
}

variable "model_max_retries" {
  description = "How often a model call answered 429 or 5xx is sent again before the model counts as unavailable (spine AD-16: three)."
  type        = number

  validation {
    condition     = var.model_max_retries == floor(var.model_max_retries) && var.model_max_retries >= 0 && var.model_max_retries <= 10
    error_message = "model_max_retries must be a whole number from 0 to 10."
  }
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
