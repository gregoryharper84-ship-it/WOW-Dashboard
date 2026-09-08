"""One-shot authenticated production acceptance for the V17 MLB event bridge.

This probe selects the newest still-pregame canonical MLB snapshot with PASS
feature hydration, a confirmed lineup, and a completed forward baseline. It then
calls the deployed authenticated /score-team-event boundary using the server's
existing WOW_ACTION_API_KEY.

Acceptance is intentionally narrow: the response must either contain a complete
calibrated sporting probability package or a canonical completion failure
(MODEL_INPUTS_INSUFFICIENT, MODEL_SCORER_FAILED, MODEL_OUTPUT_INVALID). The old
EVENT_MODEL_BRIDGE_UNAVAILABLE value may remain only as secondary blocker detail;
it cannot be the sole diagnosis. Probability values and credentials are never
logged. Wager execution remains impossible.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

import httpx

CAN_EXECUTE = False
DEFAULT_SERVICE_URL = "https://wow-governed-probability-engine.onrender.com"
_CANONICAL_FAILURES = {
    "MODEL_INPUTS_INSUFFICIENT",
    "MODEL_SCORER_FAILED",
    "MODEL_OUTPUT_INVALID",
}
_REQUIRED_PACKAGE_FIELDS = (
    "calibrated_home_probability",
    "calibrated_away_probability",
    "calibrated_home_lower_bound",
    "calibrated_away_lower_bound",
    "model_version",
    "model_timestamp",
)
_TRANSIENT_GATEWAY_STATUSES = {502, 503, 504}
_RETRY_DELAYS = (0.0, 0.5, 1.0, 2.0)


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_confirmed_candidate(event_api: Any, now: datetime) -> dict[str, Any] | None:
    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        return None
    try:
        rows = (
            get_client()
            .table("wow_mlb_forward_shadow_events")
            .select(
                "official_event_id,official_date,event_start_time,event_status,home_team,away_team,"
                "snapshot_id,snapshot_timestamp,feature_hydration_status,lineup_status,model_score_status"
            )
            .gt("event_start_time", now.isoformat())
            .order("event_start_time")
            .limit(500)
            .execute().data or []
        )
    except Exception:
        return None

    latest_by_event: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        event_id = str(raw.get("official_event_id") or "").strip()
        snap_time = _aware(raw.get("snapshot_timestamp"))
        start_time = _aware(raw.get("event_start_time"))
        if not event_id or snap_time is None or start_time is None or start_time <= now:
            continue
        current = latest_by_event.get(event_id)
        if current is None or snap_time > current[0]:
            latest_by_event[event_id] = (snap_time, dict(raw))

    eligible: list[dict[str, Any]] = []
    for _, row in latest_by_event.values():
        if str(row.get("feature_hydration_status") or "").upper() != "PASS":
            continue
        if str(row.get("lineup_status") or "").upper() not in {"CONFIRMED", "OFFICIAL", "FINAL"}:
            continue
        if str(row.get("model_score_status") or "").upper() != "SHADOW_SCORED_PREGAME":
            continue
        if str(row.get("event_status") or "").upper() not in {"PRE-GAME", "PREGAME", "SCHEDULED"}:
            continue
        eligible.append(row)

    if not eligible:
        return None
    eligible.sort(key=lambda row: _aware(row.get("event_start_time")) or datetime.max.replace(tzinfo=timezone.utc))
    return dict(eligible[0])


def _request_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "requester_host_identity": "WOW_BETTING_ENGINE",
        "research_run_id": "V17-MLB-EVENT-BRIDGE-LIVE-SELF-ACCEPTANCE",
        "requested_slate_date": str(row["official_date"]),
        "requested_timezone": "America/Chicago",
        "scan_stage": "PREGAME",
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


def _response_body(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    detail = payload.get("detail")
    return detail if isinstance(detail, dict) else payload


def _finite_probability(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0.0 <= number <= 1.0


def evaluate_bridge_response(http_status: int, payload: Any) -> dict[str, Any]:
    body = _response_body(payload)
    code = str(body.get("code") or body.get("status") or "").strip().upper() or None
    blocker_code = str(body.get("blocker_code") or "").strip().upper() or None
    can_execute_false = body.get("can_execute") is False
    complete_package = bool(
        all(body.get(field) not in (None, "") for field in _REQUIRED_PACKAGE_FIELDS)
        and all(_finite_probability(body.get(field)) for field in _REQUIRED_PACKAGE_FIELDS[:4])
    )
    canonical_failure = code in _CANONICAL_FAILURES
    sole_legacy_bridge_diagnosis = code == "EVENT_MODEL_BRIDGE_UNAVAILABLE"
    accepted = bool(
        not sole_legacy_bridge_diagnosis
        and can_execute_false
        and (complete_package or canonical_failure)
        and 200 <= int(http_status) < 600
    )
    return {
        "accepted": accepted,
        "http_status": int(http_status),
        "code": code,
        "blocker_code": blocker_code,
        "complete_probability_package": complete_package,
        "canonical_failure": canonical_failure,
        "sole_legacy_bridge_diagnosis": sole_legacy_bridge_diagnosis,
        "can_execute_false": can_execute_false,
        "can_execute": False,
    }


async def _post(client: httpx.AsyncClient, url: str, *, headers: dict[str, str], payload: dict[str, Any]) -> tuple[httpx.Response, int]:
    response: httpx.Response | None = None
    attempts = 0
    for delay in _RETRY_DELAYS:
        if delay:
            await asyncio.sleep(delay)
        attempts += 1
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code not in _TRANSIENT_GATEWAY_STATUSES:
            return response, attempts
    assert response is not None
    return response, attempts


async def run_mlb_event_bridge_self_acceptance(logger: logging.Logger, *, event_api: Any, now: datetime | None = None) -> dict[str, Any]:
    api_key = os.getenv("WOW_ACTION_API_KEY", "").strip()
    if not api_key:
        result = {"status": "FAILED", "code": "WOW_ACTION_API_KEY_MISSING", "can_execute": False}
        logger.error("WOW_V17_MLB_EVENT_BRIDGE_SELF_ACCEPTANCE status=FAILED code=WOW_ACTION_API_KEY_MISSING can_execute=false")
        return result

    now = now or datetime.now(timezone.utc)
    candidate = _latest_confirmed_candidate(event_api, now)
    if candidate is None:
        result = {"status": "SKIPPED", "code": "NO_ELIGIBLE_CONFIRMED_PREGAME_MLB_EVENT", "can_execute": False}
        logger.warning("WOW_V17_MLB_EVENT_BRIDGE_SELF_ACCEPTANCE status=SKIPPED code=NO_ELIGIBLE_CONFIRMED_PREGAME_MLB_EVENT can_execute=false")
        return result

    base_url = os.getenv("RENDER_EXTERNAL_URL", DEFAULT_SERVICE_URL).strip().rstrip("/") or DEFAULT_SERVICE_URL
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response, attempts = await _post(
                client,
                f"{base_url}/score-team-event",
                headers=headers,
                payload=_request_payload(candidate),
            )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        evaluation = evaluate_bridge_response(response.status_code, payload)
    except Exception as exc:
        result = {
            "status": "FAILED",
            "code": "MLB_EVENT_BRIDGE_ACCEPTANCE_REQUEST_FAILED",
            "error_type": type(exc).__name__,
            "event_id": str(candidate.get("official_event_id") or ""),
            "can_execute": False,
        }
        logger.error(
            "WOW_V17_MLB_EVENT_BRIDGE_SELF_ACCEPTANCE status=FAILED code=MLB_EVENT_BRIDGE_ACCEPTANCE_REQUEST_FAILED event_id=%s error_type=%s can_execute=false",
            result["event_id"], result["error_type"],
        )
        return result

    status = "PASS" if evaluation["accepted"] else "FAIL"
    result = {
        "status": status,
        "event_id": str(candidate.get("official_event_id") or ""),
        "attempts": attempts,
        **evaluation,
        "can_execute": False,
    }
    log = logger.warning if status == "PASS" else logger.error
    log(
        "WOW_V17_MLB_EVENT_BRIDGE_SELF_ACCEPTANCE status=%s event_id=%s http_status=%s code=%s blocker_code=%s complete_probability_package=%s canonical_failure=%s sole_legacy_bridge_diagnosis=%s attempts=%s can_execute=false",
        status,
        result["event_id"],
        evaluation["http_status"],
        evaluation["code"],
        evaluation["blocker_code"],
        evaluation["complete_probability_package"],
        evaluation["canonical_failure"],
        evaluation["sole_legacy_bridge_diagnosis"],
        attempts,
    )
    return result
