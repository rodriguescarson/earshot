# Infrastructure

Everything runs in subscription `$EARSHOT_SUBSCRIPTION` (from `.env`), resource group `rg-earshot`, Sweden Central, and everything is keyless. Pass `--subscription` on every command: the CLI login can see other subscriptions.

## What `main.bicep` creates

- **Log Analytics** (1 GB/day cap) and **Application Insights** (Entra-only ingestion, `DisableLocalAuth`).
- **Storage** `stearshot…`, with `allowSharedKeyAccess=false`. Containers:
  - `admin`: the flag blob (cached-only, judge code hashes, spend counters);
  - `snapshots`;
  - `audio`;
  - `clips`;
  - `hidden`: the truth log, which no app identity can read.
- **Container Registry** (Basic). Images are built remotely with `az acr build`, never on a laptop.
- **User-assigned identities** `id-earshot-worker` and `id-earshot-mcp`, with RBAC scoped to single containers.
- **Web PubSub**: Free_F1 by default, `disableLocalAuth`, hub `earshot` with anonymous connect denied. Publishing is opt-in (`EARSHOT_WEBPUBSUB_PUBLISH=1`); the web client listens over SSE.
- **Container Apps environment** with two apps from the same image:
  - `ca-earshot-worker`: `uvicorn earshot.api.server:app`. Min = max = 1, because the match state lives in-process.
  - `ca-earshot-mcp`: `uvicorn earshot.api.mcp:app`. Min 0, max 2.

## Deploy

**By hand** (what-if first):

```bash
IMG=crearshot<suffix>.azurecr.io/earshot:<tag>
az deployment group what-if --subscription "$EARSHOT_SUBSCRIPTION" -g rg-earshot -f infra/main.bicep \
  -p environmentName=earshot location=swedencentral webPubSubSku=Free_F1 \
     allowedOrigins=https://kind-island-0f3823d0f.5.azurestaticapps.net workerImage=$IMG mcpImage=$IMG
```

Then run the same command with `az deployment group create … -n earshot-infra-<n>`. `main.parameters.json` carries azd placeholders, so pass the parameters explicitly as above.

**From GitHub** (`.github/workflows/deploy.yml`, OIDC, no stored credentials):
- It runs on pushes to `main` while the repository variable `DEPLOY_ENABLED` is `true`, and always on `workflow_dispatch`.
- It builds `earshot:<sha>` in ACR, rolls both apps with `az containerapp update --image`, and smoke-tests `/healthz`, `/api/status` and `/api/live`.
- **Identity:** the Entra app `earshot-github-deploy` (the `AZURE_CLIENT_ID` repository variable).
  - Contributor on the registry only, for `az acr build`.
  - Container Apps Contributor on `rg-earshot`.
- **Federated credentials, both for `main` only:**
  - `github-main`: `repo:rodriguescarson/earshot:ref:refs/heads/main`.
  - `github-main-ids`: `repo:rodriguescarson@6134699/earshot@1421880152:ref:refs/heads/main`. This is the immutable-ID subject format GitHub presents now. Without it, `azure/login` fails with AADSTS700213, as the first dispatch on 2026-10-16 did.
- **At submission**, set `DEPLOY_ENABLED=false` (T5), so after the deadline only a deliberate manual restore of the submitted revision ships.

## Runtime switches, no redeploy

- **Cached-only mode:** `earshot flags --cached-only on|off --reason "…"`. The worker polls the flag blob every 30 s and flips without a restart, and listeners stay connected.
- **Judge codes:** `earshot flags --add-judge-code <code>`, `--remove-judge-code <code>`, or `--rotate-judge-codes`. Only SHA-256 hashes are stored; codes work, or stop working, within 30 s.
- **The daily spend caps** (400K speech characters, 2.5M tokens) flip cached-only by themselves.

## Kill switch

- **Trigger:** usage alerts (500K tokens an hour, 3M a day, 150K speech characters an hour, 500K a day) fire action group `ag-earshot-killswitch`.
- **What it does:** it runs `infra/killswitch/disable-ai.ps1` in `aa-earshot-killswitch`. That disables the AI account's network access, then sets cached-only.
- **Undo:** `EARSHOT_SUBSCRIPTION=… infra/killswitch/restore-ai.sh` restores the AI account. Then turn cached-only off by hand.
