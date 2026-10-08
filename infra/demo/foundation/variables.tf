variable "workload" {
  description = "Workload short name, the first part of every resource name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]+$", var.workload))
    error_message = "workload must be lower-case letters and digits only: it is part of the storage account and registry names."
  }
  validation {
    # The storage account name is st + workload + environment + region_short, at most 24 characters.
    condition     = length("${var.workload}${var.environment}${var.region_short}") <= 22
    error_message = "workload, environment and region_short together must be at most 22 characters, so the storage account name fits in 24."
  }
}

variable "environment" {
  description = "Environment name. This project has exactly one: demo."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]+$", var.environment))
    error_message = "environment must be lower-case letters and digits only: it is part of the storage account and registry names."
  }
}

variable "location" {
  description = "Azure region for every resource in the stack."
  type        = string
  default     = "westus3"
}

variable "region_short" {
  description = "Short code for the region, used in resource names."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]+$", var.region_short))
    error_message = "region_short must be lower-case letters and digits only: it is part of the storage account and registry names."
  }
}

variable "owner" {
  description = "Alias of the person responsible for the environment (the owner tag)."
  type        = string
}

variable "data_classification" {
  description = "Data classification of the environment (the datatype tag)."
  type        = string
}

variable "repository_url" {
  description = "URL of the source repository (the repo tag)."
  type        = string
}

variable "deploy_principal_id" {
  description = "Object id of the pipeline's deployment identity, which becomes the PostgreSQL Entra administrator. Set it with TF_VAR_deploy_principal_id; it is not committed."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$", var.deploy_principal_id))
    error_message = "deploy_principal_id must be an object id (a lower-case GUID)."
  }
}

variable "postgresql_extra_admin_object_ids" {
  description = "Object ids of users who are PostgreSQL Entra administrators beside the deployment identity, such as the operator of a local bring-up. Set them with TF_VAR_postgresql_extra_admin_object_ids; they are not committed."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for id in var.postgresql_extra_admin_object_ids : can(regex("^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$", id))])
    error_message = "Every entry of postgresql_extra_admin_object_ids must be an object id (a lower-case GUID)."
  }
}

variable "alert_email_addresses" {
  description = "Email addresses that receive the budget and alert mail in addition to holders of the Owner role. Set them with TF_VAR_alert_email_addresses; they are not committed."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for address in var.alert_email_addresses : can(regex("^[^@\\s]+@[^@\\s]+$", address))])
    error_message = "Every entry of alert_email_addresses must be an email address."
  }
}

variable "runtime_identity_roles" {
  description = "Service app ids; one user-assigned identity is created for each."
  type        = set(string)

  validation {
    condition     = length(var.runtime_identity_roles) > 0
    error_message = "runtime_identity_roles must name at least one service."
  }
}

variable "log_sku" {
  description = "Pricing tier of the Log Analytics workspace."
  type        = string
  default     = "PerGB2018"
}

variable "log_daily_cap_gb" {
  description = "Daily ingestion cap of the Log Analytics workspace and of Application Insights, in GB."
  type        = number

  validation {
    condition     = var.log_daily_cap_gb > 0
    error_message = "log_daily_cap_gb must be above 0."
  }
}

variable "log_retention_days" {
  description = "Retention of the Log Analytics workspace and of Application Insights, in days."
  type        = number

  validation {
    condition     = var.log_retention_days >= 30
    error_message = "log_retention_days must be at least 30."
  }
}

variable "log_cap_alert_ratio" {
  description = "Share of the daily cap at which the Log Analytics cap alert fires."
  type        = number

  validation {
    condition     = var.log_cap_alert_ratio > 0 && var.log_cap_alert_ratio <= 1
    error_message = "log_cap_alert_ratio must be above 0 and at most 1."
  }
}

variable "budget_amount" {
  description = "Budget for the resource group per time grain, in the subscription's billing currency."
  type        = number
}

variable "budget_start_date" {
  description = "Start of the budget, the first day of a month in RFC 3339 form. Leave unset to start on the first day of the month in which the budget is created."
  type        = string
  default     = null
}

variable "budget_time_grain" {
  description = "Period the budget amount covers."
  type        = string
}

