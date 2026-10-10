#!/usr/bin/env bash
# One-time bootstrap for the aiuw demo environment (docs/standards/azure.md rule 31).
#
# Run by an operator who holds Owner on the subscription and admin on the
# GitHub repository, signed in with `az login` and `gh auth login`.
# Idempotent: every step checks first and changes only what is missing, so a
# second run prints "No changes." and exits 0. Stops on the first error.
#
# It creates, outside Terraform:
#   - the project resource group, with the six required tags
#   - the project's container in the central Terraform state account
#   - the pipeline's deployment identity, its two federated credentials and
#     its three scoped role assignments
#   - the operator's blob role on the state container (Entra auth for state)
#   - the GitHub repository variables and the `demo` environment
#
# It never changes the central state account itself, and never touches any
# other project's resources.

set -euo pipefail

# --- Names (same parts as infra/demo/foundation/locals.tf) -------------------
WORKLOAD="aiuw"
ENVIRONMENT="demo"
LOCATION="westus3"
REGION_SHORT="wus3"
OWNER="darrel"
GITHUB_REPO="schuaschua/ai-medical-underwriting-assistant"
REPO_URL="https://github.com/${GITHUB_REPO}"

RESOURCE_GROUP="rg-${WORKLOAD}-${ENVIRONMENT}-${REGION_SHORT}"
DEPLOY_IDENTITY="id-${WORKLOAD}-${ENVIRONMENT}-${REGION_SHORT}-deploy"

# Central state backend, shared by every project. Only a container is added.
STATE_RESOURCE_GROUP="rg-tfstate-sea"
STATE_ACCOUNT="stdjtfstatesea"
STATE_CONTAINER="${WORKLOAD}"

# The subscription this script may change, by name (the id is never committed).
# Override with EXPECTED_SUBSCRIPTION_NAME if the subscription is renamed.
EXPECTED_SUBSCRIPTION_NAME="${EXPECTED_SUBSCRIPTION_NAME:-Babaloo}"

# Resource providers the stacks use (the azurerm provider registers none).
PROVIDERS=(
  Microsoft.App
  Microsoft.DBforPostgreSQL
  Microsoft.CognitiveServices
  Microsoft.Search
  Microsoft.DurableTask
  Microsoft.Storage
  Microsoft.ContainerRegistry
  Microsoft.OperationalInsights
  Microsoft.Insights
  Microsoft.AlertsManagement
  Microsoft.ManagedIdentity
  Microsoft.Consumption
)

# The only roles the deployment identity may assign or remove, and only for
# service principals (azure.md rule 9; the list is in the project table there).
RUNTIME_ROLES=(
  "AcrPull"
  "Monitoring Metrics Publisher"
  "Storage Blob Data Contributor"
  "Storage Blob Data Reader"
  "Cognitive Services User"
  "Foundry User"
  "Search Index Data Contributor"
  "Search Service Contributor"
  "Durable Task Data Contributor"
)

CHANGES=0
changed() {
  CHANGES=$((CHANGES + 1))
  echo "  changed: $*"
}
ok() { echo "  ok:      $*"; }

for tool in az gh python3; do
  command -v "$tool" >/dev/null || {
    echo "error: $tool is not installed" >&2
    exit 1
  }
done

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
SUBSCRIPTION_NAME="$(az account show --query name -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
OPERATOR_OBJECT_ID="$(az ad signed-in-user show --query id -o tsv)"
echo "Subscription: ${SUBSCRIPTION_NAME}"

# --- Preflight: nothing is changed until all three checks pass ---------------
[[ "$SUBSCRIPTION_NAME" == "$EXPECTED_SUBSCRIPTION_NAME" ]] || {
  echo "error: az is signed in to subscription '${SUBSCRIPTION_NAME}', expected '${EXPECTED_SUBSCRIPTION_NAME}'" >&2
  exit 1
}
az storage account show -n "$STATE_ACCOUNT" -g "$STATE_RESOURCE_GROUP" -o none 2>/dev/null || {
  echo "error: state account ${STATE_ACCOUNT} not found in ${STATE_RESOURCE_GROUP}, or it cannot be read" >&2
  exit 1
}
if [[ "$(az group exists -n "$RESOURCE_GROUP")" == "true" ]]; then
  EXISTING_LOCATION="$(az group show -n "$RESOURCE_GROUP" --query location -o tsv)"
  [[ "$EXISTING_LOCATION" == "$LOCATION" ]] || {
    echo "error: ${RESOURCE_GROUP} exists in '${EXISTING_LOCATION}', expected '${LOCATION}'" >&2
    exit 1
  }
