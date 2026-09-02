"""Production wrapper for the WOW governed MLB event path.

The wrapper supports two explicitly governed response modes from the Supabase
bridge:

* HELD: fitted-model evidence is proven but numeric probabilities are withheld.
* PUBLISHABLE: numeric MLB event probabilities are allowed only after the live
  deployment/calibration/runtime/ratification state says publication is allowed.

Neither mode can execute a wager. ``can_execute`` is always false.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException

import api as base_api
from wolfram_arithmetic_auditor import readiness as wolfram_arithmetic_readiness

ScoreEventRequest = base_api.ScoreEventRequest
_require_action_api_key = base_api._require_action_api_key
_score_event_contract_errors = base_api._score_event_contract_errors
get_client = base_api.get_client

_logger = logging.getLogger("wow.g11.acceptance")
_background_tasks: set[asyncio.Task] = set()

# Build a production wrapper without mutating base_api.app. Replace the legacy
# /score-event and /governance handlers while retaining every other base route.
app = FastAPI(
    title=base_api.app.title,
    version=base_api.app.version,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.router.routes.extend(
    route
    for route in base_api.app.router.routes
    if not (
        (
            getattr(route, "path", None) == "/score-event"
            and "POST" in (getattr(route, "methods", set()) or set())
        )
        or (
            getattr(route, "path", None) == "/governance"
            and "GET" in (getattr(route, "methods", set()) or set())
        )
    )
)
app.exception_handlers.update(base_api.app.exception_handlers)

_NUMERIC_PROBABILITY_FIELDS = {
    "raw_home_probability",
    "raw_away_probability",
    "independent_home_probability",
    "independent_away_probability",
    "calibrated_home_probability",
    "calibrated_away_probability",
    "calibrated_home_lower_bound",
    "calibrated_home_upper_bound",
    "calibrated_away_lower_bound",
    "calibrated_away_upper_bound",
    "projected_runs_home",
    "projected_runs_away",
    "tie_after_9_probability",
}

_PUBLISHED_REQUIRED_NUMERIC_FIELDS = {
    "raw_home_probability",
    "raw_away_probability",
    "calibrated_home_probability",
    "calibrated_away_probability",
    "calibrated_home_lower_bound",
    "calibrated_home_upper_bound",
    "calibrated_away_lower_bound",
    "calibrated_away_upper_bound",
    "projected_runs_home",
    "projected_runs_away",
    "tie_after_9_probability",
}


def _bridge_rpc(req: ScoreEventRequest) -> dict[str, Any]:
    """Call the server-owned fitted-model bridge. Missing evidence fails closed."""
    try:
        result = get_client().rpc(
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
        raise HTTPException(
            status_code=503,
            detail={
                "code": "EVENT_MODEL_BRIDGE_UNAVAILABLE",
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc

    payload = result.data
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "EVENT_MODEL_BRIDGE_INVALID_RESPONSE",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    return payload


def _validate_held_bridge_payload(payload: dict[str, Any]) -> None:
    """Prevent DB/API drift from leaking unpublished numeric probabilities."""
    leaked = sorted(_NUMERIC_PROBABILITY_FIELDS.intersection(payload))
    if leaked:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "HELD_PROBABILITY_LEAK_BLOCKED",
                "fields": leaked,
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    required = {
        "scoring_evidence_produced": True,
        "probability_fields_withheld": True,
        "probability_publishable": False,
        "can_execute": False,
    }
    bad = [key for key, expected in required.items() if payload.get(key) is not expected]
    if bad:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "EVENT_MODEL_BRIDGE_GOVERNANCE_VIOLATION",
                "fields": bad,
                "probability_publishable": False,
                "can_execute": False,
            },
        )


def _finite_number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(key)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(key)
    return value


def _validate_published_bridge_payload(payload: dict[str, Any]) -> None:
    """Validate a ratified numeric response before exposing it through HTTP."""
    required_flags = {
        "scoring_evidence_produced": True,
        "probability_fields_withheld": False,
        "probability_publishable": True,
        "can_execute": False,
    }
    bad_flags = [key for key, expected in required_flags.items() if payload.get(key) is not expected]

    missing = sorted(_PUBLISHED_REQUIRED_NUMERIC_FIELDS.difference(payload))
    invalid: list[str] = []
    values: dict[str, float] = {}
    if not missing:
        for key in sorted(_PUBLISHED_REQUIRED_NUMERIC_FIELDS):
            try:
                values[key] = _finite_number(payload, key)
            except ValueError:
                invalid.append(key)

    if not missing and not invalid:
        probability_keys = [
            "raw_home_probability",
            "raw_away_probability",
            "calibrated_home_probability",
            "calibrated_away_probability",
            "calibrated_home_lower_bound",
            "calibrated_home_upper_bound",
            "calibrated_away_lower_bound",
            "calibrated_away_upper_bound",
            "tie_after_9_probability",
        ]
        invalid.extend(key for key in probability_keys if not (0.0 < values[key] < 1.0))
        if abs(values["raw_home_probability"] + values["raw_away_probability"] - 1.0) > 1e-6:
            invalid.append("raw_probability_sum")
        if abs(values["calibrated_home_probability"] + values["calibrated_away_probability"] - 1.0) > 1e-6:
            invalid.append("calibrated_probability_sum")
        if not (
            values["calibrated_home_lower_bound"]
            <= values["calibrated_home_probability"]
            <= values["calibrated_home_upper_bound"]
        ):
            invalid.append("calibrated_home_bounds")
        if not (
            values["calibrated_away_lower_bound"]
            <= values["calibrated_away_probability"]
            <= values["calibrated_away_upper_bound"]
        ):
            invalid.append("calibrated_away_bounds")
        if values["projected_runs_home"] <= 0 or values["projected_runs_away"] <= 0:
            invalid.append("projected_runs")

    if bad_flags or missing or invalid:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "PUBLISHED_PROBABILITY_VALIDATION_FAILED",
                "flag_errors": bad_flags,
                "missing_fields": missing,
                "invalid_fields": sorted(set(invalid)),
                "probability_publishable": False,
                "can_execute": False,
            },
        )


def _validate_bridge_payload(payload: dict[str, Any]) -> None:
    code = payload.get("code")
    if code == "REAL_FITTED_MODEL_PATH_PROVEN":
        _validate_held_bridge_payload(payload)
    elif code == "GOVERNED_PROBABILITY_PUBLISHED":
        _validate_published_bridge_payload(payload)


@app.get("/governance")
def governance():
    """Expose the live multi-latch publication state without optimistic defaults."""
    try:
        gate_state = get_client().rpc("wow_governed_deployment_state", {}).execute().data
    except Exception:
        gate_state = None

    calibration_health = base_api._query_calibration_health()
    if not isinstance(gate_state, dict):
        return {
            "governed_probability_capability": "UNAVAILABLE",
            "governed_probability_status": "NOT_PRODUCED",
            "deployment_contract_status": "UNAVAILABLE",
            "calibration_health_status": "UNAVAILABLE",
            "runtime_capability_status": "UNAVAILABLE",
            "ratification_status": "NOT_RATIFIED",
            "production_feature_ready": False,
            "probability_publishable": False,
            "can_execute": False,
            "deployment_gates": "GATE_LEDGER_UNREACHABLE",
            "calibration_health": calibration_health,
            "arithmetic_audit": wolfram_arithmetic_readiness(),
        }

    return {
        "governed_probability_capability": gate_state.get("governed_probability_capability", "UNAVAILABLE"),
        "governed_probability_status": gate_state.get("governed_probability_status", "NOT_PRODUCED"),
        "deployment_contract_status": gate_state.get("deployment_contract_status", "FAIL"),
        "calibration_health_status": gate_state.get("calibration_health_status", "UNAVAILABLE"),
        "calibration_health_assessed_at": gate_state.get("calibration_health_assessed_at"),
        "runtime_capability_status": gate_state.get("runtime_capability_status", "UNAVAILABLE"),
        "runtime_capability_updated_at": gate_state.get("runtime_capability_updated_at"),
        "ratification_status": gate_state.get("ratification_status", "NOT_RATIFIED"),
        "ratification_id": gate_state.get("ratification_id"),
        "ratification_created_at": gate_state.get("ratification_created_at"),
        "production_feature_ready": bool(gate_state.get("production_feature_ready", False)),
        "probability_publishable": bool(gate_state.get("probability_publishable", False)),
        "can_execute": False,
        "deployment_gates": gate_state.get("deployment_gates", []),
        "calibration_health": calibration_health,
        "arithmetic_audit": wolfram_arithmetic_readiness(),
    }


@app.post(
    "/score-event",
    dependencies=[Depends(_require_action_api_key)],
    operation_id="scoreWowEvent",
)
def score_event(req: ScoreEventRequest):
    """Return held metadata or a separately ratified governed probability."""
    errors = _score_event_contract_errors(req)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EVENT_CONTRACT_INVALID",
                "probability_publishable": False,
                "errors": errors,
                "can_execute": False,
            },
        )

    payload = _bridge_rpc(req)
    code = payload.get("code")
    if code not in {"REAL_FITTED_MODEL_PATH_PROVEN", "GOVERNED_PROBABILITY_PUBLISHED"}:
        raise HTTPException(status_code=409, detail=payload)

    _validate_bridge_payload(payload)
    return {"ok": True, **payload}


def _latest_pregame_acceptance_event() -> dict[str, Any] | None:
    """Select a latest real hydrated pregame shadow event for live acceptance."""
    now = datetime.now(timezone.utc).isoformat()
    result = (
        get_client()
        .table("wow_mlb_forward_shadow_events")
        .select(
            "official_event_id,official_date,event_start_time,home_team,away_team,"
            "venue_name,home_probable_pitcher,away_probable_pitcher,snapshot_id,snapshot_timestamp"
        )
        .eq("feature_hydration_status", "PASS")
        .gt("event_start_time", now)
        .order("snapshot_timestamp", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def _acceptance_request(row: dict[str, Any]) -> dict[str, Any]:
    event_id = str(row["official_event_id"])
    return {
        "research_run_id": f"g11-live-self-acceptance-{event_id}",
        "requested_slate_date": str(row["official_date"]),
        "requested_timezone": "America/Chicago",
        "scan_stage": "PREGAME",
        "event_key": f"MLB:{event_id}",
        "official_event_id": event_id,
        "event_start_time_utc": row["event_start_time"],
        "sport": "MLB",
        "league": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "settlement_basis": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "venue": row["venue_name"],
        "home_starting_pitcher": row["home_probable_pitcher"],
        "away_starting_pitcher": row["away_probable_pitcher"],
        "home_starter_status": "PROBABLE",
        "away_starter_status": "PROBABLE",
        "home_lineup_status": "PROJECTED",
        "away_lineup_status": "PROJECTED",
        "source_snapshot_id": row["snapshot_id"],
    }


async def _run_g11_live_self_acceptance() -> None:
    """Exercise the live auth + fitted-model path without logging probabilities."""
    key = os.getenv("WOW_ACTION_API_KEY")
    port = os.getenv("PORT")
    if not key or not port:
        _logger.error("WOW_G11_SELF_ACCEPTANCE result=FAIL reason=RUNTIME_AUTH_OR_PORT_MISSING")
        return

    try:
        row = _latest_pregame_acceptance_event()
    except Exception as exc:
        _logger.error(
            "WOW_G11_SELF_ACCEPTANCE result=FAIL reason=EVENT_LOOKUP_FAILED error_type=%s",
            type(exc).__name__,
        )
        return
    if not row:
        _logger.error("WOW_G11_SELF_ACCEPTANCE result=FAIL reason=NO_PREGAME_EVENT")
        return

    payload = _acceptance_request(row)
    url = f"http://127.0.0.1:{port}/score-event"
    last_status: int | None = None
    last_code = "NO_RESPONSE"

    for attempt in range(1, 6):
        await asyncio.sleep(2.0 if attempt == 1 else 1.0)
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    json=payload,
                )
            last_status = response.status_code
            body = response.json()
            detail = body.get("detail") if isinstance(body, dict) else None
            result_body = detail if isinstance(detail, dict) else body
            last_code = str(result_body.get("code", "MISSING_CODE")) if isinstance(result_body, dict) else "INVALID_BODY"

            if response.status_code == 200 and isinstance(result_body, dict):
                try:
                    _validate_bridge_payload(result_body)
                except HTTPException:
                    pass
                else:
                    if last_code in {"REAL_FITTED_MODEL_PATH_PROVEN", "GOVERNED_PROBABILITY_PUBLISHED"}:
                        mode = "PUBLISHED" if last_code == "GOVERNED_PROBABILITY_PUBLISHED" else "HELD"
                        numeric_n = len(_NUMERIC_PROBABILITY_FIELDS.intersection(result_body))
                        _logger.warning(
                            "WOW_G11_SELF_ACCEPTANCE result=PASS status=200 code=%s mode=%s event_id=%s "
                            "scoring_evidence_produced=true can_execute=false numeric_probability_fields=%s",
                            last_code,
                            mode,
                            payload["official_event_id"],
                            numeric_n,
                        )
                        return
        except Exception as exc:
            last_code = type(exc).__name__

    _logger.error(
        "WOW_G11_SELF_ACCEPTANCE result=FAIL status=%s code=%s event_id=%s",
        last_status,
        last_code,
        payload["official_event_id"],
    )


@app.on_event("startup")
async def _schedule_g11_live_self_acceptance() -> None:
    if os.getenv("WOW_G11_SELF_ACCEPTANCE", "0") != "1":
        return
    task = asyncio.create_task(_run_g11_live_self_acceptance())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
