"""Rehydrate immutable projected-lineup MLB probability before LLP governance.

The production MLB bridge intentionally withholds numeric fields while final
publication gates are held. For an already-scored projected-lineup event, V17 may
still preserve the completed sporting probability while rank/final publication
waits for official lineup refresh. This adapter reads only the immutable score
snapshot named by the held bridge receipt and only when the receipt proves the
fitted route, ratification, calibration health, feature hydration, and projected-
lineup score status.

It never fits, recalibrates, interpolates, or manufactures probability. Any
identity/status/numeric mismatch returns the original held payload unchanged.
can_execute remains false.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

CAN_EXECUTE = False
_ELIGIBLE_CODES = {"REAL_FITTED_MODEL_PATH_PROVEN"}
_ELIGIBLE_SCORE_STATUS = {"SHADOW_SCORED_LINEUP_PENDING"}
_ELIGIBLE_LINEUP = {"NOT_YET_AVAILABLE", "PROJECTED", "PROJECTED_HIGH_CONFIDENCE", "PROJECTED_MEDIUM_CONFIDENCE"}
_REQUIRED_CURRENT_BLOCKERS = {"LINEUP_NOT_CONFIRMED", "OFFICIAL_LINEUP_REFRESH_OFFICIAL_LINEUP_NOT_AVAILABLE", "POST_LINEUP_SCORE_SNAPSHOT_REQUIRED"}
_NUMERIC_MAPPING = {
    "raw_home_probability": "raw_home_probability",
    "raw_away_probability": "raw_away_probability",
    "calibrated_home_probability": "calibrated_home_probability",
    "calibrated_away_probability": "calibrated_away_probability",
    "calibrated_home_lower_bound": "home_lower_bound",
    "calibrated_home_upper_bound": "home_upper_bound",
    "calibrated_away_lower_bound": "away_lower_bound",
    "calibrated_away_upper_bound": "away_upper_bound",
    "tie_after_9_probability": "tie_after_9_probability",
}


def _finite_probability(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and 0.0 <= parsed <= 1.0 else None


def _receipt_eligible(payload: dict[str, Any]) -> bool:
    blockers = {str(x) for x in (payload.get("current_publication_blockers") or [])}
    return bool(
        payload.get("code") in _ELIGIBLE_CODES
        and payload.get("probability_fields_withheld") is True
        and payload.get("scoring_evidence_produced") is True
        and payload.get("governed_probability_capability") == "AVAILABLE"
        and payload.get("score_status") in _ELIGIBLE_SCORE_STATUS
        and str(payload.get("lineup_status") or "").upper() in _ELIGIBLE_LINEUP
        and payload.get("ratification_status") == "RATIFIED"
        and payload.get("calibration_health_status") == "PASS"
        and payload.get("feature_hydration_status") == "PASS"
        and bool(payload.get("score_snapshot_id"))
        and bool(payload.get("shadow_event_id"))
        and bool(payload.get("server_snapshot_id"))
        and blockers
        and blockers <= _REQUIRED_CURRENT_BLOCKERS
    )


def _load_score(payload: dict[str, Any], req: Any, *, event_api: Any) -> dict[str, Any] | None:
    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        return None
    try:
        rows = (
            get_client().table("wow_mlb_forward_score_snapshots")
            .select(
                "score_snapshot_id,shadow_event_id,model_timestamp,model_version,calibration_id,calibration_method,"
                "raw_home_probability,raw_away_probability,calibrated_home_probability,"
                "calibrated_away_probability,home_lower_bound,home_upper_bound,away_lower_bound,"
                "away_upper_bound,home_bound_status,away_bound_status,tie_after_9_probability,"
                "lineup_status_at_score,score_status,blockers,probability_publishable,can_execute"
            )
            .eq("score_snapshot_id", str(payload["score_snapshot_id"]))
            .eq("shadow_event_id", str(payload["shadow_event_id"]))
            .limit(1).execute().data or []
        )
    except Exception:
        return None
    if not rows or not isinstance(rows[0], dict):
        return None
    score = dict(rows[0])
    if str(score.get("score_snapshot_id")) != str(payload.get("score_snapshot_id")):
        return None
    if str(score.get("shadow_event_id")) != str(payload.get("shadow_event_id")):
        return None
    if score.get("score_status") not in _ELIGIBLE_SCORE_STATUS:
        return None
    if str(score.get("lineup_status_at_score") or "").upper() not in _ELIGIBLE_LINEUP:
        return None
    if score.get("home_bound_status") not in {"PASS", "PASS_RESEARCH_BOUND"}:
        return None
    if score.get("away_bound_status") not in {"PASS", "PASS_RESEARCH_BOUND"}:
        return None
    if score.get("can_execute") is not False:
        return None
    if not str(score.get("calibration_id") or "").strip():
        return None
    if not str(score.get("calibration_method") or "").strip():
        return None

    numerics: dict[str, float] = {}
    for output_name, source_name in _NUMERIC_MAPPING.items():
        value = _finite_probability(score.get(source_name))
        if value is None:
            return None
        numerics[output_name] = value
    if abs(numerics["raw_home_probability"] + numerics["raw_away_probability"] - 1.0) > 1e-6:
        return None
    if abs(numerics["calibrated_home_probability"] + numerics["calibrated_away_probability"] - 1.0) > 1e-6:
        return None
    if not (numerics["calibrated_home_lower_bound"] <= numerics["calibrated_home_probability"] <= numerics["calibrated_home_upper_bound"]):
        return None
    if not (numerics["calibrated_away_lower_bound"] <= numerics["calibrated_away_probability"] <= numerics["calibrated_away_upper_bound"]):
        return None

    latest = str(getattr(req, "latest_material_update_timestamp", None) or "")
    model_timestamp = str(score.get("model_timestamp") or "")
    if latest and model_timestamp and model_timestamp < latest:
        return None
    return {**score, **numerics}


def rehydrate_projected_probability(payload: dict[str, Any], req: Any, *, event_api: Any) -> dict[str, Any]:
    """Return original payload unless immutable score evidence proves completion."""
    if not _receipt_eligible(payload):
        return payload
    score = _load_score(payload, req, event_api=event_api)
    if score is None:
        return payload
    out = dict(payload)
    for field in _NUMERIC_MAPPING:
        out[field] = score[field]
    calibration_id = str(score["calibration_id"])
    calibration_method = str(score["calibration_method"])
    out.update({
        "model_version": score.get("model_version") or payload.get("model_version"),
        "model_timestamp": score.get("model_timestamp") or payload.get("model_timestamp"),
        "calibration_method": calibration_method,
        "calibration_version": calibration_id,
        "calibration_sample_scope": f"IMMUTABLE_FORWARD_SCORE_SNAPSHOT:{calibration_id}",
        "probability_fields_withheld": False,
        "sporting_probability_completed": True,
        "sporting_probability_status": "COMPLETED_IMMUTABLE_SCORE_REHYDRATED",
        "projected_lineup_score_rehydration": {
            "status": "PASS",
            "source": "wow_mlb_forward_score_snapshots",
            "score_snapshot_id": str(score["score_snapshot_id"]),
            "calibration_id": calibration_id,
            "calibration_method": calibration_method,
            "probabilities_recomputed": False,
            "calibration_recomputed": False,
            "can_execute": False,
        },
        "can_execute": False,
    })
    return out


def install_projected_lineup_score_rehydration(runtime: Any) -> bool:
    """Wrap the common MLB LLP governance seam before preservation captures it."""
    if getattr(runtime, "_v17_projected_score_rehydration_installed", False):
        return True
    original = getattr(runtime, "_run_mlb_llp_governance", None)
    if not callable(original):
        return False

    def wrapper(req: Any, route: Any, model_result: dict[str, Any], envelope: Any | None = None, *, event_api: Any):
        hydrated = rehydrate_projected_probability(model_result, req, event_api=event_api)
        return original(req, route, hydrated, envelope=envelope, event_api=event_api)

    runtime._run_mlb_llp_governance = wrapper
    runtime._v17_projected_score_rehydration_installed = True
    return True


__all__ = ["rehydrate_projected_probability", "install_projected_lineup_score_rehydration"]
