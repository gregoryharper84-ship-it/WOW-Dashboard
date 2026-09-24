"""Canonical V17 failure taxonomy for the MLB prospective specialist.

The prospective specialist historically used one exception class for multiple
failure stages.  Classification therefore belongs at the runtime boundary,
where V17 can preserve the established model failure contract without changing
sporting probability mathematics.

Unknown exceptions are deliberately classified as MODEL_SCORER_FAILED, never as
MODEL_UNAVAILABLE.  MODEL_UNAVAILABLE is reserved for an explicitly recognized
absence/unavailability of the governed fitted capability/artifact/calibration.
"""
from __future__ import annotations

from dataclasses import dataclass

MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
MODEL_INPUTS_INSUFFICIENT = "MODEL_INPUTS_INSUFFICIENT"
MODEL_SCORER_FAILED = "MODEL_SCORER_FAILED"
MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
CAN_EXECUTE = False


@dataclass(frozen=True)
class MLBProspectiveFailure:
    code: str
    status_code: int
    blocker_code: str
    reason: str
    can_execute: bool = False


_INPUT_REASONS = {
    "bridge_missing_score_identity",
    "confirmed_strict_pregame_lineup_missing",
    "feature_snapshots_missing",
    "official_lineup_feed_invalid_json",
    "official_lineup_feed_invalid",
    "starter_id_missing",
    "starter_handedness_missing",
    "official_player_platoon_stats_unavailable",
    "batting_order_incomplete",
    "lineup_platoon_evidence_too_thin",
    "feature_vector_mismatch",
    "simulation_count_below_50000",
}

_OUTPUT_REASONS = {
    "prospective_bounds_invalid",
}

_UNAVAILABLE_REASONS = {
    "calibration_health_not_pass",
    "prospective_certified_artifact_missing",
    "artifact_lifecycle_not_eligible",
    "prospective_path_requires_held_fitted_baseline",
}

_SCORER_REASONS = {
    "immutable_specialist_snapshot_write_failed",
}

# Rows required to prove the fitted model/calibrator/distribution capability.
_UNAVAILABLE_ROW_PREFIXES = (
    "wow_mlb_v2d_calibration_health:required_row_missing",
    "wow_mlb_v2d_intercept_calibration:required_row_missing",
    "wow_mlb_v2b_distribution_state:required_row_missing",
)

# Event/score identity rows are inputs for an otherwise selected specialist.
_INPUT_ROW_PREFIXES = (
    "wow_mlb_forward_score_snapshots:required_row_missing",
    "wow_mlb_forward_shadow_events:required_row_missing",
)


def classify_mlb_prospective_failure(exc: BaseException) -> MLBProspectiveFailure:
    reason = str(exc).strip() or type(exc).__name__

    if reason in _INPUT_REASONS or reason.startswith(_INPUT_ROW_PREFIXES):
        return MLBProspectiveFailure(
            code=MODEL_INPUTS_INSUFFICIENT,
            status_code=422,
            blocker_code="SPORT_SPECIFIC_MODEL_INPUTS_INSUFFICIENT",
            reason=reason,
        )

    if reason in _OUTPUT_REASONS:
        return MLBProspectiveFailure(
            code=MODEL_OUTPUT_INVALID,
            status_code=409,
            blocker_code="SCORED_MODEL_VALIDATION_FAILED",
            reason=reason,
        )

    if reason in _UNAVAILABLE_REASONS or reason.startswith(_UNAVAILABLE_ROW_PREFIXES):
        return MLBProspectiveFailure(
            code=MODEL_UNAVAILABLE,
            status_code=409,
            blocker_code="MLB_EVENT_MODEL_NOT_AVAILABLE",
            reason=reason,
        )

    if reason in _SCORER_REASONS:
        return MLBProspectiveFailure(
            code=MODEL_SCORER_FAILED,
            status_code=503,
            blocker_code="EVENT_MODEL_BRIDGE_UNAVAILABLE",
            reason=reason,
        )

    # Fail closed. An invoked scorer throwing an unknown condition is a scorer
    # failure until positively proven to be true capability unavailability.
    return MLBProspectiveFailure(
        code=MODEL_SCORER_FAILED,
        status_code=503,
        blocker_code="EVENT_MODEL_BRIDGE_UNAVAILABLE",
        reason=reason,
    )


__all__ = [
    "CAN_EXECUTE",
    "MLBProspectiveFailure",
    "MODEL_INPUTS_INSUFFICIENT",
    "MODEL_OUTPUT_INVALID",
    "MODEL_SCORER_FAILED",
    "MODEL_UNAVAILABLE",
    "classify_mlb_prospective_failure",
]
