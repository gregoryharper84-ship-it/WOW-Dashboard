#!/usr/bin/env bash
set -euo pipefail

# Machine-enforce the WOW V17 repository governance contract on main.
# Requires: gh authenticated as a repository administrator.
# Safe default: prints the intended ruleset and performs no mutation unless --apply is supplied.

REPO="gregoryharper84-ship-it/WOW-Dashboard"
RULESET_NAME="WOW main protection"
MODE="${1:-}"

payload_file="$(mktemp)"
trap 'rm -f "$payload_file"' EXIT

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
  echo "Run '$0 --apply' as a repo admin to install/update this ruleset."
  exit 0
fi

command -v gh >/dev/null 2>&1 || { echo "ERROR: gh CLI is required." >&2; exit 2; }

gh auth status >/dev/null

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
