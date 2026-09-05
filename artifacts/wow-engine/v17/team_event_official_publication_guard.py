"""Fail-closed official-publication guard for V17 team/event results.

This is a presentation/materialization boundary, not a sporting model.  It never
creates, changes, calibrates, or suppresses a valid sporting probability.  It
only decides whether an already-scored team/event result has proved the exact
V17 governance state required to be surfaced as an official ranked result.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

V17_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
FINAL_APPROVED = "FINAL_APPROVED"
PASS_PROBABILITY_AUDIT = "PASS_PROBABILITY_AUDIT"

_RESEARCH_ONLY_MARKERS = (
    "FORWARD_SHADOW",
    "RESEARCH_ONLY",
    "PASS_RESEARCH_BOUND",
    "SHADOW_SCORED",
)

_MARKER_FIELDS = (
    "source_mode",
    "artifact_status",
    "qualification_status",
    "calibration_status",
    "claim_status",
    "probability_claim_status",
    "publication_tier",
    "scoring_mode",
)


def _probability(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(parsed) or parsed < 0.0 or parsed > 1.0:
        return None
    return parsed


def _valid_calibrated_package(payload: dict[str, Any]) -> bool:
    """Accept the canonical scalar or team-side calibrated package shape."""
    pairs = (
        ("calibrated_probability", "calibrated_lower_bound"),
        ("calibrated_probability", "calibrated_probability_lower_bound"),
        ("calibrated_home_probability", "calibrated_home_lower_bound"),
        ("calibrated_away_probability", "calibrated_away_lower_bound"),
    )
    for probability_key, lower_key in pairs:
        probability = _probability(payload.get(probability_key))
        lower = _probability(payload.get(lower_key))
        if probability is not None and lower is not None and lower <= probability:
            return True
    return False


def _explicit_research_marker(payload: dict[str, Any]) -> str | None:
    for flag in ("research_only", "shadow_only"):
        if payload.get(flag) is True:
            return flag.upper()
    for field in _MARKER_FIELDS:
        value = str(payload.get(field, "")).upper()
        if any(marker in value for marker in _RESEARCH_ONLY_MARKERS):
            return f"{field}={value}"
    return None


def evaluate_team_event_official_publication(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic V17 official-publication decision.

    The guard intentionally requires more than the two legacy booleans.  A raw
    forward-shadow/research artifact can therefore never become an official
    leaderboard/card row merely because ``probability_publishable`` and
    ``rank_eligible`` were accidentally copied as true.
    """
    blockers: list[str] = []

    marker = _explicit_research_marker(payload)
    if marker is not None:
        blockers.append(f"TEAM_EVENT_RESEARCH_ARTIFACT_NOT_OFFICIAL:{marker}")

    if payload.get("probability_publishable") is not True:
        blockers.append("TEAM_EVENT_PROBABILITY_PUBLISHABLE_NOT_PROVEN")
    if payload.get("rank_eligible") is not True:
        blockers.append("TEAM_EVENT_RANK_ELIGIBILITY_NOT_PROVEN")
    if payload.get("can_execute") is not False:
        blockers.append("TEAM_EVENT_CAN_EXECUTE_INVARIANT_NOT_PROVEN")
    if payload.get("global_terminal_authority") != V17_TERMINAL_REDUCER:
        blockers.append("TEAM_EVENT_TERMINAL_AUTHORITY_NOT_PROVEN")
    if payload.get("terminal_label") != FINAL_APPROVED:
        blockers.append("TEAM_EVENT_FINAL_APPROVAL_NOT_PROVEN")
    if payload.get("llp_probability_audit_result") != PASS_PROBABILITY_AUDIT:
        blockers.append("TEAM_EVENT_PROBABILITY_AUDIT_NOT_PROVEN")
    if payload.get("event_mutex_status") != "PASS":
        blockers.append("TEAM_EVENT_MUTEX_NOT_PROVEN")
    if not _valid_calibrated_package(payload):
        blockers.append("TEAM_EVENT_CALIBRATED_BOUND_PACKAGE_NOT_PROVEN")

    governance = payload.get("llp_governance")
    if not isinstance(governance, dict):
        blockers.append("TEAM_EVENT_LLP_GOVERNANCE_PACKAGE_MISSING")
    else:
        required = {
            "probability_publishable": True,
            "rank_eligible": True,
            "global_terminal_reducer": V17_TERMINAL_REDUCER,
            "can_execute": False,
            "probability_audit_result": PASS_PROBABILITY_AUDIT,
            "event_mutex_status": "PASS",
            "postmodel_gates_status": "PASS",
            "final_gates_status": "PASS",
            "terminal_label": FINAL_APPROVED,
        }
        for field, expected in required.items():
            if governance.get(field) != expected:
                blockers.append(f"TEAM_EVENT_LLP_GOVERNANCE_NOT_PROVEN:{field}")

    blockers = list(dict.fromkeys(blockers))
    allowed = not blockers
    return {
        "status": "PASS" if allowed else "HELD",
        "official_publication_allowed": allowed,
        "rank_eligible": allowed,
        "probability_publishable": allowed,
        "blockers": blockers,
        "terminal_authority": V17_TERMINAL_REDUCER,
        "can_execute": False,
    }
