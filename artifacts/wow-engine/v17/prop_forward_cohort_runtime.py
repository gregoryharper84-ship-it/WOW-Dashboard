"""Server-owned forward prop calibration cohort capture for WOW V17.

This module does not fit, certify, or promote a calibrator.  It reuses the
canonical prop evidence snapshots and the existing fitted specialist scorer to
persist immutable pregame forecasts into ``wow_predictions`` so the existing
governed settlement loop can grade them after the event.  Calibration phase
thresholds remain backend-owned in ``wow_runtime_capabilities``; reaching a
threshold is reported as readiness for a certified fitter, never as permission
to invent or activate one.

WOW-PATCH-2026-09-02-V17-PROP-FORWARD-COHORT
can_execute=false unconditionally.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid5, NAMESPACE_URL

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


SPORT = "MLB"
STAT_TYPE = "PITCHER_STRIKEOUTS"
PROVIDER = "WOW_PROP_FITTED_MODEL_V1"
CAPABILITY_KEY = "PROP_PROBABILITY"
DIRECTIONS = ("MORE", "LESS")


class PropForwardCohortRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_snapshots: int = Field(default=50, ge=1, le=200)


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_future(value: Any, *, now: datetime) -> bool:
    parsed = _aware(value)
    return parsed is not None and parsed > now


def _snapshot_key(snapshot_id: Any, direction: str) -> tuple[str, str]:
    return str(snapshot_id), str(direction).upper()


def _eligible_snapshots(db: Any, limit: int, *, now: datetime) -> list[dict[str, Any]]:
    rows = (
        db.table("wow_prop_evidence_snapshots")
        .select(
            "source_snapshot_id,captured_at,event_id,event_start_time,sport,player,"
            "stat_type,line,hydration_status,blockers"
        )
        .eq("sport", SPORT)
        .eq("stat_type", STAT_TYPE)
        .eq("hydration_status", "PASS")
        .order("event_start_time")
        .limit(limit * 3)
        .execute()
        .data
        or []
    )
    selected: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        if row.get("blockers"):
            continue
        captured = _aware(row.get("captured_at"))
        event_start = _aware(row.get("event_start_time"))
        if captured is None or event_start is None:
            continue
        if captured >= event_start or event_start <= now:
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _existing_forward_keys(db: Any) -> set[tuple[str, str]]:
    rows = (
        db.table("wow_predictions")
        .select("source_snapshot_id,direction")
        .eq("sport", SPORT)
        .eq("stat_type", STAT_TYPE)
        .eq("model_provider_identity", PROVIDER)
        .execute()
        .data
        or []
    )
    return {
        _snapshot_key(row.get("source_snapshot_id"), row.get("direction"))
        for row in rows
        if row.get("source_snapshot_id") and str(row.get("direction") or "").upper() in DIRECTIONS
    }


def _extract_model_output(scored: dict[str, Any], direction: str) -> dict[str, Any] | None:
    prediction = scored.get("prediction")
    if isinstance(prediction, dict):
        return dict(prediction)

    research = scored.get("research_model_output")
    if not isinstance(research, dict):
        return None
    direction = direction.upper()
    raw_selected = research.get("raw_specialist_probability")
    if raw_selected is None:
        raw_selected = research.get("raw_probability_more" if direction == "MORE" else "raw_probability_less")
    return {
        "raw_model_probability": raw_selected,
        "probability_more": research.get("raw_probability_more"),
        "probability_less": research.get("raw_probability_less"),
        "push_probability": research.get("push_probability"),
        "model_provider_identity": research.get("provider_identity"),
        "model_family": research.get("model_family"),
        "model_artifact_version": research.get("model_artifact_version"),
        "model_artifact_checksum": research.get("model_artifact_checksum"),
        "specialist_version": research.get("specialist_version"),
        "certification_id": research.get("certification_id"),
        "distribution_type": research.get("distribution_type"),
        "calibration_status": research.get("calibration_status"),
        "calibrated_probability": research.get("calibrated_probability"),
        "calibrated_probability_lower_bound": research.get("calibrated_probability_lower_bound"),
        "calibrated_probability_upper_bound": research.get("calibrated_probability_upper_bound"),
        "model_timestamp": research.get("model_timestamp"),
    }


def _prediction_id(snapshot_id: str, direction: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"wow-v17-forward-prop:{snapshot_id}:{direction.upper()}"))


def _prediction_payload(
    snapshot: dict[str, Any],
    direction: str,
    scored: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any] | None:
    output = _extract_model_output(scored, direction)
    if output is None or output.get("raw_model_probability") is None:
        return None

    model_timestamp = _aware(output.get("model_timestamp")) or now
    event_start = _aware(snapshot.get("event_start_time"))
    captured_at = _aware(snapshot.get("captured_at"))
    if event_start is None or captured_at is None:
        return None
    # Forward-cohort integrity: a forecast that became knowable at/after first
    # pitch is never made eligible by this persistence layer.
    if model_timestamp >= event_start or captured_at >= event_start:
        return None

    provider = output.get("model_provider_identity") or PROVIDER
    if provider != PROVIDER:
        return None

    allowed_optional = (
        "raw_model_probability",
        "independent_model_probability",
        "effective_sample_size",
        "calibration_status",
        "calibration_method",
        "calibration_version",
        "calibration_training_n",
        "calibration_parent_cohort",
        "calibration_fit_start",
        "calibration_fit_end",
        "bounds_method_version",
        "calibrated_probability",
        "calibrated_probability_lower_bound",
        "calibrated_probability_upper_bound",
        "probability_ceiling",
        "model_provider_identity",
        "model_family",
        "model_artifact_version",
        "model_artifact_checksum",
        "model_bundle_fingerprint",
        "model_artifact_lifecycle_state",
        "feature_schema_version",
        "feature_transform_version",
        "feature_snapshot_hash",
        "training_dataset_hash",
        "training_code_sha",
        "specialist_version",
        "certification_id",
        "distribution_type",
        "probability_more",
        "probability_less",
        "push_probability",
    )
    payload: dict[str, Any] = {
        "prediction_id": _prediction_id(str(snapshot["source_snapshot_id"]), direction),
        "event_id": snapshot["event_id"],
        "event_start_time": snapshot["event_start_time"],
        "model_timestamp": model_timestamp.isoformat(),
        "player": snapshot.get("player"),
        "sport": SPORT,
        "market_type": "PLAYER_PROP",
        "stat_type": STAT_TYPE,
        "line": float(snapshot["line"]),
        "direction": direction.upper(),
        "probability_publishable": bool(scored.get("probability_publishable") is True),
        "money_lane_status": "PAYOUT_UNRESOLVED",
        "source_snapshot_id": snapshot["source_snapshot_id"],
        "locked_at": now.isoformat(),
        "blockers": list(scored.get("blockers") or []),
    }
    for key in allowed_optional:
        value = output.get(key)
        if value is not None:
            payload[key] = value
    # Persistence is a recorder, never a publisher.  A research-only return
    # must remain non-publishable even though its raw probability is useful for
    # forward calibration after settlement.
    if scored.get("research_only") is True:
        payload["probability_publishable"] = False
    return payload


def _persist_prediction(db: Any, payload: dict[str, Any]) -> None:
    # Deterministic prediction_id + ignore_duplicates makes retries idempotent.
    db.table("wow_predictions").upsert(payload, on_conflict="prediction_id", ignore_duplicates=True).execute()


def _forward_predictions(db: Any) -> list[dict[str, Any]]:
    rows = (
        db.table("wow_predictions")
        .select("prediction_id,event_start_time,model_timestamp,locked_at,source_snapshot_id,direction")
        .eq("sport", SPORT)
        .eq("stat_type", STAT_TYPE)
        .eq("model_provider_identity", PROVIDER)
        .execute()
        .data
        or []
    )
    eligible: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        start = _aware(row.get("event_start_time"))
        model_ts = _aware(row.get("model_timestamp"))
        locked = _aware(row.get("locked_at"))
        if not row.get("source_snapshot_id") or start is None or model_ts is None or locked is None:
            continue
        if model_ts < start and locked < start and str(row.get("direction") or "").upper() in DIRECTIONS:
            eligible.append(row)
    return eligible


def _settled_prediction_ids(db: Any, prediction_ids: list[str]) -> set[str]:
    if not prediction_ids:
        return set()
    rows = (
        db.table("wow_outcomes")
        .select("prediction_id,actual_stat,settlement_timestamp,void")
        .in_("prediction_id", prediction_ids)
        .execute()
        .data
        or []
    )
    return {
        str(row["prediction_id"])
        for row in rows
        if row.get("prediction_id")
        and row.get("actual_stat") is not None
        and row.get("settlement_timestamp") is not None
        and row.get("void") is not True
    }


def _capability_evidence(db: Any) -> tuple[dict[str, Any], Any]:
    result = (
        db.table("wow_runtime_capabilities")
        .select("capability_key,evidence")
        .eq("capability_key", CAPABILITY_KEY)
        .limit(1)
        .execute()
    )
    row = (result.data or [None])[0]
    if not isinstance(row, dict):
        return {}, None
    evidence = dict(row.get("evidence") or {})
    return evidence, row


def _readiness(evidence: dict[str, Any], prediction_n: int, settled_n: int) -> dict[str, Any]:
    phase_b = evidence.get("phase_b_min_settled_n")
    phase_c = evidence.get("phase_c_min_settled_n")
    try:
        phase_b_n = int(phase_b)
        phase_c_n = int(phase_c)
    except (TypeError, ValueError):
        return {
            "status": "CALIBRATION_THRESHOLDS_UNAVAILABLE",
            "forward_prediction_n": prediction_n,
            "forward_settled_n": settled_n,
            "calibrator_fit_allowed": False,
            "can_execute": False,
        }
    if phase_b_n <= 0 or phase_c_n < phase_b_n:
        return {
            "status": "CALIBRATION_THRESHOLDS_INVALID",
            "forward_prediction_n": prediction_n,
            "forward_settled_n": settled_n,
            "calibrator_fit_allowed": False,
            "can_execute": False,
        }
    if settled_n >= phase_c_n:
        status = "PHASE_C_THRESHOLD_REACHED_CALIBRATOR_FIT_REQUIRED"
    elif settled_n >= phase_b_n:
        status = "PHASE_B_THRESHOLD_REACHED_CALIBRATOR_FIT_REQUIRED"
    else:
        status = "PHASE_A_FORWARD_COHORT_BUILDING"
    return {
        "status": status,
        "forward_prediction_n": prediction_n,
        "forward_settled_n": settled_n,
        "phase_b_min_settled_n": phase_b_n,
        "phase_c_min_settled_n": phase_c_n,
        "remaining_to_phase_b": max(0, phase_b_n - settled_n),
        "remaining_to_phase_c": max(0, phase_c_n - settled_n),
        # This runtime intentionally does not contain a calibrator fitter.
        "calibrator_fit_allowed": False,
        "can_execute": False,
    }


def _reconcile_capability(db: Any) -> dict[str, Any]:
    predictions = _forward_predictions(db)
    ids = [str(row["prediction_id"]) for row in predictions]
    settled = _settled_prediction_ids(db, ids)
    evidence, capability_row = _capability_evidence(db)
    readiness = _readiness(evidence, len(predictions), len(settled))
    if capability_row is not None:
        updated = dict(evidence)
        updated["forward_prediction_n"] = len(predictions)
        updated["forward_settled_n"] = len(settled)
        updated["forward_cohort_readiness"] = readiness
        db.table("wow_runtime_capabilities").update({"evidence": updated}).eq(
            "capability_key", CAPABILITY_KEY
        ).execute()
    return readiness


def run_prop_forward_cohort(
    req: PropForwardCohortRequest,
    *,
    db: Any,
    market_api: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    snapshots = _eligible_snapshots(db, req.max_snapshots, now=now)
    existing = _existing_forward_keys(db)
    outcomes: list[dict[str, Any]] = []

    for snapshot in snapshots:
        for direction in DIRECTIONS:
            key = _snapshot_key(snapshot.get("source_snapshot_id"), direction)
            if key in existing:
                outcomes.append({
                    "source_snapshot_id": key[0], "direction": direction,
                    "status": "SKIPPED_ALREADY_CAPTURED", "can_execute": False,
                })
                continue
            identity = {
                "event_id": snapshot.get("event_id"),
                "event_start_time": snapshot.get("event_start_time"),
                "sport": SPORT,
                "player": snapshot.get("player"),
                "stat_type": STAT_TYPE,
                "line": snapshot.get("line"),
                "source_snapshot_id": snapshot.get("source_snapshot_id"),
                "direction": direction,
            }
            try:
                scored = market_api.score_prop(
                    market_api.ScorePropRequest(**identity),
                    "WOW_BETTING_ENGINE",
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                outcomes.append({
                    "source_snapshot_id": key[0], "direction": direction,
                    "status": "HELD_SCORER", "detail": detail,
                    "probability_publishable": False, "can_execute": False,
                })
                continue
            except Exception as exc:
                outcomes.append({
                    "source_snapshot_id": key[0], "direction": direction,
                    "status": "HELD_SCORER_EXCEPTION", "error_type": type(exc).__name__,
                    "probability_publishable": False, "can_execute": False,
                })
                continue

            payload = _prediction_payload(snapshot, direction, scored, now=now)
            if payload is None:
                outcomes.append({
                    "source_snapshot_id": key[0], "direction": direction,
                    "status": "HELD_FORWARD_PACKAGE_INVALID",
                    "probability_publishable": False, "can_execute": False,
                })
                continue
            _persist_prediction(db, payload)
            existing.add(key)
            outcomes.append({
                "source_snapshot_id": key[0], "direction": direction,
                "prediction_id": payload["prediction_id"],
                "status": "CAPTURED_FORWARD",
                "probability_publishable": payload["probability_publishable"],
                "research_only": bool(scored.get("research_only") is True),
                "can_execute": False,
            })

    readiness = _reconcile_capability(db)
    captured = sum(1 for row in outcomes if row["status"] == "CAPTURED_FORWARD")
    return {
        "terminal": True,
        "run_status": "COMPLETED",
        "snapshots_considered": len(snapshots),
        "directions_considered": len(snapshots) * len(DIRECTIONS),
        "captured_forward_predictions": captured,
        "rows": outcomes,
        "calibration_readiness": readiness,
        "calibrator_fit_performed": False,
        "can_execute": False,
    }


def install_prop_forward_cohort_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
    market_api: Any,
) -> None:
    if any(getattr(route, "path", None) == "/v17/prop-forward-cohort-run" for route in app.router.routes):
        return

    @app.post(
        "/v17/prop-forward-cohort-run",
        dependencies=[auth_dependency],
        operation_id="runWowV17PropForwardCohort",
    )
    def prop_forward_cohort_run(req: PropForwardCohortRequest):
        return run_prop_forward_cohort(req, db=db_client_fn(), market_api=market_api)