fi

RESOURCE_GROUP_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}"
STATE_ACCOUNT_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${STATE_RESOURCE_GROUP}/providers/Microsoft.Storage/storageAccounts/${STATE_ACCOUNT}"
STATE_CONTAINER_ID="${STATE_ACCOUNT_ID}/blobServices/default/containers/${STATE_CONTAINER}"

role_id() {
  local id
  id="$(az role definition list --name "$1" --query "[0].name" -o tsv)"
  [[ -n "$id" ]] || {
    echo "error: role '$1' not found" >&2
    exit 1
  }
  echo "$id"
}

# ensure_role <principal object id> <principal type> <role name> <scope>
ensure_role() {
  local principal="$1" type="$2" role="$3" scope="$4" existing
  existing="$(az role assignment list --assignee "$principal" --role "$role" --scope "$scope" \
    --query "length(@)" -o tsv)"
  if [[ "$existing" == "0" ]]; then
    az role assignment create --assignee-object-id "$principal" --assignee-principal-type "$type" \
      --role "$role" --scope "$scope" -o none
    changed "granted '${role}' on ${scope##*/}"
  else
    ok "'${role}' on ${scope##*/}"
  fi
}

echo "Resource providers"
for provider in "${PROVIDERS[@]}"; do
  state="$(az provider show -n "$provider" --query registrationState -o tsv)"
  if [[ "$state" != "Registered" ]]; then
    az provider register -n "$provider" --wait -o none
    changed "registered ${provider}"
  else
    ok "${provider}"
  fi
done

echo "Resource group ${RESOURCE_GROUP}"
TAGS=(
  "workload=${WORKLOAD}"
  "env=${ENVIRONMENT}"
  "owner=${OWNER}"
  "managedby=terraform"
  "datatype=synthetic"
  "repo=${REPO_URL}"
)
if [[ "$(az group exists -n "$RESOURCE_GROUP")" != "true" ]]; then
  az group create -n "$RESOURCE_GROUP" -l "$LOCATION" --tags "${TAGS[@]}" -o none
  changed "created ${RESOURCE_GROUP} in ${LOCATION}"
else
  ok "exists (its tags are managed by the foundation stack from here on)"
fi

echo "State container ${STATE_CONTAINER} in ${STATE_ACCOUNT}"
if ! az storage container-rm show --storage-account "$STATE_ACCOUNT" -g "$STATE_RESOURCE_GROUP" \
  -n "$STATE_CONTAINER" -o none 2>/dev/null; then
  az storage container-rm create --storage-account "$STATE_ACCOUNT" -g "$STATE_RESOURCE_GROUP" \
    -n "$STATE_CONTAINER" -o none
  changed "created container ${STATE_CONTAINER}"
else
  ok "exists"
fi
# Checks only: the account is shared, so this script never changes it.
if SHARED_KEY="$(az storage account show -n "$STATE_ACCOUNT" -g "$STATE_RESOURCE_GROUP" \
  --query allowSharedKeyAccess -o tsv 2>/dev/null)"; then
  [[ "$SHARED_KEY" == "false" ]] ||
    echo "  warning: shared-key access is not disabled on ${STATE_ACCOUNT} (azure.md rule 29)"
else
  echo "  warning: could not read the shared-key setting of ${STATE_ACCOUNT}; not checked"
fi
if VERSIONING="$(az storage account blob-service-properties show --account-name "$STATE_ACCOUNT" \
  -g "$STATE_RESOURCE_GROUP" --query isVersioningEnabled -o tsv 2>/dev/null)"; then
  [[ "$VERSIONING" == "true" ]] ||
    echo "  warning: blob versioning is off on ${STATE_ACCOUNT} (azure.md rule 29)"
else
  echo "  warning: could not read the blob versioning setting of ${STATE_ACCOUNT}; not checked"
fi

