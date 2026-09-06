"""Optional live self-acceptance for the real fitted player-prop path.

The probe supports governed publication, the legacy calibration/publication-
blocked research-only lane, and the V17 full sporting-probability package that
can survive a downstream official-publication hold. A publication lock must not
be misreported as model unavailability when the certified specialist can run.
No acceptance mode authorizes execution.

When WOW_PROP_MODEL_SELF_ACCEPTANCE_PICK_JSON is configured, the probe first
uses the real authenticated /score-pick-request boundary. That path performs
certified route preflight, official automatic evidence hydration, immutable
snapshot persistence when permitted, and the exact /score-prop call.
"""
from __future__ import annotations

import asyncio
import json
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
    """Preserve the pre-V17-hold governed-publication acceptance contract."""
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


def _is_sporting_probability_hold_pass(body: dict[str, Any]) -> bool:
    """Recognize a completed fitted probability while final publication is held.

    Unlike the normal governed-publication path, this contract deliberately does
    not require a persisted prediction_id: the downstream publication lock can
    legitimately prevent the official prediction-ledger write while preserving
    the completed calibrated sporting probability for assistance/review.
    """
    model = body.get("model_evidence") if isinstance(body.get("model_evidence"), dict) else {}
    lanes = body.get("objective_lanes") if isinstance(body.get("objective_lanes"), dict) else {}
    model_lane = lanes.get("MODEL") if isinstance(lanes.get("MODEL"), dict) else {}
    return (
        body.get("ok") is True
        and body.get("governed_sporting_probability_completed") is True
        and body.get("sporting_probability_publishable") is True
        and body.get("probability_publishable") is True
        and body.get("governed_publishable") is False
        and body.get("official_final_publishable") is False
        and body.get("final_approved") is False
        and isinstance(body.get("official_publication_blockers"), list)
        and model.get("provider_identity") == "WOW_PROP_FITTED_MODEL_V1"
        and model.get("model_family") == "MLB_PITCHER_SO_FAILURE_PATH_NB_V1"
        and model.get("calibration_status") == "PRECALIBRATION_SHRINKAGE"
        and _valid_probability(model.get("calibrated_probability"))
        and _valid_probability(model.get("calibrated_probability_lower_bound"))
        and model.get("probability_publishable") is True
        and model.get("can_execute") is False
        and model_lane.get("status") == "PASS"
        and model_lane.get("probability_publishable") is True
        and model_lane.get("can_execute") is False
        and body.get("can_execute") is False
    )


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
    if _is_sporting_probability_hold_pass(body):
        return True, code, None, "SPORTING_PROBABILITY_COMPLETE_PUBLICATION_HOLD"
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


