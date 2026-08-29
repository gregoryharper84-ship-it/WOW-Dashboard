"""Optional live self-acceptance for the real fitted player-prop path.

The existing startup probe proves only that missing evidence fails closed. This
module proves the opposite boundary when explicitly configured: a real, already-
persisted pregame evidence snapshot traverses the deployed /score-prop route,
loads the certified WOW_PROP_FITTED_MODEL_V1 artifact, runs its reviewed model
and Phase-A calibration adapters, and persists one immutable pregame prediction.

The probe is deliberately opt-in and idempotent by source_snapshot_id. It never
changes execution governance and never supplies market/money approval.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx


def _aware(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def _result_body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else body


def _is_model_path_pass(response: httpx.Response) -> tuple[bool, str, str | None]:
    body = _result_body(response)
    if body is None:
        return False, "INVALID_BODY", None
    prediction = body.get("prediction") if isinstance(body.get("prediction"), dict) else {}
    model = body.get("model_evidence") if isinstance(body.get("model_evidence"), dict) else {}
    lanes = body.get("objective_lanes") if isinstance(body.get("objective_lanes"), dict) else {}
    model_lane = lanes.get("MODEL") if isinstance(lanes.get("MODEL"), dict) else {}
    prediction_id = prediction.get("prediction_id")
    code = str(body.get("code") or ("PROP_MODEL_PATH_PASS" if body.get("ok") is True else "MISSING_CODE"))
    ok = (
        response.status_code == 200
        and body.get("ok") is True
        and bool(prediction_id)
        and model.get("provider_identity") == "WOW_PROP_FITTED_MODEL_V1"
        and model.get("model_family") == "MLB_PITCHER_SO_FAILURE_PATH_NB_V1"
        and model.get("calibration_status") == "PRECALIBRATION_SHRINKAGE"
        and model.get("probability_publishable") is True
        and model.get("can_execute") is False
        and model_lane.get("status") == "PASS"
        and model_lane.get("probability_publishable") is True
        and model_lane.get("can_execute") is False
        and body.get("can_execute") is False
    )
    return ok, code, str(prediction_id) if prediction_id else None


def _snapshot_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "event_start_time": row["event_start_time"],
        "sport": row["sport"],
        "player": row["player"],
        "stat_type": row["stat_type"],
        "line": float(row["line"]),
        "direction": "MORE",
        "source_snapshot_id": row["source_snapshot_id"],
        "money_lane_status": "PAYOUT_UNRESOLVED",
    }


async def run_prop_model_live_self_acceptance(market_api: Any, logger: logging.Logger | None = None) -> None:
    """Exercise one real pregame snapshot through the deployed route.

    Required env: WOW_PROP_MODEL_SELF_ACCEPTANCE_SNAPSHOT_ID. The runtime's
    existing WOW_ACTION_API_KEY and PORT are reused internally and are never
    logged. A previously persisted prediction for the same snapshot is treated
    as an idempotent PASS rather than creating duplicate forward-shadow rows.
    """
    log = logger or logging.getLogger("wow.prop.model_acceptance")
    snapshot_id = str(os.getenv("WOW_PROP_MODEL_SELF_ACCEPTANCE_SNAPSHOT_ID") or "").strip()
    if not snapshot_id:
        return
    key = os.getenv("WOW_ACTION_API_KEY")
    port = os.getenv("PORT")
    if not key or not port:
        log.error("WOW_PROP_MODEL_SELF_ACCEPTANCE result=FAIL reason=RUNTIME_AUTH_OR_PORT_MISSING can_execute=false")
        return

    try:
        db = market_api.prod.get_client()
        snapshot_result = (
            db.table("wow_prop_evidence_snapshots")
            .select("source_snapshot_id,event_id,event_start_time,sport,player,stat_type,line,hydration_status,blockers")
            .eq("source_snapshot_id", snapshot_id)
            .limit(1)
            .execute()
        )
        rows = snapshot_result.data or []
        if not rows:
            log.error("WOW_PROP_MODEL_SELF_ACCEPTANCE result=FAIL reason=SNAPSHOT_NOT_FOUND snapshot_id=%s can_execute=false", snapshot_id)
            return
        row = dict(rows[0])
        blockers = row.get("blockers") or []
        if row.get("hydration_status") != "PASS" or blockers:
            log.error("WOW_PROP_MODEL_SELF_ACCEPTANCE result=FAIL reason=SNAPSHOT_NOT_READY snapshot_id=%s can_execute=false", snapshot_id)
            return
        if _aware(row["event_start_time"]) <= datetime.now(timezone.utc):
            log.error("WOW_PROP_MODEL_SELF_ACCEPTANCE result=FAIL reason=EVENT_ALREADY_STARTED snapshot_id=%s can_execute=false", snapshot_id)
            return

        existing = (
            db.table("wow_predictions")
            .select("prediction_id,source_snapshot_id,model_provider_identity,model_family,calibration_status,probability_publishable,can_execute")
            .eq("source_snapshot_id", snapshot_id)
            .eq("direction", "MORE")
            .eq("model_provider_identity", "WOW_PROP_FITTED_MODEL_V1")
            .limit(1)
            .execute()
        ).data or []
        if existing:
            prior = dict(existing[0])
            prior_ok = (
                prior.get("model_provider_identity") == "WOW_PROP_FITTED_MODEL_V1"
                and prior.get("model_family") == "MLB_PITCHER_SO_FAILURE_PATH_NB_V1"
                and prior.get("calibration_status") == "PRECALIBRATION_SHRINKAGE"
                and prior.get("probability_publishable") is True
                and prior.get("can_execute") is False
            )
            log.warning(
                "WOW_PROP_MODEL_SELF_ACCEPTANCE result=%s mode=IDEMPOTENT_REUSE snapshot_id=%s prediction_id=%s provider=WOW_PROP_FITTED_MODEL_V1 model_family=MLB_PITCHER_SO_FAILURE_PATH_NB_V1 calibration=PRECALIBRATION_SHRINKAGE probability_publishable=%s can_execute=false",
                "PASS" if prior_ok else "FAIL",
                snapshot_id,
                prior.get("prediction_id"),
                str(bool(prior.get("probability_publishable"))).lower(),
            )
            return

        url = f"http://127.0.0.1:{port}/score-prop"
        payload = _snapshot_payload(row)
        last_status: int | None = None
        last_code = "NO_RESPONSE"
        for attempt in range(1, 6):
            await asyncio.sleep(2.0 if attempt == 1 else 1.0)
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {key}",
                            "X-WOW-Model-Identity": "WOW_BETTING_ENGINE",
                        },
                        json=payload,
                    )
                last_status = response.status_code
                passed, last_code, prediction_id = _is_model_path_pass(response)
                if passed:
                    log.warning(
                        "WOW_PROP_MODEL_SELF_ACCEPTANCE result=PASS status=200 snapshot_id=%s prediction_id=%s provider=WOW_PROP_FITTED_MODEL_V1 model_family=MLB_PITCHER_SO_FAILURE_PATH_NB_V1 calibration=PRECALIBRATION_SHRINKAGE immutable_prediction=PASS probability_publishable=true money_lane=HOLD can_execute=false",
                        snapshot_id,
                        prediction_id,
                    )
                    return
            except Exception as exc:
                last_code = type(exc).__name__
        log.error(
            "WOW_PROP_MODEL_SELF_ACCEPTANCE result=FAIL status=%s code=%s snapshot_id=%s probability_publishable=false can_execute=false",
            last_status,
            last_code,
            snapshot_id,
        )
    except Exception as exc:
        log.error(
            "WOW_PROP_MODEL_SELF_ACCEPTANCE result=FAIL reason=UNHANDLED error_type=%s snapshot_id=%s probability_publishable=false can_execute=false",
            type(exc).__name__,
            snapshot_id,
        )
