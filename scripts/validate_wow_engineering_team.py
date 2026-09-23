#!/usr/bin/env python3
"""Validate WOW multi-agent engineering-team configuration and core governance invariants."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "wow_engineering_team.json"

REQUIRED_ROLES = {
    "engineering_lead",
    "root_cause",
    "implementation",
    "adversarial_review",
    "release_verification",
    "frontier_intelligence",
    "challenger",
}


def fail(message: str) -> None:
    raise SystemExit(f"WOW_ENGINEERING_TEAM_INVALID: {message}")


def main() -> None:
    data = json.loads(REGISTRY.read_text())

    if data.get("runtime_generation") != "V17_ACTIVE":
        fail("runtime_generation must remain V17_ACTIVE")
    if data.get("terminal_authority") != "V17_TERMINAL_REDUCER":
        fail("V17_TERMINAL_REDUCER must remain sole terminal authority")
    if data.get("can_execute") is not False:
        fail("can_execute must remain false")
    if data.get("dry_run_only") is not True:
        fail("dry_run_only must remain true")
    if data.get("single_implementation_lease_per_incident") is not True:
        fail("single implementation lease must be enabled")
    if data.get("r0_r1_preempts_improvement") is not True:
        fail("R0/R1 reliability must preempt improvement work")

    roles = {role["id"]: role for role in data.get("roles", [])}
    missing = REQUIRED_ROLES - roles.keys()
    if missing:
        fail(f"missing roles: {sorted(missing)}")

    if roles["implementation"].get("requires_implementation_lease") is not True:
        fail("implementation agent must require a lease")
    if roles["frontier_intelligence"].get("may_promote_probability_behavior") is not False:
        fail("frontier intelligence may not promote probability behavior")
    if roles["challenger"].get("may_promote_probability_behavior") is not False:
        fail("challenger may not self-promote probability behavior")

    expected_class_c = [
        "DISCOVER",
        "HYPOTHESIS",
        "CHALLENGER",
        "HISTORICAL_REPLAY",
        "COUNTEREXAMPLE_REVIEW",
        "HOLDOUT_OR_FORWARD_VALIDATION",
        "REGRESSION",
        "RECOMMENDATION",
        "GOVERNED_REVIEW",
    ]
    if data.get("class_c_required_path") != expected_class_c:
        fail("Class C path changed or is incomplete")

    print("WOW_ENGINEERING_TEAM_VALID")


if __name__ == "__main__":
    main()