variable "container_registry_sku" {
  description = "Tier of the container registry."
  type        = string
}

variable "postgresql_sku" {
  description = "Compute tier and size of the PostgreSQL flexible server."
  type        = string
}

variable "postgresql_version" {
  description = "Major version of PostgreSQL."
  type        = string
}

variable "postgresql_storage_mb" {
  description = "Storage of the PostgreSQL server in MB. It cannot be scaled down later."
  type        = number
}

variable "postgresql_storage_tier" {
  description = "Disk performance tier of the PostgreSQL server."
  type        = string
}

variable "postgresql_backup_days" {
  description = "Days of point-in-time restore kept for the PostgreSQL server."
  type        = number
}

variable "storage_account_sku" {
  description = "Tier and redundancy of the Storage account."
  type        = string
}

variable "storage_containers" {
  description = "Names of the private blob containers in the Storage account."
  type        = set(string)

  validation {
    condition     = length(var.storage_containers) > 0
    error_message = "storage_containers must name at least one container."
  }
}

variable "search_sku" {
  description = "Tier of the Azure AI Search service."
  type        = string
}

variable "search_semantic_sku" {
  description = "Plan of the semantic ranker on the search service."
  type        = string
}

variable "document_intelligence_sku" {
  description = "Tier of the Document Intelligence account."
  type        = string
}

variable "language_sku" {
  description = "Tier of the Azure AI Language account."
  type        = string
}

variable "foundry_sku" {
  description = "Tier of the Foundry account."
  type        = string
}

variable "durable_task_sku" {
  description = "Billing SKU of the Durable Task Scheduler."
  type        = string
}

variable "model_deployment_sku" {
  description = "Deployment type of every model deployment that names none of its own."
  type        = string
  default     = "GlobalStandard"
}

variable "model_content_filter" {
  description = "Content filter (RAI policy) on every model deployment that names none of its own. Keep the Microsoft default."
  type        = string
  default     = "Microsoft.DefaultV2"
}

variable "model_deployments" {
  description = "Model deployments on the Foundry account, by purpose (chat, embedding, and rerank where the reranker of row r4 can be deployed): deployment name, model, exact version, capacity in the unit of the model's deployment type (thousands of tokens per minute for the OpenAI models), and the model's format in the catalogue, OpenAI unless said otherwise. A deployment may name its own content filter (an empty text: none is sent, and the service applies its default; never one weaker than the default), version upgrade option and deployment type where the stack's do not fit its model; unset, it gets model_content_filter, NoAutoUpgrade and model_deployment_sku."
  type = map(object({
    name                   = string
    model_name             = string
    model_version          = string
    capacity               = number
    model_format           = optional(string, "OpenAI")
    content_filter         = optional(string)
    version_upgrade_option = optional(string, "NoAutoUpgrade")
    sku                    = optional(string)
  }))

  validation {
    condition     = alltrue([for key in ["chat", "embedding"] : contains(keys(var.model_deployments), key)])
    error_message = "model_deployments must hold the chat and embedding deployments: the app stack reads each by that key. The rerank deployment may be left out; row r4 is then off."
  }

  validation {
    condition     = alltrue([for deployment in values(var.model_deployments) : contains(["OpenAI", "Cohere"], deployment.model_format)])
    error_message = "model_format must be OpenAI or Cohere, as the model catalogue writes them."
  }

  validation {
    condition     = alltrue([for deployment in values(var.model_deployments) : contains(["NoAutoUpgrade", "OnceNewDefaultVersionAvailable", "OnceCurrentVersionExpired"], deployment.version_upgrade_option)])
    error_message = "version_upgrade_option must be NoAutoUpgrade, OnceNewDefaultVersionAvailable or OnceCurrentVersionExpired."
  }
}

variable "originals_retention_days" {
  description = "Days an uploaded original PDF is kept in the originals container before Azure deletes it."
  type        = number
  default     = 30

  validation {
    condition     = var.originals_retention_days >= 1 && floor(var.originals_retention_days) == var.originals_retention_days
    error_message = "originals_retention_days must be a whole number of days, at least 1."
  }
}
