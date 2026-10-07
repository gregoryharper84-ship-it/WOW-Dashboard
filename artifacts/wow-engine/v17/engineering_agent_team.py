"""Deterministic operating system for the WOW V17 multi-agent engineering team.

This module coordinates engineering work only. It never publishes sporting
probabilities, never places wagers, and never grants execution authority.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEAM_VERSION = "5.1"

AGENT_ROLES: dict[str, dict[str, Any]] = {
    "ENGINEERING_LEAD_AGENT": {
        "mission": "Own priority, single-incident focus, dedupe, and handoff discipline.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "LIFECYCLE_CONTROLLER_AGENT": {
        "mission": "Own one closure journey from detection through terminal disposition and prevent lifecycle handoff loss.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "QUEUE_STEWARD_AGENT": {
        "mission": "Enforce closure-first WIP, dedupe stale work, and keep active engineering focused on the highest-priority parent incident.",
        "may_write_code": False,
        "may_approve_own_work": False,
        "may_change_probability_behavior": False,
    },
    "RELEASE_VERIFICATION_OWNER_AGENT": {
        "mission": "Own exact-head governance follow-through, merge-to-deploy continuity, production acceptance, and terminal verification evidence.",
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
USER_CRITICAL_JOURNEYS = ("ALL_SPORTS_PROPS", "ALL_SPORTS_ML_WINNERS", "ALL_SPORTS_SPREADS", "ALL_SPORTS_TEAM_MARKETS", "ALL_SPORTS_UPSETS")
MAX_ACTIVE_PRODUCT_RECOVERY = 1
MAX_ACTIVE_SUPPORTING_INVESTIGATION = 1

LIFECYCLE_CELL = {
    "controller": "LIFECYCLE_CONTROLLER_AGENT",
    "queue_steward": "QUEUE_STEWARD_AGENT",
    "release_verification_owner": "RELEASE_VERIFICATION_OWNER_AGENT",
}
CHANGE_CLASS_WEIGHT = {"A": 0, "B": 1, "C": 2}
CLASS_B_SCOPE_FLAGS = (
    "changes_model_registration",
    "changes_routing_or_hydration",
    "changes_reconciliation_behavior",
    "changes_prediction_persistence_contract",
)
CLASS_C_SCOPE_FLAGS = (
    "changes_sporting_probability_math",
    "changes_fitted_artifacts_or_coefficients",
    "changes_calibration_or_lower_bounds",
    "changes_qualification_thresholds",
    "changes_failure_path_weighting",
)
LIFECYCLE_SCOPE_FLAGS = CLASS_B_SCOPE_FLAGS + CLASS_C_SCOPE_FLAGS

LIFECYCLE_CONTROL_PATHS = frozenset({
    ".github/workflows/wow-v17-24h-engineering-closure-loop.yml",
    ".github/workflows/wow-v17-engineering-auditor-code-health.yml",
    ".github/workflows/wow-v17-terminal-closure-controller.yml",
    "artifacts/wow-engine/v17/engineering_agent_team.py",
    "artifacts/wow-engine/v17/terminal_closure_controller.py",
})
LIFECYCLE_CONTROL_PREFIXES = (
    ".agents/skills/wow-engineering-lifecycle-closure-cell/",
)

BASE_DRIFT_ACTIONS = frozenset({
    "NO_BASE_DRIFT",
    "REVALIDATE_IN_PLACE",
    "RESTACK_SAME_PR",
})

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


@dataclass(frozen=True)
class BaseDriftDecision:
    action: str
    rerun_candidate_gates: bool
    restack_same_pr: bool
    close_pr: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "rerun_candidate_gates": self.rerun_candidate_gates,
            "restack_same_pr": self.restack_same_pr,
            "close_pr": self.close_pr,
            "reason": self.reason,
            "can_execute": False,
        }


def decide_base_drift(
    *,
    base_advanced: bool,
    mergeable: bool,
    semantic_conflict: bool = False,
    protected_path_overlap: bool = False,
) -> BaseDriftDecision:
    """Keep a valid PR in place when main advances.

    Base movement invalidates only candidate certification. It does not make the
    implementation a duplicate. Restacking is reserved for an actual merge
    conflict or a proven semantic incompatibility with the new base.
    """
    if not base_advanced:
        return BaseDriftDecision(
            action="NO_BASE_DRIFT",
            rerun_candidate_gates=False,
            restack_same_pr=False,
            close_pr=False,
            reason="Base has not advanced; preserve current certification state.",
        )

    if not mergeable or semantic_conflict:
        return BaseDriftDecision(
            action="RESTACK_SAME_PR",
            rerun_candidate_gates=True,
            restack_same_pr=True,
            close_pr=False,
            reason=(
                "Current base introduces a real merge or semantic conflict; "
                "restack the existing PR branch and recertify it without PR recreation."
            ),
        )

    scope = "protected-path overlap" if protected_path_overlap else "unrelated or compatible base drift"
    return BaseDriftDecision(
        action="REVALIDATE_IN_PLACE",
        rerun_candidate_gates=True,
        restack_same_pr=False,
        close_pr=False,
        reason=(
            f"{scope}; preserve PR/head identity and certify a new prospective "
            "merge candidate against current main."
        ),
    )


def validate_merge_candidate_receipt(
    receipt: dict[str, Any],
    *,
    expected_pr_number: int,
    expected_head_sha: str,
    expected_base_sha: str,
    expected_candidate_sha: str,
) -> list[str]:
    """Validate immutable identity for the prospective merge artifact."""
    errors: list[str] = []
    identity = receipt.get("merge_candidate")
    if not isinstance(identity, dict):
        return ["merge candidate receipt requires merge_candidate identity"]

    expected = {
        "pr_number": expected_pr_number,
        "pr_head_sha": expected_head_sha,
        "base_sha": expected_base_sha,
        "candidate_sha": expected_candidate_sha,
    }
    for field, expected_value in expected.items():
        if identity.get(field) != expected_value:
            errors.append(
                f"merge candidate {field} mismatch: "
                f"{identity.get(field)!r} != {expected_value!r}"
            )

    if receipt.get("certification_status") != "PASS":
        errors.append("merge candidate certification_status must be PASS")
    if receipt.get("terminal_authority") != "V17_TERMINAL_REDUCER":
        errors.append("merge candidate receipt must preserve V17_TERMINAL_REDUCER")
    if receipt.get("can_execute") is not False:
        errors.append("merge candidate receipt must set can_execute=false")
    return errors


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
    """Any actionable engineering backlog preempts discretionary frontier work.

    In closure-focus mode, model/frontier exploration resumes only after the
    approved engineering queue has no actionable item. Severity changes order,
    not whether closure work deserves capacity.
    """
    return any(is_actionable(record) for record in records)


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
    """Run the whole engineering team in 24/7 closure-focus mode.

    Existing restoration and acceleration entries are one closure queue. Exactly
    one highest-priority actionable incident owns engineering attention at a
    time; no second acceleration incident is dispatched in parallel. Read-only
    specialists, review, QA, release, and observability may still work in
    parallel, but only on that same parent closure journey.
    """
    actionable = [record for record in records if is_actionable(record)]
    primary = select_priority_incident(actionable)
    none = select_priority_incident([])
    if primary.incident_id is None:
        return DualStreamDecision(
            restoration=primary,
            acceleration=none,
            acceleration_blocked_reason=None,
        )
    return DualStreamDecision(
        restoration=primary,
        acceleration=none,
        acceleration_blocked_reason=(
            "CLOSURE_FOCUS_MODE: all engineering capacity is attached to the "
            "highest-priority existing closure journey; no parallel acceleration incident."
        ),
    )

def is_lifecycle_control_path(path: str) -> bool:
    normalized = str(path or "").strip().replace("\\", "/")
    return (
        normalized in LIFECYCLE_CONTROL_PATHS
        or any(normalized.startswith(prefix) for prefix in LIFECYCLE_CONTROL_PREFIXES)
    )


def lifecycle_record_from_pr_body(
    body: str,
    *,
    changed_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Parse machine-readable lifecycle attestation from a PR body.

    Touching a known lifecycle control path activates the boundary even if the
    PR author omitted the marker, so omission cannot silently bypass Class A
    validation.
    """
    text = str(body or "")
    marker = re.search(
        r"(?im)^\s*Lifecycle-Control-Plane\s*:\s*true\s*$",
        text,
    )
    lifecycle_touched = any(is_lifecycle_control_path(path) for path in changed_paths)
    if marker is None and not lifecycle_touched:
        return {"lifecycle_control_plane": False}

    record: dict[str, Any] = {"lifecycle_control_plane": True}
    change_class = re.search(r"(?im)^\s*Change-Class\s*:\s*([ABC])\s*$", text)
    if change_class:
        record["change_class"] = change_class.group(1).upper()
    for flag in LIFECYCLE_SCOPE_FLAGS:
        match = re.search(
            rf"(?im)^\s*-\s*{re.escape(flag)}\s*:\s*(true|false)\s*$",
            text,
        )
        if match:
            record[flag] = match.group(1).lower() == "true"
    return record


