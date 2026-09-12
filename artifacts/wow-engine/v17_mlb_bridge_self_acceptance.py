"""One-shot authenticated production acceptance for the V17 MLB event bridge.

This probe selects the newest still-pregame canonical MLB snapshot with PASS
feature hydration, a confirmed lineup, and a completed forward baseline. It then
calls the deployed authenticated /score-team-event boundary using the server's
existing WOW_ACTION_API_KEY.

Acceptance is intentionally narrow: a complete calibrated sporting probability
package is not enough by itself. When the fitted MLB model completes, the response
must also prove that the downstream LLP probability audit / event-decision / mutex
governance bridge was actually reached. A governed NO_PICK/HOLD remains a valid
operational result; gates are never weakened to manufacture a pick. Canonical
model completion failures remain typed failures. Legacy bridge-unavailable errors
cannot pass acceptance. Probability values and credentials are never logged.
Wager execution remains impossible.
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
_GOVERNANCE_BRIDGE_FAILURE_CODES = {
    "EVENT_MODEL_BRIDGE_UNAVAILABLE",
    "V17_EVENT_GOVERNANCE_BRIDGE_UNAVAILABLE",
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


def _latest_confirmed_candidate(event_api: Any, now: datetime) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    unavailable = {"future_events": 0, "eligible": 0, "canonical_ledger_readable": False}
    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        return None, unavailable
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
        return None, unavailable

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

    # Count why each future event was rejected. Without this, a SKIPPED run is
    # indistinguishable from a broken canonical producer, which is exactly how an
    # on-schedule pregame lineup wait gets misread as a production defect.
    breakdown = {
        "future_events": len(latest_by_event),
        "rejected_hydration_not_pass": 0,
        "rejected_lineup_pending": 0,
        "rejected_not_shadow_scored_pregame": 0,
        "rejected_event_status": 0,
        "eligible": 0,
    }
    eligible: list[dict[str, Any]] = []
    for _, row in latest_by_event.values():
        if str(row.get("feature_hydration_status") or "").upper() != "PASS":
            breakdown["rejected_hydration_not_pass"] += 1
            continue
        if str(row.get("lineup_status") or "").upper() not in {"CONFIRMED", "OFFICIAL", "FINAL"}:
            breakdown["rejected_lineup_pending"] += 1
            continue
        if str(row.get("model_score_status") or "").upper() != "SHADOW_SCORED_PREGAME":
            breakdown["rejected_not_shadow_scored_pregame"] += 1
            continue
        if str(row.get("event_status") or "").upper() not in {"PRE-GAME", "PREGAME", "SCHEDULED"}:
            breakdown["rejected_event_status"] += 1
            continue
        eligible.append(row)

    breakdown["eligible"] = len(eligible)
    if not eligible:
        return None, breakdown
    eligible.sort(key=lambda row: _aware(row.get("event_start_time")) or datetime.max.replace(tzinfo=timezone.utc))
    return dict(eligible[0]), breakdown


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


def _governance_reached(body: dict[str, Any]) -> bool:
    governance = body.get("llp_governance")
    if not isinstance(governance, dict):
        return False
    status = str(governance.get("status") or "").strip().upper()
    if status in {"", "UNAVAILABLE", "INVALID", "NOT_PROVEN"}:
        return False
    audit = str(governance.get("probability_audit_result") or "").strip().upper()
    decision = str(governance.get("event_decision") or "").strip().upper()
    mutex = str(governance.get("event_mutex_status") or "").strip().upper()
    return bool(
        audit and audit != "NOT_PROVEN"
        and decision and decision != "NOT_PROVEN"
        and mutex and mutex != "NOT_PROVEN"
        and governance.get("global_terminal_reducer") == "V17_TERMINAL_REDUCER"
        and governance.get("can_execute") is False
    )


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
    governance_reached = _governance_reached(body)
    bridge_unavailable = bool(
        code in _GOVERNANCE_BRIDGE_FAILURE_CODES
        or blocker_code in _GOVERNANCE_BRIDGE_FAILURE_CODES
        or "V17_EVENT_GOVERNANCE_BRIDGE_UNAVAILABLE" in (body.get("blockers") or [])
    )
    sole_legacy_bridge_diagnosis = code == "EVENT_MODEL_BRIDGE_UNAVAILABLE"
    accepted = bool(
        not bridge_unavailable
        and can_execute_false
        and (
            canonical_failure
            or (complete_package and governance_reached)
        )
        and 200 <= int(http_status) < 600
    )
    return {
        "accepted": accepted,
        "http_status": int(http_status),
        "code": code,
        "blocker_code": blocker_code,
        "complete_probability_package": complete_package,
        "canonical_failure": canonical_failure,
        "governance_reached": governance_reached,
        "bridge_unavailable": bridge_unavailable,
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
    candidate, breakdown = _latest_confirmed_candidate(event_api, now)
    if candidate is None:
        # Every future event still waiting on an official lineup is the normal
        # pregame state, not a canonical producer gap. Report it as its own code
        # so the two are never conflated again.
        lineup_pending_only = bool(
            breakdown.get("future_events")
            and breakdown.get("rejected_lineup_pending") == breakdown.get("future_events")
        )
        code = (
            "ALL_FUTURE_MLB_EVENTS_AWAITING_OFFICIAL_LINEUP"
            if lineup_pending_only
            else "NO_ELIGIBLE_CONFIRMED_PREGAME_MLB_EVENT"
        )
        result = {"status": "SKIPPED", "code": code, "candidate_breakdown": breakdown, "can_execute": False}
        logger.warning(
            "WOW_V17_MLB_EVENT_BRIDGE_SELF_ACCEPTANCE status=SKIPPED code=%s future_events=%s lineup_pending=%s hydration_not_pass=%s not_shadow_scored_pregame=%s event_status_rejected=%s can_execute=false",
            code,
            breakdown.get("future_events"),
            breakdown.get("rejected_lineup_pending"),
            breakdown.get("rejected_hydration_not_pass"),
            breakdown.get("rejected_not_shadow_scored_pregame"),
            breakdown.get("rejected_event_status"),
        )
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
        "WOW_V17_MLB_EVENT_BRIDGE_SELF_ACCEPTANCE status=%s event_id=%s http_status=%s code=%s blocker_code=%s complete_probability_package=%s governance_reached=%s bridge_unavailable=%s canonical_failure=%s attempts=%s can_execute=false",
        status,
        result["event_id"],
        evaluation["http_status"],
        evaluation["code"],
        evaluation["blocker_code"],
        evaluation["complete_probability_package"],
        evaluation["governance_reached"],
        evaluation["bridge_unavailable"],
        evaluation["canonical_failure"],
        attempts,
    )
    return result