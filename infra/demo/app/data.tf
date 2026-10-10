# The only way this stack learns about the foundation stack (terraform.md rule 7).
data "terraform_remote_state" "foundation" {
  backend = "azurerm"

  config = {
    resource_group_name  = var.state_resource_group_name
    storage_account_name = var.state_storage_account_name
    container_name       = var.state_container_name
    key                  = var.foundation_state_key
    use_azuread_auth     = true
  }
}
