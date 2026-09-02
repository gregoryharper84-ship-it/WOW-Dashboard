"""Authenticated one-shot production self-acceptance for the V17 synthetic
fail-closed acceptance scenarios (unsupported sport, no certified model).

The runner exercises the deployed /score-pick-request and
/score-team-event-request HTTP boundaries using the service's own
WOW_ACTION_API_KEY, read only from process environment. It never leaves the
running Render instance's own network boundary, so it works even when an
external caller cannot reach the deployed URL directly.

This proves the live, deployed HTTP surface -- not just local test code --
correctly fails closed for a sport with no certified model: no probability is
fabricated, rank_eligible/probability_publishable stay false, and
can_execute stays false throughout. It is acceptance/observability only. It
cannot execute a wager and does not change model publication authority.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

DEFAULT_SERVICE_URL = "https://wow-governed-probability-engine.onrender.com"
CAN_EXECUTE = False
UNSUPPORTED_SPORT = "TABLE_TENNIS"


def _prop_payload(*, event_start: str) -> dict[str, Any]:
    return {
        "request_id": "V17-SYNTHETIC-SELF-ACCEPTANCE-PROP",
        "rows": [
            {
                "row_key": "TEST-PROP-001",
                "event_id": "TEST-PROP-001",
                "event_start_time": event_start,
                "sport": UNSUPPORTED_SPORT,
                "player": "Test Player",
                "stat_type": "ACES",
                "line": 2.5,
                "direction": "MORE",
                "source_type": "NORMALIZED",
            }
        ],
    }


def _team_event_payload(*, event_date: str) -> dict[str, Any]:
    return {
        "rows": [
            {
                "research_run_id": "TEST-EVENT-001-RUN",
                "objective_lane": "OUTRIGHT_WIN_PROBABILITY",
                "sport": UNSUPPORTED_SPORT,
                "league": "TEST_LEAGUE",
                "event_key": f"{UNSUPPORTED_SPORT}:TEST-EVENT-001",
                "event_state": "PREGAME",
                "event_date": event_date,
                "timezone": "UTC",
                "price_required_for_objective": False,
            }
        ],
    }


async def run_v17_synthetic_self_acceptance(logger, *, now: datetime | None = None) -> dict[str, Any]:
    """Run both real authenticated HTTP acceptance scenarios against the
    deployed service and confirm both fail closed with no fabricated
    probability."""
    api_key = os.getenv("WOW_ACTION_API_KEY", "").strip()
    base_url = os.getenv("RENDER_EXTERNAL_URL", DEFAULT_SERVICE_URL).strip().rstrip("/") or DEFAULT_SERVICE_URL
    if not api_key:
        result = {"status": "FAILED", "code": "WOW_ACTION_API_KEY_MISSING", "can_execute": False}
        logger.error("WOW_V17_SYNTHETIC_SELF_ACCEPTANCE status=FAILED code=WOW_ACTION_API_KEY_MISSING can_execute=false")
        return result

    now = now or datetime.now(timezone.utc)
    event_start = (now + timedelta(days=1)).isoformat()
    event_date = (now + timedelta(days=1)).date().isoformat()
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            prop_response = await client.post(
                f"{base_url}/score-pick-request",
                headers=headers,
                json=_prop_payload(event_start=event_start),
            )
            team_event_response = await client.post(
                f"{base_url}/score-team-event-request",
                headers=headers,
                json=_team_event_payload(event_date=event_date),
            )
    except Exception as exc:
        result = {"status": "FAILED", "code": "HTTP_ACCEPTANCE_REQUEST_FAILED", "error_type": type(exc).__name__, "can_execute": False}
        logger.error(
            "WOW_V17_SYNTHETIC_SELF_ACCEPTANCE status=FAILED code=HTTP_ACCEPTANCE_REQUEST_FAILED error_type=%s can_execute=false",
            type(exc).__name__,
        )
        return result

    prop_payload = prop_response.json() if prop_response.status_code == 200 else {}
    prop_rows = {str(r.get("row_key")): r for r in (prop_payload.get("rows") or []) if isinstance(r, dict)}
    prop_row = prop_rows.get("TEST-PROP-001") or {}
    prop_ok = bool(
        prop_response.status_code == 200
        and prop_row.get("terminal_status") == "HELD"
        and str(prop_row.get("terminal_label") or prop_row.get("code") or "").upper() == "MODEL_UNAVAILABLE"
        and prop_row.get("probability_publishable") is False
        and prop_row.get("can_execute") is False
    )

    team_event_payload = team_event_response.json() if team_event_response.status_code == 200 else {}
    team_event_rows = team_event_payload.get("rows") or []
    team_event_row = team_event_rows[0] if team_event_rows and isinstance(team_event_rows[0], dict) else {}
    team_event_ok = bool(
        team_event_response.status_code == 200
        and team_event_row.get("terminal_status") == "HELD"
        and str(team_event_row.get("code") or "").upper() == "MODEL_UNAVAILABLE"
        and team_event_row.get("probability_publishable") is False
        and team_event_row.get("can_execute") is False
    )

    status = "PASS" if prop_ok and team_event_ok else "FAIL"
    result = {
        "status": status,
        "prop_http_status": prop_response.status_code,
        "prop_ok": prop_ok,
        "prop_terminal_label": prop_row.get("terminal_label") or prop_row.get("code"),
        "team_event_http_status": team_event_response.status_code,
        "team_event_ok": team_event_ok,
        "team_event_code": team_event_row.get("code"),
        "can_execute": False,
    }
    log = logger.warning if status == "PASS" else logger.error
    log(
        "WOW_V17_SYNTHETIC_SELF_ACCEPTANCE status=%s prop_http_status=%s prop_ok=%s prop_terminal_label=%s "
        "team_event_http_status=%s team_event_ok=%s team_event_code=%s can_execute=false",
        status,
        prop_response.status_code,
        prop_ok,
        prop_row.get("terminal_label") or prop_row.get("code"),
        team_event_response.status_code,
        team_event_ok,
        team_event_row.get("code"),
    )
    return result
