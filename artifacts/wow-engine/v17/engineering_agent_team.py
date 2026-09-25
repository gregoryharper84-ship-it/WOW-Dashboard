"""Deterministic operating system for the WOW V17 multi-agent engineering team.

This module coordinates engineering work only. It never publishes sporting
probabilities, never places wagers, and never grants execution authority.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEAM_VERSION = "3.0"

AGENT_ROLES: dict[str, dict[str, Any]] = {
    "ENGINEERING_LEAD_AGENT": {
        "mission": "Own priority, single-incident focus, dedupe, and handoff discipline.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "RESEARCH_TRIAGE_AGENT": {
        "mission": "Reproduce defects and prove root cause before implementation.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "ENGINEERING_AGENT": {
        "mission": "Implement the smallest safe repair for a proven root cause.",
        "may_write_code": True,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "INDEPENDENT_REVIEW_AGENT": {
        "mission": "Adversarially review implementation correctness and governance safety.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "SYSTEM_ARCHITECT_AGENT": {
        "mission": "Review protected contracts and architecture boundaries independently.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "QA_VERIFICATION_AGENT": {
        "mission": "Verify acceptance criteria, regressions, and adjacent-lane safety.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "RELEASE_OBSERVABILITY_AGENT": {
        "mission": "Verify protected main, exact deploy, production acceptance, and reconciliation.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "PRODUCT_ACCEPTANCE_AGENT": {
        "mission": "Independently verify the real golden user journey without inferring product health from CI or component health.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "REPORTER_AGENT": {
        "mission": "Publish truthful closure receipts from durable evidence only.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "FRONTIER_INTELLIGENCE_AGENT": {
        "mission": "Continuously scan AI and sports-betting technology for evidence-backed improvements.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
}

TERMINAL_ISSUE_STATES = {
    "VERIFIED_CLOSED",
    "FIXED_AND_VERIFIED",
    "DUPLICATE",
    "NOT_REPRODUCIBLE",
    "BLOCKED_WITH_EXACT_REASON",
    "DEFERRED_WITH_JUSTIFICATION",
}

SEVERITY_WEIGHT = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
ACTIVE_RELEASE_STATES = {"DEPLOYED_PENDING_VERIFY", "MERGED_PENDING_DEPLOY", "PR_CREATED"}
USER_CRITICAL_JOURNEYS = ("ALL_SPORTS_PROPS", "ALL_SPORTS_ML_WINNERS", "ALL_SPORTS_UPSETS")
MAX_ACTIVE_PRODUCT_RECOVERY = 1
MAX_ACTIVE_SUPPORTING_INVESTIGATION = 1

REQUIRED_CLOSURE_FIELDS = {
    "expected_behavior", "observed_behavior", "reproduction", "evidence",
    "affected_component", "environment", "severity", "change_class",
    "acceptance_criteria", "regression_guard",
}

FAILURE_OWNERS = {
    "DISCOVERY_FAILURE": "acquisition",
    "PROVIDER_FAILURE": "acquisition",
    "CANONICAL_IDENTITY_FAILURE": "identity",
    "HYDRATION_FAILURE": "hydration",
    "MODEL_INPUTS_INSUFFICIENT": "specialist-inputs",
    "MODEL_UNAVAILABLE": "model-capability",
    "SCORER_FAILURE": "scoring",
    "ACTION_TRANSPORT_FAILURE": "transport",
    "PERSISTENCE_FAILURE": "persistence",
}
FRONTIER_RADAR = {"ADOPT", "TRIAL", "ASSESS", "WATCH", "REJECT", "DUPLICATE"}


@dataclass(frozen=True)
class PriorityDecision:
    incident_id: str | None
    severity: str
    state: str
    reason: str
    frontier_allowed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "severity": self.severity,
            "state": self.state,
            "reason": self.reason,
            "frontier_allowed": self.frontier_allowed,
        }


def _record_id(record: dict[str, Any]) -> str:
    return str(record.get("postmortem_id") or record.get("incident_id") or "")


def is_actionable(record: dict[str, Any]) -> bool:
    state = str(record.get("state") or "OPEN").upper()
    if state in TERMINAL_ISSUE_STATES:
        return False
    severity = str(record.get("severity") or "P4").upper()
    return severity in SEVERITY_WEIGHT


def reliability_blocks_frontier(records: list[dict[str, Any]]) -> bool:
    """P0/P1 reliability work always preempts discretionary frontier work."""
    for record in records:
        if not is_actionable(record):
            continue
        if str(record.get("severity") or "").upper() in {"P0", "P1"}:
            return True
    return False


def select_priority_incident(records: list[dict[str, Any]]) -> PriorityDecision:
    actionable = [r for r in records if is_actionable(r)]
    if not actionable:
        return PriorityDecision(
            incident_id=None,
            severity="NONE",
            state="NONE",
            reason="No actionable incident in durable ledger.",
            frontier_allowed=True,
        )

    def sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
        severity = str(record.get("severity") or "P4").upper()
        state = str(record.get("state") or "OPEN").upper()
        release_first = 0 if state in ACTIVE_RELEASE_STATES else 1
        updated = str(record.get("updated_utc") or record.get("created_utc") or "")
        return (SEVERITY_WEIGHT.get(severity, 99), release_first, updated, _record_id(record))

    chosen = sorted(actionable, key=sort_key)[0]
    severity = str(chosen.get("severity") or "P4").upper()
    state = str(chosen.get("state") or "OPEN").upper()
    return PriorityDecision(
        incident_id=_record_id(chosen) or None,
        severity=severity,
        state=state,
        reason=f"Highest-priority actionable ledger item: {severity} {state}.",
        frontier_allowed=not reliability_blocks_frontier(actionable),
    )



def validate_closure_record(record: dict[str, Any]) -> list[str]:
    """Enforce one durable closure unit instead of disconnected progress markers."""
    errors: list[str] = []
    missing = sorted(k for k in REQUIRED_CLOSURE_FIELDS if not record.get(k))
    if missing:
        errors.append("missing closure fields: " + ", ".join(missing))
    journey = str(record.get("user_journey") or "")
    if journey and journey not in USER_CRITICAL_JOURNEYS:
        errors.append(f"unknown user_journey: {journey}")
    failure = str(record.get("typed_failure") or "")
    if failure and failure not in FAILURE_OWNERS:
        errors.append(f"unowned typed_failure: {failure}")
    if record.get("can_execute") is not False:
        errors.append("closure record must set can_execute=false")
    if record.get("terminal_authority") != "V17_TERMINAL_REDUCER":
        errors.append("closure record must preserve V17_TERMINAL_REDUCER")
    return errors


def closure_wip(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Hard WIP gate: one product recovery plus one supporting investigation."""
    active = [r for r in records if is_actionable(r)]
    product = [r for r in active if str(r.get("severity") or "").upper() in {"P0", "P1"}]
    supporting = [r for r in active if r not in product]
    return {
        "product_recovery_active": len(product),
        "supporting_investigation_active": len(supporting),
        "product_recovery_limit": MAX_ACTIVE_PRODUCT_RECOVERY,
        "supporting_investigation_limit": MAX_ACTIVE_SUPPORTING_INVESTIGATION,
        "new_product_work_allowed": len(product) < MAX_ACTIVE_PRODUCT_RECOVERY,
        "new_supporting_work_allowed": len(supporting) < MAX_ACTIVE_SUPPORTING_INVESTIGATION,
    }


