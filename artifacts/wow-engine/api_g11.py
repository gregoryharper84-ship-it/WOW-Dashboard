"""Production wrapper for the WOW governed backend G11 MLB event path.

Builds a separate FastAPI app from the existing api.py routes, replacing only
POST /score-event with a fail-closed bridge to the real frozen MLB fitted-model
scorer in Supabase. Importing this module never mutates base_api.app, so the
legacy API contract tests remain isolated. Held probabilities are never
returned while publication gates remain blocked. can_execute remains false.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException

import api as base_api

ScoreEventRequest = base_api.ScoreEventRequest
_require_action_api_key = base_api._require_action_api_key
_score_event_contract_errors = base_api._score_event_contract_errors
get_client = base_api.get_client

_logger = logging.getLogger("wow.g11.acceptance")
_background_tasks: set[asyncio.Task] = set()

# Build a production wrapper without mutating base_api.app. Disable new docs
# routes here and copy the existing base routes (including its docs/openapi
# routes) except the legacy hardcoded POST /score-event handler.
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
        getattr(route, "path", None) == "/score-event"
        and "POST" in (getattr(route, "methods", set()) or set())
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

    if payload.get("code") == "REAL_FITTED_MODEL_PATH_PROVEN":
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


@app.post(
    "/score-event",
    dependencies=[Depends(_require_action_api_key)],
    operation_id="scoreWowEvent",
)
def score_event(req: ScoreEventRequest):
    """Prove the real MLB fitted-model path while preserving publication holds."""
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
    _validate_held_bridge_payload(payload)

    if payload.get("code") != "REAL_FITTED_MODEL_PATH_PROVEN" or payload.get("scoring_evidence_produced") is not True:
        raise HTTPException(
            status_code=409,
            detail=payload,
        )

    # Successful model-path proof is not a betting approval and not a
    # publishable probability. Calibration/lineup/final-publication gates
    # remain visible in blockers and must clear independently.
    return {
        "ok": True,
        **payload,
    }


def _latest_pregame_acceptance_event() -> dict[str, Any] | None:
    """Select one real hydrated pregame shadow event for a one-time live probe."""
    now = datetime.now(timezone.utc).isoformat()
    result = (
        get_client()
        .table("wow_mlb_forward_shadow_events")
        .select(
            "official_event_id,official_date,event_start_time,home_team,away_team,"
            "venue_name,home_probable_pitcher,away_probable_pitcher,snapshot_id"
        )
        .eq("feature_hydration_status", "PASS")
        .gt("event_start_time", now)
        .order("event_start_time")
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
    """Use the existing production action secret against the live HTTP route.

    The secret and numeric probability values are never logged or returned.
    This probe exists only to produce evidence that the deployed service can
    traverse its real HTTP auth boundary and real frozen-model bridge.
    """
    key = os.getenv("WOW_ACTION_API_KEY")
    port = os.getenv("PORT")
    if not key or not port:
        _logger.error(
            "WOW_G11_SELF_ACCEPTANCE result=FAIL reason=RUNTIME_AUTH_OR_PORT_MISSING"
        )
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

            leaked = (
                sorted(_NUMERIC_PROBABILITY_FIELDS.intersection(result_body))
                if isinstance(result_body, dict)
                else ["INVALID_BODY"]
            )
            passed = (
                response.status_code == 200
                and isinstance(result_body, dict)
                and last_code == "REAL_FITTED_MODEL_PATH_PROVEN"
                and result_body.get("scoring_evidence_produced") is True
                and result_body.get("probability_fields_withheld") is True
                and result_body.get("probability_publishable") is False
                and result_body.get("can_execute") is False
                and not leaked
            )
            if passed:
                _logger.warning(
                    "WOW_G11_SELF_ACCEPTANCE result=PASS status=200 code=%s event_id=%s "
                    "scoring_evidence_produced=true probability_fields_withheld=true "
                    "probability_publishable=false can_execute=false leaked_probability_fields=0",
                    last_code,
                    payload["official_event_id"],
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
