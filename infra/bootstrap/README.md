# Bootstrap and operations for the demo environment

Everything here is run by an operator, outside the pipeline. It covers the one-time bootstrap, bringing the `foundation` stack up, tearing it down, and a dated log of every out-of-band change made to Azure or GitHub.

The environment has a fixed cost of roughly USD 91 a month while it is up (Azure AI Search Basic and PostgreSQL B1ms, about USD 86, plus about USD 5 for the Basic container registry), before logs and usage, so it is brought up for testing and torn down afterwards. The bootstrap pieces are free and stay in place across teardowns.

## What you need

- `az` signed in to the subscription "Babaloo" as a user who holds Owner.
- `gh` signed in with admin rights on `schuaschua/ai-medical-underwriting-assistant`.
- Terraform 1.16.5 exactly (the stack pins it).

Nothing in this file has been run yet (see the log at the end). Where it says what a command prints or how long it takes, that is what the code is written to do, not something observed.

## 1. Bootstrap (once)

Run it before the first pull request that touches `infra/`: the pull-request workflow signs in with the identity and the repository variables this script creates, and fails without them.

```bash
bash infra/bootstrap/state-backend.sh
```

Before changing anything the script stops unless `az` is signed in to the subscription named "Babaloo" (set `EXPECTED_SUBSCRIPTION_NAME` if it is renamed), the state account `stdjtfstatesea` exists in `rg-tfstate-sea`, and any existing `rg-aiuw-demo-wus3` is in West US 3.

The script is written to be idempotent: a second run is expected to print `No changes.` and exit 0 (not yet observed). It stops on the first error. It creates:

| What | Where | Notes |
| --- | --- | --- |
| Resource group `rg-aiuw-demo-wus3` | West US 3 | The six required tags. The `foundation` stack adopts it with an `import` block. |
| State container `aiuw` | Account `stdjtfstatesea`, resource group `rg-tfstate-sea` | The account is shared with other projects; nothing else on it is changed. |
| Deployment identity `id-aiuw-demo-wus3-deploy` | Resource group `rg-tfstate-sea`, location West US 3 | Kept outside the group it can change. Tagged `managedby=bootstrap`, because Terraform does not manage it. |
| Federated credentials `github-pull-request` and `github-environment-demo` | On the deployment identity | Subjects `<prefix>:pull_request` and `<prefix>:environment:demo`. The prefix is read from GitHub's API in its immutable form. |
| Contributor | Deployment identity on `rg-aiuw-demo-wus3` | |
| Role Based Access Control Administrator, with a condition | Deployment identity on `rg-aiuw-demo-wus3` | May assign or remove only the runtime roles listed in `docs/standards/azure.md`, and only for service principals. |
| Storage Blob Data Contributor | Deployment identity on the `aiuw` container | State access with Entra auth. |
| Storage Blob Data Contributor | The operator on the `aiuw` container | Owner gives no data-plane access; Terraform needs it to read and write state. |
| Repository variables `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_CLIENT_ID`, `AZURE_DEPLOY_PRINCIPAL_ID` | GitHub | Non-secret ids for OIDC sign-in. The last one feeds `TF_VAR_deploy_principal_id`. |
| Environment `demo`, deploying only from `main` | GitHub | |

It also registers any resource provider the stacks use that is not yet registered.

## 2. Bring up

Run from the repository root. None of these values is written to a file.

```bash
export ARM_SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
export TF_VAR_deploy_principal_id="$(az identity show -g rg-tfstate-sea -n id-aiuw-demo-wus3-deploy --query principalId -o tsv)"

# Your own object id, so that you are a PostgreSQL Entra administrator and can
# create the database principals. Your database role is named after this id.
export TF_VAR_postgresql_extra_admin_object_ids="[\"$(az ad signed-in-user show --query id -o tsv)\"]"

# Where budget and alert mail goes, beside holders of the Owner role.
export TF_VAR_alert_email_addresses='["you@example.com"]'

terraform -chdir=infra/demo/foundation init
terraform -chdir=infra/demo/foundation plan -out=tfplan
terraform -chdir=infra/demo/foundation apply tfplan
```

Both optional variables default to an empty list. Set the same values on every plan and apply, including a teardown: a run without them plans to remove the extra administrator and the email receivers. The pull-request workflow does not set them, so its plan shows that difference while they are in use.

Expected, not yet observed: the first plan, and the first plan after a teardown, show `1 to import` (the resource group) and the rest `to add`; an apply probably takes 15 to 20 minutes, with PostgreSQL and the Container Apps environment the slow ones.

