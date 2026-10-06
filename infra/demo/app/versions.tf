terraform {
  required_version = "1.16.5"

  required_providers {
    # Same pins as the foundation stack.
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "4.81.0"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "2.13.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "0.14.2"
    }
  }

  # Central state account, shared by all projects; this project owns only the
  # `aiuw` container. Entra auth, no account key.
  backend "azurerm" {
    resource_group_name  = "rg-tfstate-sea"
    storage_account_name = "stdjtfstatesea"
    container_name       = "aiuw"
    key                  = "demo/app.tfstate"
    use_azuread_auth     = true
  }
}
