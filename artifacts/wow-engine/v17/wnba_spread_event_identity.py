"""Identity-only current WNBA event verification for spread shadow scoring."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from basketball_event_hydration_runtime import ESPN_BASE_URLS, _espn_competitors
from v17.spread_margin_challenger import SpreadChallengerUnavailable, _dt
from v17.wnba_team_identity_aliases import espn_team_id_for_wnba_stats_tricode


def _resolve_wnba_stats_event_identity(
    *,
    event_id: str,
    event_start_time: str,
    home_team_id: str,
    away_team_id: str,
    fetcher: Callable[..., Any],
) -> dict[str, Any]:
    from v17 import wnba_prop_evidence_control_plane as control

    target = _dt(event_start_time)
    raw_event_id = str(event_id).removeprefix("wnba-stats-").strip()
    if not raw_event_id or not str(home_team_id).startswith("espn-") or not str(away_team_id).startswith("espn-"):
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_STATS_IDENTITY_REQUIRED",
            "WNBA Stats event identity plus ESPN-keyed team aliases are required",
        )
    local_day = target.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    identity_provider = "WNBA_STATS_SCOREBOARD_V3"
    identity_source = str(getattr(control, "STATS_SCOREBOARD_URL", ""))
    try:
        schedule = control._scoreboard_schedule_for_date(local_day, http_get=fetcher)
    except Exception as stats_exc:  # noqa: BLE001
        stats_code = str(getattr(stats_exc, "code", "") or type(stats_exc).__name__)
        try:
            schedule = control._livedata_schedule_for_date(local_day, http_get=fetcher)
            identity_provider = str(
                getattr(control, "LIVEDATA_SCOREBOARD_PROVIDER", "WNBA_LIVEDATA_SCOREBOARD_10_RENDER_RECOVERY")
            )
            identity_source = str(getattr(control, "LIVEDATA_SCOREBOARD_URL", ""))
        except Exception as live_exc:  # noqa: BLE001
            live_code = str(getattr(live_exc, "code", "") or type(live_exc).__name__)
            raise SpreadChallengerUnavailable(
                "WNBA_SPREAD_FORWARD_IDENTITY_SOURCE_UNAVAILABLE",
                f"official WNBA identity re-verification failed: stats={stats_code}; livedata={live_code}",
            ) from live_exc
    league = schedule.get("leagueSchedule") if isinstance(schedule, dict) else None
    blocks = league.get("gameDates") if isinstance(league, dict) else None
    games: list[dict[str, Any]] = []
    if isinstance(blocks, list):
        for block in blocks:
            rows = block.get("games") if isinstance(block, dict) else None
            if isinstance(rows, list):
                games.extend(row for row in rows if isinstance(row, dict))
    matches = [game for game in games if str(game.get("gameId") or "").strip() == raw_event_id]
    if len(matches) != 1:
        code = "WNBA_SPREAD_FORWARD_EVENT_IDENTITY_AMBIGUOUS" if len(matches) > 1 else "WNBA_SPREAD_FORWARD_EVENT_NOT_FOUND"
        raise SpreadChallengerUnavailable(code, "exact WNBA Stats event identity could not be resolved")
    game = matches[0]
    home = game.get("homeTeam") if isinstance(game.get("homeTeam"), dict) else {}
    away = game.get("awayTeam") if isinstance(game.get("awayTeam"), dict) else {}
    try:
        actual_home = espn_team_id_for_wnba_stats_tricode(home.get("teamTricode"))
        actual_away = espn_team_id_for_wnba_stats_tricode(away.get("teamTricode"))
    except ValueError as exc:
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_TEAM_IDENTITY_UNMAPPED",
            "WNBA Stats team identity is not in the audited ESPN alias registry",
        ) from exc
    if actual_home != str(home_team_id) or actual_away != str(away_team_id):
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_TEAM_IDENTITY_MISMATCH",
            "WNBA Stats event teams do not match requested governed ESPN aliases",
        )
    event_time = _dt(game.get("gameDateTimeUTC") or game.get("gameDateUTC"))
    if abs((event_time - target).total_seconds()) > 300:
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_EVENT_TIME_MISMATCH",
            "WNBA Stats event start differs from requested start by more than five minutes",
        )
    try:
        game_status = int(game.get("gameStatus") or 0)
    except (TypeError, ValueError):
        game_status = 0
    if game_status != 1 or target <= datetime.now(timezone.utc):
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_EVENT_NOT_PREGAME",
            "WNBA Stats event is not a future pregame event",
        )
    return {
        "event_id": f"wnba-stats-{raw_event_id}",
        "event_start_time": event_time.isoformat(),
        "home_team_id": actual_home,
        "away_team_id": actual_away,
        "identity_provider": identity_provider,
        "identity_source": identity_source,
        "identity_verified_at": datetime.now(timezone.utc).isoformat(),
        "market_features_used": False,
        "can_execute": False,
    }


def _canonical_official_event_for_espn_alias(
    *,
    target: datetime,
    home_team_id: str,
    away_team_id: str,
    fetcher: Callable[..., Any],
) -> dict[str, Any]:
    """Resolve an ESPN discovery alias to exactly one league-owned WNBA game."""
    from v17 import wnba_official_schedule_web_fallback as official

    try:
        payload = official._request_json_once(
            official.SCHEDULE_API_URL,
            http_get=fetcher,
            headers=official._api_headers(),
            params=official._schedule_api_params(),
        )
        schedule = official._validate_official_schedule_payload(payload)
    except Exception as exc:  # noqa: BLE001 - preserve typed canonical-source boundary
        code = str(getattr(exc, "code", "") or type(exc).__name__)
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_CANONICAL_SOURCE_UNAVAILABLE",
            f"official WNBA schedule reconciliation failed: {code}",
        ) from exc

    league = schedule.get("leagueSchedule") if isinstance(schedule, dict) else None
    blocks = league.get("gameDates") if isinstance(league, dict) else None
    matches: list[dict[str, Any]] = []
    if isinstance(blocks, list):
        for block in blocks:
            games = block.get("games") if isinstance(block, dict) else None
            if not isinstance(games, list):
                continue
            for game in games:
                if not isinstance(game, dict):
                    continue
                home = game.get("homeTeam") if isinstance(game.get("homeTeam"), dict) else {}
                away = game.get("awayTeam") if isinstance(game.get("awayTeam"), dict) else {}
                try:
                    official_home = espn_team_id_for_wnba_stats_tricode(home.get("teamTricode"))
                    official_away = espn_team_id_for_wnba_stats_tricode(away.get("teamTricode"))
                    official_start = _dt(game.get("gameDateTimeUTC") or game.get("gameDateUTC"))
                except (ValueError, TypeError):
                    continue
                if official_home != str(home_team_id) or official_away != str(away_team_id):
                    continue
                if abs((official_start - target).total_seconds()) > 300:
                    continue
                game_id = str(game.get("gameId") or "").strip()
                if game_id:
                    matches.append({
                        "game_id": game_id,
                        "event_start_time": official_start.isoformat(),
                    })

    if len(matches) != 1:
        code = (
            "WNBA_SPREAD_FORWARD_CANONICAL_IDENTITY_AMBIGUOUS"
            if len(matches) > 1
            else "WNBA_SPREAD_FORWARD_CANONICAL_EVENT_NOT_FOUND"
        )
        raise SpreadChallengerUnavailable(
            code,
            "ESPN WNBA alias did not reconcile to exactly one official WNBA schedule event",
        )
    return matches[0]


def resolve_wnba_current_event_identity(*, event_id: str, event_start_time: str,
                                        home_team_id: str, away_team_id: str,
                                        fetcher: Callable[..., Any] = httpx.get,
                                        official_fetcher: Callable[..., Any] | None = None) -> dict[str, Any]:
    if str(event_id).startswith("wnba-stats-"):
        return _resolve_wnba_stats_event_identity(
            event_id=event_id,
            event_start_time=event_start_time,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            fetcher=fetcher,
        )

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

    canonical = _canonical_official_event_for_espn_alias(
        target=event_time,
        home_team_id=actual_home,
        away_team_id=actual_away,
        fetcher=official_fetcher or fetcher,
    )
    return {
        "event_id": f"wnba-stats-{canonical['game_id']}",
        "provider_event_alias": f"espn-{raw_event_id}",
        "event_start_time": canonical["event_start_time"],
        "home_team_id": actual_home,
        "away_team_id": actual_away,
        "identity_provider": "WNBA_OFFICIAL_SCHEDULE_API",
        "identity_source": "https://www.wnba.com/api/schedule",
        "identity_alias_provider": "ESPN_SCOREBOARD",
        "identity_verified_at": datetime.now(timezone.utc).isoformat(),
        "market_features_used": False,
        "can_execute": False,
    }


__all__ = ["resolve_wnba_current_event_identity"]