def route_failure(typed_failure: str) -> str:
    """Return the owning layer without collapsing failures into MODEL_UNAVAILABLE."""
    key = str(typed_failure or "").upper()
    if key not in FAILURE_OWNERS:
        raise ValueError(f"unowned typed failure: {key!r}")
    return FAILURE_OWNERS[key]


def validate_frontier_candidate(candidate: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    radar = str(candidate.get("radar_status") or "").upper()
    if radar not in FRONTIER_RADAR:
        errors.append(f"invalid radar_status: {radar!r}")
    if candidate.get("production_change") is not False:
        errors.append("frontier candidate must set production_change=false")
    if not str(candidate.get("title") or "").strip():
        errors.append("frontier candidate requires title")
    if not str(candidate.get("fingerprint") or "").strip():
        errors.append("frontier candidate requires fingerprint")
    if radar in {"ADOPT", "TRIAL", "ASSESS"}:
        if not candidate.get("evidence"):
            errors.append(f"{radar} requires evidence")
        if not candidate.get("validation_plan"):
            errors.append(f"{radar} requires validation_plan")
    return errors


def load_ledger(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text())
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("ledger records must be a list")
    return records


def self_check() -> dict[str, Any]:
    assert AGENT_ROLES["ENGINEERING_AGENT"]["may_write_code"] is True
    assert "PRODUCT_ACCEPTANCE_AGENT" in AGENT_ROLES
    for name, role in AGENT_ROLES.items():
        if name != "ENGINEERING_AGENT":
            assert role["may_write_code"] is False
        assert role["may_change_probability_behavior"] is False
        assert role["may_approve_own_work"] is False
    assert reliability_blocks_frontier([{"severity": "P1", "state": "OPEN"}])
    assert not reliability_blocks_frontier([{"severity": "P2", "state": "OPEN"}])
    assert route_failure("ACTION_TRANSPORT_FAILURE") == "transport"
    assert route_failure("MODEL_UNAVAILABLE") == "model-capability"
    assert USER_CRITICAL_JOURNEYS == ("ALL_SPORTS_PROPS", "ALL_SPORTS_ML_WINNERS", "ALL_SPORTS_UPSETS")
    assert closure_wip([{"severity": "P0", "state": "OPEN"}])["new_product_work_allowed"] is False
    return {
        "team_version": TEAM_VERSION,
        "agent_roles": sorted(AGENT_ROLES),
        "frontier_radar": sorted(FRONTIER_RADAR),
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["self-check", "priority", "frontier-gate"])
    parser.add_argument("--ledger", default=str(Path(__file__).with_name("incident-ledger.json")))
    args = parser.parse_args()

    if args.command == "self-check":
        print(json.dumps(self_check(), indent=2, sort_keys=True))
        return

    records = load_ledger(args.ledger)
    if args.command == "priority":
        print(json.dumps(select_priority_incident(records).as_dict(), indent=2, sort_keys=True))
        return
    print(json.dumps({"frontier_allowed": not reliability_blocks_frontier(records)}, sort_keys=True))


if __name__ == "__main__":
    main()
