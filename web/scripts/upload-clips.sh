#!/usr/bin/env bash
# Upload the recorded clips (data/clips) to the public "clips" container in the earshotclips
# storage account. Keyless: it uses your az login (Storage Blob Data Contributor on the account).
# Files go one by one with retries, so a slow connection only makes it slower, not fail; the site
# itself then deploys without the clips (deploy-swa.sh).
# Needs: az login, EARSHOT_SUBSCRIPTION (repo .env).
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${EARSHOT_SUBSCRIPTION:?set EARSHOT_SUBSCRIPTION (see .env)}"

account=earshotclips
common=(--account-name "$account" --auth-mode login --subscription "$EARSHOT_SUBSCRIPTION"
  -d clips -s data/clips --overwrite --max-connections 4 -o none)

# Manifests first would point at audio that is not there yet, so audio goes first.
az storage blob upload-batch "${common[@]}" --pattern "*.m4a" \
  --content-type audio/mp4 --content-cache-control "public, max-age=3600"
az storage blob upload-batch "${common[@]}" --pattern "*.json" \
  --content-type application/json --content-cache-control "public, max-age=300"

# Check: every local manifest is served, byte for byte.
base="https://$account.blob.core.windows.net/clips"
bad=0
while IFS= read -r f; do
  rel=${f#data/clips/}
  want=$(shasum -a 256 "$f" | cut -d' ' -f1)
  got=$(curl -s -m 30 "$base/$rel" | shasum -a 256 | cut -d' ' -f1)
  [ "$want" = "$got" ] || { echo "mismatch: $rel" >&2; bad=1; }
done < <(find data/clips -name '*.json')
[ "$bad" = 0 ] || exit 1
echo "clips live at $base"
