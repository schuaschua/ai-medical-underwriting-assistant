# Bootstrap and operations for the demo environment

Everything here is run by an operator, outside the pipeline. It covers the one-time bootstrap, bringing the `foundation` stack up, tearing it down, and a dated log of every out-of-band change made to Azure or GitHub.

The environment has a fixed cost of roughly USD 91 a month while it is up (Azure AI Search Basic and PostgreSQL B1ms, about USD 86, plus about USD 5 for the Basic container registry), before logs and usage, so it is brought up for testing and torn down afterwards. The bootstrap pieces are free and stay in place across teardowns.

## What you need

- `az` signed in to the subscription "Babaloo" as a user who holds Owner.
- `gh` signed in with admin rights on `schuaschua/ai-medical-underwriting-assistant`.
- Terraform 1.16.5 exactly (the stack pins it).

Most of this file has now been run; the log at the end says what and when. Where a passage still says "not yet run" or "expected", that part is written from the code and has not been observed.

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

Instead of exporting the two optional variables each time, an operator can keep them in a local file `infra/demo/foundation/owner.auto.tfvars`, which Terraform loads by itself and git ignores (`*.auto.tfvars`). On this machine that file already holds the owner's alert address and object id.

Both optional variables default to an empty list. Set the same values on every plan and apply, including a teardown: a run without them plans to remove the extra administrator and the email receivers. The pull-request workflow does not set them, so its plan shows that difference while they are in use.

Expected, not yet observed: the first plan, and the first plan after a teardown, show `1 to import` (the resource group) and the rest `to add`; an apply probably takes 15 to 20 minutes, with PostgreSQL and the Container Apps environment the slow ones.

The budget starts on the first day of the month in which it is created; nothing needs editing when the stack is re-created in a later month.

Since 2026-10-08 the stack creates a third model deployment on the Foundry account, beside the chat and the embedding one: `Cohere-rerank-v4.0-fast` (format `Cohere`, version `1`, Global Standard), the reranker of retrieval row `r4`. It has never been applied. A read-only catalogue query on that day (`az cognitiveservices model list --location westus3`) listed the model; whether the deployment is created from Terraform as written shows at the first apply. If the apply fails on that deployment alone, note the error in the log below and try, in this order:

1. **Marketplace terms.** If the error asks for them, accept the Cohere model's terms in the subscription (an out-of-band step for the log), then plan and apply again.
2. **Its own values.** If the error is about the content filter, the upgrade option, the deployment type or the capacity, give the `rerank` entry of `infra/demo/foundation/terraform.tfvars` its own (`content_filter`, where an empty text sends none; `version_upgrade_option`; `sku`; `capacity`), then plan and apply again.
3. **Without the reranker.** If the model cannot be deployed in this session, the environment still comes up without row `r4`:
   - take the whole `rerank` entry out of `model_deployments` in `infra/demo/foundation/terraform.tfvars` (the stack asks for the chat and the embedding deployment only) and apply the stack;
   - take `"r4"` out of `available_retriever_configs` in `infra/demo/app/terraform.tfvars` and apply that stack. It gives `retrieval` an empty `RETRIEVAL_RERANK_DEPLOYMENT`, which the service reads as none: it logs `row r4 is off` at its start and refuses the row with 409 `retriever_not_available`, and `workflow` and `verdict` no longer list it. The other five rows are unaffected;
   - what then shows: Compare falls back to its second pair, `r3` and `r5` (`WEB_COMPARE_FALLBACK_PAIR`), without any change to `web`; the bake-off runner scores the five rows that are listed, and the retrieval scoreboard shows `r4` as "not measured", with its method still named;
   - record in `_bmad-output/implementation-artifacts/deferred-work.md` that `r4` was not measured and why. Putting both entries back and applying both stacks brings the row back.

The checks of the call itself are in `deferred-work.md` (story 3.7, changed). If only its address or its sign-in is wrong (every `r4` search answers 502 and `retrieval` logs `rerank refused: reason=model_status_404`, `_401` or `_403`), set `rerank_url` (the whole address, a query string included) or `rerank_token_scope` in `infra/demo/app/terraform.tfvars` and apply the `app` stack: no code changes.

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

Added by story 1.5. Steps 0 to 3 were run as written on 2026-10-07 (test session 3) and worked; the upgrade note at the end has not been run. Add each run to the log below.

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
-- Created only if it is not there yet, so this step can be run again.
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$SERVICE_ROLE') THEN
    PERFORM pgaadauth_create_principal('$SERVICE_ROLE', false, false);
  END IF;
END
\$\$;
GRANT "$DEPLOY_ROLE" TO "$ME";
SQL
```

The step is safe to run again: the role is created only when it is missing, and the grant changes nothing the second time. After a teardown the identity is new but the database is new as well, so the role is missing and is created.

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
-- Every table and every sequence in the schema, whatever migrations have
-- added since this was written. A sequence that belongs to a table column
-- follows its table and is left out.
DO \$\$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT c.relname, c.relkind FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'intake' AND c.relkind IN ('r', 'p', 'S')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.objid = c.oid AND d.deptype IN ('a', 'i') AND c.relkind = 'S'
      )
    ORDER BY c.relkind DESC
  LOOP
    EXECUTE format(
      'ALTER %s intake.%I OWNER TO %I',
      CASE WHEN item.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
      item.relname, '$DEPLOY_ROLE'
    );
  END LOOP;
END
\$\$;

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

Its access to the two AI services it calls needs no operator step: the `app` stack gives the `intake` identity Cognitive Services User on the Azure AI Language account, for the redaction job, and on the Document Intelligence account, for the read model that reads the page text from each redacted PDF (its pages are pictures; owner's decision of 2026-10-10), and sets `INTAKE_READ_ENDPOINT`, `INTAKE_READ_ENTRA_AUTH` and `INTAKE_READ_API_VERSION`. In an environment that was up before that change, the next `terraform apply` of `infra/demo/app` adds the role assignment and a new `intake` revision; the image must be the one built with the reading.

**Upgrading an environment that is already set up.** Also not yet run. When `intake` ships a new migration and the database was bootstrapped before (the role exists, the schema is migrated and owned by the pipeline's role), do not repeat the whole section. Run step 0, then step 2 alone: the migrations bring the schema to the new head, and the default privileges of step 3 already cover tables and sequences that the pipeline's role creates. Run step 3 again only when a migration added objects while you, not the pipeline's role, ran it: the step hands every table and sequence in the schema over and repeats the grants, and is safe to run again. Finish with step 4. Until the migration step has run, `intake` reports "not ready", because its image carries a newer head than the database.

## 5. Database role for `workflow`

Added by story 1.6. Steps 0 to 3 were run as written on 2026-10-07 (test session 3) and worked, giving the rights table below; the upgrade note at the end has not been run. Add each run to the log below.

`workflow` owns schema `workflow`: case status, page status and the audit trail (spine AD-4, AD-8). As for `intake`, an operator does these steps once after the `foundation` stack is up, and again after every teardown. Until this section is done, `workflow` reports "not ready", a case cannot be started, and the upload screen shows an uploaded case as received but not started.

One thing differs from section 4. `workflow`'s migrations grant the service role its rights themselves, table by table, so there is no step that grants on "all tables" and there are no default privileges. On `workflow.audit_event` the service role gets `SELECT` and `INSERT` and nothing else: the trail is append-only, and the database is what refuses an `UPDATE` or a `DELETE`. A trigger on the table refuses `UPDATE`, `DELETE` and `TRUNCATE` for every other role as well, the owner included, and a downgrade of the migrations is refused while the table holds events. Since migration `0004` the table has a column `audit_event_seq` that the database numbers itself (an identity column): the service role's `INSERT` on the table covers it, its sequence needs no grant of its own, and step 3 leaves that sequence with its table. Migration `0005` adds two unique indexes on the table (one `case.started` and one `case.completed` event per case); they need no grant. Do not add a wider grant by hand: `workflow` checks the role it is connected as, and reports "not ready" (log line `not ready: code=audit_trail_writable`) if that role holds `UPDATE` or `DELETE` on the table or owns it.

Run the steps in this order, in one shell, from the repository root: the role first, because the migrations grant to it and fail if it does not exist.

**Step 0. Set up the shell, and open the firewall for your address.** As step 0 of section 4, with the role names of this service:

```bash
set -euo pipefail