The budget starts on the first day of the month in which it is created; nothing needs editing when the stack is re-created in a later month.

Check the result:

```bash
terraform -chdir=infra/demo/foundation plan -detailed-exitcode   # exit 0: no changes
az resource list -g rg-aiuw-demo-wus3 --query "[].{type:type,name:name,location:location}" -o table
```

If the apply fails because a name is held by a soft-deleted Cognitive Services account, run the purge step of the teardown below, then plan and apply again.

## 3. Tear down

This removes everything inside `rg-aiuw-demo-wus3` and keeps the resource group itself, the state container, the deployment identity with its role assignments, and the GitHub variables and environment. All data in the environment (database, blobs, search indexes, registry images, logs) is lost.

`terraform.md` says to destroy only through the pipeline's destroy workflow. That workflow does not exist yet, so a local teardown is an out-of-band step: the owner decides, and each run is added to the log below.

```bash
export ARM_SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
export TF_VAR_deploy_principal_id="$(az identity show -g rg-tfstate-sea -n id-aiuw-demo-wus3-deploy --query principalId -o tsv)"
# Also export the two optional variables exactly as at bring-up, if they were used.
terraform -chdir=infra/demo/foundation init

# 1. Take the resource group out of state so that destroy keeps it. It carries
#    prevent_destroy, so if this step is skipped the destroy fails; it does not
#    delete the group. The import block adopts the group again at the next
#    bring-up.
terraform -chdir=infra/demo/foundation state rm azurerm_resource_group.this

# 2. Review what will go, then destroy.
terraform -chdir=infra/demo/foundation plan -destroy
terraform -chdir=infra/demo/foundation destroy

# 3. Purge the soft-deleted Cognitive Services accounts of this resource group
#    (expected: aif-, di- and lang-aiuw-demo-wus3). Until they are purged (or
#    48 hours pass) their names and custom subdomains cannot be used again,
#    and the next bring-up fails. The loop takes only what is actually
#    soft-deleted, so it can be run again safely.
for name in $(az cognitiveservices account list-deleted \
  --query "[?contains(id, '/resourceGroups/rg-aiuw-demo-wus3/')].name" -o tsv); do
  az cognitiveservices account purge -l westus3 -g rg-aiuw-demo-wus3 -n "$name"
done

# 4. Confirm the group is empty and still there.
az resource list -g rg-aiuw-demo-wus3 -o table
az group show -n rg-aiuw-demo-wus3 --query "{name:name,tags:tags}"
```

What to expect on a teardown and re-create (from the configuration and Azure's documented behaviour; not yet observed):

- **Resource group:** kept. Never run `az group delete` on it: that would also remove the deployment identity's role assignments.
- **Cognitive Services accounts** (Foundry, Document Intelligence, Azure AI Language): soft-deleted on destroy, so step 3 is needed. Purging needs Contributor or Owner on the subscription or resource group.
- **Log Analytics workspace:** deleted for good by the destroy (the provider's `permanently_delete_on_destroy` feature is on), so the next bring-up starts with an empty workspace. No manual step.
- **Storage account, registry, PostgreSQL, Azure AI Search, Durable Task Scheduler, Application Insights, the identities, the budget and the alert:** deleted outright; nothing blocks re-creating them under the same names. The storage account has no blob or container soft delete, and nothing in the stack has purge protection or a lock.
- **Runtime identities:** re-created with new principal ids, so every role assignment and PostgreSQL role that pointed at the old ones must be made again by the `app` stack and the database bootstrap.
- **Model quota:** a deleted deployment frees its quota within minutes.
- **State:** the file `demo/foundation.tfstate` should stay in the `aiuw` container, empty of resources.

## Out-of-band log

Every command that changed Azure or GitHub outside the pipeline, newest last (`terraform.md` rule 29).

| Date | Who | Command | What it did |
| --- | --- | --- | --- |
| 2026-10-06 | Coding agent, as operator | none | Wrote the bootstrap script and the `foundation` stack. Nothing has been changed in Azure or GitHub yet: the bootstrap grants roles, and the agent's session was not permitted to run it. The steps below are still to be run by the owner. |
| pending | Owner | `bash infra/bootstrap/state-backend.sh` | Creates everything in the table in section 1. |
| pending | Owner | `terraform -chdir=infra/demo/foundation init`, `plan -out=tfplan`, `apply tfplan` | First apply of the `foundation` stack, run locally under the recorded exception to `terraform.md` rules 26 and 33. |
