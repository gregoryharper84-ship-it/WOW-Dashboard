"""Independent, fail-closed release-closure evidence validator (Class A).

This is a pure validator, not an approval service. It cannot mint QA evidence,
merge code, promote models, publish predictions, or execute orders.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_ROLES = {"ENGINEERING", "REVIEW", "RELEASE", "INDEPENDENT_QA"}
PROTECTED = ("custom_gpt_identity", "runtime_generation", "terminal_authority",
             "can_execute", "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS")
EXPECTED = {
    "custom_gpt_identity": "WOW_BETTING_ENGINE",
    "runtime_generation": "V17_ACTIVE",
    "terminal_authority": "V17_TERMINAL_REDUCER",
    "can_execute": False,
    "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS": True,
}

def validate_closure(receipt: Any) -> list[str]:
    """Return deterministic failures; empty list is necessary, not sufficient, for production closure."""
    if not isinstance(receipt, dict):
        return ["receipt must be an object"]
    errors: list[str] = []
    def require_str(key: str) -> str:
        value = receipt.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} must be a non-empty string")
            return ""
        return value.strip()

    for key in ("incident_id", "work_item_id", "pr_url", "root_cause",
                "test_evidence", "production_acceptance_evidence",
                "persistence_reconciliation_evidence"):
        require_str(key)
    if receipt.get("disposition") != "FIXED_AND_VERIFIED":
        errors.append("disposition must be FIXED_AND_VERIFIED")
    if receipt.get("change_class") not in ("A", "B", "C"):
        errors.append("change_class must be A, B or C")
    sha_keys = ("reviewed_sha", "tested_sha", "merged_sha", "deployed_sha", "qa_verified_sha")
    shas = [receipt.get(key) for key in sha_keys]
    for key, value in zip(sha_keys, shas):
        if not isinstance(value, str) or not SHA.fullmatch(value):
            errors.append(f"{key} must be a full lowercase commit SHA")
    if len(set(shas)) != 1:
        errors.append("reviewed, tested, merged, deployed and QA-verified SHAs must match")
    actors = receipt.get("actors")
    if not isinstance(actors, dict):
        errors.append("actors must be an object")
    else:
        for role in sorted(REQUIRED_ROLES):
            if not isinstance(actors.get(role), str) or not actors[role].strip():
                errors.append(f"actors.{role} must identify a real independent principal")
        engineering = actors.get("ENGINEERING")
        qa = actors.get("INDEPENDENT_QA")
        if engineering and qa and engineering == qa:
            errors.append("Engineering may not self-certify independent QA")
        review = actors.get("REVIEW")
        if review and engineering and review == engineering:
            errors.append("Engineering may not independently review its own change")
        if review and qa and review == qa:
            errors.append("Review and independent QA must be separate principals")
    checks = receipt.get("checks")
    mandatory = ("unit", "integration", "negative_path", "regression",
                 "adjacent_lane", "independent_review", "authorized_merge",
                 "production_acceptance", "independent_qa",
                 "persistence_reconciliation")
    if not isinstance(checks, dict):
        errors.append("checks must be an object")
    else:
        for name in mandatory:
            if checks.get(name) is not True:
                errors.append(f"checks.{name} must be true")
        if receipt.get("change_class") in ("B", "C") and checks.get("governed_approval") is not True:
            errors.append("checks.governed_approval must be true for B/C")
        if receipt.get("change_class") == "C" and checks.get("historical_and_forward_validation") is not True:
            errors.append("checks.historical_and_forward_validation must be true for C")
    invariants = receipt.get("invariants")
    if not isinstance(invariants, dict):
        errors.append("invariants must be an object")
    else:
        for key in PROTECTED:
            if type(invariants.get(key)) is not type(EXPECTED[key]) or invariants.get(key) != EXPECTED[key]:
                errors.append(f"invariants.{key} must remain {EXPECTED[key]!r}")
    if type(receipt.get("unresolved_p0_p1_regressions")) is not int or receipt["unresolved_p0_p1_regressions"] != 0:
        errors.append("unresolved_p0_p1_regressions must be zero")
    if receipt.get("independent_qa_decision") != "PASS":
        errors.append("independent_qa_decision must be PASS")
    return errors

def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Class A closure receipt checker")
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    try:
        errors = validate_closure(json.loads(args.receipt.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        errors = [f"receipt unreadable or invalid: {type(exc).__name__}"]
    print(json.dumps({"accepted": not errors, "errors": errors}, sort_keys=True))
    return 1 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())
