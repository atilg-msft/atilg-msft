#!/usr/bin/env bash
# Deploys polybot to Azure Container Apps.
#
# Requirements: az CLI logged in (`az login`), a target subscription selected
# (`az account set --subscription <id>`). Docker is NOT required locally --
# the image is built remotely with `az acr build`.
#
# Usage:
#   ./infra/deploy.sh <resource-group> [location] [namePrefix] [polybotMode]
#
# Example:
#   ./infra/deploy.sh polybot-rg                    # deploys to centralus
#   ./infra/deploy.sh polybot-rg westeurope          # or override the region

set -euo pipefail

RESOURCE_GROUP="${1:?Usage: deploy.sh <resource-group> [location] [namePrefix] [polybotMode]}"
LOCATION="${2:-centralus}"
NAME_PREFIX="${3:-polybot}"
POLYBOT_MODE="${4:-paper}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "==> Ensuring resource group $RESOURCE_GROUP exists in $LOCATION"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "==> Deploying infrastructure (Bicep) -- ACR/Key Vault/App Config/storage/Container App"
echo "    The Container App starts on a public placeholder image until the real one is built below."
DEPLOY_OUTPUT=$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$SCRIPT_DIR/main.bicep" \
  --parameters namePrefix="$NAME_PREFIX" polybotMode="$POLYBOT_MODE" \
  --query properties.outputs -o json)

ACR_LOGIN_SERVER=$(echo "$DEPLOY_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['acrLoginServer']['value'])")
ACR_NAME="${ACR_LOGIN_SERVER%%.*}"
APP_CONFIG_NAME=$(echo "$DEPLOY_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['appConfigName']['value'])")
APP_CONFIG_ENDPOINT=$(echo "$DEPLOY_OUTPUT" | python3 -c "import json,sys; print(json.load(sys.stdin)['appConfigEndpoint']['value'])")
CONTAINER_APP_NAME="${NAME_PREFIX}-app"

echo "==> Granting the deploying user 'App Configuration Data Owner' (needed to seed values below; harmless if already assigned)"
APP_CONFIG_ID=$(az appconfig show --name "$APP_CONFIG_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)
CURRENT_USER_OID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)
if [ -n "$CURRENT_USER_OID" ]; then
  az role assignment create \
    --assignee-object-id "$CURRENT_USER_OID" \
    --assignee-principal-type User \
    --role "App Configuration Data Owner" \
    --scope "$APP_CONFIG_ID" \
    --output none 2>/dev/null || true
fi

