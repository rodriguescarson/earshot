#!/usr/bin/env bash
# Merge a pull request without GitHub Actions: run scripts/ci.sh on the PR's head merged with the
# current main, in a throwaway worktree, post the `local-ci` status on the head, and squash-merge
# only if it passed and nobody pushed to the PR meanwhile.
#
#   scripts/merge-pr.sh <number>
set -euo pipefail
n=${1:?usage: scripts/merge-pr.sh <pr-number>}
repo=$(git rev-parse --show-toplevel)
ci="$repo/scripts/ci.sh"  # main's copy, so a PR from before this script still gets the same checks

head=$(gh pr view "$n" --json headRefOid -q .headRefOid)
state=$(gh pr view "$n" --json state -q .state)
[ "$state" = OPEN ] || { echo "merge-pr: #$n is $state" >&2; exit 1; }

git fetch -q origin main "pull/$n/head"
wt=$(mktemp -d)/pr-$n
git worktree add -q --detach "$wt" "$head"
cleanup() { git worktree remove --force "$wt" 2>/dev/null || true; }
trap cleanup EXIT

(
  cd "$wt"
  git -c user.name=local-ci -c user.email=local-ci@localhost merge -q --no-edit origin/main ||
    { echo "merge-pr: #$n conflicts with main; resolve it on the branch first" >&2; exit 1; }
  CI_REPORT_SHA=$head "$ci" --report
)
gh pr merge "$n" --squash --match-head-commit "$head" --delete-branch
echo "merge-pr: #$n merged ($head tested merged with $(git rev-parse --short origin/main))"
