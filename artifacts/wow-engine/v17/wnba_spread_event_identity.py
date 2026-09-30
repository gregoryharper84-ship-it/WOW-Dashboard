"""Identity-only current WNBA event verification for spread shadow scoring.

ESPN remains the preferred identity transport for backward compatibility with the
historical WNBA feature store. When that public transport is unavailable, the
resolver may verify the same event against the league-owned WNBA Stats scoreboard
using exact start time plus home/away team identity. The fallback changes identity
transport only; it does not change fitted-model math, calibration, publication,
or execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import requests

from basketball_event_hydration_runtime import ESPN_BASE_URLS, _espn_competitors
from v17.spread_margin_challenger import SpreadChallengerUnavailable, _dt


# League-owned Stats tricodes mapped to the stable ESPN team IDs used by the
# governed WNBA historical feature corpus. Expansion teams are included so an
# unsupported-history failure remains a model-data failure rather than an
# identity-transport failure.
WNBA_STATS_TRICODE_TO_ESPN_ID: dict[str, str] = {
    "ATL": "20",
    "CHI": "19",
    "CON": "18",
    "DAL": "3",
    "GSV": "129689",
    "IND": "5",
    "LVA": "17",
    "LAS": "6",
    "MIN": "8",
    "NYL": "9",
    "PHX": "11",
    "PDX": "132052",
    "SEA": "14",
    "TOR": "131935",
    "WAS": "16",
}
WNBA_ESPN_ID_TO_STATS_TRICODE = {
    espn_id: tricode for tricode, espn_id in WNBA_STATS_TRICODE_TO_ESPN_ID.items()
}


def _official_schedule_for_date(
    requested_date: str,
    *,
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    # Lazy import avoids pulling the WNBA evidence control plane into web-process
    # construction. The helper already normalizes the league-owned Scoreboard V3
    # payload into the governed schedule contract and whitelists identity fields.
    from v17.wnba_prop_evidence_control_plane import _scoreboard_schedule_for_date

    return _scoreboard_schedule_for_date(requested_date, http_get=http_get)


def _schedule_games(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    league = payload.get("leagueSchedule")
    dates = league.get("gameDates") if isinstance(league, Mapping) else None
    if not isinstance(dates, list):
        return []
    games: list[dict[str, Any]] = []
    for date_row in dates:
        if not isinstance(date_row, Mapping):
            continue
        rows = date_row.get("games")
        if isinstance(rows, list):
            games.extend(row for row in rows if isinstance(row, dict))
    return games


def _stats_tricode(team: Any) -> str:
    if not isinstance(team, Mapping):
        return ""
    return str(team.get("teamTricode") or team.get("teamTriCode") or "").strip().upper()


def _resolve_from_official_scoreboard(
    *,
    event_id: str,
    event_start_time: str,
    home_team_id: str,
    away_team_id: str,
    fetcher: Callable[..., Any],
) -> dict[str, Any]:
    target = _dt(event_start_time)
    home_espn = str(home_team_id).removeprefix("espn-").strip()
    away_espn = str(away_team_id).removeprefix("espn-").strip()
    home_tricode = WNBA_ESPN_ID_TO_STATS_TRICODE.get(home_espn)
    away_tricode = WNBA_ESPN_ID_TO_STATS_TRICODE.get(away_espn)
    if not home_tricode or not away_tricode:
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_OFFICIAL_TEAM_ALIAS_UNAVAILABLE",
            "requested WNBA team identity cannot be mapped to the official schedule",
        )

    local_day = target.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    try:
        payload = _official_schedule_for_date(local_day, http_get=fetcher)
    except Exception as exc:  # noqa: BLE001 - preserve typed identity failure
        code = str(getattr(exc, "code", "") or "WNBA_SPREAD_FORWARD_OFFICIAL_IDENTITY_SOURCE_UNAVAILABLE")
        raise SpreadChallengerUnavailable(
            code,
            "official WNBA identity schedule acquisition failed",
        ) from exc

    explicit_stats_id = (
        str(event_id).removeprefix("wnba-stats-").strip()
        if str(event_id).startswith("wnba-stats-")
        else ""
    )
    matches: list[tuple[dict[str, Any], datetime]] = []
    for game in _schedule_games(payload):
        game_id = str(game.get("gameId") or game.get("gameID") or "").strip()
        if explicit_stats_id and game_id != explicit_stats_id:
            continue
        try:
            start = _dt(game.get("gameDateTimeUTC") or game.get("gameTimeUTC") or game.get("gameDateUTC"))
        except Exception:
            continue
        if abs((start - target).total_seconds()) > 300:
            continue
        if _stats_tricode(game.get("homeTeam")) != home_tricode:
            continue
        if _stats_tricode(game.get("awayTeam")) != away_tricode:
            continue
        try:
            status = int(game.get("gameStatus") or 0)
        except (TypeError, ValueError):
            continue
        if status not in {0, 1}:
            continue
        matches.append((game, start))

    if len(matches) != 1:
        code = (
            "WNBA_SPREAD_FORWARD_EVENT_IDENTITY_AMBIGUOUS"
            if len(matches) > 1
            else "WNBA_SPREAD_FORWARD_EVENT_NOT_FOUND"
        )
        raise SpreadChallengerUnavailable(
            code,
            "exact official WNBA event identity could not be resolved",
        )

    game, event_time = matches[0]
    if event_time <= datetime.now(timezone.utc):
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_EVENT_NOT_PREGAME",
            "requested WNBA event start is not in the future",
        )
    game_id = str(game.get("gameId") or game.get("gameID") or "").strip()
    return {
        "event_id": f"wnba-stats-{game_id}",
        "event_start_time": event_time.isoformat(),
        "home_team_id": f"espn-{home_espn}",
        "away_team_id": f"espn-{away_espn}",
        "identity_provider": "WNBA_STATS_SCOREBOARD_V3",
        "identity_source": "WNBA_STATS_SCOREBOARD_V3",
        "identity_verified_at": datetime.now(timezone.utc).isoformat(),
        "market_features_used": False,
        "can_execute": False,
    }


def resolve_wnba_current_event_identity(
    *,
    event_id: str,
    event_start_time: str,
    home_team_id: str,
    away_team_id: str,
    fetcher: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    target = _dt(event_start_time)
    requested_event_id = str(event_id).strip()
    raw_event_id = requested_event_id.removeprefix("espn-").strip()
    if not requested_event_id or not str(home_team_id).startswith("espn-") or not str(away_team_id).startswith("espn-"):
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_ESPN_IDENTITY_REQUIRED",
            "ESPN-prefixed team identities are required by the historical WNBA feature corpus",
        )

    if requested_event_id.startswith("wnba-stats-"):
        return _resolve_from_official_scoreboard(
            event_id=requested_event_id,
            event_start_time=event_start_time,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            fetcher=fetcher,
        )
    if not requested_event_id.startswith("espn-") or not raw_event_id:
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_EVENT_IDENTITY_FORMAT_INVALID",
            "WNBA event identity must be ESPN- or WNBA-Stats-prefixed",
        )

    local_day = target.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    try:
        response = fetcher(
            ESPN_BASE_URLS["WNBA"],
            params={"dates": local_day, "limit": 100},
            headers={"Accept": "application/json", "User-Agent": "WOW-V17-WNBA-Spread-Identity/1.0"},
            timeout=20,
        )
    except Exception:
        return _resolve_from_official_scoreboard(
            event_id=requested_event_id,
            event_start_time=event_start_time,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            fetcher=fetcher,
        )
    if int(getattr(response, "status_code", 0)) != 200:
        return _resolve_from_official_scoreboard(
            event_id=requested_event_id,
            event_start_time=event_start_time,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            fetcher=fetcher,
        )
    try:
        body = response.json()
    except Exception:
        return _resolve_from_official_scoreboard(
            event_id=requested_event_id,
            event_start_time=event_start_time,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            fetcher=fetcher,
        )
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        return _resolve_from_official_scoreboard(
            event_id=requested_event_id,
            event_start_time=event_start_time,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            fetcher=fetcher,
        )
    matches = [event for event in events if isinstance(event, dict) and str(event.get("id") or "") == raw_event_id]
    if len(matches) != 1:
        # An unavailable/stale ESPN event list must not defeat a verifiable
        # official-league identity at the same start time and team pairing.
        return _resolve_from_official_scoreboard(
            event_id=requested_event_id,
            event_start_time=event_start_time,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            fetcher=fetcher,
        )
    event = matches[0]
    home, away = _espn_competitors(event)
    if not isinstance(home, dict) or not isinstance(away, dict):
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_EVENT_IDENTITY_INCOMPLETE",
            "ESPN event is missing home/away competitors",
        )
    home_payload = home.get("team") if isinstance(home.get("team"), dict) else {}
    away_payload = away.get("team") if isinstance(away.get("team"), dict) else {}
    actual_home = f"espn-{str(home_payload.get('id') or '').strip()}"
    actual_away = f"espn-{str(away_payload.get('id') or '').strip()}"
    if actual_home != str(home_team_id) or actual_away != str(away_team_id):
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_TEAM_IDENTITY_MISMATCH",
            "ESPN event teams do not match requested governed identities",
        )
    event_time = _dt(event.get("date"))
    if abs((event_time - target).total_seconds()) > 300:
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_EVENT_TIME_MISMATCH",
            "ESPN event start differs from requested start by more than five minutes",
        )
    status = event.get("status") if isinstance(event.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    state = str(status_type.get("state") or status_type.get("name") or status_type.get("description") or "").lower()
    completed = status_type.get("completed") is True
    if completed or any(token in state for token in ("in progress", "final", "post", "complete")):
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_EVENT_NOT_PREGAME",
            "ESPN event is not pregame",
        )
    if target <= datetime.now(timezone.utc):
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_EVENT_NOT_PREGAME",
            "requested WNBA event start is not in the future",
        )
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


__all__ = [
    "WNBA_ESPN_ID_TO_STATS_TRICODE",
    "WNBA_STATS_TRICODE_TO_ESPN_ID",
    "resolve_wnba_current_event_identity",
]
