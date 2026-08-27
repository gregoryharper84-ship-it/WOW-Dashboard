"""Fail-closed audit/governor boundary for a future fitted MLB event model.

This module does not score games, fit parameters, calibrate probabilities, or
persist rows. It only validates a candidate result produced later by the one
shared full-game MLB simulation required by the implementation contract.
"""
from __future__ import annotations

from typing import Any, Optional


MIN_EVENT_SIMULATIONS = 50_000
NORMALIZATION_TOLERANCE = 0.000001


def _normalized_pair(home: Optional[float], away: Optional[float]) -> bool:
    return (
        home is not None and away is not None
        and 0 < home < 1 and 0 < away < 1
        and abs(home + away - 1.0) <= NORMALIZATION_TOLERANCE
    )


def audit_future_event_claim(claim: dict[str, Any]) -> str:
    """Reject any future probability claim that cannot pass the LLP audit."""
    if claim.get("simulation_count", 0) < MIN_EVENT_SIMULATIONS:
        raise ValueError("SIMULATION_COUNT_BELOW_MINIMUM")
    if not claim.get("shared_simulation_run_id"):
        raise ValueError("SHARED_GAME_SIMULATION_REQUIRED")

    for prefix in ("raw", "independent"):
        if not _normalized_pair(
            claim.get(f"{prefix}_home_probability"),
            claim.get(f"{prefix}_away_probability"),
        ):
            raise ValueError("OUTCOME_SPACE_NOT_NORMALIZED")

    keys = (
        "calibrated_home_probability", "calibrated_home_lower_bound",
        "calibrated_home_upper_bound", "calibrated_away_probability",
        "calibrated_away_lower_bound", "calibrated_away_upper_bound",
    )
    if any(claim.get(key) is None for key in keys):
        raise ValueError("NOT_RANK_ELIGIBLE")
    hp, hl, hu, ap, al, au = (claim[key] for key in keys)
    if not (0 < hl <= hp <= hu < 1 and 0 < al <= ap <= au < 1):
        raise ValueError("PROBABILITY_RANGE_UNSUPPORTED")
    if not _normalized_pair(hp, ap):
        raise ValueError("OUTCOME_SPACE_NOT_NORMALIZED")

    for key in (
        "model_version", "model_artifact_id", "calibration_method",
        "calibration_version", "bounds_method_version", "source_snapshot_id",
        "model_timestamp",
    ):
        if not claim.get(key):
            raise ValueError("PROBABILITY_AUDIT_FAILURE")

    latest_update = claim.get("latest_material_update_timestamp")
    if latest_update and claim["model_timestamp"] < latest_update:
        raise ValueError("STALE_MODEL_INVALIDATED")

    if (
        claim.get("non_normal_regime_probability", 0) > 0
        and claim.get("normal_regime_favorite_probability")
        == claim.get("final_favorite_probability")
    ):
        raise ValueError("UNCONDITIONAL_PROBABILITY_REQUIRED")

    if claim.get("market_prior_weight", 0) > 0.50:
        return "PASS_WITH_CONFIDENCE_CEILING:MARKET_DEPENDENT_MODEL"
    return "PASS_PROBABILITY_AUDIT"


def govern_event_decision(home_lower: float, away_lower: float) -> dict[str, Optional[str]]:
    """Enforce one side or no-pick using audited lower bounds, never price/EV."""
    if abs(home_lower - away_lower) < 0.04:
        return {"event_decision": "NO_PICK_CLOSE_GAME", "selected_side": None}
    return {
        "event_decision": "SELECT_ONE_SIDE",
        "selected_side": "HOME" if home_lower > away_lower else "AWAY",
    }
