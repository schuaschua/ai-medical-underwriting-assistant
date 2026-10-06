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

# 4. Remove the alert rule that Application Insights creates on its own
#    ("Failure Anomalies - appi-aiuw-demo-wus3"). Terraform does not manage it,
#    so the destroy leaves it behind. It costs nothing.
az resource list -g rg-aiuw-demo-wus3 \
  --resource-type microsoft.alertsmanagement/smartDetectorAlertRules --query "[].id" -o tsv |
  while IFS= read -r id; do az resource delete --ids "$id"; done

# 5. Confirm the group is empty and still there.
az resource list -g rg-aiuw-demo-wus3 -o table
az group show -n rg-aiuw-demo-wus3 --query "{name:name,tags:tags}"
```

Observed on 2026-10-06 over two full cycles: a bring-up takes about 11 minutes in one apply; a teardown takes about 26 minutes, nearly all of it the Container Apps environment, plus about 3 minutes to purge the three AI accounts. After a re-create, the plan run straight after the apply reports no changes.

What to expect on a teardown and re-create:

- **Resource group:** kept. Never run `az group delete` on it: that would also remove the deployment identity's role assignments.
- **Cognitive Services accounts** (Foundry, Document Intelligence, Azure AI Language): soft-deleted on destroy, so step 3 is needed. Purging needs Contributor or Owner on the subscription or resource group.
- **Log Analytics workspace:** deleted for good by the destroy (the provider's `permanently_delete_on_destroy` feature is on), so the next bring-up starts with an empty workspace. No manual step.
- **Storage account, registry, PostgreSQL, Azure AI Search, Durable Task Scheduler, Application Insights, the identities, the budget and the alert:** deleted outright; nothing blocks re-creating them under the same names. The storage account has no blob or container soft delete, and nothing in the stack has purge protection or a lock.
- **Runtime identities:** re-created with new principal ids, so every role assignment and PostgreSQL role that pointed at the old ones must be made again by the `app` stack and the database bootstrap.
- **Model quota:** a deleted deployment frees its quota within minutes.
- **State:** the file `demo/foundation.tfstate` should stay in the `aiuw` container, empty of resources.

## 4. Database role for `intake`

Added by story 1.5. **None of the commands in this section has been run yet**: they are written from the configuration and from Azure's documentation, so read each step's output before going on. Add each run to the log below.

`intake` is the first service with a database schema (spine AD-4). PostgreSQL access is not an Azure role, so Terraform cannot grant it: after the `foundation` stack is up, an operator does the steps below once, and again after every teardown, because the re-created identity has a new principal id. The deploy workflow has no migration step yet (`terraform.md` rule 36), so the migrations are run here by hand as well. Until this section is done, `intake` reports "not ready" and an upload is answered with 502.

You need to be one of the server's Entra administrators (`TF_VAR_postgresql_extra_admin_object_ids` at bring-up, section 2), with `psql`, `uv` and this repository checked out. Run the steps in this order, in one shell, from the repository root. The order matters: the roles first, then the migrations (which create the schema), then the grants (which need the schema and its tables to exist).

**Step 0. Set up the shell, and open the firewall for your address.** The `trap` removes the firewall rule and the token when the shell exits, also after a failed step; `set -e` stops at the first error.

```bash
set -euo pipefail

SERVER="$(terraform -chdir=infra/demo/foundation output -raw postgresql_server_name)"
HOST="$(terraform -chdir=infra/demo/foundation output -raw postgresql_fqdn)"
DATABASE="$(terraform -chdir=infra/demo/foundation output -raw postgresql_database_name)"
ME="$(az ad signed-in-user show --query id -o tsv)"   # your database role is named after your object id
SERVICE_ROLE="id-aiuw-demo-wus3-intake"               # must equal: terraform -chdir=infra/demo/app output intake_database_role
DEPLOY_ROLE="id-aiuw-demo-wus3-deploy"                # the pipeline's role, which will run migrations later

cleanup() {
  # Always: the temporary firewall rule (azure.md rule 13) and the token.
  az postgres flexible-server firewall-rule delete -g rg-aiuw-demo-wus3 -n "$SERVER" \
    --rule-name operator-bootstrap --yes || true
  unset PGPASSWORD
}
trap cleanup EXIT

az postgres flexible-server firewall-rule create -g rg-aiuw-demo-wus3 -n "$SERVER" \
  --rule-name operator-bootstrap --start-ip-address "$(curl -s https://api.ipify.org)"