SERVER="$(terraform -chdir=infra/demo/foundation output -raw postgresql_server_name)"
HOST="$(terraform -chdir=infra/demo/foundation output -raw postgresql_fqdn)"
DATABASE="$(terraform -chdir=infra/demo/foundation output -raw postgresql_database_name)"
ME="$(az ad signed-in-user show --query id -o tsv)"   # your database role is named after your object id
SERVICE_ROLE="id-aiuw-demo-wus3-workflow"             # must equal: terraform -chdir=infra/demo/app output workflow_database_role
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

**Step 1. Role.** The service's role is named after its identity. If section 4 was done in this bring-up, your own role is already a member of the pipeline's role and the second statement changes nothing.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=postgres user=$ME sslmode=require" <<SQL
-- Created only if it is not there yet, so this step can be run again.
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$SERVICE_ROLE') THEN
    PERFORM pgaadauth_create_principal('$SERVICE_ROLE', false, false);
  END IF;
END
\$\$;
GRANT "$DEPLOY_ROLE" TO "$ME";
SQL
```

The step is safe to run again: the role is created only when it is missing, and the grant changes nothing the second time.

**Step 2. Schema, migrations and the service role's rights.** This creates schema `workflow`, its tables and its version table, and grants the service role its rights on each. `WORKFLOW_DATABASE_SERVICE_ROLE` names the role to grant to; the run stops with a message if it is not set. It signs in with your own Azure sign-in, not with `PGPASSWORD`.

```bash
WORKFLOW_DATABASE_HOST="$HOST" WORKFLOW_DATABASE_NAME="$DATABASE" WORKFLOW_DATABASE_USER="$ME" \
WORKFLOW_DATABASE_SERVICE_ROLE="$SERVICE_ROLE" WORKFLOW_DATABASE_ENTRA_AUTH=true \
  uv run alembic -c services/workflow/alembic.ini upgrade head
```

**Step 3. Ownership.** The schema and every table, sequence and function in it are handed to the pipeline's role, so that it owns them as the spine's conventions say and can run later migrations. The step is safe to run again. Ownership does not change what the service role was granted in step 2. The last statement shows those rights: check that the `audit_event` row lists `INSERT` and `SELECT` only.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=$DATABASE user=$ME sslmode=require" <<SQL
ALTER SCHEMA workflow OWNER TO "$DEPLOY_ROLE";
-- Every table and every sequence in the schema, whatever migrations have
-- added since this was written. A sequence that belongs to a table column
-- follows its table and is left out.
DO \$\$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT c.relname, c.relkind FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'workflow' AND c.relkind IN ('r', 'p', 'S')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.objid = c.oid AND d.deptype IN ('a', 'i') AND c.relkind = 'S'
      )
    ORDER BY c.relkind DESC
  LOOP
    EXECUTE format(
      'ALTER %s workflow.%I OWNER TO %I',
      CASE WHEN item.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
      item.relname, '$DEPLOY_ROLE'
    );
  END LOOP;
  -- Functions too (the trigger function that guards the audit trail).
  FOR item IN
    SELECT p.oid::regprocedure AS signature FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'workflow'
  LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO %I', item.signature, '$DEPLOY_ROLE');
  END LOOP;
END
\$\$;

SELECT table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type) AS service_role_rights
FROM information_schema.role_table_grants
WHERE table_schema = 'workflow' AND grantee = '$SERVICE_ROLE'
GROUP BY table_name ORDER BY table_name;
SQL
```

Expected, not yet observed:

| table_name | service_role_rights |
| --- | --- |
| `alembic_version` | `SELECT` |
| `audit_event` | `INSERT, SELECT` |
| `case_status` | `INSERT, SELECT, UPDATE` |
| `page_status` | `INSERT, SELECT, UPDATE` |

**Step 4. Close up and check.** As step 4 of section 4.

```bash
cleanup; trap - EXIT
az postgres flexible-server firewall-rule list -g rg-aiuw-demo-wus3 -n "$SERVER" -o table   # no operator-bootstrap rule
```

Then the `workflow` app's latest revision becomes ready within a minute or so, and the deploy workflow's last step says so on its next run. `workflow`'s other access, to the Durable Task Scheduler's task hub, is an Azure role that the `app` stack assigns; nothing is done for it here.

**Upgrading an environment that is already set up.** Also not yet run. When `workflow` ships a new migration and the database was bootstrapped before, do not repeat the whole section. Run step 0, then step 2 alone, with `WORKFLOW_DATABASE_SERVICE_ROLE` set as there: the migrations bring the schema to the new head and grant the service role its rights on anything they add. Run step 3 again when a migration added a table, sequence or function while you, not the pipeline's role, ran it; the step takes whatever the schema holds and is safe to run again. Finish with step 4. Until the migration step has run, `workflow` reports "not ready", and its worker does not start, because its image carries a newer head than the database.

## 6. Database role for `classification`

Added by story 1.8. Not yet run: the environment was down while the story was built, so these steps are written from sections 4 and 5, which have been run, and are on the list for the final test session (`_bmad-output/implementation-artifacts/deferred-work.md`). Add each run to the log below.

`classification` owns schema `classification`: one table that holds the key row of each classification and, once it is done, what the page was classified as (spine AD-4, AD-6, AD-13). As for `intake`, an operator does these steps once after the `foundation` stack is up, and again after every teardown. Until this section is done, `classification` reports "not ready", and a started case fails once its document is redacted, because no page can be classified.

The steps are those of section 4 with this service's names: its migrations create the schema and the table and grant nothing, so step 3 grants the service role its rights and hands the schema to the pipeline's role. Run them in this order, in one shell, from the repository root.

**Step 0. Set up the shell, and open the firewall for your address.** The `trap` removes the firewall rule and the token when the shell exits, also after a failed step; `set -e` stops at the first error.