def validate_lifecycle_pr_body(
    body: str,
    *,
    changed_paths: tuple[str, ...] = (),
) -> list[str]:
    return validate_lifecycle_classification(
        lifecycle_record_from_pr_body(body, changed_paths=changed_paths)
    )


def required_lifecycle_change_class(record: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Derive the minimum governed change class from explicit lifecycle scope flags."""
    class_c = tuple(flag for flag in CLASS_C_SCOPE_FLAGS if record.get(flag) is True)
    if class_c:
        return "C", class_c
    class_b = tuple(flag for flag in CLASS_B_SCOPE_FLAGS if record.get(flag) is True)
    if class_b:
        return "B", class_b
    return "A", ()


def validate_lifecycle_classification(record: dict[str, Any]) -> list[str]:
    """Fail closed when lifecycle work claims a lower class than its declared scope.

    This validator applies only to lifecycle-control-plane changes. It does not
    classify sporting output and cannot authorize Class B/C promotion.
    """
    if record.get("lifecycle_control_plane") is not True:
        return []

    errors: list[str] = []
    declared = str(record.get("change_class") or "").strip().upper()
    if declared not in CHANGE_CLASS_WEIGHT:
        errors.append(f"invalid lifecycle change_class: {declared or 'MISSING'}")

    missing_flags = tuple(flag for flag in LIFECYCLE_SCOPE_FLAGS if flag not in record)
    if missing_flags:
        errors.append(
            "lifecycle classification requires explicit scope flags: "
            + ", ".join(missing_flags)
        )

    required, triggers = required_lifecycle_change_class(record)
    if declared in CHANGE_CLASS_WEIGHT and CHANGE_CLASS_WEIGHT[declared] < CHANGE_CLASS_WEIGHT[required]:
        trigger_text = ", ".join(triggers) if triggers else "governance scope"
        errors.append(
            f"CLASSIFICATION_ESCALATION_REQUIRED: declared {declared}, required {required}: {trigger_text}"
        )
    return errors


def validate_closure_record(record: dict[str, Any]) -> list[str]:
    """Enforce one durable closure unit instead of disconnected progress markers."""
    errors: list[str] = []
    errors.extend(validate_lifecycle_classification(record))
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
    assert set(LIFECYCLE_CELL.values()) <= set(AGENT_ROLES)
    drift = decide_base_drift(base_advanced=True, mergeable=True)
    assert drift.action == "REVALIDATE_IN_PLACE"
    assert drift.rerun_candidate_gates is True
    assert drift.restack_same_pr is False
    assert drift.close_pr is False
    conflict = decide_base_drift(base_advanced=True, mergeable=False)
    assert conflict.action == "RESTACK_SAME_PR"
    assert conflict.restack_same_pr is True
    assert conflict.close_pr is False
    assert {drift.action, conflict.action, "NO_BASE_DRIFT"} <= BASE_DRIFT_ACTIONS
    candidate_receipt = {
        "merge_candidate": {
            "pr_number": 1457,
            "pr_head_sha": "head",
            "base_sha": "base",
            "candidate_sha": "candidate",
        },
        "certification_status": "PASS",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }
    assert validate_merge_candidate_receipt(
        candidate_receipt,
        expected_pr_number=1457,
        expected_head_sha="head",
        expected_base_sha="base",
        expected_candidate_sha="candidate",
    ) == []
    lifecycle_a = {
        "lifecycle_control_plane": True,
        "change_class": "A",
        **{flag: False for flag in LIFECYCLE_SCOPE_FLAGS},
    }
    assert validate_lifecycle_classification(lifecycle_a) == []
    lifecycle_b = dict(lifecycle_a)
    lifecycle_b["changes_model_registration"] = True
    assert required_lifecycle_change_class(lifecycle_b)[0] == "B"
    assert any("CLASSIFICATION_ESCALATION_REQUIRED" in error for error in validate_lifecycle_classification(lifecycle_b))
    lifecycle_c = dict(lifecycle_a)
    lifecycle_c["changes_calibration_or_lower_bounds"] = True
    assert required_lifecycle_change_class(lifecycle_c)[0] == "C"
    assert any("CLASSIFICATION_ESCALATION_REQUIRED" in error for error in validate_lifecycle_classification(lifecycle_c))
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
    assert reliability_blocks_frontier([{"severity": "P2", "state": "OPEN"}])
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
    assert dual.acceleration.incident_id is None
    assert dual.acceleration_blocked_reason and "CLOSURE_FOCUS_MODE" in dual.acceleration_blocked_reason
    assert dual.as_dict()["can_execute"] is False
    assert dual.as_dict()["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert route_failure("ACTION_TRANSPORT_FAILURE") == "transport"
    assert route_failure("MODEL_UNAVAILABLE") == "model-capability"
    assert select_support_subagent({"typed_failure": "ACTION_TRANSPORT_FAILURE"}).subagent == "RUNTIME_TRANSPORT_SUBAGENT"
    assert select_support_subagent({"typed_failure": "PERSISTENCE_FAILURE"}).subagent == "DATA_PERSISTENCE_SUBAGENT"
    assert select_support_subagent({"wait_state": "CI_PENDING"}).subagent == "CI_REPOSITORY_SUBAGENT"
    assert USER_CRITICAL_JOURNEYS == ("ALL_SPORTS_PROPS", "ALL_SPORTS_ML_WINNERS", "ALL_SPORTS_SPREADS", "ALL_SPORTS_TEAM_MARKETS", "ALL_SPORTS_UPSETS")
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
    parser.add_argument("command", choices=["self-check", "priority", "dual-priority", "frontier-gate", "support-route", "lifecycle-classify"])
    parser.add_argument("--ledger", default=str(Path(__file__).with_name("incident-ledger.json")))
    parser.add_argument("--pr-body-file", default="")
    parser.add_argument("--changed-paths-file", default="")
    parser.add_argument("--typed-failure", default="")
    parser.add_argument("--subsystem", default="")
    parser.add_argument("--wait-state", default="")
    args = parser.parse_args()

    if args.command == "lifecycle-classify":
        body = Path(args.pr_body_file).read_text(encoding="utf-8") if args.pr_body_file else ""
        changed_paths: tuple[str, ...] = ()
        if args.changed_paths_file:
            changed_paths = tuple(
                line.strip()
                for line in Path(args.changed_paths_file).read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        record = lifecycle_record_from_pr_body(body, changed_paths=changed_paths)
        errors = validate_lifecycle_classification(record)
        print(json.dumps({
            "lifecycle_control_plane": record.get("lifecycle_control_plane") is True,
            "declared_change_class": record.get("change_class"),
            "required_change_class": required_lifecycle_change_class(record)[0] if record.get("lifecycle_control_plane") is True else "N/A",
            "errors": errors,
            "can_execute": False,
        }, indent=2, sort_keys=True))
        if errors:
            raise SystemExit(1)
        return

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
