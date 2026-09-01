from __future__ import annotations

import math
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

import api as base_api
from mlb_event_specialist_v16 import ProspectiveModelUnavailable, score_prospective_event

ScoreEventRequest = base_api.ScoreEventRequest


def _bridge_rpc(req: ScoreEventRequest) -> dict[str, Any]:
    try:
        result = base_api.get_client().rpc(
            "wow_mlb_score_event_bridge",
            {
                "p_official_event_id": req.official_event_id,
                "p_event_start_time": req.event_start_time_utc,
                "p_requested_slate_date": req.requested_slate_date,
                "p_home_team": req.home_team,
                "p_away_team": req.away_team,
                "p_venue": req.venue,
                "p_home_starting_pitcher": req.home_starting_pitcher,
                "p_away_starting_pitcher": req.away_starting_pitcher,
                "p_source_snapshot_id": req.source_snapshot_id,
            },
        ).execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "EVENT_MODEL_BRIDGE_UNAVAILABLE", "model_probability_publishable": False, "probability_publishable": False, "can_execute": False}) from exc
    payload = result.data
    if not isinstance(payload, dict):
        raise HTTPException(status_code=503, detail={"code": "EVENT_MODEL_BRIDGE_INVALID_RESPONSE", "model_probability_publishable": False, "probability_publishable": False, "can_execute": False})
    return payload


def _validate_full_published(payload: dict[str, Any]) -> None:
    required = ["raw_home_probability", "raw_away_probability", "calibrated_home_probability", "calibrated_away_probability", "calibrated_home_lower_bound", "calibrated_home_upper_bound", "calibrated_away_lower_bound", "calibrated_away_upper_bound"]
    try:
        vals = {k: float(payload[k]) for k in required}
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "PUBLISHED_PROBABILITY_VALIDATION_FAILED", "probability_publishable": False, "can_execute": False}) from exc
    if any(not math.isfinite(v) or not 0 < v < 1 for v in vals.values()):
        raise HTTPException(status_code=500, detail={"code": "PUBLISHED_PROBABILITY_VALIDATION_FAILED", "probability_publishable": False, "can_execute": False})
    if abs(vals["raw_home_probability"] + vals["raw_away_probability"] - 1) > 1e-6 or abs(vals["calibrated_home_probability"] + vals["calibrated_away_probability"] - 1) > 1e-6:
        raise HTTPException(status_code=500, detail={"code": "PUBLISHED_PROBABILITY_VALIDATION_FAILED", "probability_publishable": False, "can_execute": False})


def _prospective_state() -> dict[str, Any]:
    try:
        data = base_api.get_client().rpc("wow_mlb_prospective_model_state", {}).execute().data
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def install_mlb_prospective_event_routes(app: FastAPI) -> None:
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (
            getattr(route, "path", None) in {"/score-event", "/governance"}
            and ({"POST"} if getattr(route, "path", None) == "/score-event" else {"GET"}) & (getattr(route, "methods", set()) or set())
        )
    ]

    @app.get("/governance", operation_id="getWowGovernance")
    def governance():
        try:
            deployment = base_api.get_client().rpc("wow_governed_deployment_state", {}).execute().data
        except Exception:
            deployment = {}
        prospective = _prospective_state()
        model_available = prospective.get("model_probability_capability") == "AVAILABLE" and prospective.get("calibration_health_status") == "PASS"
        return {
            "governed_probability_capability": "AVAILABLE" if model_available else "UNAVAILABLE",
            "governed_probability_status": "PROSPECTIVE_MODEL_ONLY" if model_available else "NOT_PRODUCED",
            "model_probability_capability": prospective.get("model_probability_capability", "UNAVAILABLE"),
            "model_probability_publishable": bool(prospective.get("model_probability_publishable", False)),
            "specialist_lifecycle_state": prospective.get("lifecycle_state"),
            "specialist_certification_id": prospective.get("certification_id"),
            "calibration_health_status": prospective.get("calibration_health_status", "UNAVAILABLE"),
            "graded_forward_shadow_n": prospective.get("graded_forward_shadow_n", 0),
            "pending_forward_shadow_n": prospective.get("pending_forward_shadow_n", 0),
            "terminal_ceiling": prospective.get("terminal_ceiling"),
            "deployment_contract_status": (deployment or {}).get("deployment_contract_status", "UNAVAILABLE"),
            "deployment_gates": (deployment or {}).get("deployment_gates", []),
            "production_feature_ready": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    @app.post("/score-event", dependencies=[Depends(base_api._require_action_api_key)], operation_id="scoreWowEvent")
    def score_event(req: ScoreEventRequest):
        errors = base_api._score_event_contract_errors(req)
        if errors:
            raise HTTPException(status_code=422, detail={"code": "EVENT_CONTRACT_INVALID", "errors": errors, "model_probability_publishable": False, "probability_publishable": False, "can_execute": False})
        payload = _bridge_rpc(req)
        code = payload.get("code")
        if code == "GOVERNED_PROBABILITY_PUBLISHED":
            _validate_full_published(payload)
            return {"ok": True, **payload}
        if code != "REAL_FITTED_MODEL_PATH_PROVEN":
            raise HTTPException(status_code=409, detail=payload)
        try:
            result = score_prospective_event(req, payload, base_api.get_client())
        except ProspectiveModelUnavailable as exc:
            raise HTTPException(status_code=409, detail={"code": "MODEL_UNAVAILABLE", "controlling_specialist": "wow.mlb-game-win-probability-expert", "reason": str(exc), "model_probability_publishable": False, "probability_publishable": False, "can_execute": False}) from exc
        return {"ok": True, **result}