```bash
set -euo pipefail

SERVER="$(terraform -chdir=infra/demo/foundation output -raw postgresql_server_name)"
HOST="$(terraform -chdir=infra/demo/foundation output -raw postgresql_fqdn)"
DATABASE="$(terraform -chdir=infra/demo/foundation output -raw postgresql_database_name)"
ME="$(az ad signed-in-user show --query id -o tsv)"   # your database role is named after your object id
SERVICE_ROLE="id-aiuw-demo-wus3-classification"       # must equal: terraform -chdir=infra/demo/app output classification_database_role
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

**Step 1. Roles.** Principals are created in the `postgres` database; the service's role is named after its identity. Your own role is made a member of the pipeline's role, which step 3 needs; if section 4 was done in this bring-up it is one already, and that statement changes nothing.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=postgres user=$ME sslmode=require" <<SQL
-- Created only if it is not there yet, so this step can be run again.
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$SERVICE_ROLE') THEN
    PERFORM pgaadauth_create_principal('$SERVICE_ROLE', false, false);
  END IF;
END
\$\$;
GRANT "$DEPLOY_ROLE" TO "$ME";
SQL
```

The step is safe to run again: the role is created only when it is missing, and the grant changes nothing the second time. After a teardown the identity is new but the database is new as well, so the role is missing and is created.

**Step 2. Schema and migrations.** The service never migrates at start-up, and its readiness probe fails until the schema is at the migration head bundled in its image. This creates schema `classification`, its one table and its version table. It signs in with your own Azure sign-in, not with `PGPASSWORD`.

```bash
CLASSIFICATION_DATABASE_HOST="$HOST" CLASSIFICATION_DATABASE_NAME="$DATABASE" CLASSIFICATION_DATABASE_USER="$ME" \
CLASSIFICATION_DATABASE_ENTRA_AUTH=true \
  uv run alembic -c services/classification/alembic.ini upgrade head
```

**Step 3. Grants.** The service role gets data rights on its own schema and nothing else: no `CREATE`, because only migrations change the schema, and read-only on the version table, so the service can check its revision but never change it. Sequences are included for tables that later get one. The schema and its tables are handed to the pipeline's role, so that it owns them as the spine's conventions say and its later migrations need no further grant.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=$DATABASE user=$ME sslmode=require" <<SQL
-- Hand the schema and everything in it to the pipeline's role.
ALTER SCHEMA classification OWNER TO "$DEPLOY_ROLE";
-- Every table and every sequence in the schema, whatever migrations have
-- added since this was written. A sequence that belongs to a table column
-- follows its table and is left out.
DO \$\$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT c.relname, c.relkind FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'classification' AND c.relkind IN ('r', 'p', 'S')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.objid = c.oid AND d.deptype IN ('a', 'i') AND c.relkind = 'S'
      )
    ORDER BY c.relkind DESC
  LOOP
    EXECUTE format(
      'ALTER %s classification.%I OWNER TO %I',
      CASE WHEN item.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
      item.relname, '$DEPLOY_ROLE'
    );
  END LOOP;
END
\$\$;

GRANT USAGE ON SCHEMA classification TO "$SERVICE_ROLE";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA classification TO "$SERVICE_ROLE";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA classification TO "$SERVICE_ROLE";
-- The version table is read-only for the service.
REVOKE INSERT, UPDATE, DELETE ON classification.alembic_version FROM "$SERVICE_ROLE";

-- Tables and sequences that later migrations add, run by the pipeline's role.
-- This needs membership of that role, granted in step 1.
ALTER DEFAULT PRIVILEGES FOR ROLE "$DEPLOY_ROLE" IN SCHEMA classification
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "$SERVICE_ROLE";
ALTER DEFAULT PRIVILEGES FOR ROLE "$DEPLOY_ROLE" IN SCHEMA classification
  GRANT USAGE, SELECT ON SEQUENCES TO "$SERVICE_ROLE";
SQL
```

**Step 4. Close up and check.** Leaving the shell runs the `trap`; to stay in the shell, run it now.

```bash
cleanup; trap - EXIT
az postgres flexible-server firewall-rule list -g rg-aiuw-demo-wus3 -n "$SERVER" -o table   # no operator-bootstrap rule
```

Then the `classification` app's latest revision becomes ready within a minute or so, and the deploy workflow's last step says so on its next run. Its other access, to the chat deployment, is the Azure role Foundry User on the Foundry project, which the `app` stack assigns; nothing is done for it here.

**Upgrading an environment that is already set up.** Not yet run. When `classification` ships a new migration and the database was bootstrapped before (the role exists, the schema is migrated and owned by the pipeline's role), do not repeat the whole section. Run step 0, then step 2 alone: the migrations bring the schema to the new head, and the default privileges of step 3 already cover tables and sequences that the pipeline's role creates. Run step 3 again only when a migration added objects while you, not the pipeline's role, ran it: the step hands every table and sequence in the schema over and repeats the grants, and is safe to run again. Finish with step 4. Until the migration step has run, `classification` reports "not ready", because its image carries a newer head than the database.

## 7. Database role for `retrieval`, the manual and the ingestion job

Added by story 2.2. Not yet run: the environment was down while the story was built, so these steps are written from sections 4 and 6, which have been run or mirror ones that have, and are on the list for the final test session (`_bmad-output/implementation-artifacts/deferred-work.md`). Add each run to the log below.

`retrieval` owns schema `retrieval`: one table, `chunk`, that holds one row per rule of the underwriting manual with its text, context line and vector (spine AD-4, AD-12), and the blob container `manual`, which holds the manual's PDF. The service and its ingestion job share one image, one identity and so one database role. As for `intake`, an operator does these steps once after the `foundation` and `app` stacks are up, and again after every teardown. Until steps 0 to 4 are done, `retrieval` reports "not ready"; until steps 5 and 6 are done, the table is empty and no rule can be found.

Steps 0 to 4 are those of section 6 with this service's names, and one difference: the first migration creates the `vector` extension. The extension is on the server's allow-list (`azure.extensions = VECTOR` in the `foundation` stack), and creating it needs the `azure_pg_admin` role, which you hold as an Entra administrator of the server. Run the steps in this order, in one shell, from the repository root.

**Step 0. Set up the shell, and open the firewall for your address.** The `trap` removes the firewall rule and the token when the shell exits, also after a failed step; `set -e` stops at the first error.

```bash
set -euo pipefail

SERVER="$(terraform -chdir=infra/demo/foundation output -raw postgresql_server_name)"
HOST="$(terraform -chdir=infra/demo/foundation output -raw postgresql_fqdn)"
DATABASE="$(terraform -chdir=infra/demo/foundation output -raw postgresql_database_name)"
ME="$(az ad signed-in-user show --query id -o tsv)"   # your database role is named after your object id
SERVICE_ROLE="id-aiuw-demo-wus3-retrieval"            # must equal: terraform -chdir=infra/demo/app output retrieval_database_role
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

**Step 1. Roles.** Principals are created in the `postgres` database; the service's role is named after its identity. Your own role is made a member of the pipeline's role, which step 3 needs; if section 4 was done in this bring-up it is one already, and that statement changes nothing.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=postgres user=$ME sslmode=require" <<SQL
-- Created only if it is not there yet, so this step can be run again.
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$SERVICE_ROLE') THEN
    PERFORM pgaadauth_create_principal('$SERVICE_ROLE', false, false);
  END IF;
