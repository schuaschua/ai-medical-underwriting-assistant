terraform {
  required_version = "1.16.5"

  required_providers {
    # 4.81.0 is the latest release the Azure Verified Modules accept: every one
    # of them caps azurerm below 5.0.0.
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "4.81.0"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "2.13.0"
    }
  }

  # Central state account, shared by all projects; this project owns only the
  # `aiuw` container. Entra auth, no account key.
  backend "azurerm" {
    resource_group_name  = "rg-tfstate-sea"
    storage_account_name = "stdjtfstatesea"
    container_name       = "aiuw"
    key                  = "demo/foundation.tfstate"
    use_azuread_auth     = true
  }
}
