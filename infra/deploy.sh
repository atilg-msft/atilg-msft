#!/usr/bin/env bash
# Deploys polybot to Azure Container Apps.
#
# Requirements: az CLI logged in (`az login`), a target subscription selected
# (`az account set --subscription <id>`). Docker is NOT required locally --
# the image is built remotely with `az acr build`.
#
# Usage:
#   ./infra/deploy.sh <resource-group> <location> [namePrefix] [polybotMode]
#
# Example:
#   ./infra/deploy.sh polybot-rg westeurope polybot paper

set -euo pipefail

RESOURCE_GROUP="${1:?Usage: deploy.sh <resource-group> <location> [namePrefix] [polybotMode]}"
LOCATION="${2:?location is required, e.g. westeurope}"
NAME_PREFIX="${3:-polybot}"
POLYBOT_MODE="${4:-paper}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "==> Ensuring resource group $RESOURCE_GROUP exists in $LOCATION"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "==> Deploying infrastructure (Bicep) -- first pass creates ACR/Key Vault/App Config/storage"
echo "    The container app's first revision will fail to pull an image yet; that's expected."
DEPLOY_OUTPUT=$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$SCRIPT_DIR/main.bicep" \
  --parameters namePrefix="$NAME_PREFIX" polybotMode="$POLYBOT_MODE" \
  --query properties.outputs -o json)

ACR_LOGIN_SERVER=$(echo "$DEPLOY_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['acrLoginServer']['value'])")
ACR_NAME="${ACR_LOGIN_SERVER%%.*}"
CONTAINER_APP_NAME="${NAME_PREFIX}-app"

echo "==> Building and pushing the image remotely via ACR Tasks (no local Docker needed)"
az acr build \
  --registry "$ACR_NAME" \
  --image "polybot:latest" \
  "$REPO_ROOT"

echo "==> Forcing the Container App to pick up the freshly built image"
az containerapp update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --image "${ACR_LOGIN_SERVER}/polybot:latest" \
  --output none

echo ""
echo "==> Done. Outputs:"
echo "$DEPLOY_OUTPUT" | python3 -m json.tool

echo ""
echo "Next steps:"
echo "  - Open the control panel at the containerAppUrl above."
echo "  - For live trading, set Key Vault secrets:"
echo "      az keyvault secret set --vault-name <keyVaultName> --name polybot-private-key --value <0x...>"
echo "      az keyvault secret set --vault-name <keyVaultName> --name polybot-funder-address --value <0x...>"
echo "    then redeploy with polybotMode=live (az deployment group create ... -p polybotMode=live)."
echo "  - Tune strategy parameters any time via:"
echo "      az appconfig kv set --name <appConfigName> --key POLYBOT_MOMENTUM_THRESHOLD --value 0.1 --auth-mode login --yes"
