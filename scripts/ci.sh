#!/usr/bin/env bash
# Local CI: the checks in .github/workflows/ci.yml, run on this machine, because GitHub Actions starts
# no jobs on this repository (a billing hold that stays). With --report it posts the result as a
# `local-ci` commit status, so pull requests still show a check (statuses don't need Actions).
#
#   scripts/ci.sh             run every check on the working tree
#   scripts/ci.sh --report    the same, then post the status for HEAD (the tree must be clean)
#
# CI_REPORT_SHA overrides the commit the status is posted on (merge-pr.sh tests the PR merged with
# main, and reports on the PR's head).
set -Eeuo pipefail
cd "$(git rev-parse --show-toplevel)"

report=0
[ "${1:-}" = "--report" ] && report=1
sha=${CI_REPORT_SHA:-$(git rev-parse HEAD)}
if [ "$report" = 1 ] && [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "ci: the tree has uncommitted changes; a status must describe a commit" >&2
  exit 2
fi

status() {  # state, description
  [ "$report" = 1 ] || return 0
  gh api "repos/{owner}/{repo}/statuses/$sha" -f state="$1" -f context=local-ci \
    -f description="$2" >/dev/null
}

step="setup"
trap 'status failure "failed at: $step"; echo "ci: FAILED at: $step" >&2' ERR
status pending "running on $(hostname -s)"
run() {  # name, command...
  step=$1
  shift
  echo "== $step"
  "$@"
}

# python (ci.yml job "python")
run "uv sync" uv sync --frozen --quiet
run "ruff format" uv run ruff format --check .
run "ruff check" uv run ruff check .
run "pyright" uv run pyright
run "pytest" uv run pytest -q -o addopts="" -m "not slow"
req=$(mktemp)
run "uv export" uv export --frozen --no-dev --no-hashes --no-emit-project --quiet -o "$req"
run "pip-audit" uvx pip-audit==2.9.0 -r "$req" --strict --progress-spinner off
rm -f "$req"

# web (ci.yml job "web")
run "pnpm install" pnpm --dir web install --frozen-lockfile --silent
run "biome" pnpm --dir web exec biome check .
run "tsc" pnpm --dir web exec tsc -b --noEmit
run "vitest" pnpm --dir web exec vitest run
run "vite build" pnpm --dir web exec vite build --logLevel warn

# secrets (ci.yml job "secrets"): the whole history, as the Action scans every commit it is given
run "gitleaks" gitleaks git --redact --no-banner --log-level warn .

status success "ruff, pyright, pytest, pip-audit, biome, tsc, vitest, vite build, gitleaks"
echo "ci: all checks passed for $sha"
