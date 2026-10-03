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

TEAM_VERSION = "4.0"

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

SPECIALIST_SUBAGENTS: dict[str, dict[str, Any]] = {
    "CI_REPOSITORY_SUBAGENT": {
        "mission": "Inspect exact-head CI, workflow triggers, branch state, protected checks, and merge readiness.",
        "support_only": True,
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "RUNTIME_TRANSPORT_SUBAGENT": {
        "mission": "Inspect Action transport, HTTP/runtime behavior, cold starts, middleware, deploy state, and production request flow.",
        "support_only": True,
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "ACQUISITION_IDENTITY_SUBAGENT": {
        "mission": "Inspect provider acquisition, governed fallback, freshness, canonical identity, aliases, hydration boundaries, and reconciliation loss.",
        "support_only": True,
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "DATA_PERSISTENCE_SUBAGENT": {
        "mission": "Inspect immutable prediction writes, receipts, exact-once behavior, persistence/reconciliation evidence, and durable row integrity.",
        "support_only": True,
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "MODEL_CAPABILITY_GOVERNANCE_SUBAGENT": {
        "mission": "Inspect fitted specialist registration, artifact certification, model-input availability, calibration state, and typed scorer boundaries without changing model math.",
        "support_only": True,
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "SECURITY_BOUNDARY_SUBAGENT": {
        "mission": "Inspect auth, secret availability, permission, RLS, and safety boundaries without exposing, rotating, or weakening credentials or policy.",
        "support_only": True,
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "REGRESSION_SAFETY_SUBAGENT": {
        "mission": "Inspect adjacent-lane risk, regression coverage, rollback readiness, counterexamples, and acceptance-harness completeness.",
        "support_only": True,
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
PARKED_WAIT_STATES = {
    "REVIEW_PENDING", "APPROVAL_PENDING", "MERGE_AUTHORITY_PENDING",
    "EXTERNAL_WAIT", "PROVIDER_WAIT", "PERMISSION_WAIT", "SECRET_WAIT",
    "PLATFORM_LIMITATION",
}
USER_CRITICAL_JOURNEYS = ("ALL_SPORTS_PROPS", "ALL_SPORTS_ML_WINNERS", "ALL_SPORTS_UPSETS")
MAX_ACTIVE_PRODUCT_RECOVERY = 3
MAX_ACTIVE_SUPPORTING_INVESTIGATION = 1

REQUIRED_CLOSURE_FIELDS = {
    "expected_behavior", "observed_behavior", "reproduction", "evidence",
    "affected_component", "environment", "severity", "change_class",
    "acceptance_criteria", "regression_guard",
}

CAPABILITY_DIMENSIONS = (
    "discovery_supported",
    "canonicalization_supported",
    "hydration_supported",
    "fitted_specialist_registered",
    "artifact_certified",
    "calibration_valid",
    "production_enabled",
)

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

SUBAGENT_BY_FAILURE_OWNER = {
    "acquisition": "ACQUISITION_IDENTITY_SUBAGENT",
    "identity": "ACQUISITION_IDENTITY_SUBAGENT",
    "hydration": "ACQUISITION_IDENTITY_SUBAGENT",
    "specialist-inputs": "MODEL_CAPABILITY_GOVERNANCE_SUBAGENT",
    "model-capability": "MODEL_CAPABILITY_GOVERNANCE_SUBAGENT",
    "scoring": "MODEL_CAPABILITY_GOVERNANCE_SUBAGENT",
    "transport": "RUNTIME_TRANSPORT_SUBAGENT",
    "persistence": "DATA_PERSISTENCE_SUBAGENT",
}

SUBAGENT_BY_SUBSYSTEM = {
    "DEPLOYMENT_RUNTIME": "RUNTIME_TRANSPORT_SUBAGENT",
    "WOW_HOST_ORCHESTRATION": "RUNTIME_TRANSPORT_SUBAGENT",
    "SLATE_IDENTITY": "ACQUISITION_IDENTITY_SUBAGENT",
    "PERSISTENCE_POSTMORTEM": "DATA_PERSISTENCE_SUBAGENT",
    "WOW_PROP_ENGINE": "MODEL_CAPABILITY_GOVERNANCE_SUBAGENT",
    "LLP_TEAM_EVENT_ENGINE": "MODEL_CAPABILITY_GOVERNANCE_SUBAGENT",
    "DYNAMIC_CALIBRATION": "MODEL_CAPABILITY_GOVERNANCE_SUBAGENT",
    "SECURITY_CREDENTIAL": "SECURITY_BOUNDARY_SUBAGENT",
}

WAIT_STATE_SUPPORT = {
    "CI_PENDING": (
        "CI_REPOSITORY_SUBAGENT",
        "Verify exact-head trigger/check coverage and prepare the next protected CI action.",
    ),
    "REVIEW_PENDING": (
        "REGRESSION_SAFETY_SUBAGENT",
        "Inspect adjacent-lane risks, rollback, and acceptance coverage while review is external.",
    ),
    "MERGE_PENDING": (
        "CI_REPOSITORY_SUBAGENT",
        "Verify exact-head freshness, branch protection, and protected auto-merge eligibility.",
    ),
    "DEPLOYMENT_PENDING": (
        "RUNTIME_TRANSPORT_SUBAGENT",
        "Prepare exact-SHA runtime probes and production acceptance evidence.",
    ),
    "PROVIDER_WAIT": (
        "ACQUISITION_IDENTITY_SUBAGENT",
        "Exercise governed fallback, freshness, canonicalization, and exhaustion evidence.",
    ),
    "EXTERNAL_WAIT": (
        "REGRESSION_SAFETY_SUBAGENT",
        "Advance non-conflicting regression, rollback, and acceptance preparation on the same incident.",
    ),
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


@dataclass(frozen=True)
class DualStreamDecision:
    restoration: PriorityDecision
    acceleration: PriorityDecision
    acceleration_blocked_reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "restoration": self.restoration.as_dict(),
            "acceleration": self.acceleration.as_dict(),
            "acceleration_blocked_reason": self.acceleration_blocked_reason,
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }


@dataclass(frozen=True)
class SupportDecision:
    subagent: str
    reason: str
    implementation_lease: bool = False
    support_only: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "subagent": self.subagent,
            "reason": self.reason,
            "implementation_lease": self.implementation_lease,
            "support_only": self.support_only,
            "can_execute": False,
        }


def _record_id(record: dict[str, Any]) -> str:
    return str(record.get("postmortem_id") or record.get("incident_id") or "")


def is_actionable(record: dict[str, Any]) -> bool:
    state = str(record.get("state") or "OPEN").upper()
    if state in TERMINAL_ISSUE_STATES:
        return False
    wait_state = str(record.get("wait_state") or "").upper()
    if wait_state in PARKED_WAIT_STATES:
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
        priority_rank = int(record.get("priority_rank") or 9999)
        updated = str(record.get("updated_utc") or record.get("created_utc") or "")
        return (SEVERITY_WEIGHT.get(severity, 99), release_first, priority_rank, updated, _record_id(record))

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


def _work_stream(record: dict[str, Any]) -> str:
    return str(record.get("work_stream") or "RESTORATION").upper()


def _conflict_keys(record: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in ("conflict_keys", "production_code_owners"):
        raw = record.get(field) or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            keys.update(str(value).strip().upper() for value in raw if str(value).strip())
    for field in ("primary_subsystem", "affected_component"):
        value = str(record.get(field) or "").strip().upper()
        if value:
            keys.add(value)
    return keys


def _records_conflict(restoration: dict[str, Any], acceleration: dict[str, Any]) -> tuple[bool, str]:
    restoration_keys = _conflict_keys(restoration)
    acceleration_keys = _conflict_keys(acceleration)
    if not restoration_keys or not acceleration_keys:
        return True, "Missing explicit conflict metadata; acceleration fails closed."
    overlap = restoration_keys & acceleration_keys
    if overlap:
        return True, "Overlapping conflict keys: " + ", ".join(sorted(overlap))
    return False, ""


def select_dual_stream_work(records: list[dict[str, Any]]) -> DualStreamDecision:
    """Select restoration first, then at most one provably non-conflicting acceleration item."""
    restoration_records = [
        record for record in records
        if is_actionable(record) and _work_stream(record) != "ACCELERATION"
    ]
    acceleration_records = [
        record for record in records
        if is_actionable(record) and _work_stream(record) == "ACCELERATION"
    ]

    restoration = select_priority_incident(restoration_records)
    if not acceleration_records:
        return DualStreamDecision(
            restoration=restoration,
            acceleration=select_priority_incident([]),
            acceleration_blocked_reason=None,
        )

    if restoration.incident_id is None:
        return DualStreamDecision(
            restoration=restoration,
            acceleration=select_priority_incident(acceleration_records),
            acceleration_blocked_reason=None,
        )

    restoration_record = next(
        record for record in restoration_records if _record_id(record) == restoration.incident_id
    )
    ordered_acceleration = sorted(
        acceleration_records,
        key=lambda record: (
            SEVERITY_WEIGHT.get(str(record.get("severity") or "P4").upper(), 99),
            0 if str(record.get("state") or "OPEN").upper() in ACTIVE_RELEASE_STATES else 1,
            int(record.get("priority_rank") or 9999),
            str(record.get("updated_utc") or record.get("created_utc") or ""),
            _record_id(record),
        ),
    )

    blocked_reasons: list[str] = []
    for candidate in ordered_acceleration:
        conflicts, reason = _records_conflict(restoration_record, candidate)
        if conflicts:
            blocked_reasons.append(f"{_record_id(candidate)}: {reason}")
            continue
        return DualStreamDecision(
            restoration=restoration,
            acceleration=select_priority_incident([candidate]),
            acceleration_blocked_reason=None,
        )

    return DualStreamDecision(
        restoration=restoration,
        acceleration=select_priority_incident([]),
        acceleration_blocked_reason="; ".join(blocked_reasons) or "No non-conflicting acceleration item.",
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
    """Hard WIP gate: up to three conflict-arbitrated product recoveries plus one supporting investigation."""
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


def select_support_subagent(record: dict[str, Any]) -> SupportDecision:
    """Assign exactly one read-only specialist to the active parent incident.

    The specialist never owns the parent incident, never receives the implementation
    lease, and may only advance evidence, adjacent-risk, fallback, CI, or release
    preparation for the same closure journey.
    """
    wait_state = str(record.get("wait_state") or "").upper()
    if wait_state in WAIT_STATE_SUPPORT:
        subagent, reason = WAIT_STATE_SUPPORT[wait_state]
        return SupportDecision(subagent=subagent, reason=reason)

    failure = str(record.get("typed_failure") or "").upper()
    if failure in FAILURE_OWNERS:
        owner = FAILURE_OWNERS[failure]
        subagent = SUBAGENT_BY_FAILURE_OWNER[owner]
        return SupportDecision(
            subagent=subagent,
            reason=f"Typed failure {failure} is owned by {owner}; route specialist evidence there.",
        )

    subsystem = str(record.get("primary_subsystem") or record.get("affected_component") or "").upper()
    if subsystem in SUBAGENT_BY_SUBSYSTEM:
        subagent = SUBAGENT_BY_SUBSYSTEM[subsystem]
        return SupportDecision(
            subagent=subagent,
            reason=f"Primary subsystem {subsystem} maps to its specialist support lane.",
        )

    return SupportDecision(
        subagent="REGRESSION_SAFETY_SUBAGENT",
        reason="No narrower specialist mapping exists; use regression/acceptance safety support without widening scope.",
    )


def validate_capability_matrix(matrix: dict[str, Any]) -> list[str]:
    """Require explicit sport x market capability truth; never infer support."""
    errors: list[str] = []
    for key, row in sorted(matrix.items()):
        if not isinstance(row, dict):
            errors.append(f"{key}: capability row must be an object")
            continue
        missing = [d for d in CAPABILITY_DIMENSIONS if d not in row]
        if missing:
            errors.append(f"{key}: missing capability dimensions: {', '.join(missing)}")
            continue
        for dim in CAPABILITY_DIMENSIONS:
            if not isinstance(row[dim], bool):
                errors.append(f"{key}: {dim} must be boolean")
        if row.get("production_enabled") and not all(
            row.get(d) is True for d in (
                "discovery_supported", "canonicalization_supported",
                "hydration_supported", "fitted_specialist_registered",
                "artifact_certified", "calibration_valid",
            )
        ):
            errors.append(f"{key}: production_enabled requires every upstream capability")
    return errors


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
    for subagent in SPECIALIST_SUBAGENTS.values():
        assert subagent["support_only"] is True
        assert subagent["may_write_code"] is False
        assert subagent["may_change_probability_behavior"] is False
        assert subagent["may_approve_own_work"] is False
    assert reliability_blocks_frontier([{"severity": "P1", "state": "OPEN"}])
    assert not reliability_blocks_frontier([{"severity": "P2", "state": "OPEN"}])
    assert not is_actionable({"severity": "P0", "state": "PR_CREATED", "wait_state": "REVIEW_PENDING"})
    parked_then_executable = [
        {"incident_id": "960", "severity": "P0", "state": "PR_CREATED", "wait_state": "REVIEW_PENDING"},
        {"incident_id": "502", "severity": "P0", "state": "OPEN"},
    ]
    assert select_priority_incident(parked_then_executable).incident_id == "502"
    ranked = [
        {"incident_id": "960", "severity": "P0", "state": "OPEN", "priority_rank": 2},
        {"incident_id": "502", "severity": "P0", "state": "OPEN", "priority_rank": 1},
    ]
    assert select_priority_incident(ranked).incident_id == "502"
    dual = select_dual_stream_work([
        {"incident_id": "502", "severity": "P0", "state": "OPEN", "conflict_keys": ["interactive-runtime"]},
        {"incident_id": "1135", "severity": "P2", "state": "OPEN", "work_stream": "ACCELERATION", "conflict_keys": ["test-harness"]},
    ])
    assert dual.restoration.incident_id == "502"
    assert dual.acceleration.incident_id == "1135"
    assert dual.as_dict()["can_execute"] is False
    assert dual.as_dict()["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert route_failure("ACTION_TRANSPORT_FAILURE") == "transport"
    assert route_failure("MODEL_UNAVAILABLE") == "model-capability"
    assert select_support_subagent({"typed_failure": "ACTION_TRANSPORT_FAILURE"}).subagent == "RUNTIME_TRANSPORT_SUBAGENT"
    assert select_support_subagent({"typed_failure": "PERSISTENCE_FAILURE"}).subagent == "DATA_PERSISTENCE_SUBAGENT"
    assert select_support_subagent({"wait_state": "CI_PENDING"}).subagent == "CI_REPOSITORY_SUBAGENT"
    assert USER_CRITICAL_JOURNEYS == ("ALL_SPORTS_PROPS", "ALL_SPORTS_ML_WINNERS", "ALL_SPORTS_UPSETS")
    assert validate_capability_matrix({"NFL:ML": {d: True for d in CAPABILITY_DIMENSIONS}}) == []
    assert closure_wip([
        {"severity": "P0", "state": "OPEN"},
        {"severity": "P0", "state": "OPEN"},
        {"severity": "P0", "state": "OPEN"},
    ])["new_product_work_allowed"] is False
    return {
        "team_version": TEAM_VERSION,
        "agent_roles": sorted(AGENT_ROLES),
        "specialist_subagents": sorted(SPECIALIST_SUBAGENTS),
        "frontier_radar": sorted(FRONTIER_RADAR),
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["self-check", "priority", "dual-priority", "frontier-gate", "support-route"])
    parser.add_argument("--ledger", default=str(Path(__file__).with_name("incident-ledger.json")))
    parser.add_argument("--typed-failure", default="")
    parser.add_argument("--subsystem", default="")
    parser.add_argument("--wait-state", default="")
    args = parser.parse_args()

    if args.command == "self-check":
        print(json.dumps(self_check(), indent=2, sort_keys=True))
        return

    if args.command == "support-route":
        decision = select_support_subagent(
            {
                "typed_failure": args.typed_failure,
                "primary_subsystem": args.subsystem,
                "wait_state": args.wait_state,
            }
        )
        print(json.dumps(decision.as_dict(), indent=2, sort_keys=True))
        return

    records = load_ledger(args.ledger)
    if args.command == "priority":
        print(json.dumps(select_priority_incident(records).as_dict(), indent=2, sort_keys=True))
        return
    if args.command == "dual-priority":
        print(json.dumps(select_dual_stream_work(records).as_dict(), indent=2, sort_keys=True))
        return
    print(json.dumps({"frontier_allowed": not reliability_blocks_frontier(records)}, sort_keys=True))


if __name__ == "__main__":
    main()
