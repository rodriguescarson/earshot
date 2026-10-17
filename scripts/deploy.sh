#!/usr/bin/env bash
# Deploy origin/main without GitHub Actions: what .github/workflows/deploy.yml does, from this
# machine. Builds the image remotely in ACR from a clean export of origin/main (never the working
# tree), rolls the worker and the MCP app to it, and runs the same smoke tests.
#
#   scripts/deploy.sh            refuses unless DEPLOY_ENABLED is "true" and main passed local-ci
#   scripts/deploy.sh --infra    the same, through infra/main.bicep (for changes to the infra itself)
#   scripts/deploy.sh --force    skips both checks (a manual restore after submission, T5)
#
# Needs `az login` and `gh auth`. The subscription comes from the repository's .env (never committed).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
env_file=${EARSHOT_ENV:-$(git rev-parse --path-format=absolute --git-common-dir)/../.env}
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a
: "${EARSHOT_SUBSCRIPTION:?not set in $env_file}"
SUB=(--subscription "$EARSHOT_SUBSCRIPTION")
RG=rg-earshot

mode=update
force=0
for a in "$@"; do
  case "$a" in
    --infra) mode=infra ;;
    --force) force=1 ;;
    *) echo "deploy: unknown option $a" >&2; exit 2 ;;
  esac
done

git fetch -q origin main
sha=$(git rev-parse origin/main)
tag=$(git rev-parse --short=7 origin/main)
if [ "$force" = 0 ]; then
  [ "$(gh variable get DEPLOY_ENABLED)" = true ] ||
    { echo "deploy: DEPLOY_ENABLED is not \"true\" (T5); nothing deployed" >&2; exit 1; }
  ci=$(gh api "repos/{owner}/{repo}/commits/$sha/statuses" -q '[.[] | select(.context=="local-ci")][0].state')
  [ "$ci" = success ] ||
    { echo "deploy: main $tag has local-ci \"${ci:-none}\"; run scripts/ci.sh --report on it" >&2; exit 1; }
fi

ACR=$(gh variable get ACR_NAME)
IMAGE="$ACR.azurecr.io/earshot:$tag"
echo "== build $IMAGE in ACR"
src=$(mktemp -d)
git archive "$sha" | tar -x -C "$src"
az acr build "${SUB[@]}" --registry "$ACR" --image "earshot:$tag" --file "$src/Dockerfile" "$src" \
  --no-logs --only-show-errors -o none
rm -rf "$src"

if [ "$mode" = infra ]; then
  n=$(az deployment group list "${SUB[@]}" -g "$RG" --query "length([?starts_with(name, 'earshot-infra-')])" -o tsv)
  echo "== bicep deployment earshot-infra-$((n + 1))"
  az deployment group create "${SUB[@]}" -g "$RG" -n "earshot-infra-$((n + 1))" -f infra/main.bicep \
    -p environmentName=earshot location=swedencentral webPubSubSku=Free_F1 \
    allowedOrigins=https://kind-island-0f3823d0f.5.azurestaticapps.net \
    workerImage="$IMAGE" mcpImage="$IMAGE" --only-show-errors -o none
else
  echo "== roll both apps (single-revision mode)"
  az containerapp update "${SUB[@]}" -g "$RG" -n ca-earshot-worker --image "$IMAGE" --only-show-errors -o none
  az containerapp update "${SUB[@]}" -g "$RG" -n ca-earshot-mcp --image "$IMAGE" --only-show-errors -o none
fi

echo "== smoke tests"
W=$(az containerapp show "${SUB[@]}" -g "$RG" -n ca-earshot-worker --query properties.configuration.ingress.fqdn -o tsv)
M=$(az containerapp show "${SUB[@]}" -g "$RG" -n ca-earshot-mcp --query properties.configuration.ingress.fqdn -o tsv)
for app in ca-earshot-worker ca-earshot-mcp; do
  img=$(az containerapp show "${SUB[@]}" -g "$RG" -n "$app" --query "properties.template.containers[0].image" -o tsv)
  [ "$img" = "$IMAGE" ] || { echo "deploy: $app runs $img, not $IMAGE" >&2; exit 1; }
done
for _ in 1 2 3 4 5 6 7 8 9 10; do if curl -fsS "https://$W/healthz" >/dev/null; then break; fi; sleep 15; done
curl -fsS "https://$W/api/status" && echo
# The live broadcaster is mounted: /api/live answers 200 (a match is on) or 503 (between matches).
code=$(curl -s -o /dev/null -w '%{http_code}' "https://$W/api/live")
[ "$code" = 200 ] || [ "$code" = 503 ] || { echo "deploy: /api/live answered $code" >&2; exit 1; }
for _ in 1 2 3 4 5 6 7 8 9 10; do if curl -fsS "https://$M/healthz" >/dev/null; then break; fi; sleep 15; done
echo "deploy: $IMAGE is live on the worker and the MCP app"
