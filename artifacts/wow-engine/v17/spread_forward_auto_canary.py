"""Backend-owned real-event discovery for NFL/WNBA spread forward canaries.

GitHub-hosted runners must not depend directly on public scoreboard availability.
This helper performs schedule identity acquisition inside the governed Render
runtime, then invokes the existing research-only spread forward shadow lane.
It does not change fitted model math, calibration, certification, publication,
promotion, or execution authority.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

import requests

from v17.spread_forward_shadow_leagues import run_nfl_forward_shadow, run_wnba_forward_shadow
from v17.spread_margin_challenger import SpreadChallengerUnavailable

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True

ESPN_SCOREBOARDS = {
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "WNBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard",
}
DEFAULT_HORIZON_DAYS = {"NFL": 8, "WNBA": 15}


def _governance() -> dict[str, Any]:
    return {
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "dry_run_only_no_live_trading_no_market_orders": True,
        "can_execute": False,
    }


def _utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
    return parsed.astimezone(timezone.utc)


def _competitors(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    competitions = event.get("competitions") or []
    if not competitions or not isinstance(competitions[0], dict):
        raise ValueError("EVENT_COMPETITION_MISSING")
    competitors = competitions[0].get("competitors") or []
    home = next((row for row in competitors if isinstance(row, dict) and row.get("homeAway") == "home"), None)
    away = next((row for row in competitors if isinstance(row, dict) and row.get("homeAway") == "away"), None)
    if not isinstance(home, dict) or not isinstance(away, dict):
        raise ValueError("EVENT_COMPETITORS_MISSING")
    return home, away


def discover_future_espn_event(
    sport: Literal["NFL", "WNBA"],
    *,
    fetcher: Callable[..., Any] = requests.get,
    now: datetime | None = None,
    horizon_days: int | None = None,
) -> dict[str, Any] | None:
    """Return the first exact future pregame ESPN event, or None after valid empty responses."""
    normalized = str(sport).upper()
    if normalized not in ESPN_SCOREBOARDS:
        raise SpreadChallengerUnavailable("SPREAD_CANARY_SPORT_UNSUPPORTED", normalized)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    horizon = int(horizon_days or DEFAULT_HORIZON_DAYS[normalized])
    successful_response = False
    source_failures: list[str] = []

    for offset in range(max(horizon, 1)):
        day = (current + timedelta(days=offset)).strftime("%Y%m%d")
        try:
            response = fetcher(
                ESPN_SCOREBOARDS[normalized],
                params={"dates": day, "limit": 100},
                headers={"Accept": "application/json", "User-Agent": "WOW-V17-Spread-Canary/1.0"},
                timeout=20,
            )
        except Exception as exc:  # noqa: BLE001 - converted to typed acquisition failure
            source_failures.append(type(exc).__name__)
            continue
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            source_failures.append(f"HTTP_{status}")
            continue
        successful_response = True
        try:
            body = response.json()
        except Exception as exc:  # noqa: BLE001
            source_failures.append(type(exc).__name__)
            continue
        events = body.get("events") if isinstance(body, dict) else None
        if not isinstance(events, list):
            source_failures.append("INVALID_EVENT_LIST")
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                start = _utc(event.get("date"))
            except Exception:
                continue
            status_payload = event.get("status") if isinstance(event.get("status"), dict) else {}
            status_type = status_payload.get("type") if isinstance(status_payload.get("type"), dict) else {}
            state = str(status_type.get("state") or "").lower()
            completed = status_type.get("completed") is True
            if start <= current or completed or state not in {"pre", "scheduled"}:
                continue
            try:
                home, away = _competitors(event)
            except ValueError:
                continue
            home_team = home.get("team") if isinstance(home.get("team"), dict) else {}
            away_team = away.get("team") if isinstance(away.get("team"), dict) else {}
            raw_event_id = str(event.get("id") or "").strip()
            home_id = str(home_team.get("id") or "").strip()
            away_id = str(away_team.get("id") or "").strip()
            home_name = str(home_team.get("displayName") or home_team.get("name") or "").strip()
            away_name = str(away_team.get("displayName") or away_team.get("name") or "").strip()
            if not raw_event_id or not home_id or not away_id or not home_name or not away_name:
                continue
            return {
                "sport": normalized,
                "raw_event_id": raw_event_id,
                "event_start_time": start.isoformat(),
                "home_team": home_name,
                "away_team": away_name,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "identity_provider": "ESPN_SCOREBOARD",
                "identity_acquisition_location": "BACKEND_RUNTIME",
                "can_execute": False,
            }

    if successful_response:
        return None
    detail = source_failures[-1] if source_failures else "NO_RESPONSE"
    raise SpreadChallengerUnavailable(
        f"{normalized}_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE",
        f"backend ESPN schedule acquisition failed: {detail}",
    )


def _deferred(sport: str) -> dict[str, Any]:
    return {
        "status": "DEFERRED_WITH_JUSTIFICATION",
        "code": f"{sport}_SPREAD_CANARY_NO_ELIGIBLE_FUTURE_EVENT",
        "sport": sport,
        "identity_provider": "ESPN_SCOREBOARD",
        "identity_acquisition_location": "BACKEND_RUNTIME",
        **_governance(),
    }


def run_nfl_spread_auto_canary(db: Any, *, fetcher: Callable[..., Any] = requests.get) -> dict[str, Any]:
    event = discover_future_espn_event("NFL", fetcher=fetcher)
    if event is None:
        return _deferred("NFL")
    result = run_nfl_forward_shadow(
        db,
        event_id=event["raw_event_id"],
        event_start_time=event["event_start_time"],
        home_team=event["home_team"],
        away_team=event["away_team"],
        home_spread=0.0,
    )
    return {
        **result,
        "canary_identity": event,
        "identity_provider": "ESPN_SCOREBOARD",
        "identity_acquisition_location": "BACKEND_RUNTIME",
        **_governance(),
    }


def run_wnba_spread_auto_canary(db: Any, *, fetcher: Callable[..., Any] = requests.get) -> dict[str, Any]:
    event = discover_future_espn_event("WNBA", fetcher=fetcher)
    if event is None:
        return _deferred("WNBA")
    result = run_wnba_forward_shadow(
        db,
        event_id=f"espn-{event['raw_event_id']}",
        event_start_time=event["event_start_time"],
        home_team_id=f"espn-{event['home_team_id']}",
        away_team_id=f"espn-{event['away_team_id']}",
        home_spread=0.0,
    )
    return {
        **result,
        "canary_identity": event,
        "identity_provider": "ESPN_SCOREBOARD",
        "identity_acquisition_location": "BACKEND_RUNTIME",
        **_governance(),
    }


__all__ = [
    "discover_future_espn_event",
    "run_nfl_spread_auto_canary",
    "run_wnba_spread_auto_canary",
]
