"""Exact NFL Daily stage proof from an immutable, event-bound prediction readback.

This does not fit or calibrate a model, alter probabilities, or override LLP.
A missing, stale, conflicting, or unreadable receipt leaves the original result
HELD at the existing full-board publication gate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

STAGE_SOURCE = "NFL_IMMUTABLE_PREGAME_PREDICTION_READBACK"
CAN_EXECUTE = False


def _instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _same_instant(a: Any, b: Any) -> bool:
    left, right = _instant(a), _instant(b)
    return left is not None and right is not None and left == right


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if isfinite(x) else None


def _same_number(a: Any, b: Any) -> bool:
    left, right = _number(a), _number(b)
    return left is not None and right is not None and abs(left - right) <= 1e-10


def attach_nfl_daily_stage_receipt(*, db: Any, req: Any, governed: Mapping[str, Any]) -> dict[str, Any]:
    """Attach PASS fields only after exact immutable event/prediction readback.

    Caller must still enforce SQL governance, final refresh and terminal reducer.
    Those are independent gates, never replaced by this evidence.
    """
    out = dict(governed)
    reasons: list[str] = []
    prediction_id = str(out.get("event_prediction_id") or "")
    snapshot_id = str(out.get("score_snapshot_id") or "")
    if not prediction_id or not snapshot_id:
        reasons.append("NFL_STAGE_PROOF_PREDICTION_ID_MISSING")
        rows: list[dict[str, Any]] = []
    else:
        try:
            query = (
                db.table("wow_nfl_event_predictions")
                .select("*")
                .eq("event_prediction_id", prediction_id)
                .limit(2)
                .execute()
            )
            rows = [dict(row) for row in query.data] if isinstance(query.data, list) else []
        except Exception:
            rows = []
            reasons.append("NFL_STAGE_PROOF_READBACK_FAILED")

    if len(rows) != 1:
        reasons.append("NFL_STAGE_PROOF_EXACT_ROW_UNAVAILABLE")
    if not reasons:
        row = rows[0]
        identity_checks = (
            ("official_event_id", getattr(req, "official_event_id", None)),
            ("event_key", getattr(req, "event_key", None)),
            ("requested_slate_date", getattr(req, "requested_slate_date", None)),
            ("selected_participant", out.get("selected_participant")),
            ("source_snapshot_id", getattr(req, "source_snapshot_id", None)),
            ("score_snapshot_id", snapshot_id),
            ("model_artifact_id", out.get("model_artifact_id")),
            ("model_version", out.get("model_version")),
            ("calibration_method", out.get("calibration_method")),
            ("calibration_version", out.get("calibration_version")),
        )
        for field, expected in identity_checks:
            if expected in (None, "") or str(row.get(field) or "") != str(expected):
                reasons.append("NFL_STAGE_PROOF_" + field.upper() + "_MISMATCH")

        for field, expected in (
            ("event_start_time_utc", getattr(req, "event_start_time_utc", None)),
            ("source_snapshot_timestamp", getattr(req, "latest_material_update_timestamp", None)),
            ("model_timestamp", out.get("model_timestamp")),
        ):
            if not _same_instant(row.get(field), expected):
                reasons.append("NFL_STAGE_PROOF_" + field.upper() + "_MISMATCH")

        model_at = _instant(row.get("model_timestamp"))
        material_at = _instant(row.get("latest_material_update_timestamp"))
        if model_at is None or material_at is None or model_at < material_at:
            reasons.append("NFL_STAGE_PROOF_STALE_MODEL")

        if any(row.get(flag) is not True for flag in (
            "model_package_valid", "provenance_complete", "model_probability_publishable"
        )) or row.get("can_execute") is not False:
            reasons.append("NFL_STAGE_PROOF_MODEL_PACKAGE_NOT_CERTIFIED")

        p, lower, upper = (_number(row.get(name)) for name in (
            "calibrated_selection_probability",
            "calibrated_selection_lower_bound",
            "calibrated_selection_upper_bound",
        ))
        if (p is None or lower is None or upper is None
                or not 0 <= lower <= p <= upper <= 1):
            reasons.append("NFL_STAGE_PROOF_CALIBRATED_BOUNDS_INVALID")

        for field, outer in (
            ("calibrated_selection_probability", out.get("calibrated_selection_probability")),
            ("calibrated_selection_lower_bound", out.get("rank_calibrated_lower_bound")),
            ("calibrated_selection_upper_bound", out.get("calibrated_selection_upper_bound")),
        ):
            if not _same_number(row.get(field), outer):
                reasons.append("NFL_STAGE_PROOF_" + field.upper() + "_MISMATCH")

        training_n = row.get("calibration_training_n")
        if (str(row.get("calibration_health_status") or "").upper() != "PASS"
                or not row.get("calibration_method")
                or not row.get("calibration_version")
                or not row.get("uncertainty_method")
                or not isinstance(training_n, int) or training_n <= 0):
            reasons.append("NFL_STAGE_PROOF_DYNAMIC_CALIBRATION_UNPROVEN")

    if reasons:
        # Never adopt a success boolean from an earlier source or a prior event.
        out.pop("probability_package_valid", None)
        out.pop("dynamic_calibration_complete", None)
        out["nfl_stage_receipt"] = {
            "status": "HOLD",
            "source": STAGE_SOURCE,
            "prediction_id": prediction_id or None,
            "blockers": sorted(set(reasons)),
            "can_execute": False,
        }
        out["can_execute"] = False
        return out

    # All fields below originate from the unique persisted pregame prediction,
    # not a market estimate, narrative, or a receipt from another event.
    out.update({
        "prediction_id": prediction_id,
        "immutable_model_timestamp": _instant(row["model_timestamp"]).isoformat(),
        "calibrated_probability": p,
        "calibrated_lower_bound": lower,
        "calibrated_upper_bound": upper,
        "source_snapshot_id": str(row["source_snapshot_id"]),
        "source_snapshot_timestamp": _instant(row["source_snapshot_timestamp"]).isoformat(),
        "outcome_space": "NFL_FULL_GAME_HOME_AWAY",
        "probability_package_valid": True,
        "dynamic_calibration_complete": True,
        "calibration_status": "PASS",
        "nfl_stage_receipt": {
            "status": "PASS",
            "source": STAGE_SOURCE,
            "prediction_id": prediction_id,
            "score_snapshot_id": snapshot_id,
            "official_event_id": str(row["official_event_id"]),
            "model_timestamp": _instant(row["model_timestamp"]).isoformat(),
            "source_snapshot_id": str(row["source_snapshot_id"]),
            "calibration_version": str(row["calibration_version"]),
            "can_execute": False,
        },
        "can_execute": False,
    })
    return out
