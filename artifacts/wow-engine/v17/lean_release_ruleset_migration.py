"""WOW V17 lean release: inspect-only ruleset migration planner.

This deliberately does not mutate GitHub. A separately authenticated
repository administrator must approve/apply the generated payload after
source tests and independent transition review. It never changes model gates.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
from pathlib import Path
from typing import Any

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
RULESET_ID = 24907422
ACTIONS_APP = 15368
QA_APP = 5245832
RELEASE_APP = 5245853

TO_REMOVE = {
    "Trusted exact-head engineering governance": ACTIONS_APP,
    "WOW Independent Security Review": QA_APP,
}
TO_KEEP = {
    "WOW required-three regression": ACTIONS_APP,
    "WOW governed probability backend": ACTIONS_APP,
    "WOW additional required regression": ACTIONS_APP,
    "WOW Independent QA exact-head": QA_APP,
    "WOW Release Authority exact-head": RELEASE_APP,
}


class MigrationBlocked(ValueError):
    """The live protection state is not the exact reviewed source shape."""


def _contexts(ruleset: dict[str, Any]) -> dict[str, int]:
    rules = [
        rule for rule in ruleset.get("rules", [])
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks"
    ]
    if len(rules) != 1:
        raise MigrationBlocked("REQUIRED_CHECK_RULE_NOT_UNIQUE")
    params = rules[0].get("parameters")
    if not isinstance(params, dict) or params.get("strict_required_status_checks_policy") is not True:
        raise MigrationBlocked("STRICT_BASE_RECERTIFICATION_MISSING")
    entries = params.get("required_status_checks")
    if not isinstance(entries, list):
        raise MigrationBlocked("REQUIRED_CHECKS_INVALID")
    names: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("context"), str):
            raise MigrationBlocked("REQUIRED_CHECK_ENTRY_INVALID")
        context, app = entry["context"], entry.get("integration_id")
        if not isinstance(app, int) or context in names:
            raise MigrationBlocked("REQUIRED_CHECK_PROVENANCE_INVALID")
        names[context] = app
    return names


def _guard(ruleset: dict[str, Any]) -> None:
    if ruleset.get("id") != RULESET_ID or ruleset.get("name") != "wow-v17-main-agent-separation":
        raise MigrationBlocked("RULESET_IDENTITY_MISMATCH")
    if ruleset.get("target") != "branch" or ruleset.get("enforcement") != "active":
        raise MigrationBlocked("RULESET_NOT_ACTIVE_BRANCH")
    conditions = ruleset.get("conditions") or {}
    refs = conditions.get("ref_name") or {}
    if refs.get("include") != ["~DEFAULT_BRANCH"] or refs.get("exclude") != []:
        raise MigrationBlocked("DEFAULT_BRANCH_SCOPE_CHANGED")
    if ruleset.get("bypass_actors") != []:
        raise MigrationBlocked("BYPASS_ACTORS_CHANGED")
    types = [rule.get("type") for rule in ruleset.get("rules", []) if isinstance(rule, dict)]
    for protective in ("deletion", "non_fast_forward", "pull_request"):
        if types.count(protective) != 1:
            raise MigrationBlocked("PROTECTIVE_RULE_MISSING:" + protective)
    pr = next(rule for rule in ruleset["rules"] if rule["type"] == "pull_request")
    params = pr.get("parameters")
    if not isinstance(params, dict):
        raise MigrationBlocked("PULL_REQUEST_RULE_INVALID")
    # The reviewed source state currently contains these settings. The
    # migration is restricted to two named status checks, not PR protection.
    if (
        params.get("dismiss_stale_reviews_on_push") is not True
        or params.get("required_approving_review_count") != 0
        or params.get("allowed_merge_methods") != ["merge", "squash", "rebase"]
    ):
        raise MigrationBlocked("PULL_REQUEST_PROTECTION_DRIFT")


def plan_migration(ruleset: dict[str, Any]) -> dict[str, Any]:
    """Change only the two named checks, preserving every other branch rule."""
    _guard(ruleset)
    current = _contexts(ruleset)
    if current != {**TO_KEEP, **TO_REMOVE}:
        raise MigrationBlocked("LIVE_REQUIRED_CHECK_INVENTORY_DRIFT")
    target = copy.deepcopy(ruleset)
    for rule in target["rules"]:
        if rule["type"] == "required_status_checks":
            rule["parameters"]["required_status_checks"] = [
                entry for entry in rule["parameters"]["required_status_checks"]
                if entry["context"] not in TO_REMOVE
            ]
    if _contexts(target) != TO_KEEP:
        raise MigrationBlocked("TARGET_FIVE_CHECK_INVARIANT_FAILED")
    return {
        "name": target["name"],
        "target": target["target"],
        "enforcement": target["enforcement"],
        "conditions": target["conditions"],
        "rules": target["rules"],
        "bypass_actors": target["bypass_actors"],
    }


def verify_target(
    ruleset: dict[str, Any], *, approved_payload: dict[str, Any] | None = None,
) -> None:
    _guard(ruleset)
    if _contexts(ruleset) != TO_KEEP:
        raise MigrationBlocked("FIVE_CHECK_TARGET_NOT_ACTIVE")
    if approved_payload is not None:
        # Compare every applied protection, not just the names of five checks.
        # GitHub adds read-only metadata, so compare only PUT payload fields.
        keys = ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")
        if (
            not isinstance(approved_payload, dict)
            or any(ruleset.get(k) != approved_payload.get(k) for k in keys)
        ):
            raise MigrationBlocked("APPROVED_RULESET_PAYLOAD_DRIFT")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", help="Previously read live ruleset JSON; otherwise use gh api GET")
    parser.add_argument("--output", help="Destination for proposed GitHub PUT payload")
    parser.add_argument("--verify-target", action="store_true", help="Read-only verification after authorized update")
    parser.add_argument("--approved-payload", help="Previously reviewed exact migration PUT JSON")
    args = parser.parse_args()
    if args.source:
        ruleset = json.loads(Path(args.source).read_text(encoding="utf-8"))
    else:
        result = subprocess.run(
            ["gh", "api", f"/repos/{REPO}/rulesets/{RULESET_ID}"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        ruleset = json.loads(result.stdout)
    if args.verify_target:
        if not args.approved_payload:
            raise MigrationBlocked("APPROVED_RULESET_PAYLOAD_REQUIRED")
        approved = json.loads(Path(args.approved_payload).read_text(encoding="utf-8"))
        verify_target(ruleset, approved_payload=approved)
        print("FIVE_CHECK_TARGET_VERIFIED_WITH_EXACT_PROTECTION_COMPARISON")
        return 0
    payload = plan_migration(ruleset)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(encoded, encoding="utf-8")
        print("PLAN_WRITTEN_NOT_APPLIED: " + args.output)
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
