"""Production wrapper for the WOW governed backend G11 MLB event path.

Keeps the existing api.py app and routes intact except for POST /score-event.
That route is replaced with a fail-closed bridge to the real frozen MLB
fitted-model scorer in Supabase. Held probabilities are never returned while
publication gates remain blocked. can_execute remains false.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import Depends, HTTPException

import api as base_api

app = base_api.app
ScoreEventRequest = base_api.ScoreEventRequest
_require_action_api_key = base_api._require_action_api_key
_score_event_contract_errors = base_api._score_event_contract_errors
get_client = base_api.get_client

# Replace only the legacy hardcoded POST /score-event route. Preserve every
# other route and middleware from api.py.
app.router.routes = [
    route
    for route in app.router.routes
    if not (
        getattr(route, "path", None) == "/score-event"
        and "POST" in (getattr(route, "methods", set()) or set())
    )
]

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

    # This is intentionally a successful model-path response, not a betting
    # approval and not a publishable probability. Calibration/lineup/final
    # publication gates remain visible in blockers and must clear separately.
    return {
        "ok": True,
        **payload,
    }
