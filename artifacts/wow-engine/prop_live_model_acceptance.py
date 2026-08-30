"""Optional live self-acceptance for the real fitted player-prop path.

The probe supports both governed publication and the calibration/publication-
blocked research-only lane. A publication lock must not be misreported as model
unavailability when the certified specialist can still run. In research-only
mode the probe requires raw specialist output, null calibrated claims, no
publishable prediction claim, MODEL_QUALIFIED_HOLD-or-lower ceiling, and
can_execute=false.
"""
from __future__ import annotations

import asyncio
import logging
import math
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


def _valid_probability(value: Any) -> bool:
    try:
        p = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(p) and 0.0 < p < 1.0


def _is_governed_model_path_pass(body: dict[str, Any]) -> tuple[bool, str | None]:
    prediction = body.get("prediction") if isinstance(body.get("prediction"), dict) else {}
    model = body.get("model_evidence") if isinstance(body.get("model_evidence"), dict) else {}
    lanes = body.get("objective_lanes") if isinstance(body.get("objective_lanes"), dict) else {}
    model_lane = lanes.get("MODEL") if isinstance(lanes.get("MODEL"), dict) else {}
    prediction_id = prediction.get("prediction_id")
    ok = (
        body.get("ok") is True
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
    return ok, str(prediction_id) if prediction_id else None


def _is_research_only_model_path_pass(body: dict[str, Any]) -> bool:
    research = body.get("research_model_output") if isinstance(body.get("research_model_output"), dict) else {}
    lanes = body.get("objective_lanes") if isinstance(body.get("objective_lanes"), dict) else {}
    model_lane = lanes.get("MODEL") if isinstance(lanes.get("MODEL"), dict) else {}
    calibration_lane = lanes.get("CALIBRATION") if isinstance(lanes.get("CALIBRATION"), dict) else {}
    publication_lane = lanes.get("PUBLICATION") if isinstance(lanes.get("PUBLICATION"), dict) else {}
    traversal = body.get("backend_traversal") if isinstance(body.get("backend_traversal"), dict) else {}
    terminal = str(body.get("terminal_ceiling") or "")
    return (
        body.get("ok") is True
        and body.get("research_only") is True
        and body.get("specialist_model_capability") == "AVAILABLE"
        and body.get("specialist_model_status") == "COMPLETED"
        and _valid_probability(research.get("raw_specialist_probability"))
        and _valid_probability(research.get("raw_probability_more"))
        and _valid_probability(research.get("raw_probability_less"))
        and research.get("provider_identity") == "WOW_PROP_FITTED_MODEL_V1"
        and research.get("model_family") == "MLB_PITCHER_SO_FAILURE_PATH_NB_V1"
        and research.get("calibration_status") == "UNKNOWN_OR_BLOCKED"
        and research.get("calibrated_probability") is None
        and research.get("calibrated_probability_lower_bound") is None
        and research.get("calibrated_probability_upper_bound") is None
        and body.get("probability_publishable") is False
        and body.get("governed_publishable") is False
        and terminal in {"MODEL_QUALIFIED_HOLD", "RESEARCH_INTEREST"}
        and model_lane.get("status") == "PASS_RESEARCH_ONLY"
        and model_lane.get("specialist_invoked") is True
        and calibration_lane.get("status") == "HOLD"
        and publication_lane.get("status") == "BLOCKED"
        and traversal.get("raw_specialist_model") == "PASS"
        and traversal.get("prediction_ledger_write") == "NOT_ATTEMPTED_PUBLICATION_LOCK"
        and body.get("can_execute") is False
    )


def _is_model_path_pass(response: httpx.Response) -> tuple[bool, str, str | None, str]:
    body = _result_body(response)
    if body is None:
        return False, "INVALID_BODY", None, "INVALID"
    code = str(body.get("code") or ("PROP_MODEL_PATH_PASS" if body.get("ok") is True else "MISSING_CODE"))
    if response.status_code != 200:
        return False, code, None, "FAIL"
    governed_ok, prediction_id = _is_governed_model_path_pass(body)
    if governed_ok:
        return True, code, prediction_id, "GOVERNED_PUBLISHABLE"
    if _is_research_only_model_path_pass(body):
        return True, code, None, "RESEARCH_ONLY_PUBLICATION_LOCK"
    return False, code, prediction_id, "UNRECOGNIZED_200"


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
    """Exercise one real pregame snapshot through the deployed /score-prop route."""
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

        preflight_result = db.rpc("wow_governed_probability_preflight", {}).execute()
        preflight = preflight_result.data if isinstance(preflight_result.data, dict) else {}
        publication_blocked = not bool(preflight.get("governed_publishable") or preflight.get("probability_publishable"))

        if not publication_blocked:
            existing = (
                db.table("wow_predictions")
                .select("prediction_id,source_snapshot_id,model_provider_identity,model_family,calibration_status,probability_publishable")
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
        last_mode = "NO_RESPONSE"
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
                passed, last_code, prediction_id, last_mode = _is_model_path_pass(response)
                if passed:
                    if last_mode == "RESEARCH_ONLY_PUBLICATION_LOCK":
                        log.warning(
                            "WOW_PROP_MODEL_SELF_ACCEPTANCE result=PASS status=200 mode=RESEARCH_ONLY_PUBLICATION_LOCK snapshot_id=%s provider=WOW_PROP_FITTED_MODEL_V1 model_family=MLB_PITCHER_SO_FAILURE_PATH_NB_V1 specialist_invoked=true calibration=UNKNOWN_OR_BLOCKED governed_publishable=false immutable_prediction=NOT_ATTEMPTED_PUBLICATION_LOCK terminal_ceiling=MODEL_QUALIFIED_HOLD can_execute=false",
                            snapshot_id,
                        )
                    else:
                        log.warning(
                            "WOW_PROP_MODEL_SELF_ACCEPTANCE result=PASS status=200 mode=GOVERNED_PUBLISHABLE snapshot_id=%s prediction_id=%s provider=WOW_PROP_FITTED_MODEL_V1 model_family=MLB_PITCHER_SO_FAILURE_PATH_NB_V1 calibration=PRECALIBRATION_SHRINKAGE immutable_prediction=PASS probability_publishable=true money_lane=HOLD can_execute=false",
                            snapshot_id,
                            prediction_id,
                        )
                    return
            except Exception as exc:
                last_code = type(exc).__name__
                last_mode = "EXCEPTION"
        log.error(
            "WOW_PROP_MODEL_SELF_ACCEPTANCE result=FAIL status=%s code=%s mode=%s snapshot_id=%s probability_publishable=false can_execute=false",
            last_status,
            last_code,
            last_mode,
            snapshot_id,
        )
    except Exception as exc:
        log.error(
            "WOW_PROP_MODEL_SELF_ACCEPTANCE result=FAIL reason=UNHANDLED error_type=%s snapshot_id=%s probability_publishable=false can_execute=false",
            type(exc).__name__,
            snapshot_id,
        )