END
\$\$;
GRANT "$DEPLOY_ROLE" TO "$ME";
SQL
```

**Step 2. Schema and migrations.** The service never migrates at start-up, and its readiness probe fails until the schema is at the migration head bundled in its image. This creates the `vector` extension (in schema `public`, where the service role finds its type), schema `retrieval`, the `chunk` table with its stored full-text column and index, and the version table. It signs in with your own Azure sign-in, not with `PGPASSWORD`.

```bash
RETRIEVAL_DATABASE_HOST="$HOST" RETRIEVAL_DATABASE_NAME="$DATABASE" RETRIEVAL_DATABASE_USER="$ME" \
RETRIEVAL_DATABASE_ENTRA_AUTH=true \
  uv run alembic -c services/retrieval/alembic.ini upgrade head
```

If this step fails at `CREATE EXTENSION`, the server does not allow the extension or your role may not create it: check `az postgres flexible-server parameter show -g rg-aiuw-demo-wus3 -s "$SERVER" -n azure.extensions`, and note the finding in the log below. The extension stays with you as its owner; a later migration run by the pipeline's role does not create it again (`IF NOT EXISTS`).

**Step 3. Grants.** The service role gets data rights on its own schema and nothing else: no `CREATE`, because only migrations change the schema, and read-only on the version table. The ingestion job writes and deletes chunks with this role. The schema and its table are handed to the pipeline's role, as in section 6.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=$DATABASE user=$ME sslmode=require" <<SQL
-- Hand the schema and everything in it to the pipeline's role.
ALTER SCHEMA retrieval OWNER TO "$DEPLOY_ROLE";
DO \$\$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT c.relname, c.relkind FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'retrieval' AND c.relkind IN ('r', 'p', 'S')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.objid = c.oid AND d.deptype IN ('a', 'i') AND c.relkind = 'S'
      )
    ORDER BY c.relkind DESC
  LOOP
    EXECUTE format(
      'ALTER %s retrieval.%I OWNER TO %I',
      CASE WHEN item.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
      item.relname, '$DEPLOY_ROLE'
    );
  END LOOP;
END
\$\$;

GRANT USAGE ON SCHEMA retrieval TO "$SERVICE_ROLE";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA retrieval TO "$SERVICE_ROLE";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA retrieval TO "$SERVICE_ROLE";
-- The version table is read-only for the service.
REVOKE INSERT, UPDATE, DELETE ON retrieval.alembic_version FROM "$SERVICE_ROLE";

-- Tables and sequences that later migrations add, run by the pipeline's role.
-- This needs membership of that role, granted in step 1.
ALTER DEFAULT PRIVILEGES FOR ROLE "$DEPLOY_ROLE" IN SCHEMA retrieval
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "$SERVICE_ROLE";
ALTER DEFAULT PRIVILEGES FOR ROLE "$DEPLOY_ROLE" IN SCHEMA retrieval
  GRANT USAGE, SELECT ON SEQUENCES TO "$SERVICE_ROLE";
SQL
```

**Step 4. Close up and check.** Leaving the shell runs the `trap`; to stay in the shell, run it now.

```bash
cleanup; trap - EXIT
az postgres flexible-server firewall-rule list -g rg-aiuw-demo-wus3 -n "$SERVER" -o table   # no operator-bootstrap rule
```

Then the `retrieval` app's latest revision becomes ready within a minute or so.

**Step 5. Upload the manual.** The `foundation` stack owns the `manual` container; the manual is the committed, synthetic PDF. The upload signs in with your own Azure sign-in (`--auth-mode login`, never an account key), so you need a data role on the container for it: Storage Blob Data Contributor on the `manual` container, assigned to yourself for the upload only. The `trap` takes the role away again when the shell exits, also when the upload fails under `set -e`; the upload starts only once the role is honoured. Record the commands in the log below. (`retrieval` itself may only read the container.)

```bash
ACCOUNT="$(terraform -chdir=infra/demo/foundation output -raw storage_account_name)"
MANUAL_SCOPE="$(terraform -chdir=infra/demo/foundation output -json storage_container_ids | jq -r .manual)"

remove_upload_role() {
  # Always: your own write access to the container ends with this step.
  az role assignment delete --assignee "$ME" --role "Storage Blob Data Contributor" \
    --scope "$MANUAL_SCOPE" || true
}
trap remove_upload_role EXIT

az role assignment create --assignee-object-id "$ME" --assignee-principal-type User \
  --role "Storage Blob Data Contributor" --scope "$MANUAL_SCOPE"

# A new role assignment takes a minute or two to be honoured: wait until the
# container can be listed with it, for five minutes at most.
for attempt in $(seq 1 30); do
  if az storage blob list --auth-mode login --account-name "$ACCOUNT" --container-name manual \
    --num-results 1 --output none 2>/dev/null; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    echo "The role on the manual container is still not honoured." >&2
    exit 1
  fi
  sleep 10
done

az storage blob upload --auth-mode login --account-name "$ACCOUNT" --container-name manual \
  --name underwriting-manual.pdf --file data/manual/underwriting-manual.pdf \
  --content-type application/pdf --overwrite

remove_upload_role; trap - EXIT
az role assignment list --assignee "$ME" --scope "$MANUAL_SCOPE" -o table   # no Storage Blob Data Contributor of yours
```

The blob's name must equal `manual_blob_name` in `infra/demo/app/terraform.tfvars`.

**Step 6. Start the ingestion job and read its result.** The job is the Container Apps job `caj-aiuw-demo-wus3-ingest` (`terraform -chdir=infra/demo/app output -raw retrieval_ingest_job_name`), on the `retrieval` image and identity. It never starts by itself.

```bash
JOB="$(terraform -chdir=infra/demo/app output -raw retrieval_ingest_job_name)"
EXECUTION="$(az containerapp job start -g rg-aiuw-demo-wus3 -n "$JOB" --query name -o tsv)"
az containerapp job execution show -g rg-aiuw-demo-wus3 -n "$JOB" --job-execution-name "$EXECUTION" \
  --query properties.status -o tsv        # Running, then Succeeded or Failed
az containerapp job logs show -g rg-aiuw-demo-wus3 -n "$JOB" --execution "$EXECUTION" --container ingest
```

