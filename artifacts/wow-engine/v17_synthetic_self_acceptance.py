"""Authenticated one-shot production self-acceptance for V17.

The runner verifies both synthetic fail-closed contracts and, when a real future
MLB shadow event exists with fitted probability but lineup still pending, the
projected-lineup sporting-probability contract through the deployed authenticated
/score-team-event HTTP boundary.

Projected sporting probability and global terminal disposition are orthogonal:
a valid projected probability may remain visible while V17_TERMINAL_REDUCER
applies a stronger downstream hold/purge. Acceptance therefore proves probability
preservation, rank exclusion, and terminal authority without overriding the
reducer's label.

Acceptance never places or approves a wager. It does not expose the Action key or
log sporting probability values. Diagnostic output names only failed contract
predicates and typed response codes. can_execute remains false.
"""
from __future__ import annotations

import asyncio
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

DEFAULT_SERVICE_URL = "https://wow-governed-probability-engine.onrender.com"
CAN_EXECUTE = False
UNSUPPORTED_SPORT = "TABLE_TENNIS"
_TRANSIENT_GATEWAY_STATUSES = {502, 503, 504}
_TRANSIENT_RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0)


def _prop_payload(*, event_start: str) -> dict[str, Any]:
    return {
        "request_id": "V17-SYNTHETIC-SELF-ACCEPTANCE-PROP",
        "rows": [{
            "row_key": "TEST-PROP-001", "event_id": "TEST-PROP-001",
            "event_start_time": event_start, "sport": UNSUPPORTED_SPORT,
            "player": "Test Player", "stat_type": "ACES", "line": 2.5,
            "direction": "MORE", "source_type": "NORMALIZED",
        }],
    }


def _team_event_payload(*, event_date: str) -> dict[str, Any]:
    return {
        "rows": [{
            "research_run_id": "TEST-EVENT-001-RUN",
            "objective_lane": "OUTRIGHT_WIN_PROBABILITY",
            "sport": UNSUPPORTED_SPORT, "league": "TEST_LEAGUE",
            "event_key": f"{UNSUPPORTED_SPORT}:TEST-EVENT-001",
            "event_state": "PREGAME", "event_date": event_date,
            "timezone": "UTC", "price_required_for_objective": False,
        }],
    }


def _real_projected_mlb_candidate(now: datetime) -> dict[str, Any] | None:
    """Select one real fitted, still-pregame MLB event whose lineup is pending."""
    try:
        import api_prod_market_acceptance as production
        rows = (
            production.market_api.prod.get_client()
            .table("wow_mlb_forward_shadow_events")
            .select(
                "official_event_id,official_date,event_start_time,home_team,away_team,"
                "snapshot_id,snapshot_timestamp,lineup_status,feature_hydration_status,model_score_status"
            )
            .gt("event_start_time", now.isoformat())
            .eq("feature_hydration_status", "PASS")
            .eq("model_score_status", "SHADOW_SCORED_LINEUP_PENDING")
            .order("event_start_time")
            .limit(1)
            .execute().data or []
        )
    except Exception:
        return None
    if not rows:
        return None
    row = dict(rows[0])
    if str(row.get("lineup_status") or "").upper() == "CONFIRMED":
        return None
    return row


def _projected_team_event_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "requester_host_identity": "WOW_BETTING_ENGINE",
        "research_run_id": "V17-PROJECTED-LINEUP-SELF-ACCEPTANCE",
        "requested_slate_date": str(row["official_date"]),
        "requested_timezone": "America/Chicago",
        "candidate_family": "OUTRIGHT_WINNER",
        "decision_intent": "BEST_SIDE",
        "event_key": f"MLB:{row['official_event_id']}",
        "official_event_id": str(row["official_event_id"]),
        "event_start_time_utc": str(row["event_start_time"]),
        "sport": "MLB",
        "league": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "settlement_basis": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
        "home_team": str(row["home_team"]),
        "away_team": str(row["away_team"]),
        "source_snapshot_id": str(row["snapshot_id"]),
        "latest_material_update_timestamp": str(row["snapshot_timestamp"]),
        "sport_specific_evidence": {},
    }


def _finite_probability(payload: dict[str, Any], name: str) -> bool:
    try:
        value = float(payload[name])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(value) and 0.0 <= value <= 1.0


