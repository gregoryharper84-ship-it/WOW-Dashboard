"""V17 advisory capture preflight; never use post-event or held rows as fitted grades.

This module checks source-ledger metadata. It does not authenticate external
provider signatures or approve a probability/certification. Rows failing it
remain in their immutable source tables and are counted as skipped, not erased.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from math import isfinite
from typing import Any, Mapping

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
BINARY_RESULTS = frozenset({"WIN", "LOSS", "HIT", "MISS"})
NON_BINARY_RESULTS = frozenset({"PUSH", "VOID", "REFUND", "TIE"})


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    if not isinstance(value, str) or not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return date.astimezone(timezone.utc) if date.tzinfo else None


def _valid_probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    p = float(value)
    return p if isfinite(p) and 0 < p < 1 else None


def capture_eligibility(
    prediction: Mapping[str, Any],
    settlement: Mapping[str, Any],
    *,
    kind: str,
) -> str | None:
    """Return None only for a valid governed **binary calibration** candidate.

    Returned strings are internal diagnostic reason labels, not new backend
    error codes or V17 terminal dispositions. Immutable originals are untouched.
    """
    if kind not in ("PROP", "EVENT"):
        return "UNSUPPORTED_CAPTURE_KIND"
    id_name = "prediction_id" if kind == "PROP" else "event_prediction_id"
    source_id = str(prediction.get(id_name) or "").strip()
    if not source_id or source_id != str(settlement.get(id_name) or "").strip():
        return "SOURCE_PREDICTION_IDENTITY_UNRESOLVED"

    start = _utc(prediction.get("event_start_time"))
    model = _utc(prediction.get("model_timestamp"))
    created = _utc(prediction.get("created_at"))
    settled = _utc(settlement.get("settlement_timestamp"))
    if not (start and model and created and settled
            and model < start and created < start and settled >= start):
        return "FROZEN_PREGAME_OR_SETTLEMENT_TIMING_UNVERIFIED"
    if not prediction.get("source_snapshot_id"):
        return "SOURCE_SNAPSHOT_ID_UNVERIFIED"
    if not str(settlement.get("settlement_source") or "").strip():
        return "SETTLEMENT_PROVENANCE_UNVERIFIED"

    result = str(settlement.get("official_result") or "").strip().upper()
    if kind == "EVENT":
        # Home binary observation is invalid for draw-enabled 1X2.
        family = str(prediction.get("market_family") or "").upper()
        if "1X2" in family or family in {"THREE_WAY", "THREE_WAY_WINNER"}:
            return "MULTICLASS_ROUTE_REQUIRED"
        if not (prediction.get("model_artifact_id")
                and prediction.get("controlling_specialist")
                and prediction.get("model_valid_after_latest_update") is True):
            return "FITTED_ARTIFACT_OR_MATERIAL_UPDATE_UNVERIFIED"
        latest = _utc(prediction.get("latest_material_update_timestamp"))
        if latest is None or model < latest:
            return "LATEST_MATERIAL_UPDATE_NOT_CONSUMED"
        if str(prediction.get("calibration_status") or "").upper() != "PASS":
            return "CALIBRATION_NOT_VERIFIED"
    else:
        locked = _utc(prediction.get("locked_at"))
        if locked is None or locked >= start:
            return "IMMUTABLE_PREGAME_LOCK_UNVERIFIED"
        if not (prediction.get("model_artifact_checksum")
                and prediction.get("model_family")
                and prediction.get("model_artifact_version")):
            return "FITTED_ARTIFACT_PROVENANCE_MISSING"
        if not (prediction.get("evidence_source_kind")
                or prediction.get("feature_snapshot_hash")):
            return "PREGAME_SOURCE_SNAPSHOT_UNVERIFIED"
        if str(prediction.get("direction") or "").upper() not in {"MORE", "LESS"}:
            return "PROP_DIRECTION_UNRESOLVED"
    if prediction.get("probability_publishable") is not True:
        return "MODEL_NOT_PUBLISHABLE_FOR_CALIBRATION"

    point_field = "calibrated_probability" if kind == "PROP" else "calibrated_home_probability"
    lower_field = ("calibrated_probability_lower_bound" if kind == "PROP"
                   else "calibrated_home_lower_bound")
    point = _valid_probability(prediction.get(point_field))
    lower = _valid_probability(prediction.get(lower_field))
    if point is None or lower is None or lower > point:
        return "GOVERNED_CALIBRATION_PACKAGE_INVALID"

    # The core-intelligence primitive currently accepts raw-probability
    # fallbacks and legacy VOID_*; callers must exclude both from grading.
    if kind == "PROP":
        if not result:
            return "OFFICIAL_SETTLEMENT_RESULT_MISSING"
        if result.startswith("VOID_") or result in NON_BINARY_RESULTS:
            return "NONBINARY_SETTLEMENT_EXCLUDED_FROM_CALIBRATION"
        if result not in BINARY_RESULTS:
            return "OFFICIAL_SETTLEMENT_RESULT_UNSUPPORTED"
    else:
        if settlement.get("void") is True:
            return "NONBINARY_SETTLEMENT_EXCLUDED_FROM_CALIBRATION"
        home = settlement.get("home_score")
        away = settlement.get("away_score")
        if (type(home) is not int or type(away) is not int
                or home < 0 or away < 0 or home == away):
            return "NONBINARY_OR_UNVERIFIED_EVENT_SCORE"
        winner = str(settlement.get("official_winner") or "").strip().casefold()
        home_name = str(prediction.get("home_team") or "").strip().casefold()
        away_name = str(prediction.get("away_team") or "").strip().casefold()
        if not winner or not home_name or not away_name:
            return "EVENT_WINNER_IDENTITY_UNVERIFIED"
        if (home > away and winner != home_name) or (away > home and winner != away_name):
            return "OFFICIAL_EVENT_SCORE_WINNER_CONFLICT"
    return None


__all__ = ["CAN_EXECUTE", "TERMINAL_AUTHORITY", "capture_eligibility"]