A run that succeeds ends with one line per chunk set (story 3.2): `ingestion done: pages=211 chunks=111 written=111 moved=0 removed=0 unchanged=0 skipped=no chunk_set=smart`, and the same for `chunk_set=fixed` with about 150 chunks (row `r1`'s baseline: vectors only, no chat call). A set that fails has its own `ingestion failed` line and leaves the other as written. A run that fails ends with `ingestion failed: code=<error code> reason=<what exactly>` and leaves the table as it was; the reasons are listed in the root `README.md`. Starting the job again is safe: over the same manual, with the same deployments and prompt, it ends early with `written=0 ... unchanged=111 skipped=yes`, without sending the manual to Document Intelligence or calling a model. What the index was built from is one row of `retrieval.ingest_run` (the manual's SHA-256, the prompt's digest, the deployment names). A run after which more than a tenth of the rules a chunk set defines would be defined by none of its chunks is refused (`too_many_chunks_removed`), because a manual that was parsed badly looks like one that lost its rules; its log names the chunks and the rules, and after checking them the one run is let through with `RETRIEVAL_INGEST_ALLOW_LARGE_REMOVAL` set to the chunk set it is meant for (`["smart"]`, `["fixed"]` or both) on that execution. A job that ends with `reason=chunk_sets_built_from_different_manuals` left one set on the manual before: run it again once the failing set is mended. Start it again after uploading a changed manual, and after a deploy that changed the context-line prompt or a deployment name (every chunk is then written again, about a hundred chat calls). The job's last step (story 3.3, retrieval row `r5`) loads the Azure AI Search index `manual-smart` from the `smart` chunks the table then holds, creating the index on the first run, and compares the two stores: `index load done: documents=111 uploaded=111 removed=0 unchanged=0 created=yes index=manual-smart`, and `uploaded=0 ... unchanged=111 created=no` on a run that changed nothing. It calls no model. `index load failed: code=<error code> reason=search_...` ends the job non-zero and leaves the table as the lines above it say; starting the job again brings the index up to date. It needs the two roles the `app` stack gives the `retrieval` identity on the search service (Search Service Contributor, Search Index Data Contributor); role assignments on a search service can take some minutes to apply, so a first run right after the apply may fail with `reason=search_read_index_status_403`.

When the index is loaded, the job's last step (story 3.8, retrieval row `r6`) has the search service hold the knowledge source `manual-smart-source` over that index and the knowledge base `manual-smart-base` on it, creating each only where it is missing: `knowledge base done: source=manual-smart-source base=manual-smart-base source_created=yes base_created=yes`, and `source_created=no base_created=no` on every later run. Not yet run in Azure. It calls no model itself: the knowledge base names the chat deployment as its planning model, and the index names the embedding deployment as its vectorizer, and the search service reaches both with its own identity, which the `app` stack gives Cognitive Services User on the Foundry account (`azurerm_role_assignment.search_foundry_user`). `knowledge base failed: code=<error code> reason=search_...` ends the job non-zero and leaves the table and the index as the lines above it say; starting the job again makes what is missing. These calls use the preview REST version (`search_agentic_api_version`, `2026-08-01-preview`). An index is created once and left as it is: over one that names no vectorizer the step fails with `reason=search_index_without_vectorizer`, so give `search_index_name` a new value and run the job again (no such index exists in Azure today). A knowledge source or knowledge base that is there is compared with what the settings name (the source's index; the base's source and its planning model) and never changed: after a new index name, or another chat deployment, the step fails with `reason=search_knowledge_source_differs` or `search_knowledge_base_differs` until they get new names too (`search_knowledge_source_name`, `search_knowledge_base_name`) or are deleted on the service first. Until the job has made the knowledge base, a search with `r6` answers 409 `retriever_not_available`.

**Upgrading an environment that is already set up.** Not yet run. When `retrieval` ships a new migration and the database was bootstrapped before, run step 0, then step 2 alone, and finish with step 4, as in section 6. The job does not need to run again for a migration that keeps the `chunk` table's rows.

## 8. Database role for `extraction`

Added by story 2.4. Not yet run: the environment was down while the story was built, so these steps are written from sections 4 and 6, and are on the list for the final test session (`_bmad-output/implementation-artifacts/deferred-work.md`). Add each run to the log below.

`extraction` owns schema `extraction`: table `fact_set`, which holds the key row and the stored result of each page's extraction, and table `fact`, one row per fact with its quote, whether the quote was found on the page and where (spine AD-4, AD-6, AD-14). As for `intake`, an operator does these steps once after the `foundation` stack is up, and again after every teardown. Until this section is done, `extraction` reports "not ready", and a case fails once a page reaches extraction (about five minutes after, when `workflow`'s retries of the extract command are spent), because no facts can be stored.

The steps are those of section 4 with this service's names: its migrations create the schema and the two tables and grant nothing, so step 3 grants the service role its rights and hands the schema to the pipeline's role. Run them in this order, in one shell, from the repository root.

**Step 0. Set up the shell, and open the firewall for your address.** The `trap` removes the firewall rule and the token when the shell exits, also after a failed step; `set -e` stops at the first error.

```bash
set -euo pipefail

SERVER="$(terraform -chdir=infra/demo/foundation output -raw postgresql_server_name)"
HOST="$(terraform -chdir=infra/demo/foundation output -raw postgresql_fqdn)"
DATABASE="$(terraform -chdir=infra/demo/foundation output -raw postgresql_database_name)"
ME="$(az ad signed-in-user show --query id -o tsv)"   # your database role is named after your object id
SERVICE_ROLE="id-aiuw-demo-wus3-extraction"           # must equal: terraform -chdir=infra/demo/app output extraction_database_role
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

**Step 1. Roles.** Principals are created in the `postgres` database; the service's role is named after its identity. Your own role is made a member of the pipeline's role, which step 3 needs; if section 4 was done in this bring-up it is one already, and that statement changes nothing.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=postgres user=$ME sslmode=require" <<SQL
-- Created only if it is not there yet, so this step can be run again.
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$SERVICE_ROLE') THEN
    PERFORM pgaadauth_create_principal('$SERVICE_ROLE', false, false);
  END IF;
END
\$\$;
GRANT "$DEPLOY_ROLE" TO "$ME";
SQL
```

The step is safe to run again: the role is created only when it is missing, and the grant changes nothing the second time. After a teardown the identity is new but the database is new as well, so the role is missing and is created.

**Step 2. Schema and migrations.** The service never migrates at start-up, and its readiness probe fails until the schema is at the migration head bundled in its image. This creates schema `extraction`, its two tables and its version table. It signs in with your own Azure sign-in, not with `PGPASSWORD`.

```bash
EXTRACTION_DATABASE_HOST="$HOST" EXTRACTION_DATABASE_NAME="$DATABASE" EXTRACTION_DATABASE_USER="$ME" \
EXTRACTION_DATABASE_ENTRA_AUTH=true \
  uv run alembic -c services/extraction/alembic.ini upgrade head
```

**Step 3. Grants.** The service role gets, table by table, the rights its code uses and nothing else. On `fact_set` all four data rights: a key row is inserted as `running`, updated when its result is stored or a stale one is taken over, and deleted when it is given up for a repeat. On `fact` only `SELECT` and `INSERT`: a fact is written once with its fact set's result and never changed or removed, so the role cannot alter a stored quote or its verification. No `CREATE`, because only migrations change the schema, and read-only on the version table, so the service can check its revision but never change it. Nothing is granted by default on tables a later migration adds: add each new table's line to this step with the migration that creates it. Sequences are included for tables that later get one. The schema and its tables are handed to the pipeline's role, so that it owns them as the spine's conventions say and its later migrations need no further grant.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=$DATABASE user=$ME sslmode=require" <<SQL
-- Hand the schema and everything in it to the pipeline's role.
ALTER SCHEMA extraction OWNER TO "$DEPLOY_ROLE";
-- Every table and every sequence in the schema, whatever migrations have
-- added since this was written. A sequence that belongs to a table column
-- follows its table and is left out.
DO \$\$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT c.relname, c.relkind FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'extraction' AND c.relkind IN ('r', 'p', 'S')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.objid = c.oid AND d.deptype IN ('a', 'i') AND c.relkind = 'S'
      )
    ORDER BY c.relkind DESC
  LOOP
    EXECUTE format(
      'ALTER %s extraction.%I OWNER TO %I',
      CASE WHEN item.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
      item.relname, '$DEPLOY_ROLE'
    );
  END LOOP;
END
\$\$;