echo "Deployment identity ${DEPLOY_IDENTITY}"
# It lives in the state resource group, outside the group it can change, but
# its location is the project region.
if ! az identity show -g "$STATE_RESOURCE_GROUP" -n "$DEPLOY_IDENTITY" -o none 2>/dev/null; then
  az identity create -g "$STATE_RESOURCE_GROUP" -n "$DEPLOY_IDENTITY" -l "$LOCATION" --tags \
    "workload=${WORKLOAD}" "env=${ENVIRONMENT}" "owner=${OWNER}" "managedby=bootstrap" \
    "datatype=synthetic" "repo=${REPO_URL}" -o none
  changed "created ${DEPLOY_IDENTITY}"
  # A new principal takes a moment to appear in the directory.
  sleep 30
else
  ok "exists"
fi
DEPLOY_CLIENT_ID="$(az identity show -g "$STATE_RESOURCE_GROUP" -n "$DEPLOY_IDENTITY" --query clientId -o tsv)"
DEPLOY_PRINCIPAL_ID="$(az identity show -g "$STATE_RESOURCE_GROUP" -n "$DEPLOY_IDENTITY" --query principalId -o tsv)"

echo "Federated credentials"
# The immutable subject prefix comes from GitHub, never from the repo name.
SUBJECT_PREFIX="$(gh api "repos/${GITHUB_REPO}/actions/oidc/customization/sub" --jq '.sub_claim_prefix')"
[[ "$SUBJECT_PREFIX" == repo:*@*/*@* ]] || {
  echo "error: GitHub did not return an immutable subject prefix (got '${SUBJECT_PREFIX}')" >&2
  exit 1
}
ensure_federated_credential() {
  local name="$1" subject="$2" current
  current="$(az identity federated-credential show -g "$STATE_RESOURCE_GROUP" \
    --identity-name "$DEPLOY_IDENTITY" -n "$name" --query subject -o tsv 2>/dev/null || true)"
  if [[ "$current" != "$subject" ]]; then
    az identity federated-credential create -g "$STATE_RESOURCE_GROUP" \
      --identity-name "$DEPLOY_IDENTITY" -n "$name" \
      --issuer "https://token.actions.githubusercontent.com" \
      --subject "$subject" --audiences "api://AzureADTokenExchange" -o none
    changed "set federated credential ${name}"
  else
    ok "${name}"
  fi
}
ensure_federated_credential "github-pull-request" "${SUBJECT_PREFIX}:pull_request"
ensure_federated_credential "github-environment-${ENVIRONMENT}" "${SUBJECT_PREFIX}:environment:${ENVIRONMENT}"

echo "Deployment identity roles"
ensure_role "$DEPLOY_PRINCIPAL_ID" ServicePrincipal "Contributor" "$RESOURCE_GROUP_ID"
ensure_role "$DEPLOY_PRINCIPAL_ID" ServicePrincipal "Storage Blob Data Contributor" "$STATE_CONTAINER_ID"

# Role Based Access Control Administrator, limited by a condition to the
# runtime roles and to service principals.
ROLE_GUIDS=""
for role in "${RUNTIME_ROLES[@]}"; do
  ROLE_GUIDS+="${ROLE_GUIDS:+, }$(role_id "$role")"
done
CONDITION="((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${ROLE_GUIDS}} AND @Request[Microsoft.Authorization/roleAssignments:PrincipalType] ForAnyOfAnyValues:StringEqualsIgnoreCase {'ServicePrincipal'})) AND ((!(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})) OR (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${ROLE_GUIDS}} AND @Resource[Microsoft.Authorization/roleAssignments:PrincipalType] ForAnyOfAnyValues:StringEqualsIgnoreCase {'ServicePrincipal'}))"
RBAC_ROLE="Role Based Access Control Administrator"
RBAC_ASSIGNMENTS="$(az role assignment list --assignee "$DEPLOY_PRINCIPAL_ID" --role "$RBAC_ROLE" \
  --scope "$RESOURCE_GROUP_ID" -o json)"
RBAC_COUNT="$(python3 -c 'import json, sys; print(len(json.load(sys.stdin)))' <<<"$RBAC_ASSIGNMENTS")"
if [[ "$RBAC_COUNT" -gt 1 ]]; then
  echo "error: ${DEPLOY_IDENTITY} holds ${RBAC_COUNT} '${RBAC_ROLE}' assignments on ${RESOURCE_GROUP}; expected one. Remove the extra ones by hand." >&2
  exit 1
elif [[ "$RBAC_COUNT" -eq 0 ]]; then
  az role assignment create --assignee-object-id "$DEPLOY_PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal --role "$RBAC_ROLE" --scope "$RESOURCE_GROUP_ID" \
    --condition "$CONDITION" --condition-version "2.0" -o none
  changed "granted '${RBAC_ROLE}' with the runtime-role condition"
else
  # Prints the assignment with the wanted condition, or nothing if it already has it.
  RBAC_UPDATED="$(CONDITION="$CONDITION" python3 -c '
import json, os, sys
assignment = json.load(sys.stdin)[0]
wanted = os.environ["CONDITION"]
if assignment.get("condition") != wanted or assignment.get("conditionVersion") != "2.0":
    assignment["condition"] = wanted
    assignment["conditionVersion"] = "2.0"
    print(json.dumps(assignment))
' <<<"$RBAC_ASSIGNMENTS")"
  if [[ -n "$RBAC_UPDATED" ]]; then
    # The runtime role list changed: update the condition in place.
    az role assignment update --role-assignment "$RBAC_UPDATED" -o none
    changed "updated the runtime-role condition on '${RBAC_ROLE}'"
  else
    ok "'${RBAC_ROLE}' with the runtime-role condition"
  fi
fi

echo "Operator access to the state container"
# Entra auth for state needs a data-plane role; Owner alone does not give one.
ensure_role "$OPERATOR_OBJECT_ID" User "Storage Blob Data Contributor" "$STATE_CONTAINER_ID"

echo "GitHub repository variables"
ensure_variable() {
  local name="$1" value="$2" current
  current="$(gh variable get "$name" -R "$GITHUB_REPO" 2>/dev/null || true)"
  if [[ "$current" != "$value" ]]; then
    gh variable set "$name" -R "$GITHUB_REPO" --body "$value"
    changed "set ${name}"
  else
    ok "${name}"
  fi
}
ensure_variable AZURE_TENANT_ID "$TENANT_ID"
ensure_variable AZURE_SUBSCRIPTION_ID "$SUBSCRIPTION_ID"
ensure_variable AZURE_CLIENT_ID "$DEPLOY_CLIENT_ID"
# The deployment identity cannot look itself up (it has no rights on the state
# resource group), so the stack takes its object id as an input.
ensure_variable AZURE_DEPLOY_PRINCIPAL_ID "$DEPLOY_PRINCIPAL_ID"

echo "GitHub environment ${ENVIRONMENT}"
ENVIRONMENT_POLICY="$(gh api "repos/${GITHUB_REPO}/environments/${ENVIRONMENT}" \
  --jq '.deployment_branch_policy.custom_branch_policies' 2>/dev/null || true)"
if [[ "$ENVIRONMENT_POLICY" != "true" ]]; then
  gh api -X PUT "repos/${GITHUB_REPO}/environments/${ENVIRONMENT}" --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
  changed "created environment ${ENVIRONMENT} with a custom branch policy"
else
  ok "exists"
fi
BRANCH_POLICIES="$(gh api "repos/${GITHUB_REPO}/environments/${ENVIRONMENT}/deployment-branch-policies" \
  --jq '[.branch_policies[].name] | join(",")')"
if [[ "$BRANCH_POLICIES" != "main" ]]; then
  [[ -z "$BRANCH_POLICIES" ]] || {
    echo "error: environment ${ENVIRONMENT} allows branches '${BRANCH_POLICIES}'; expected only 'main'" >&2
    exit 1
  }
  gh api -X POST "repos/${GITHUB_REPO}/environments/${ENVIRONMENT}/deployment-branch-policies" \
    -f name=main -f type=branch >/dev/null
  changed "limited environment ${ENVIRONMENT} to branch main"
else
  ok "deploys only from main"
fi

echo
if [[ "$CHANGES" -eq 0 ]]; then
  echo "No changes."
else
  echo "${CHANGES} change(s) made."
fi
