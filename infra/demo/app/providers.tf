# The subscription comes from ARM_SUBSCRIPTION_ID; it is never written here.
provider "azurerm" {
  # The bootstrap script registers the resource providers.
  resource_provider_registrations = "none"
  storage_use_azuread             = true

  features {}
}

provider "azapi" {}