GRANT USAGE ON SCHEMA extraction TO "$SERVICE_ROLE";
-- The key row of a page's extraction: inserted, settled or taken over, released.
GRANT SELECT, INSERT, UPDATE, DELETE ON extraction.fact_set TO "$SERVICE_ROLE";
-- Facts are added and read, never changed or removed.
GRANT SELECT, INSERT ON extraction.fact TO "$SERVICE_ROLE";
-- The version table is read-only for the service.
GRANT SELECT ON extraction.alembic_version TO "$SERVICE_ROLE";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA extraction TO "$SERVICE_ROLE";

-- Sequences that later migrations add, run by the pipeline's role. This needs
-- membership of that role, granted in step 1. Tables get no default grant:
-- each new table is granted by name above.
ALTER DEFAULT PRIVILEGES FOR ROLE "$DEPLOY_ROLE" IN SCHEMA extraction
  GRANT USAGE, SELECT ON SEQUENCES TO "$SERVICE_ROLE";
SQL
```

**Step 4. Close up and check.** Leaving the shell runs the `trap`; to stay in the shell, run it now.

```bash
cleanup; trap - EXIT
az postgres flexible-server firewall-rule list -g rg-aiuw-demo-wus3 -n "$SERVER" -o table   # no operator-bootstrap rule
```

Then the `extraction` app's latest revision becomes ready within a minute or so, and the deploy workflow's last step says so on its next run. Its other access, to the chat deployment it shares with `classification`, is the Azure role Foundry User on the Foundry project, which the `app` stack assigns; nothing is done for it here. It holds no role on storage: it reads pages from `intake` through Dapr.

`workflow` gets a migration with this story too (`0006`, table `decision_told`, with SELECT and INSERT for the service role): run section 5's upgrade steps for it in the same session, or `workflow` reports "not ready" and its worker does not start.

**Upgrading an environment that is already set up.** Not yet run. When `extraction` ships a new migration and the database was bootstrapped before (the role exists, the schema is migrated and owned by the pipeline's role), do not repeat the whole section. Run step 0, then step 2. If the migration added a table, add its grant to step 3 (the rights its code uses, by name) and run step 3 again: tables get no default grant here, and the step hands every table and sequence in the schema over and repeats the grants, so it is safe to run again. Finish with step 4. Until the migration step has run, `extraction` reports "not ready", because its image carries a newer head than the database.

## 9. Database role for `verdict`

Added by stories 2.5 and 2.6. Not yet run: the environment was down while the stories were built, so these steps are written from section 5, which has been run, and are on the list for the final test session (`_bmad-output/implementation-artifacts/deferred-work.md`). Add each run to the log below.

`verdict` owns schema `verdict`: table `verdict_run`, which holds the key row and the stored result of each run of the agent for a case and a retriever configuration, table `reason`, one row per cited reason of a run, and table `agent_step`, the log of every tool call the agent made (spine AD-4, AD-6, AD-15). As for `intake`, an operator does these steps once after the `foundation` stack is up, and again after every teardown. Until this section is done, `verdict` reports "not ready", and a case fails once every page is final and `workflow` commands its verdict runs, because no run can be stored.

The steps are those of section 5 with this service's names, not those of section 8: `verdict`'s migrations grant the service role its rights themselves, table by table, so there is no step that grants on "all tables" and there are no default privileges. The grants come from migration `0001`. On `verdict.agent_step` the service role gets `SELECT` and `INSERT` and nothing else: the step log is append-only, and the database is what refuses an `UPDATE` or a `DELETE`. A trigger on the table refuses `UPDATE`, `DELETE` and `TRUNCATE` for every other role as well, the owner included, and a downgrade of the migrations is refused while the table holds steps. The table's column `agent_step_seq` is numbered by the database itself (an identity column): the service role's `INSERT` on the table covers it, its sequence needs no grant of its own, and step 3 leaves that sequence with its table. On `verdict.verdict_run` the role gets `SELECT`, `INSERT`, `UPDATE` and `DELETE` (the key row is inserted, settled or taken over, released), and on `verdict.reason` `SELECT` and `INSERT`. Do not add a wider grant by hand.

Run the steps in this order, in one shell, from the repository root: the role first, because the migrations grant to it and fail if it does not exist.

**Step 0. Set up the shell, and open the firewall for your address.** As step 0 of section 4, with the role names of this service:

```bash
set -euo pipefail

SERVER="$(terraform -chdir=infra/demo/foundation output -raw postgresql_server_name)"
HOST="$(terraform -chdir=infra/demo/foundation output -raw postgresql_fqdn)"
DATABASE="$(terraform -chdir=infra/demo/foundation output -raw postgresql_database_name)"
ME="$(az ad signed-in-user show --query id -o tsv)"   # your database role is named after your object id
SERVICE_ROLE="id-aiuw-demo-wus3-verdict"              # must equal: terraform -chdir=infra/demo/app output verdict_database_role
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

**Step 1. Role.** The service's role is named after its identity. If section 4 or 5 was done in this bring-up, your own role is already a member of the pipeline's role and the second statement changes nothing.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=postgres user=$ME sslmode=require" <<SQL
-- Created only if it is not there yet, so this step can be run again.
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$SERVICE_ROLE') THEN
    PERFORM pgaadauth_create_principal('$SERVICE_ROLE', false, false);
  END IF;
END
\$\$;
GRANT "$DEPLOY_ROLE" TO "$ME";
SQL
```

The step is safe to run again: the role is created only when it is missing, and the grant changes nothing the second time.

**Step 2. Schema, migrations and the service role's rights.** This creates schema `verdict`, its three tables and its version table, and grants the service role its rights on each. `VERDICT_DATABASE_SERVICE_ROLE` names the role to grant to; the run stops with a message if it is not set. It signs in with your own Azure sign-in, not with `PGPASSWORD`.

```bash
VERDICT_DATABASE_HOST="$HOST" VERDICT_DATABASE_NAME="$DATABASE" VERDICT_DATABASE_USER="$ME" \
VERDICT_DATABASE_SERVICE_ROLE="$SERVICE_ROLE" VERDICT_DATABASE_ENTRA_AUTH=true \
  uv run alembic -c services/verdict/alembic.ini upgrade head
```

**Step 3. Ownership.** The schema and every table, sequence and function in it are handed to the pipeline's role, so that it owns them as the spine's conventions say and can run later migrations. The step is safe to run again. Ownership does not change what the service role was granted in step 2. The last statement shows those rights: check that the `agent_step` row lists `INSERT` and `SELECT` only.

```bash
psql -v ON_ERROR_STOP=1 "host=$HOST dbname=$DATABASE user=$ME sslmode=require" <<SQL
ALTER SCHEMA verdict OWNER TO "$DEPLOY_ROLE";
-- Every table and every sequence in the schema, whatever migrations have
-- added since this was written. A sequence that belongs to a table column
-- follows its table and is left out.
DO \$\$
DECLARE item record;
BEGIN
  FOR item IN
    SELECT c.relname, c.relkind FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'verdict' AND c.relkind IN ('r', 'p', 'S')
      AND NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.objid = c.oid AND d.deptype IN ('a', 'i') AND c.relkind = 'S'
      )
    ORDER BY c.relkind DESC
  LOOP
    EXECUTE format(
      'ALTER %s verdict.%I OWNER TO %I',
      CASE WHEN item.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
      item.relname, '$DEPLOY_ROLE'
    );
  END LOOP;
  -- Functions too (the trigger function that guards the step log).
  FOR item IN
    SELECT p.oid::regprocedure AS signature FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'verdict'
  LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO %I', item.signature, '$DEPLOY_ROLE');
  END LOOP;
