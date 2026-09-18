"""Sport x prop-category focus policy for user-facing WOW prop recommendations.

Scout discovery remains intentionally broad. This module governs only whether a
scored prop category is eligible to reach FINAL_APPROVED/user-facing output.

Initial policy is evidence-conservative and derives category readiness from the
canonical model registry:
  ACTIVE              -> FOCUS_QUALIFIED
  PROVISIONAL         -> FOCUS_PROVISIONAL
  NO_REGISTERED_MODEL -> RESEARCH_ONLY

Calibration-health suppression always overrides model readiness and yields
FOCUS_SUSPENDED. Unknown sport/category combinations fail closed as RESEARCH_ONLY.
The policy never changes a probability; it is an eligibility/governance gate.
"""
from __future__ import annotations

from typing import Any

from . import model_registry

FOCUS_QUALIFIED = "FOCUS_QUALIFIED"
FOCUS_PROVISIONAL = "FOCUS_PROVISIONAL"
RESEARCH_ONLY = "RESEARCH_ONLY"
FOCUS_SUSPENDED = "FOCUS_SUSPENDED"

FOCUS_POLICY_VERSION = "v17-prop-focus-1.0"
USER_FACING_STATES = frozenset({FOCUS_QUALIFIED})


def evaluate(row: dict[str, Any]) -> dict[str, Any]:
    """Return fail-closed focus metadata for a normalized prop row."""
    sport = str(row.get("sport") or "").upper().strip()
    prop_type = str(row.get("prop_type") or "").strip()
    line = row.get("line")

    try:
        numeric_line = float(line) if line is not None else None
    except (TypeError, ValueError):
        numeric_line = None

    health = (row.get("gates") or {}).get("calibration_health") or {}
    health_grade = str(health.get("grade") or "").upper().strip()
    health_ceiling = str(health.get("ceiling") or "").upper().strip()

    if health_grade == "SUPPRESS" or health_ceiling in {"LLP_REJECT", "NO_PLAY"}:
        state = FOCUS_SUSPENDED
        reason = "CALIBRATION_HEALTH_SUPPRESSED"
        registry_status = None
        model_id = None
    else:
        registry = model_registry.lookup(sport, prop_type, numeric_line)
        registry_status = registry.get("status")
        model_id = registry.get("model_id")
        if registry_status == "ACTIVE":
            state = FOCUS_QUALIFIED
            reason = "ACTIVE_PUBLISHABLE_MODEL"
        elif registry_status == "PROVISIONAL":
            state = FOCUS_PROVISIONAL
            reason = "PROVISIONAL_OR_NOT_YET_BACKTESTED"
        else:
            state = RESEARCH_ONLY
            reason = "NO_ACTIVE_REGISTERED_MODEL"

    return {
        "policy_version": FOCUS_POLICY_VERSION,
        "sport": sport,
        "prop_category": prop_type,
        "focus_state": state,
        "focus_qualified": state in USER_FACING_STATES,
        "reason": reason,
        "registry_status": registry_status,
        "model_id": model_id,
    }


def apply(row: dict[str, Any]) -> dict[str, Any]:
    """Attach focus metadata without discarding the row or altering probability."""
    focus = evaluate(row)
    row.setdefault("gates", {})["prop_category_focus"] = focus
    row["focus_state"] = focus["focus_state"]
    row["focus_qualified"] = focus["focus_qualified"]
    row["focus_policy_version"] = focus["policy_version"]
    return row


def user_facing_eligible(row: dict[str, Any]) -> bool:
    """True only when this sport/category is currently focus-qualified."""
    focus = (row.get("gates") or {}).get("prop_category_focus")
    if not isinstance(focus, dict):
        focus = evaluate(row)
    return bool(focus.get("focus_qualified"))
