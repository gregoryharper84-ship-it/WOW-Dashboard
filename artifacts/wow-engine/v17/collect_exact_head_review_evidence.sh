#!/usr/bin/env bash
# Read-only snapshot ONLY; never execute code from a PR head.
# Invocation must originate from protected main with a read-only GitHub token.
set -euo pipefail
: "${GITHUB_REPOSITORY:?missing repository}"
: "${GH_TOKEN:?missing read-only github token}"
: "${PR_NUMBER:?missing PR_NUMBER}"
: "${EXPECTED_HEAD_SHA:?missing SHA}"
: "${EVIDENCE_OUTPUT:?missing output path}"
[ "$GITHUB_REPOSITORY" = "gregoryharper84-ship-it/WOW-Dashboard" ] || { echo 'REPO_MISMATCH' >&2; exit 2; }
[[ "$PR_NUMBER" =~ ^[1-9][0-9]*$ ]] || { echo 'PR_NUMBER_INVALID' >&2; exit 2; }
[[ "$EXPECTED_HEAD_SHA" =~ ^[a-f0-9]{40}$ ]] || { echo 'SHA_INVALID' >&2; exit 2; }
pr=$(gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}")
[[ $(jq -r '.head.sha' <<< "$pr") = "$EXPECTED_HEAD_SHA" ]] || { echo 'PR_HEAD_MOVED' >&2; exit 2; }
files=$(gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}/files?per_page=100")
reviews=$(gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}/reviews?per_page=100")
checks_json=$(gh api "/repos/${GITHUB_REPOSITORY}/commits/${EXPECTED_HEAD_SHA}/check-runs?per_page=100")
[[ $(jq -r 'length' <<< "$files") -lt 100 ]] || { echo 'FILES_INVENTORY_TRUNCATED' >&2; exit 2; }
[[ $(jq -r 'length' <<< "$reviews") -lt 100 ]] || { echo 'REVIEWS_INVENTORY_TRUNCATED' >&2; exit 2; }
[[ $(jq -r '.total_count' <<< "$checks_json") -lt 100 ]] || { echo 'CHECKS_INVENTORY_TRUNCATED' >&2; exit 2; }
current=$(gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" --jq '.head.sha')
[[ "$current" = "$EXPECTED_HEAD_SHA" ]] || { echo 'PR_MOVED_DURING_CAPTURE' >&2; exit 2; }
mkdir -p "$(dirname "$EVIDENCE_OUTPUT")"
jq -n --argjson pr "$pr" --argjson changed_files "$files" \
  --argjson reviews "$reviews" --argjson check_runs "$(jq .check_runs <<< "$checks_json")" \
  '{pr:$pr,changed_files:$changed_files,reviews:$reviews,check_runs:$check_runs,complete_inventory:true}' > "$EVIDENCE_OUTPUT"