END
\$\$;

SELECT table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type) AS service_role_rights
FROM information_schema.role_table_grants
WHERE table_schema = 'verdict' AND grantee = '$SERVICE_ROLE'
GROUP BY table_name ORDER BY table_name;
SQL
```

Expected, not yet observed:

| table_name | service_role_rights |
| --- | --- |
| `agent_step` | `INSERT, SELECT` |
| `alembic_version` | `SELECT` |
| `reason` | `INSERT, SELECT` |
| `verdict_run` | `DELETE, INSERT, SELECT, UPDATE` |

**Step 4. Close up and check.** As step 4 of section 4.

```bash
cleanup; trap - EXIT
az postgres flexible-server firewall-rule list -g rg-aiuw-demo-wus3 -n "$SERVER" -o table   # no operator-bootstrap rule
```

Then the `verdict` app's latest revision becomes ready within a minute or so, and the deploy workflow's last step says so on its next run. Its other access, to the chat deployment it shares with `classification` and `extraction`, is the Azure role Foundry User on the Foundry project, which the `app` stack assigns; nothing is done for it here. It holds no role on storage and none on a search service: it reads the case's facts from `extraction` and the manual's rules from `retrieval` through Dapr.

`workflow` may get a migration with these stories too: if its image carries a newer head than the database, run section 5's upgrade steps for it in the same session, or `workflow` reports "not ready" and its worker does not start.

**Upgrading an environment that is already set up.** Not yet run. When `verdict` ships a new migration and the database was bootstrapped before, do not repeat the whole section. Run step 0, then step 2 alone, with `VERDICT_DATABASE_SERVICE_ROLE` set as there: the migrations bring the schema to the new head and grant the service role its rights on anything they add. Run step 3 again when a migration added a table, sequence or function while you, not the pipeline's role, ran it; the step takes whatever the schema holds and is safe to run again. Finish with step 4. Until the migration step has run, `verdict` reports "not ready", because its image carries a newer head than the database.

## 10. The classifier's training pages and the training job

Added by story 4.2. Not yet run: the environment was down while the story was built, so these steps are written from section 7, and are on the list for the final test session (`_bmad-output/implementation-artifacts/deferred-work.md`). Add each run to the log below.

`classification` can classify with a second contender, `doc-intelligence`: a custom classification model of the Document Intelligence account, which a one-off job on the `classification` image and identity has the service build. The job calls no service of ours and redacts nothing. The pages it trains on are redacted before, by the pipeline itself, and an operator puts them into the blob container `classifier-training`, which `classification` owns. Do these steps once, after sections 4 to 6 (a case must run as far as the gate: `intake`, `workflow` and `classification` ready), and again after every teardown: the container and the classifier go with the environment. Until step 3 has run, a classify command with `doc-intelligence` stores nothing and answers `upstream_unavailable`, so a case started with it fails once `workflow`'s retries run out; cases started with `llm`, which is the default, are not affected.

**Step 1. Prepare the redacted training pages.** From the repository root, against the deployed `web`. Each of the 46 pages of `data/classifier-training/` is uploaded as a case of an eval run (so it is in neither of the underwriter's lists), redacted by Azure AI Language like any case page, classified by the chat model at the gate (five runs each) and stopped there. The redacted file `web` serves is written to `.work/classifier-training/`, with one list, `redacted-pages.json`.

One of the 46 pages is not prepared (owner's decision of 2026-10-10): `attending_physician_statement/train-001-p09-handwritten_note.pdf` is a picture with no text layer. Azure AI Language finds no text in such a document and redacts nothing; `intake` then has the read model look at the page, finds words in the picture that nobody checked for identifiers, and fails the case (`redaction_failed`; `reason=text_not_checked` in `intake`'s log). The tool skips that page, says so (`page skipped: file=... reason=text_not_checked`, and `prepared=45 skipped=1 skipped_files=...` at the end) and lists the other 45. The two blank pages have no text either: `intake` passes them on as redacted with nothing found (`code=no_text_to_redact`), and they are prepared. Every page type keeps at least five pages (`attending_physician_statement` has six). If the tool reports any other count of skipped pages, stop and look: it skips a page when its case failed at redaction and its source has no text layer, which is all `web` lets it see of the reason.

```bash
WEB="$(terraform -chdir=infra/demo/app output -raw web_url)"            # web's public address
uv run python -m bakeoff.training_pages --web-address "$WEB"
ls .work/classifier-training                                           # six folders and redacted-pages.json
```

A page whose case fails stops the command, naming the page; start it again with `--eval-run-id <the id it logged>`: no page is uploaded twice, but for the one whose case failed, which is uploaded again as a new case (a failed case stays failed). The command removes PDFs an earlier run left in the folder. Look at a few of the written PDFs before going on: names, addresses and numbers must be masked. **Never upload `data/classifier-training/` itself: those pages are unredacted, and they have the same file names as the prepared ones.** The job refuses them (the list names the MD5 of every redacted file, and no list vouches for the unredacted ones), but they would still be in the container.

**Step 2. Upload the folder.** The `foundation` stack owns the container. As for the manual (section 7, step 5), the upload signs in with your own Azure sign-in, and you hold a data role on the container for the upload only.

```bash
set -euo pipefail
ME="$(az ad signed-in-user show --query id -o tsv)"
ACCOUNT="$(terraform -chdir=infra/demo/foundation output -raw storage_account_name)"
TRAINING_SCOPE="$(terraform -chdir=infra/demo/foundation output -json storage_container_ids | jq -r '."classifier-training"')"

remove_upload_role() {
  # Always: your own write access to the container ends with this step.
  az role assignment delete --assignee "$ME" --role "Storage Blob Data Contributor" \
    --scope "$TRAINING_SCOPE" || true
}
trap remove_upload_role EXIT

az role assignment create --assignee-object-id "$ME" --assignee-principal-type User \
  --role "Storage Blob Data Contributor" --scope "$TRAINING_SCOPE"

# A new role assignment takes a minute or two to be honoured: wait until the
# container can be listed with it, for five minutes at most.
for attempt in $(seq 1 30); do
  if az storage blob list --auth-mode login --account-name "$ACCOUNT" \
    --container-name classifier-training --num-results 1 --output none 2>/dev/null; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    echo "The role on the classifier-training container is still not honoured." >&2
    exit 1
  fi
  sleep 10
done

# Empty the container first: it must hold this folder and nothing else.
az storage blob delete-batch --auth-mode login --account-name "$ACCOUNT" \
  --source classifier-training
az storage blob upload-batch --auth-mode login --account-name "$ACCOUNT" \
  --destination classifier-training --source .work/classifier-training --overwrite

