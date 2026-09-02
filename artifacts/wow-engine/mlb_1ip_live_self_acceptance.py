"""Authenticated one-shot production self-acceptance for MLB 1IP.

The runner discovers the next official MLB game with a named probable pitcher,
then exercises the deployed /score-pick-request HTTP boundary using the service's
existing WOW_ACTION_API_KEY. The secret is read only from process environment
and is never logged or returned.

This is acceptance/observability only. It cannot execute a wager and does not
change model publication authority.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
DEFAULT_SERVICE_URL = "https://wow-governed-probability-engine.onrender.com"
SUPPORTED_LINE = 15.5
UNSUPPORTED_LINE = 16.5
CAN_EXECUTE = False


def _next_probable_pitcher(*, now: datetime, http_get=httpx.get) -> dict[str, str] | None:
    start = now.date()
    end = start + timedelta(days=3)
    response = http_get(
        MLB_SCHEDULE_URL,
        params={
            "sportId": "1",
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "hydrate": "probablePitcher,team",
        },
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    candidates: list[tuple[datetime, dict[str, str]]] = []
    for date_block in payload.get("dates") or []:
        for game in (date_block or {}).get("games") or []:
            game_pk = game.get("gamePk")
            game_date = game.get("gameDate")
            if not game_pk or not game_date:
                continue
            try:
                event_start = datetime.fromisoformat(str(game_date).replace("Z", "+00:00")).astimezone(timezone.utc)
            except (TypeError, ValueError):
                continue
            if event_start <= now + timedelta(minutes=10):
                continue
            teams = game.get("teams") or {}
            for side in ("away", "home"):
                probable = ((teams.get(side) or {}).get("probablePitcher") or {})
                name = str(probable.get("fullName") or "").strip()
                if not name:
                    continue
                candidates.append(
                    (
                        event_start,
                        {
                            "event_id": str(game_pk),
                            "event_start_time": event_start.isoformat(),
                            "player": name,
                        },
                    )
                )
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1] if candidates else None


def _row(*, candidate: dict[str, str], row_key: str, line: float) -> dict[str, Any]:
    return {
        "row_key": row_key,
        "event_id": candidate["event_id"],
        "event_start_time": candidate["event_start_time"],
        "sport": "MLB",
        "player": candidate["player"],
        "stat_type": "1ST_INNING_PITCHES_THROWN",
        "line": line,
        "direction": "MORE",
        "source_type": "AUTONOMOUS_DISCOVERY",
        "platform": "WOW_LIVE_SELF_ACCEPTANCE",
        "money_lane_status": "PAYOUT_UNRESOLVED",
    }


async def run_live_self_acceptance(logger, *, startup_delay_seconds: float = 5.0) -> dict[str, Any]:
    """Run one real authenticated HTTP acceptance against the deployed service."""
    await asyncio.sleep(max(0.0, float(startup_delay_seconds)))
    api_key = os.getenv("WOW_ACTION_API_KEY", "").strip()
    base_url = os.getenv("RENDER_EXTERNAL_URL", DEFAULT_SERVICE_URL).strip().rstrip("/") or DEFAULT_SERVICE_URL
    if not api_key:
        result = {"status": "FAILED", "code": "WOW_ACTION_API_KEY_MISSING", "can_execute": False}
        logger.error("WOW_MLB_1IP_LIVE_SELF_ACCEPTANCE status=FAILED code=WOW_ACTION_API_KEY_MISSING can_execute=false")
        return result

    now = datetime.now(timezone.utc)
    try:
        candidate = await asyncio.to_thread(_next_probable_pitcher, now=now)
    except Exception as exc:
        result = {"status": "FAILED", "code": "MLB_SCHEDULE_DISCOVERY_FAILED", "error_type": type(exc).__name__, "can_execute": False}
        logger.error(
            "WOW_MLB_1IP_LIVE_SELF_ACCEPTANCE status=FAILED code=MLB_SCHEDULE_DISCOVERY_FAILED error_type=%s can_execute=false",
            type(exc).__name__,
        )
        return result
    if candidate is None:
        result = {"status": "BLOCKED", "code": "NO_FUTURE_PROBABLE_PITCHER_FOUND", "can_execute": False}
        logger.warning("WOW_MLB_1IP_LIVE_SELF_ACCEPTANCE status=BLOCKED code=NO_FUTURE_PROBABLE_PITCHER_FOUND can_execute=false")
        return result

    batch = {
        "request_id": f"MLB-1IP-LIVE-SELF-ACCEPTANCE-{candidate['event_id']}",
        "rows": [
            _row(candidate=candidate, row_key="supported-15.5", line=SUPPORTED_LINE),
            _row(candidate=candidate, row_key="unsupported-16.5", line=UNSUPPORTED_LINE),
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{base_url}/score-pick-request",
                headers={"Authorization": f"Bearer {api_key}"},
                json=batch,
            )
        payload = response.json()
    except Exception as exc:
        result = {"status": "FAILED", "code": "HTTP_ACCEPTANCE_REQUEST_FAILED", "error_type": type(exc).__name__, "can_execute": False}
        logger.error(
            "WOW_MLB_1IP_LIVE_SELF_ACCEPTANCE status=FAILED code=HTTP_ACCEPTANCE_REQUEST_FAILED error_type=%s can_execute=false",
            type(exc).__name__,
        )
        return result

    rows = {str(row.get("row_key")): row for row in (payload.get("rows") or []) if isinstance(row, dict)} if isinstance(payload, dict) else {}
    supported = rows.get("supported-15.5") or {}
    unsupported = rows.get("unsupported-16.5") or {}
    supported_ok = bool(
        response.status_code == 200
        and supported.get("model_evaluated") is True
        and supported.get("terminal_status") == "COMPLETED"
        and isinstance((supported.get("result") or {}).get("raw_probability"), (int, float))
        and supported.get("can_execute") is False
    )
    unsupported_ok = bool(
        response.status_code == 200
        and unsupported.get("terminal_status") == "REJECTED"
        and unsupported.get("terminal_label") == "REJECT_OOD"
        and unsupported.get("can_execute") is False
    )
    status = "PASS" if supported_ok and unsupported_ok else "FAIL"
    result = {
        "status": status,
        "http_status": response.status_code,
        "event_id": candidate["event_id"],
        "player": candidate["player"],
        "supported_ok": supported_ok,
        "supported_terminal": supported.get("terminal_label"),
        "unsupported_ok": unsupported_ok,
        "unsupported_terminal": unsupported.get("terminal_label"),
        "unsupported_code": unsupported.get("code"),
        "can_execute": False,
    }
    log = logger.warning if status == "PASS" else logger.error
    log(
        "WOW_MLB_1IP_LIVE_SELF_ACCEPTANCE status=%s http_status=%s event_id=%s player=%s supported_ok=%s supported_terminal=%s unsupported_ok=%s unsupported_terminal=%s unsupported_code=%s can_execute=false",
        status,
        response.status_code,
        candidate["event_id"],
        candidate["player"],
        supported_ok,
        supported.get("terminal_label"),
        unsupported_ok,
        unsupported.get("terminal_label"),
        unsupported.get("code"),
    )
    return result
