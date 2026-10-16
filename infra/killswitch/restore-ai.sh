#!/usr/bin/env bash
# Undo the budget kill switch: re-enable public network access on every
# Cognitive Services / Foundry account in rg-earshot.
# Usage: EARSHOT_SUBSCRIPTION=<subscription id> infra/killswitch/restore-ai.sh
set -euo pipefail
: "${EARSHOT_SUBSCRIPTION:?set EARSHOT_SUBSCRIPTION to the Earshot subscription id}"
RG=rg-earshot
API=2024-10-01
for id in $(az cognitiveservices account list -g "$RG" --subscription "$EARSHOT_SUBSCRIPTION" --query "[].id" -o tsv); do
  az rest --method patch --url "https://management.azure.com${id}?api-version=${API}" \
    --body '{"properties":{"publicNetworkAccess":"Enabled"}}' --query "properties.publicNetworkAccess" -o tsv
done