def _bootstrap_pick_payload(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("bootstrap JSON must be an object")
    required = ("event_id", "event_start_time", "sport", "player", "stat_type", "line")
    missing = [key for key in required if value.get(key) in (None, "")]
    if missing:
        raise ValueError("bootstrap JSON missing required fields: " + ",".join(missing))
    event_start = _aware(value["event_start_time"])
    if event_start <= datetime.now(timezone.utc):
        raise ValueError("bootstrap event already started")
    row = {
        "row_key": "prop-live-e2e-acceptance",
        "event_id": str(value["event_id"]),
        "event_start_time": event_start.isoformat(),
        "sport": str(value["sport"]).upper(),
        "player": str(value["player"]),
        "stat_type": str(value["stat_type"]).upper(),
        "line": float(value["line"]),
        "direction": str(value.get("direction") or "MORE").upper(),
        "source_type": "AUTONOMOUS_DISCOVERY",
        "platform": "WOW_PRODUCTION_SELF_ACCEPTANCE",
        "league": str(value.get("league") or value["sport"]).upper(),
        "opponent": value.get("opponent"),
        "money_lane_status": "PAYOUT_UNRESOLVED",
    }
    return {
        "request_id": "wow-prop-live-e2e-acceptance",
        "rows": [row],
    }


def _is_bootstrap_pick_pass(response: httpx.Response) -> tuple[bool, str, str | None, str]:
    body = _result_body(response)
    if body is None:
        return False, "INVALID_BODY", None, "INVALID"
    rows = body.get("rows") if isinstance(body.get("rows"), list) else []
    row = rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else {}
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    acquisition = row.get("acquisition") if isinstance(row.get("acquisition"), dict) else {}
    code = str(row.get("code") or body.get("code") or "MISSING_CODE")
    snapshot_id = row.get("source_snapshot_id")
    if response.status_code != 200:
        return False, code, str(snapshot_id) if snapshot_id else None, "HTTP_FAIL"
    common = (
        body.get("rows_in") == 1
        and body.get("rows_completed") == 1
        and body.get("rows_held") == 0
        and body.get("rows_rejected") == 0
        and body.get("reconciliation_pass") is True
        and body.get("can_execute") is False
        and row.get("terminal_status") == "COMPLETED"
        and acquisition.get("mode") == "AUTO_HYDRATION"
        and acquisition.get("status") == "PASS"
        and acquisition.get("snapshot_status") == "FROZEN"
        and bool(snapshot_id)
        and row.get("can_execute") is False
    )
    if not common:
        return False, code, str(snapshot_id) if snapshot_id else None, "CONTRACT_FAIL"
    if row.get("code") == "MODEL_QUALIFIED_HOLD" and _is_sporting_probability_hold_pass(result):
        return True, code, str(snapshot_id), "SPORTING_PROBABILITY_COMPLETE_PUBLICATION_HOLD"
    if row.get("code") == "MODEL_QUALIFIED_HOLD" and _is_research_only_model_path_pass(result):
        return True, code, str(snapshot_id), "RESEARCH_ONLY_PUBLICATION_LOCK"
    governed_ok, prediction_id = _is_governed_model_path_pass(result)
    if row.get("code") == "MODEL_QUALIFIED" and governed_ok:
        return True, code, str(snapshot_id), "GOVERNED_PUBLISHABLE"
    return False, code, str(snapshot_id) if snapshot_id else prediction_id, "MODEL_RESULT_FAIL"


async def _bootstrap_fresh_snapshot(key: str, port: str, raw: str, log: logging.Logger) -> str | None:
    try:
        payload = _bootstrap_pick_payload(raw)
    except Exception as exc:
        log.error(
            "WOW_PROP_MODEL_SELF_ACCEPTANCE result=FAIL mode=AUTO_HYDRATED_E2E reason=BOOTSTRAP_CONFIG_INVALID error_type=%s can_execute=false",
            type(exc).__name__,
        )
        return None

    url = f"http://127.0.0.1:{port}/score-pick-request"
    last_status: int | None = None
    last_code = "NO_RESPONSE"
    last_mode = "NO_RESPONSE"
    last_snapshot: str | None = None
    for attempt in range(1, 4):
        await asyncio.sleep(2.0 if attempt == 1 else 1.0)
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "X-WOW-Model-Identity": "WOW_BETTING_ENGINE",
                    },
                    json=payload,
                )
            last_status = response.status_code
            passed, last_code, last_snapshot, last_mode = _is_bootstrap_pick_pass(response)
            if passed:
                log.warning(
                    "WOW_PROP_MODEL_SELF_ACCEPTANCE result=PASS status=200 mode=AUTO_HYDRATED_E2E_%s snapshot_id=%s acquisition=PASS snapshot=FROZEN specialist_invoked=true governed_publishable=%s terminal=%s can_execute=false",
                    last_mode,
                    last_snapshot,
                    str(last_mode == "GOVERNED_PUBLISHABLE").lower(),
                    "MODEL_QUALIFIED" if last_mode == "GOVERNED_PUBLISHABLE" else "MODEL_QUALIFIED_HOLD",
                )
                return last_snapshot
        except Exception as exc:
            last_code = type(exc).__name__
            last_mode = "EXCEPTION"
    log.error(
        "WOW_PROP_MODEL_SELF_ACCEPTANCE result=FAIL status=%s code=%s mode=AUTO_HYDRATED_E2E_%s snapshot_id=%s probability_publishable=false can_execute=false",
        last_status,
        last_code,
        last_mode,
        last_snapshot,
    )
    return None


async def run_prop_model_live_self_acceptance(market_api: Any, logger: logging.Logger | None = None) -> None:
    """Exercise one real pregame snapshot through the deployed prop path."""
    log = logger or logging.getLogger("wow.prop.model_acceptance")
    snapshot_id = str(os.getenv("WOW_PROP_MODEL_SELF_ACCEPTANCE_SNAPSHOT_ID") or "").strip()
    bootstrap_raw = str(os.getenv("WOW_PROP_MODEL_SELF_ACCEPTANCE_PICK_JSON") or "").strip()
    if not snapshot_id and not bootstrap_raw:
        return
    key = os.getenv("WOW_ACTION_API_KEY")
    port = os.getenv("PORT")
    if not key or not port:
        log.error("WOW_PROP_MODEL_SELF_ACCEPTANCE result=FAIL reason=RUNTIME_AUTH_OR_PORT_MISSING can_execute=false")
        return

    if bootstrap_raw and (not snapshot_id or snapshot_id.upper() == "AUTO"):
        await _bootstrap_fresh_snapshot(key, port, bootstrap_raw, log)
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
                    elif last_mode == "SPORTING_PROBABILITY_COMPLETE_PUBLICATION_HOLD":
                        log.warning(
                            "WOW_PROP_MODEL_SELF_ACCEPTANCE result=PASS status=200 mode=SPORTING_PROBABILITY_COMPLETE_PUBLICATION_HOLD snapshot_id=%s provider=WOW_PROP_FITTED_MODEL_V1 model_family=MLB_PITCHER_SO_FAILURE_PATH_NB_V1 calibration=PRECALIBRATION_SHRINKAGE sporting_probability_publishable=true governed_publishable=false final_approved=false can_execute=false",
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
