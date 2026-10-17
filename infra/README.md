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
  - `ca-earshot-worker`: `uvicorn earshot.api.server:app`, 1 vCPU / 2 GiB. Min = max = 1, because the match state lives in-process.
    Until the day before submission it runs with an override, so it costs nothing idle:
    `az containerapp update -n ca-earshot-worker --min-replicas 0 --max-replicas 1`.
    `scripts/deploy.sh --infra` restores always-on.
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

**From this machine, without GitHub Actions** (the way to deploy now). GitHub Actions starts no
jobs on this repository: there is a billing hold, and it stays. So CI, merges and deploys run
locally, through three scripts:

| Script | What it does |
|---|---|
| `scripts/ci.sh [--report]` | The checks in `ci.yml`: ruff, pyright, pytest, pip-audit, biome, tsc, vitest, vite build, gitleaks. `--report` posts the result as a `local-ci` commit status, which the API allows without Actions. |
| `scripts/merge-pr.sh <n>` | Runs `ci.sh` on the PR's head merged with current `main` in a throwaway worktree, posts the status on the head, then squash-merges with `--match-head-commit`. |
| `scripts/deploy.sh [--infra] [--force]` | `deploy.yml` from here. It refuses unless `DEPLOY_ENABLED` is `true` and `main` has `local-ci` success. It builds `earshot:<short sha>` in ACR from `git archive origin/main`, rolls both apps (or runs `main.bicep` with `--infra`), then runs the smoke tests. |

- **Order:** merge with `merge-pr.sh`, then run `scripts/ci.sh --report` on the updated `main`,
  then `deploy.sh`.
- **Identity:** your own `az login`; `.env` gives the subscription.
- **Checks:** there's no branch protection on a private free-plan repository. The `local-ci` status
  is the check, and the `pre-push` hook still refuses pushes to `main`.

**From GitHub** (`.github/workflows/deploy.yml`, OIDC, no stored credentials; it doesn't run while
the billing hold stays):
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
