"""Identity-only current WNBA event verification for spread shadow scoring."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from basketball_event_hydration_runtime import ESPN_BASE_URLS, _espn_competitors
from v17.spread_margin_challenger import SpreadChallengerUnavailable, _dt


def resolve_wnba_current_event_identity(*, event_id: str, event_start_time: str,
                                        home_team_id: str, away_team_id: str,
                                        fetcher: Callable[..., Any] = requests.get) -> dict[str, Any]:
    target = _dt(event_start_time)
    raw_event_id = str(event_id).removeprefix("espn-").strip()
    if not raw_event_id or not str(home_team_id).startswith("espn-") or not str(away_team_id).startswith("espn-"):
        raise SpreadChallengerUnavailable("WNBA_SPREAD_FORWARD_ESPN_IDENTITY_REQUIRED", "ESPN-prefixed event/team identities are required")
    local_day = target.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    try:
        response = fetcher(
            ESPN_BASE_URLS["WNBA"],
            params={"dates": local_day, "limit": 100},
            headers={"Accept": "application/json", "User-Agent": "WOW-V17-WNBA-Spread-Identity/1.0"},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        raise SpreadChallengerUnavailable("WNBA_SPREAD_FORWARD_IDENTITY_SOURCE_UNAVAILABLE", "ESPN WNBA identity request failed") from exc
    if int(getattr(response, "status_code", 0)) != 200:
        raise SpreadChallengerUnavailable("WNBA_SPREAD_FORWARD_IDENTITY_SOURCE_UNAVAILABLE", f"ESPN WNBA identity HTTP {getattr(response, 'status_code', 0)}")
    try:
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        raise SpreadChallengerUnavailable("WNBA_SPREAD_FORWARD_IDENTITY_SOURCE_INVALID", "ESPN WNBA identity response is not JSON") from exc
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        raise SpreadChallengerUnavailable("WNBA_SPREAD_FORWARD_IDENTITY_SOURCE_INVALID", "ESPN WNBA identity response has no event list")
    matches = [event for event in events if isinstance(event, dict) and str(event.get("id") or "") == raw_event_id]
    if len(matches) != 1:
        code = "WNBA_SPREAD_FORWARD_EVENT_IDENTITY_AMBIGUOUS" if len(matches) > 1 else "WNBA_SPREAD_FORWARD_EVENT_NOT_FOUND"
        raise SpreadChallengerUnavailable(code, "exact ESPN WNBA event identity could not be resolved")
    event = matches[0]
    home, away = _espn_competitors(event)
    if not isinstance(home, dict) or not isinstance(away, dict):
        raise SpreadChallengerUnavailable("WNBA_SPREAD_FORWARD_EVENT_IDENTITY_INCOMPLETE", "ESPN event is missing home/away competitors")
    home_payload = home.get("team") if isinstance(home.get("team"), dict) else {}
    away_payload = away.get("team") if isinstance(away.get("team"), dict) else {}
    actual_home = f"espn-{str(home_payload.get('id') or '').strip()}"
    actual_away = f"espn-{str(away_payload.get('id') or '').strip()}"
    if actual_home != str(home_team_id) or actual_away != str(away_team_id):
        raise SpreadChallengerUnavailable("WNBA_SPREAD_FORWARD_TEAM_IDENTITY_MISMATCH", "ESPN event teams do not match requested governed identities")
    event_time = _dt(event.get("date"))
    if abs((event_time - target).total_seconds()) > 300:
        raise SpreadChallengerUnavailable("WNBA_SPREAD_FORWARD_EVENT_TIME_MISMATCH", "ESPN event start differs from requested start by more than five minutes")
    status = event.get("status") if isinstance(event.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    state = str(status_type.get("state") or status_type.get("name") or status_type.get("description") or "").lower()
    completed = status_type.get("completed") is True
    if completed or any(token in state for token in ("in progress", "final", "post", "complete")):
        raise SpreadChallengerUnavailable("WNBA_SPREAD_FORWARD_EVENT_NOT_PREGAME", "ESPN event is not pregame")
    if target <= datetime.now(timezone.utc):
        raise SpreadChallengerUnavailable("WNBA_SPREAD_FORWARD_EVENT_NOT_PREGAME", "requested WNBA event start is not in the future")
    return {
        "event_id": f"espn-{raw_event_id}",
        "event_start_time": event_time.isoformat(),
        "home_team_id": actual_home,
        "away_team_id": actual_away,
        "identity_provider": "ESPN_SCOREBOARD",
        "identity_source": ESPN_BASE_URLS["WNBA"],
        "identity_verified_at": datetime.now(timezone.utc).isoformat(),
        "market_features_used": False,
        "can_execute": False,
    }


__all__ = ["resolve_wnba_current_event_identity"]