remove_upload_role; trap - EXIT
az role assignment list --assignee "$ME" --scope "$TRAINING_SCOPE" -o table   # no Storage Blob Data Contributor of yours
```

The container must hold these 46 files (45 pages and the list) and nothing else, each with the content the list names: anything the list does not name, a listed page that is missing, or a page with other content stops the job.

**Step 3. Start the training job and read its result.** The job is the Container Apps job `caj-aiuw-demo-wus3-train` (`terraform -chdir=infra/demo/app output -raw classification_train_job_name`). It never starts by itself, and no deploy starts it.

```bash
JOB="$(terraform -chdir=infra/demo/app output -raw classification_train_job_name)"
EXECUTION="$(az containerapp job start -g rg-aiuw-demo-wus3 -n "$JOB" --query name -o tsv)"
az containerapp job execution show -g rg-aiuw-demo-wus3 -n "$JOB" --job-execution-name "$EXECUTION" \
  --query properties.status -o tsv        # Running, then Succeeded or Failed
az containerapp job logs show -g rg-aiuw-demo-wus3 -n "$JOB" --execution "$EXECUTION" --container train
```

A run that succeeds ends with `training done: classifier_id=page-types-v1 trained=yes pages=45 page_types=6`. Starting it again is safe: once the classifier of that id exists it ends with `trained=no` and asks for nothing to be built. A run that fails ends with `training failed: code=<error code> reason=<what exactly>` and status 1:

| Reason | What it means |
| --- | --- |
| `not_configured missing=...` | The job was not given the endpoint, the classifier id or the storage account. |
| `too_few_pages subject=<page type> pages=<n>` | A page type has fewer than five pages in the container. |
| `page_without_label subject=<blob>` | A blob, PDF or not, that `redacted-pages.json` does not name, or no such list: the pages were not prepared by step 1, or the container was not emptied first. |
| `page_missing subject=<blob>`, `page_listed_twice subject=<blob>` | A page the list names is not in the container, or the list names it twice. |
| `page_content_differs subject=<blob>` | The blob is not the redacted file the list vouches for: most likely the unredacted page of the same name. Empty the container and upload the prepared folder again. |
| `page_outside_its_folder subject=<blob>` | A PDF that is not in the folder of its page type. |
| `training_container_missing`, `page_list_malformed` | The container is not there, or the list is not the one step 1 writes. |
| `classifier_build_status_<code>` | Document Intelligence refused the build request: 403 while a role is not yet honoured, 400 if it does not take the request as written. A 409 (the classifier is being built already) is not a failure: the job waits for it. |
| `classifier_build_failed_<service code>` | The build was accepted and failed; the service's own code says why (for example that it cannot read the container). |
| `deadline` | The build took longer than `training_deadline_seconds`. Starting the job again finds the classifier if the build ended meanwhile. |

It needs three role assignments of the `app` stack: Cognitive Services User on the Document Intelligence account and Storage Blob Data Reader on the container for the `classification` identity, which lists the pages and reads their list, and Storage Blob Data Reader on the container for Document Intelligence's own identity, which reads the pages. The build request names the container's address and a folder per page type, and carries no key and no signed address. A classifier is built once and never changed. To train again on other pages: empty `.work/classifier-training/` and run step 1 again (a new eval run), empty the container and upload the folder as in step 2, give `classifier_id` in `infra/demo/app/terraform.tfvars` a new value, apply, and start the job.

**Step 4. Check with one case.** Upload a case as the customer, then start it with the classifier as the underwriter, and read its classifications:

```bash
curl -s -X POST "$WEB/api/cases/<case_id>/start" -H 'X-Demo-Role: underwriter' \
  -H 'content-type: application/json' -d '{"classifier_contender": "doc-intelligence"}'
curl -s "$WEB/api/cases/<case_id>/classifications" -H 'X-Demo-Role: underwriter'
```

Every page has a result with `"contender": "doc-intelligence"`, the service's confidence and the fixed reason; a page whose result failed says why in the case's audit trail.

**Step 5. Run the classification bake-off.** Added by story 4.3; not yet run. Only after step 4 shows results of `doc-intelligence`, and after the retrieval bake-off of the same session, so that the two do not share the chat deployment's token limit. From the repository root:

```bash
uv run python -m bakeoff --bake-off classification --deployed --web-address "$WEB"
```

It uploads the 22 case files twice (44 cases of an eval run, in neither of the underwriter's lists), stops each at the gate, and writes `data/scoreboards/classification.json` (`evals/README.md`, "The classification bake-off"). Read the printout before committing the file:

- Both contenders must be measured. `doc-intelligence: not measured` with a line `cannot be run here` means that the first case started with it failed with no page classified: `classification` refused the contender (no endpoint or classifier id in its settings), or the classifier is not trained and `workflow`'s retries ran out. Open that case's audit trail, go back to step 3, and run the bake-off again as a new run.
- Exit status 0 is a whole run. 3 means a file was not scored, a page has no result or a file's reasons were not read: start the command again with `--eval-run-id <the id it printed>` if it was a passing fault (a case that failed stays failed: a new run scores it again). 1 means a stored reason holds a planted identifier: do not publish, and look at the reason. 4 means the run broke off; resume it with its id.
- The cost per page of both contenders is stated in `evals/static-metrics.yaml` (2026-10-08, from Azure's price list). The `llm` figure rests on estimated tokens: after the run, replace it by the tokens the chat deployment's metrics show for the run, divided by its calls, and run the bake-off again (`deferred-work.md`, the last entry).

Commit the file, then build and deploy `web` again: the Scoreboard screen shows it in its second table.

## Out-of-band log

Every command that changed Azure or GitHub outside the pipeline, newest last (`terraform.md` rule 29).

| Date | Who | Command | What it did |
| --- | --- | --- | --- |
| 2026-10-06 | Coding agent, at the owner's request | `bash infra/bootstrap/state-backend.sh` (twice) | First run made 15 changes: resource group, state container, deployment identity, two federated credentials, three scoped roles, the operator's blob role, four repository variables and the `demo` environment. Second run: `No changes.` |
| 2026-10-06 | Coding agent, as operator | `terraform init`, `plan -out=tfplan`, `apply tfplan` (test session 1) | Applied the `foundation` stack locally under the recorded exception to `terraform.md` rules 26 and 33. The first apply failed on the Foundry project (409, fixed in code); a second apply completed. |
| 2026-10-06 | Coding agent, as operator | `terraform state rm azurerm_resource_group.this`, `plan -destroy`, `apply`, `az cognitiveservices account purge` x3, `az resource delete` on the auto-created alert rule | Tore down test session 1. The resource group, state container and deployment identity were kept. |
| 2026-10-06 | Coding agent, as operator | Bring-up and tear-down again (test session 2) | Fresh apply in one run (1 imported, 103 added); the plan straight after showed no changes; then torn down the same way. Nothing is left running. |
| 2026-10-07 | Coding agent, as operator | Bring-up; sections 4 and 5 as written; `az acr build` for `web`, `intake`, `workflow`; `terraform apply` of `infra/demo/app`; tear-down of both stacks (test session 3) | First deploy, done by hand from the branch because the deploy workflow runs only from `main`. Sections 4 and 5 ran without change. Three Container Apps came up ready; an upload and a case start worked against the public address. The `app` stack was destroyed (33 resources); the foundation tear-down was still running when this entry was written, so check that the resource group is empty. |
| 2026-10-07 | Coding agent, as operator | None: read-only check (`az resource list`, `az cognitiveservices account list-deleted`, `az group show`) | Confirmed the tear-down of test session 3 finished by itself: 103 foundation resources destroyed in about 26 minutes, the three AI accounts purged, the leftover alert rule removed. `rg-aiuw-demo-wus3` is empty and still there. |
