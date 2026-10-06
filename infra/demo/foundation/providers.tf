# The subscription comes from ARM_SUBSCRIPTION_ID; it is never written here.
provider "azurerm" {
  # The bootstrap script registers the resource providers.
  resource_provider_registrations = "none"
  storage_use_azuread             = true

  features {
    log_analytics_workspace {
      # A teardown deletes the workspace for good instead of soft-deleting it,
      # so the next bring-up starts with an empty workspace of the same name.
      permanently_delete_on_destroy = true
    }
  }
}

provider "azapi" {}
