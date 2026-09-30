"""Backend-owned real-event discovery for NFL/WNBA spread forward canaries.

GitHub-hosted runners must not depend directly on public scoreboard availability.
NFL discovery is bound to the frozen canonical schedule snapshot consumed by the
NFL specialist. WNBA prefers ESPN for backward-compatible event aliases, but
falls back to the league-owned WNBA Stats scoreboard when ESPN is unavailable.
The fallback maps official team tricodes onto the stable ESPN team identifiers
used by the governed historical WNBA feature corpus. Neither path changes fitted
model math, calibration, certification, publication, promotion, or execution
authority.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Literal, Mapping
from zoneinfo import ZoneInfo

import requests

from v17.nfl_team_event_specialist import _load_latest_schedule_snapshot
from v17.spread_forward_shadow_leagues import run_nfl_forward_shadow, run_wnba_forward_shadow
from v17.spread_margin_challenger import SpreadChallengerUnavailable
from v17.wnba_spread_event_identity import WNBA_STATS_TRICODE_TO_ESPN_ID

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True

ESPN_SCOREBOARDS = {
    "WNBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard",
}
DEFAULT_HORIZON_DAYS = {"NFL": 14, "WNBA": 15}
NFL_SCHEDULE_TIMEZONE = ZoneInfo("America/New_York")
WNBA_SCHEDULE_TIMEZONE = ZoneInfo("America/New_York")


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


def _nfl_event_start(row: dict[str, Any]) -> datetime | None:
    raw_day = str(row.get("gameday") or "").strip()[:10]
    if not raw_day:
        return None
    try:
        day = datetime.fromisoformat(raw_day).date()
    except ValueError:
        return None
    raw_time = str(row.get("gametime") or "").strip()
    parsed_time = time(12, 0)
    if raw_time:
        try:
            hour, minute = raw_time[:5].split(":", 1)
            parsed_time = time(int(hour), int(minute))
        except (TypeError, ValueError):
            return None
    return datetime.combine(day, parsed_time, tzinfo=NFL_SCHEDULE_TIMEZONE).astimezone(timezone.utc)


def discover_future_nfl_canonical_event(
    db: Any,
    *,
    now: datetime | None = None,
    horizon_days: int | None = None,
) -> dict[str, Any] | None:
    """Return the first future unscored event from the governed NFL schedule snapshot."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    horizon = int(horizon_days or DEFAULT_HORIZON_DAYS["NFL"])
    try:
        snapshot, schedule_rows = _load_latest_schedule_snapshot(db)
    except Exception as exc:  # noqa: BLE001 - preserve a typed acquisition blocker
        raise SpreadChallengerUnavailable(
            "NFL_SPREAD_CANARY_CANONICAL_SCHEDULE_UNAVAILABLE",
            f"governed canonical NFL schedule snapshot could not be loaded: {type(exc).__name__}",
        ) from exc

    candidates: list[tuple[datetime, dict[str, Any]]] = []
    deadline = current + timedelta(days=max(horizon, 1))
    for row in schedule_rows:
        if str(row.get("home_score") or "").strip() or str(row.get("away_score") or "").strip():
            continue
        event_start = _nfl_event_start(row)
        if event_start is None or event_start <= current or event_start > deadline:
            continue
        game_id = str(row.get("game_id") or "").strip()
        home = str(row.get("home_team") or "").strip().upper()
        away = str(row.get("away_team") or "").strip().upper()
        if not game_id or not home or not away:
            continue
        candidates.append((event_start, row))

    if not candidates:
        return None
    event_start, row = min(candidates, key=lambda item: (item[0], str(item[1].get("game_id") or "")))
    return {
        "sport": "NFL",
        "raw_event_id": str(row["game_id"]).strip(),
        "event_start_time": event_start.isoformat(),
        "home_team": str(row["home_team"]).strip().upper(),
        "away_team": str(row["away_team"]).strip().upper(),
        "identity_provider": "NFL_CANONICAL_SCHEDULE_SNAPSHOT",
        "identity_acquisition_location": "BACKEND_RUNTIME",
        "canonical_source_snapshot_id": str(snapshot.get("snapshot_id") or ""),
        "schedule_content_sha256": str(snapshot.get("content_sha256") or ""),
        "can_execute": False,
    }


def discover_future_espn_event(
    sport: Literal["WNBA"],
    *,
    fetcher: Callable[..., Any] = requests.get,
    now: datetime | None = None,
    horizon_days: int | None = None,
) -> dict[str, Any] | None:
    """Return the first exact future pregame WNBA ESPN event, or None after valid empty responses."""
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


def _official_schedule_for_date(
    requested_date: str,
    *,
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    from v17.wnba_prop_evidence_control_plane import _scoreboard_schedule_for_date

    return _scoreboard_schedule_for_date(requested_date, http_get=http_get)


def _official_schedule_games(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
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


def _official_team_identity(node: Any) -> tuple[str, str, str]:
    if not isinstance(node, Mapping):
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_CANARY_OFFICIAL_TEAM_IDENTITY_INVALID",
            "official WNBA scoreboard team identity was missing",
        )
    tricode = str(node.get("teamTricode") or node.get("teamTriCode") or "").strip().upper()
    official_id = str(node.get("teamId") or "").strip()
    espn_id = WNBA_STATS_TRICODE_TO_ESPN_ID.get(tricode)
    if not tricode or not official_id or not espn_id:
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_CANARY_OFFICIAL_TEAM_ALIAS_UNAVAILABLE",
            f"official WNBA team alias could not be mapped: {tricode or 'UNKNOWN'}",
        )
    name = " ".join(
        part for part in (
            str(node.get("teamCity") or "").strip(),
            str(node.get("teamName") or "").strip(),
        ) if part
    ) or tricode
    return espn_id, official_id, name