# An Entra token is the password. It lasts about an hour.
export PGPASSWORD="$(az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv)"
```

**Step 1. Roles.** Principals are created in the `postgres` database; the service's role is named after its identity. Your own role is made a member of the pipeline's role, which step 3 needs.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=postgres user=$ME sslmode=require" <<SQL
select * from pgaadauth_create_principal('$SERVICE_ROLE', false, false);
GRANT "$DEPLOY_ROLE" TO "$ME";
SQL
```

If the first statement says the role already exists, the identity was not re-created since the last run; go on with the second statement alone.

**Step 2. Schema and migrations.** The service never migrates at start-up, and its readiness probe fails until the schema is at the migration head bundled in its image. This creates schema `intake`, its tables and its version table. It signs in with your own Azure sign-in, not with `PGPASSWORD`.

```bash
INTAKE_DATABASE_HOST="$HOST" INTAKE_DATABASE_NAME="$DATABASE" INTAKE_DATABASE_USER="$ME" \
INTAKE_DATABASE_ENTRA_AUTH=true \
  uv run alembic -c services/intake/alembic.ini upgrade head
```

**Step 3. Grants.** The service role gets data rights on its own schema and nothing else: no `CREATE`, because only migrations change the schema, and read-only on the version table, so the service can check its revision but never change it. Sequences are included for tables that later get one. The schema and its tables are handed to the pipeline's role, so that it owns them as the spine's conventions say and its later migrations need no further grant.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=$DATABASE user=$ME sslmode=require" <<SQL
-- Hand the schema and everything in it to the pipeline's role.
ALTER SCHEMA intake OWNER TO "$DEPLOY_ROLE";
ALTER TABLE intake."case" OWNER TO "$DEPLOY_ROLE";
ALTER TABLE intake.document OWNER TO "$DEPLOY_ROLE";
ALTER TABLE intake.alembic_version OWNER TO "$DEPLOY_ROLE";

GRANT USAGE ON SCHEMA intake TO "$SERVICE_ROLE";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA intake TO "$SERVICE_ROLE";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA intake TO "$SERVICE_ROLE";
-- The version table is read-only for the service.
REVOKE INSERT, UPDATE, DELETE ON intake.alembic_version FROM "$SERVICE_ROLE";

-- Tables and sequences that later migrations add, run by the pipeline's role.
-- This needs membership of that role, granted in step 1.
ALTER DEFAULT PRIVILEGES FOR ROLE "$DEPLOY_ROLE" IN SCHEMA intake
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "$SERVICE_ROLE";
ALTER DEFAULT PRIVILEGES FOR ROLE "$DEPLOY_ROLE" IN SCHEMA intake
  GRANT USAGE, SELECT ON SEQUENCES TO "$SERVICE_ROLE";
SQL
```

**Step 4. Close up and check.** Leaving the shell runs the `trap`; to stay in the shell, run it now.

```bash
cleanup; trap - EXIT
az postgres flexible-server firewall-rule list -g rg-aiuw-demo-wus3 -n "$SERVER" -o table   # no operator-bootstrap rule
```

Then the `intake` app's latest revision becomes ready within a minute or so, and the deploy workflow's last step says so on its next run.

## Out-of-band log

Every command that changed Azure or GitHub outside the pipeline, newest last (`terraform.md` rule 29).

| Date | Who | Command | What it did |
| --- | --- | --- | --- |
| 2026-10-06 | Coding agent, at the owner's request | `bash infra/bootstrap/state-backend.sh` (twice) | First run made 15 changes: resource group, state container, deployment identity, two federated credentials, three scoped roles, the operator's blob role, four repository variables and the `demo` environment. Second run: `No changes.` |
| 2026-10-06 | Coding agent, as operator | `terraform init`, `plan -out=tfplan`, `apply tfplan` (test session 1) | Applied the `foundation` stack locally under the recorded exception to `terraform.md` rules 26 and 33. The first apply failed on the Foundry project (409, fixed in code); a second apply completed. |
| 2026-10-06 | Coding agent, as operator | `terraform state rm azurerm_resource_group.this`, `plan -destroy`, `apply`, `az cognitiveservices account purge` x3, `az resource delete` on the auto-created alert rule | Tore down test session 1. The resource group, state container and deployment identity were kept. |
| 2026-10-06 | Coding agent, as operator | Bring-up and tear-down again (test session 2) | Fresh apply in one run (1 imported, 103 added); the plan straight after showed no changes; then torn down the same way. Nothing is left running. |
