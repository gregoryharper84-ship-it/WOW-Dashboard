#!/usr/bin/env bash
set -euo pipefail

# Machine-enforce the WOW V17 repository governance contract on main.
# Requires: gh authenticated as a repository administrator.
# Private repositories require a GitHub plan that supports protected branches /
# repository rulesets (GitHub Pro, Team, or Enterprise as applicable).
# Safe default: prints the intended ruleset and performs no mutation unless --apply is supplied.

REPO="gregoryharper84-ship-it/WOW-Dashboard"
RULESET_NAME="WOW main protection"
MODE="${1:-}"

payload_file="$(mktemp)"
preflight_out="$(mktemp)"
preflight_err="$(mktemp)"
trap 'rm -f "$payload_file" "$preflight_out" "$preflight_err"' EXIT

cat >"$payload_file" <<'JSON'
{
  "name": "WOW main protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/main"],
      "exclude": []
    }
  },
  "bypass_actors": [],
  "rules": [
    {
      "type": "deletion"
    },
    {
      "type": "non_fast_forward"
    },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          {"context": "WOW governed probability backend"},
          {"context": "WOW required-three regression"},
          {"context": "WOW additional required regression"}
        ]
      }
    }
  ]
}
JSON

if [[ "$MODE" != "--apply" ]]; then
  echo "DRY RUN ONLY. No GitHub settings changed."
  echo "Repository: $REPO"
  echo "Ruleset: $RULESET_NAME"
  cat "$payload_file"
  echo
  echo "Prerequisite for a private repository: GitHub Pro/Team/Enterprise plan with branch-protection/ruleset support."
  echo "Run '$0 --apply' as a repo admin after that prerequisite is satisfied."
  exit 0
fi

command -v gh >/dev/null 2>&1 || { echo "ERROR: gh CLI is required." >&2; exit 2; }
gh auth status >/dev/null

# Fail closed before any mutation when GitHub itself says the current plan cannot
# use rulesets on this private repository. This prevents the old workflow from
# misleadingly looking like an auth/tooling failure when the actual blocker is
# account capability.
if ! gh api "repos/$REPO/rulesets" >"$preflight_out" 2>"$preflight_err"; then
  if grep -qi "Upgrade to GitHub Pro or make this repository public" "$preflight_err"; then
    cat >&2 <<'EOF'
ERROR: GITHUB_PLAN_INELIGIBLE_PRIVATE_REPO
GitHub reports that rulesets are unavailable for this private repository on the current plan.
Safe remediation: keep the repository private, upgrade the owner to GitHub Pro (or an eligible Team/Enterprise plan), then rerun this command.
Do not make the WOW repository public merely to bypass this governance prerequisite.
No repository settings were changed.
EOF
    exit 4
  fi
  echo "ERROR: GitHub ruleset preflight failed before mutation." >&2
  cat "$preflight_err" >&2
  exit 5
fi

existing_id="$(gh api "repos/$REPO/rulesets" --paginate --jq '.[] | select(.name == "WOW main protection") | .id' | head -n1 || true)"

if [[ -n "$existing_id" ]]; then
  echo "Updating existing ruleset id=$existing_id"
  gh api --method PUT "repos/$REPO/rulesets/$existing_id" --input "$payload_file" >/dev/null
else
  echo "Creating ruleset '$RULESET_NAME'"
  existing_id="$(gh api --method POST "repos/$REPO/rulesets" --input "$payload_file" --jq '.id')"
fi

echo "Verifying effective protection..."
protected="$(gh api "repos/$REPO/branches/main" --jq '.protected')"
[[ "$protected" == "true" ]] || { echo "ERROR: main still reports protected=$protected" >&2; exit 3; }

gh api "repos/$REPO/rulesets/$existing_id" > /tmp/wow-main-ruleset-verified.json
python3 - /tmp/wow-main-ruleset-verified.json <<'PY'
import json, sys
from pathlib import Path

obj = json.loads(Path(sys.argv[1]).read_text())
assert obj.get("enforcement") == "active", obj
rules = obj.get("rules") or []
types = {r.get("type") for r in rules}
required = {"deletion", "non_fast_forward", "pull_request", "required_status_checks"}
missing = required - types
assert not missing, f"missing rules: {sorted(missing)}"
status_rule = next(r for r in rules if r.get("type") == "required_status_checks")
contexts = {
    item.get("context")
    for item in (status_rule.get("parameters", {}).get("required_status_checks") or [])
}
expected = {
    "WOW governed probability backend",
    "WOW required-three regression",
    "WOW additional required regression",
}
assert expected <= contexts, f"missing required check contexts: {sorted(expected-contexts)}"
assert obj.get("bypass_actors") in ([], None), "unexpected bypass actors configured"
print("RULESET_VERIFIED")
print(f"ruleset_id={obj.get('id')}")
print("required_checks=" + ",".join(sorted(expected)))
PY
rm -f /tmp/wow-main-ruleset-verified.json

echo "main_protected=true"
echo "force_pushes=blocked (non_fast_forward rule)"
echo "branch_deletion=blocked"
echo "pull_request_required=true"
echo "bypass_actors=none"
echo "WOW runtime settings were not modified by this script."
