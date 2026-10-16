#!/usr/bin/env bash
# Build the static site and deploy it to the earshot-web Static Web App (Free tier, 250 MB cap).
# The deployment token is fetched at run time and never written to disk or printed.
# Needs: az login, EARSHOT_SUBSCRIPTION (repo .env).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${EARSHOT_SUBSCRIPTION:?set EARSHOT_SUBSCRIPTION (see .env)}"
# The live worker (Stream 2's Container App). The CSP in public/staticwebapp.config.json names the
# same origin; change both together. Set VITE_LIVE_BASE= (empty) to build without live mode.
export VITE_LIVE_BASE="${VITE_LIVE_BASE-https://ca-earshot-worker.kinddune-86a4b9a4.swedencentral.azurecontainerapps.io}"

corepack pnpm build
# The player only loads full/bed/mono; speech and pan are listening-test arms (clips/listen-test).
find dist/clips -path 'dist/clips/demo-*' \( -name speech.m4a -o -name pan.m4a \) -delete
size_mb=$(du -sm dist | cut -f1)
echo "site size: ${size_mb} MB"
if [ "$size_mb" -ge 245 ]; then
  echo "too close to the 250 MB Free-tier cap; not deploying" >&2
  exit 1
fi

SWA_CLI_DEPLOYMENT_TOKEN=$(az staticwebapp secrets list --name earshot-web -g rg-earshot \
  --subscription "$EARSHOT_SUBSCRIPTION" --query properties.apiKey -o tsv) \
  npx -y @azure/static-web-apps-cli@latest deploy ./dist --env production --no-use-keychain 2>&1 |
  grep -v -i "token" || true

# The CLI can report an upload failure and still exit 0, so check the live site serves this build:
# the code bundle, and a clip manifest (a deploy that only changes clips keeps the same bundle).
site="https://kind-island-0f3823d0f.5.azurestaticapps.net"
want_js=$(grep -o 'assets/index-[^"]*\.js' dist/index.html | head -1)
probe=clips/demo-en-analyst/manifest.json
want_clip=$(shasum -a 256 "dist/$probe" | cut -d' ' -f1)
ok=""
for _ in 1 2 3 4 5 6; do
  got_js=$(curl -s -m 20 "$site/" | grep -o 'assets/index-[^"]*\.js' | head -1 || true)
  got_clip=$(curl -s -m 30 "$site/$probe" | shasum -a 256 | cut -d' ' -f1)
  if [ "$got_js" = "$want_js" ] && [ "$got_clip" = "$want_clip" ]; then ok=1; break; fi
  sleep 10
done
if [ -z "$ok" ]; then
  echo "deploy NOT live: bundle ${got_js:-none} (want $want_js), $probe matches: $([ "$got_clip" = "$want_clip" ] && echo yes || echo no)" >&2
  exit 1
fi
echo "live: $site serves $want_js and the new $probe"