def _projected_acceptance_failures(payload: dict[str, Any]) -> list[str]:
    terminal_label = str(payload.get("terminal_label") or "").strip().upper()
    checks = {
        "code": payload.get("code") == "LINEUP_PROJECTED_PROBABILITY_AVAILABLE",
        "lineup_state": payload.get("lineup_state") in {"PROJECTED_HIGH_CONFIDENCE", "PROJECTED_MEDIUM_CONFIDENCE"},
        "calibrated_home_probability": _finite_probability(payload, "calibrated_home_probability"),
        "calibrated_away_probability": _finite_probability(payload, "calibrated_away_probability"),
        "calibrated_home_lower_bound": _finite_probability(payload, "calibrated_home_lower_bound"),
        "calibrated_away_lower_bound": _finite_probability(payload, "calibrated_away_lower_bound"),
        "sporting_probability_publishable": payload.get("sporting_probability_publishable") is True,
        "probability_publishable": payload.get("probability_publishable") is True,
        "rank_eligible": payload.get("rank_eligible") is False,
        "terminal_fail_closed": bool(terminal_label) and terminal_label != "FINAL_APPROVED",
        "global_terminal_authority": payload.get("global_terminal_authority") == "V17_TERMINAL_REDUCER",
        "final_refresh_required": payload.get("final_refresh_required") is True,
        "lineup_confirmation_blocker": "LINEUP_CONFIRMATION_PENDING" in (payload.get("blockers") or []),
        "can_execute": payload.get("can_execute") is False,
    }
    return [name for name, ok in checks.items() if not ok]


def _projected_acceptance_ok(payload: dict[str, Any]) -> bool:
    return not _projected_acceptance_failures(payload)


def _typed_response_code(response: httpx.Response, payload: dict[str, Any]) -> str | None:
    if isinstance(payload, dict):
        direct = payload.get("code")
        if direct:
            return str(direct)
        detail = payload.get("detail")
        if isinstance(detail, dict) and detail.get("code"):
            return str(detail.get("code"))
    return None


async def _post_with_transient_gateway_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
) -> tuple[httpx.Response, int]:
    """Retry only rolling-deploy gateway responses; never retry model semantics.

    Render can briefly route startup self-acceptance to the draining predecessor
    instance during a rolling handoff. 502/503/504 are therefore retried with a
    short bounded backoff. Any application response, including 4xx/5xx model
    contract failures outside this transport set, is returned immediately.
    """
    attempts = 0
    response: httpx.Response | None = None
    for delay in (0.0, *_TRANSIENT_RETRY_DELAYS_SECONDS):
        if delay:
            await asyncio.sleep(delay)
        attempts += 1
        response = await client.post(url, headers=headers, json=json)
        if response.status_code not in _TRANSIENT_GATEWAY_STATUSES:
            return response, attempts
    assert response is not None
    return response, attempts


