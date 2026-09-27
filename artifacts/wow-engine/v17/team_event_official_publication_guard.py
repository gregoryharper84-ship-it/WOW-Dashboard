"""Fail-closed official-publication guard for V17 team/event results.

This is a presentation/materialization boundary, not a sporting model. It never
creates, changes, calibrates, or suppresses a valid sporting probability. It
only decides whether an already-scored team/event result has proved the exact
V17 governance state required to be surfaced as an official ranked result.

Moneyline publication additionally honors the canonical probability-only lane
contract: winner rows cannot be promoted from BEST_SIDE merely because the point
estimate exceeds 50%, and explicit upset rows must remain on the underdog lane.
The guard consumes the controlling specialist's calibrated lower bound; it does
not recompute or replace that probability.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

V17_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
FINAL_APPROVED = "FINAL_APPROVED"
PASS_PROBABILITY_AUDIT = "PASS_PROBABILITY_AUDIT"

WINNER_WATCH_MIN_LOWER_BOUND = 0.55
UPSET_QUALIFIED_MIN_LOWER_BOUND = 0.40

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

_UPSET_FAMILIES = frozenset({"UNDERDOG", "UPSET"})
_UPSET_INTENTS = frozenset({"UNDERDOG", "UPSET"})


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


def _selected_calibrated_package(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    """Return the selected outcome's point estimate/lower bound when available.

    The scalar selected/generic package is authoritative at publication time.
    Home/away pairs are intentionally not guessed here because doing so could
    silently select an outcome at the publication boundary.
    """
    for probability_key, lower_key in (
        ("selected_calibrated_probability", "selected_calibrated_lower_bound"),
        ("calibrated_probability", "calibrated_lower_bound"),
        ("calibrated_probability", "calibrated_probability_lower_bound"),
    ):
        probability = _probability(payload.get(probability_key))
        lower = _probability(payload.get(lower_key))
        if probability is not None and lower is not None and lower <= probability:
            return probability, lower
    return None, None


def _valid_calibrated_package(payload: dict[str, Any]) -> bool:
    point, lower = _selected_calibrated_package(payload)
    if point is not None and lower is not None:
        return True
    pairs = (
        ("calibrated_home_probability", "calibrated_home_lower_bound"),
        ("calibrated_away_probability", "calibrated_away_lower_bound"),
    )
    for probability_key, lower_key in pairs:
        probability = _probability(payload.get(probability_key))
        bound = _probability(payload.get(lower_key))
        if probability is not None and bound is not None and bound <= probability:
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


def _requested_probability_lane(payload: dict[str, Any]) -> str:
    family = str(payload.get("candidate_family") or "").strip().upper()
    intent = str(payload.get("decision_intent") or "").strip().upper()
    if family in _UPSET_FAMILIES or intent in _UPSET_INTENTS:
        return "UPSET"
    return "WINNER"


def _market_role(payload: dict[str, Any]) -> str | None:
    role = str(payload.get("market_role") or "").strip().upper()
    if role in {"FAVORITE", "UNDERDOG", "EVEN", "CONFLICT"}:
        return role
    if payload.get("underdog_verified") is True:
        return "UNDERDOG"
    if payload.get("favorite_verified") is True:
        return "FAVORITE"
    return None


def _winner_tier(lower: float | None) -> str | None:
    if lower is None:
        return None
    if lower >= 0.70:
        return "ELITE_WINNER"
    if lower >= 0.65:
        return "STRONG_WINNER"
    if lower >= 0.60:
        return "QUALIFIED_WINNER"
    if lower >= WINNER_WATCH_MIN_LOWER_BOUND:
        return "WINNER_WATCH"
    return "WINNER_REJECT"


def _upset_tier(lower: float | None) -> str | None:
    if lower is None:
        return None
    if lower >= 0.47:
        return "ELITE_UPSET_PROFILE"
    if lower >= 0.43:
        return "STRONG_UPSET_PROFILE"
    if lower >= UPSET_QUALIFIED_MIN_LOWER_BOUND:
        return "QUALIFIED_UPSET_PROFILE"
    if lower >= 0.35:
        return "UPSET_WATCH"
    return "UPSET_REJECT"


def _moneyline_publication_blockers(payload: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Enforce lane/tier publication without mutating sporting probability."""
    blockers: list[str] = []
    lane = _requested_probability_lane(payload)
    role = _market_role(payload)
    point, lower = _selected_calibrated_package(payload)
    tier = _upset_tier(lower) if lane == "UPSET" else _winner_tier(lower)

    # Do not invent a selected outcome from only home/away values. Existing
    # calibrated-package validation below still handles malformed packages.
    if point is None or lower is None:
        return blockers, {
            "lane": lane,
            "market_role": role,
            "probability_tier": tier,
            "selected_calibrated_probability": point,
            "selected_calibrated_lower_bound": lower,
        }

    if lane == "WINNER":
        if role == "UNDERDOG":
            blockers.append("TEAM_EVENT_UNDERDOG_ON_WINNER_LANE")
        elif role == "CONFLICT":
            blockers.append("TEAM_EVENT_FAVORITE_STATUS_CONFLICT")
        if lower < WINNER_WATCH_MIN_LOWER_BOUND:
            blockers.append("TEAM_EVENT_WINNER_LOWER_BOUND_BELOW_WATCH_FLOOR")
    else:
        if role != "UNDERDOG":
            blockers.append("TEAM_EVENT_UPSET_UNDERDOG_STATUS_NOT_VERIFIED")
        if lower < UPSET_QUALIFIED_MIN_LOWER_BOUND:
            blockers.append("TEAM_EVENT_UPSET_LOWER_BOUND_BELOW_QUALIFIED_FLOOR")

    return blockers, {
        "lane": lane,
        "market_role": role,
        "probability_tier": tier,
        "selected_calibrated_probability": point,
        "selected_calibrated_lower_bound": lower,
    }


def evaluate_team_event_official_publication(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic V17 official-publication decision."""
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

    lane_blockers, classification = _moneyline_publication_blockers(payload)
    blockers.extend(lane_blockers)

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
        "publication_lane": classification["lane"],
        "market_role": classification["market_role"],
        "probability_tier": classification["probability_tier"],
        "selected_calibrated_probability": classification["selected_calibrated_probability"],
        "selected_calibrated_lower_bound": classification["selected_calibrated_lower_bound"],
        "blockers": blockers,
        "terminal_authority": V17_TERMINAL_REDUCER,
        "can_execute": False,
    }