echo "==> Seeding App Configuration with default strategy/risk/scan parameters"
# Talks to the REST API directly with the deploying user's own AAD token
# rather than `az appconfig kv set`, which hits a CLI/SDK bug turning a
# non-200 response into an unhandled JSONDecodeError instead of a clean
# error. Non-fatal: the bot falls back to its own built-in defaults
# (polybot/config.py) for any key that isn't in App Configuration yet, so a
# failure here (e.g. the RBAC grant above hasn't propagated yet) shouldn't
# block the rest of the deploy -- rerun this script, or set values by hand
# in the Portal/`az appconfig kv set --auth-mode login`, whenever it's ready.
seed_appconfig_key() {
  local key="$1" value="$2" token code attempt
  for attempt in 1 2 3 4 5 6; do
    token=$(az account get-access-token --resource https://azconfig.io --query accessToken -o tsv)
    code=$(curl -sS -o /dev/null -w "%{http_code}" -X PUT \
      "${APP_CONFIG_ENDPOINT}/kv/${key}?api-version=1.0" \
      -H "Authorization: Bearer $token" \
      -H "Content-Type: application/vnd.microsoft.appconfig.kv+json" \
      -d "{\"value\": \"${value}\"}")
    if [ "$code" = "200" ]; then return 0; fi
    sleep 10
  done
  echo "    WARNING: could not set $key (HTTP $code) -- bot will use its built-in default"
  return 1
}

SEED_SETTINGS=(
  "POLYBOT_MODE=${POLYBOT_MODE}"
  "POLYBOT_POLL_INTERVAL_SECONDS=60"
  "POLYBOT_POSITION_CHECK_INTERVAL_SECONDS=5"
  "POLYBOT_MAX_MARKETS_SCANNED=200"
  "POLYBOT_MIN_VOLUME_24H=1000"
  "POLYBOT_MIN_LIQUIDITY=500"
  "POLYBOT_MIN_PRICE=0.15"
  "POLYBOT_MAX_PRICE=0.85"
  "POLYBOT_LOOKBACK_MINUTES=15"
  "POLYBOT_MOMENTUM_THRESHOLD=0.08"
  "POLYBOT_MEAN_REVERSION_THRESHOLD=0.08"
  "POLYBOT_TAKE_PROFIT_PCT=0.15"
  "POLYBOT_STOP_LOSS_PCT=0.10"
  "POLYBOT_MAX_HOLDING_MINUTES=240"
  "POLYBOT_COOLDOWN_MINUTES=30"
  "POLYBOT_MAX_CONCURRENT_POSITIONS=8"
  "POLYBOT_MAX_POSITION_USD=50"
  "POLYBOT_POSITION_PCT_OF_EQUITY=0.02"
  "POLYBOT_MAX_TOTAL_EXPOSURE_PCT=0.6"
  "POLYBOT_STARTING_CASH=1000"
  "POLYBOT_SLIPPAGE_BPS=50"
  "POLYBOT_FEE_BPS=0"
  "POLYBOT_STRATEGY=momentum"
  "POLYBOT_SIGNAL_FILTER=none"
  "POLYBOT_SMART_WALLET_COUNT=30"
  "POLYBOT_SMART_WALLET_PERIOD=month"
  "POLYBOT_SMART_WALLET_REFRESH_MINUTES=360"
  "POLYBOT_SMART_WALLET_LOOKBACK_MINUTES=30"
  "POLYBOT_SMART_WALLET_OVERRIDES="
)
for entry in "${SEED_SETTINGS[@]}"; do
  key="${entry%%=*}"
  value="${entry#*=}"
  seed_appconfig_key "$key" "$value" || true
done

echo "==> Building and pushing the image remotely via ACR Tasks (no local Docker needed)"
# Tag with something unique per run (git SHA, falling back to a timestamp)
# rather than only "latest" -- Container Apps needs the image *reference*
# to change to reliably roll a new revision, and re-pushing the same
# "latest" tag with a new digest underneath it is not guaranteed to do
# that. Also set a matching --revision-suffix as a second, independent
# guarantee of a fresh revision.
IMAGE_TAG="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || date +%s)"
az acr build \
  --registry "$ACR_NAME" \
  --image "polybot:${IMAGE_TAG}" \
  --image "polybot:latest" \
  "$REPO_ROOT"

echo "==> Forcing the Container App to pick up the freshly built image"
az containerapp update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --image "${ACR_LOGIN_SERVER}/polybot:${IMAGE_TAG}" \
  --revision-suffix "deploy${IMAGE_TAG}" \
  --output none

echo ""
echo "==> Done. Outputs:"
echo "$DEPLOY_OUTPUT" | python3 -m json.tool

echo ""
echo "Next steps:"
echo "  - Open the control panel at the containerAppUrl above."
echo "  - For live trading, no redeploy needed -- set Key Vault secrets:"
echo "      az keyvault secret set --vault-name <keyVaultName> --name polybot-private-key --value <0x...>"
echo "      az keyvault secret set --vault-name <keyVaultName> --name polybot-funder-address --value <0x...>"
echo "    then flip the mode in App Configuration:"
echo "      az appconfig kv set --name <appConfigName> --key POLYBOT_MODE --value live --auth-mode login --yes"
echo "  - Tune strategy parameters any time via:"
echo "      az appconfig kv set --name <appConfigName> --key POLYBOT_MOMENTUM_THRESHOLD --value 0.1 --auth-mode login --yes"