def discover_future_wnba_official_event(
    *,
    fetcher: Callable[..., Any] = requests.get,
    now: datetime | None = None,
    horizon_days: int | None = None,
) -> dict[str, Any] | None:
    """Discover a future WNBA event from the league-owned Stats scoreboard."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    horizon = int(horizon_days or DEFAULT_HORIZON_DAYS["WNBA"])
    local_date = current.astimezone(WNBA_SCHEDULE_TIMEZONE).date()
    successful_response = False
    failures: list[str] = []
    candidates: list[tuple[datetime, dict[str, Any]]] = []

    for offset in range(max(horizon, 1)):
        day = (local_date + timedelta(days=offset)).isoformat()
        try:
            payload = _official_schedule_for_date(day, http_get=fetcher)
        except Exception as exc:  # noqa: BLE001 - converted to typed acquisition failure
            failures.append(str(getattr(exc, "code", "") or type(exc).__name__))
            continue
        successful_response = True
        for game in _official_schedule_games(payload):
            game_id = str(game.get("gameId") or game.get("gameID") or "").strip()
            try:
                start = _utc(game.get("gameDateTimeUTC") or game.get("gameTimeUTC") or game.get("gameDateUTC"))
                status = int(game.get("gameStatus") or 0)
            except (TypeError, ValueError):
                continue
            if not game_id or start <= current or status not in {0, 1}:
                continue
            home_espn, home_official, home_name = _official_team_identity(game.get("homeTeam"))
            away_espn, away_official, away_name = _official_team_identity(game.get("awayTeam"))
            candidates.append((start, {
                "sport": "WNBA",
                "raw_event_id": game_id,
                "event_start_time": start.isoformat(),
                "home_team": home_name,
                "away_team": away_name,
                "home_team_id": home_espn,
                "away_team_id": away_espn,
                "home_official_team_id": home_official,
                "away_official_team_id": away_official,
                "identity_provider": "WNBA_STATS_SCOREBOARD_V3",
                "identity_acquisition_location": "BACKEND_RUNTIME",
                "can_execute": False,
            }))

    if candidates:
        return min(candidates, key=lambda item: (item[0], item[1]["raw_event_id"]))[1]
    if successful_response:
        return None
    detail = failures[-1] if failures else "NO_RESPONSE"
    raise SpreadChallengerUnavailable(
        "WNBA_SPREAD_CANARY_OFFICIAL_IDENTITY_SOURCE_UNAVAILABLE",
        f"official WNBA schedule acquisition failed: {detail}",
    )


def _deferred(sport: str, *, provider: str | None = None) -> dict[str, Any]:
    identity_provider = provider or (
        "NFL_CANONICAL_SCHEDULE_SNAPSHOT" if sport == "NFL" else "ESPN_SCOREBOARD"
    )
    return {
        "status": "DEFERRED_WITH_JUSTIFICATION",
        "code": f"{sport}_SPREAD_CANARY_NO_ELIGIBLE_FUTURE_EVENT",
        "sport": sport,
        "identity_provider": identity_provider,
        "identity_acquisition_location": "BACKEND_RUNTIME",
        **_governance(),
    }


def run_nfl_spread_auto_canary(
    db: Any,
    *,
    fetcher: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    # ``fetcher`` is retained for backward-compatible call signatures only; NFL
    # discovery is intentionally canonical-snapshot bound and does not use ESPN.
    _ = fetcher
    event = discover_future_nfl_canonical_event(db)
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
        "identity_provider": "NFL_CANONICAL_SCHEDULE_SNAPSHOT",
        "identity_acquisition_location": "BACKEND_RUNTIME",
        **_governance(),
    }


def run_wnba_spread_auto_canary(
    db: Any,
    *,
    fetcher: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    provider = "ESPN_SCOREBOARD"
    try:
        event = discover_future_espn_event("WNBA", fetcher=fetcher)
    except SpreadChallengerUnavailable as exc:
        if exc.code != "WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE":
            raise
        provider = "WNBA_STATS_SCOREBOARD_V3"
        event = discover_future_wnba_official_event(fetcher=fetcher)
    if event is None:
        return _deferred("WNBA", provider=provider)

    provider = str(event.get("identity_provider") or provider)
    if provider == "ESPN_SCOREBOARD":
        forward_event_id = f"espn-{event['raw_event_id']}"
    elif provider == "WNBA_STATS_SCOREBOARD_V3":
        forward_event_id = f"wnba-stats-{event['raw_event_id']}"
    else:
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_CANARY_IDENTITY_PROVIDER_INVALID",
            f"unsupported WNBA identity provider: {provider}",
        )
    result = run_wnba_forward_shadow(
        db,
        event_id=forward_event_id,
        event_start_time=event["event_start_time"],
        home_team_id=f"espn-{event['home_team_id']}",
        away_team_id=f"espn-{event['away_team_id']}",
        home_spread=0.0,
    )
    return {
        **result,
        "canary_identity": event,
        "identity_provider": provider,
        "identity_acquisition_location": "BACKEND_RUNTIME",
        **_governance(),
    }


__all__ = [
    "discover_future_espn_event",
    "discover_future_nfl_canonical_event",
    "discover_future_wnba_official_event",
    "run_nfl_spread_auto_canary",
    "run_wnba_spread_auto_canary",
]