async def run_v17_synthetic_self_acceptance(logger, *, now: datetime | None = None) -> dict[str, Any]:
    """Exercise the live authenticated V17 HTTP surface."""
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
    projected_candidate = _real_projected_mlb_candidate(now)

    prop_attempts = team_event_attempts = projected_attempts = 0
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            prop_response, prop_attempts = await _post_with_transient_gateway_retry(
                client,
                f"{base_url}/score-pick-request",
                headers=headers,
                json=_prop_payload(event_start=event_start),
            )
            team_event_response, team_event_attempts = await _post_with_transient_gateway_retry(
                client,
                f"{base_url}/score-team-event-request",
                headers=headers,
                json=_team_event_payload(event_date=event_date),
            )
            projected_response = None
            if projected_candidate is not None:
                projected_response, projected_attempts = await _post_with_transient_gateway_retry(
                    client,
                    f"{base_url}/score-team-event",
                    headers=headers,
                    json=_projected_team_event_payload(projected_candidate),
                )
    except Exception as exc:
        result = {"status": "FAILED", "code": "HTTP_ACCEPTANCE_REQUEST_FAILED", "error_type": type(exc).__name__, "can_execute": False}
        logger.error("WOW_V17_SYNTHETIC_SELF_ACCEPTANCE status=FAILED code=HTTP_ACCEPTANCE_REQUEST_FAILED error_type=%s can_execute=false", type(exc).__name__)
        return result

    try:
        raw_prop_payload = prop_response.json()
    except ValueError:
        raw_prop_payload = {}
    prop_payload = raw_prop_payload if prop_response.status_code == 200 and isinstance(raw_prop_payload, dict) else {}
    prop_rows = {str(r.get("row_key")): r for r in (prop_payload.get("rows") or []) if isinstance(r, dict)}
    prop_row = prop_rows.get("TEST-PROP-001") or {}
    prop_error_code = _typed_response_code(prop_response, raw_prop_payload if isinstance(raw_prop_payload, dict) else {})
    prop_ok = bool(
        prop_response.status_code == 200
        and prop_row.get("terminal_status") == "HELD"
        and str(prop_row.get("terminal_label") or prop_row.get("code") or "").upper() == "MODEL_UNAVAILABLE"
        and prop_row.get("probability_publishable") is False
        and prop_row.get("can_execute") is False
    )

    try:
        raw_team_payload = team_event_response.json()
    except ValueError:
        raw_team_payload = {}
    team_payload = raw_team_payload if team_event_response.status_code == 200 and isinstance(raw_team_payload, dict) else {}
    team_rows = team_payload.get("rows") or []
    team_row = team_rows[0] if team_rows and isinstance(team_rows[0], dict) else {}
    team_ok = bool(
        team_event_response.status_code == 200
        and team_row.get("terminal_status") == "HELD"
        and str(team_row.get("code") or "").upper() == "MODEL_UNAVAILABLE"
        and team_row.get("probability_publishable") is False
        and team_row.get("can_execute") is False
    )

    projected_status = "NO_ELIGIBLE_PREGAME_EVENT"
    projected_ok: bool | None = None
    projected_http_status: int | None = None
    projected_event_id: str | None = None
    projected_code: str | None = None
    projected_failures: list[str] = []
    if projected_candidate is not None and projected_response is not None:
        projected_event_id = str(projected_candidate.get("official_event_id"))
        projected_http_status = projected_response.status_code
        try:
            projected_payload = projected_response.json() if projected_response.status_code == 200 else {}
        except ValueError:
            projected_payload = {}
        projected_code = str(projected_payload.get("code") or "") or None
        projected_failures = _projected_acceptance_failures(projected_payload) if projected_response.status_code == 200 else ["http_status"]
        projected_ok = bool(projected_response.status_code == 200 and not projected_failures)
        projected_status = "PASS" if projected_ok else "FAIL"

    required_ok = prop_ok and team_ok and (projected_ok is not False)
    status = "PASS" if required_ok else "FAIL"
    result = {
        "status": status,
        "prop_http_status": prop_response.status_code,
        "prop_ok": prop_ok,
        "prop_terminal_label": prop_row.get("terminal_label") or prop_row.get("code"),
        "prop_error_code": prop_error_code,
        "prop_attempts": prop_attempts,
        "team_event_http_status": team_event_response.status_code,
        "team_event_ok": team_ok,
        "team_event_code": team_row.get("code"),
        "team_event_attempts": team_event_attempts,
        "projected_lineup_status": projected_status,
        "projected_lineup_http_status": projected_http_status,
        "projected_lineup_ok": projected_ok,
        "projected_lineup_event_id": projected_event_id,
        "projected_lineup_code": projected_code,
        "projected_lineup_failed_checks": projected_failures,
        "projected_lineup_attempts": projected_attempts,
        "can_execute": False,
    }
    log = logger.warning if status == "PASS" else logger.error
    log(
        "WOW_V17_SYNTHETIC_SELF_ACCEPTANCE status=%s prop_http_status=%s prop_ok=%s prop_terminal_label=%s prop_error_code=%s prop_attempts=%s "
        "team_event_http_status=%s team_event_ok=%s team_event_code=%s team_event_attempts=%s projected_lineup_status=%s "
        "projected_lineup_http_status=%s projected_lineup_ok=%s projected_lineup_event_id=%s projected_lineup_code=%s "
        "projected_lineup_failed_checks=%s projected_lineup_attempts=%s can_execute=false",
        status, prop_response.status_code, prop_ok, prop_row.get("terminal_label") or prop_row.get("code"), prop_error_code, prop_attempts,
        team_event_response.status_code, team_ok, team_row.get("code"), team_event_attempts, projected_status,
        projected_http_status, projected_ok, projected_event_id, projected_code,
        ",".join(projected_failures) if projected_failures else "NONE", projected_attempts,
    )
    return result
