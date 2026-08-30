"""Automatic governed evidence hydration for certified prop routes.

P0 starts with the one prop route that currently has a certified fitted model:
MLB PITCHER_STRIKEOUTS. Evidence is acquired from the official MLB StatsAPI,
written to the backend-only wow_prop_evidence_snapshots ledger, then validated
by the existing wow_prop_evidence_snapshot RPC before model scoring.

This module never calculates or supplies a model probability and never enables
execution. Unsupported routes fail closed so model coverage remains explicit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
import math
import time
import unicodedata
import uuid

import httpx


MLB_STATS_API_BASE = "https://statsapi.mlb.com/api/v1"
AUTO_HYDRATION_PROVIDER = "MLB_STATS_API_OFFICIAL_V1"
AUTO_HYDRATION_EVIDENCE_VERSION = "PROP_EVIDENCE_V1"
HTTP_TIMEOUT_SECONDS = 8.0
HTTP_ATTEMPTS = 2
MIN_STARTS = 10


class PropAutoHydrationError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _aware(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PropAutoHydrationError("PROP_EVENT_START_INVALID", "event_start_time must be timezone-aware ISO 8601") from exc
    if parsed.utcoffset() is None:
        raise PropAutoHydrationError("PROP_EVENT_START_INVALID", "event_start_time must include a timezone")
    return parsed.astimezone(timezone.utc)


def _name_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(ascii_text.casefold().strip().split())


def _int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _outs_from_ip(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        raise PropAutoHydrationError("MLB_GAME_LOG_INVALID", "inningsPitched missing")
    if "." not in text:
        whole, frac = text, "0"
    else:
        whole, frac = text.split(".", 1)
    if frac not in {"0", "1", "2"}:
        raise PropAutoHydrationError("MLB_GAME_LOG_INVALID", f"invalid inningsPitched fraction: {text}")
    try:
        return int(whole) * 3 + int(frac)
    except ValueError as exc:
        raise PropAutoHydrationError("MLB_GAME_LOG_INVALID", f"invalid inningsPitched: {text}") from exc


def _request_json(
    url: str,
    *,
    params: dict[str, Any],
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            response = http_get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("response JSON was not an object")
            return payload
        except Exception as exc:  # network/provider failure; retry is bounded
            errors.append(type(exc).__name__)
            if attempt < HTTP_ATTEMPTS:
                time.sleep(0.05)
    raise PropAutoHydrationError(
        "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE",
        "official MLB evidence source could not be retrieved",
        detail={"source": AUTO_HYDRATION_PROVIDER, "attempts": HTTP_ATTEMPTS, "errors": errors},
    )


def _resolve_player_id(player: str, *, http_get: Callable[..., Any]) -> tuple[int, str]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/people/search",
        params={"names": player, "active": "true", "sportIds": "1"},
        http_get=http_get,
    )
    people = payload.get("people")
    if not isinstance(people, list):
        people = []
    exact = [person for person in people if isinstance(person, dict) and _name_key(person.get("fullName")) == _name_key(player)]
    if len(exact) != 1:
        raise PropAutoHydrationError(
            "PROP_PLAYER_IDENTITY_UNRESOLVED",
            "official MLB player search did not produce one exact active match",
            detail={"player": player, "exact_match_count": len(exact)},
        )
    person_id = _int(exact[0].get("id"))
    if person_id <= 0:
        raise PropAutoHydrationError("PROP_PLAYER_IDENTITY_UNRESOLVED", "official MLB player ID was missing")
    return person_id, str(exact[0].get("fullName") or player)


def _game_log(
    player_id: int,
    *,
    season: int,
    event_start: datetime,
    http_get: Callable[..., Any],
) -> tuple[list[int], list[dict[str, Any]]]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/people/{player_id}/stats",
        params={"stats": "gameLog", "group": "pitching", "season": str(season), "gameType": "R"},
        http_get=http_get,
    )
    stats = payload.get("stats")
    splits: list[Any] = []
    if isinstance(stats, list):
        for block in stats:
            if isinstance(block, dict) and isinstance(block.get("splits"), list):
                splits.extend(block["splits"])

    parsed: list[tuple[str, dict[str, Any]]] = []
    for split in splits:
        if not isinstance(split, dict):
            continue
        stat = split.get("stat")
        if not isinstance(stat, dict) or _int(stat.get("gamesStarted")) < 1:
            continue
        date_value = str(split.get("date") or "")
        try:
            game_date = datetime.fromisoformat(date_value).date()
        except ValueError:
            continue
        if game_date >= event_start.date():
            continue
        ip = stat.get("inningsPitched")
        so = _int(stat.get("strikeOuts"), default=-1)
        if so < 0:
            continue
        opponent = split.get("opponent") if isinstance(split.get("opponent"), dict) else {}
        row = {
            "date": date_value,
            "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
            "ip": str(ip),
            "outs": _outs_from_ip(ip),
            "so": so,
            "bb": _int(stat.get("baseOnBalls")),
            "er": _int(stat.get("earnedRuns")),
        }
        parsed.append((date_value, row))

    parsed.sort(key=lambda item: item[0], reverse=True)
    recent = [row for _, row in parsed[:MIN_STARTS]]
    if len(recent) < MIN_STARTS:
        raise PropAutoHydrationError(
            "MLB_RECENT_STARTS_INSUFFICIENT",
            "fewer than ten official regular-season starts were available before the event",
            detail={"starts_found": len(recent), "required": MIN_STARTS},
        )
    return [row["so"] for row in recent], recent


def _schedule_context(
    player_id: int,
    *,
    event_start: datetime,
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    start_date = (event_start.date() - timedelta(days=1)).isoformat()
    end_date = event_start.date().isoformat()
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/schedule",
        params={
            "sportId": "1",
            "startDate": start_date,
            "endDate": end_date,
            "hydrate": "probablePitcher,team,venue",
        },
        http_get=http_get,
    )
    candidates: list[tuple[float, dict[str, Any], str]] = []
    dates = payload.get("dates")
    if not isinstance(dates, list):
        dates = []
    for date_block in dates:
        games = date_block.get("games") if isinstance(date_block, dict) else None
        if not isinstance(games, list):
            continue
        for game in games:
            if not isinstance(game, dict):
                continue
            teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
            for side, opponent_side in (("home", "away"), ("away", "home")):
                side_data = teams.get(side) if isinstance(teams.get(side), dict) else {}
                probable = side_data.get("probablePitcher") if isinstance(side_data.get("probablePitcher"), dict) else {}
                if _int(probable.get("id")) != player_id:
                    continue
                try:
                    game_start = _aware(str(game.get("gameDate") or ""))
                except PropAutoHydrationError:
                    continue
                delta = abs((game_start - event_start).total_seconds())
                candidates.append((delta, game, side))

    if not candidates:
        raise PropAutoHydrationError(
            "MLB_STARTER_STATUS_UNRESOLVED",
            "official MLB schedule did not list the player as a probable pitcher for the target event window",
            detail={"player_id": player_id, "start_date": start_date, "end_date": end_date},
        )
    candidates.sort(key=lambda item: item[0])
    delta, game, side = candidates[0]
    if delta > 12 * 60 * 60:
        raise PropAutoHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "official probable-pitcher game was too far from the requested event start",
            detail={"start_delta_seconds": delta},
        )

    teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
    other_side = "away" if side == "home" else "home"
    team_node = teams.get(side) if isinstance(teams.get(side), dict) else {}
    opp_node = teams.get(other_side) if isinstance(teams.get(other_side), dict) else {}
    team = team_node.get("team") if isinstance(team_node.get("team"), dict) else {}
    opponent = opp_node.get("team") if isinstance(opp_node.get("team"), dict) else {}
    venue = game.get("venue") if isinstance(game.get("venue"), dict) else {}
    status = game.get("status") if isinstance(game.get("status"), dict) else {}
    return {
        "official_game_pk": game.get("gamePk"),
        "official_game_date": game.get("gameDate"),
        "side": side.upper(),
        "team": team.get("abbreviation") or team.get("name") or "UNKNOWN",
        "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
        "venue": venue.get("name") or "UNKNOWN",
        "schedule_status": status.get("detailedState") or status.get("abstractGameState") or "UNKNOWN",
        "starter_status": "STARTER_PROBABLE_OFFICIAL_SCHEDULE",
    }


def _insert_snapshot(client: Any, payload: dict[str, Any]) -> str:
    try:
        result = client.table("wow_prop_evidence_snapshots").insert(payload).execute()
    except Exception as exc:
        raise PropAutoHydrationError(
            "PROP_EVIDENCE_WRITE_UNAVAILABLE",
            "governed evidence snapshot could not be persisted",
            detail={"error_type": type(exc).__name__},
        ) from exc
    rows = result.data or []
    if not rows or not isinstance(rows[0], dict) or not rows[0].get("source_snapshot_id"):
        raise PropAutoHydrationError("PROP_EVIDENCE_WRITE_UNPROVEN", "evidence insert did not return a snapshot ID")
    return str(rows[0]["source_snapshot_id"])


def auto_hydrate_prop_candidate(
    req: Any,
    *,
    client: Any,
    http_get: Callable[..., Any] = httpx.get,
    now: Optional[datetime] = None,
    board_source: Optional[str] = None,
    board_capture: Optional[str] = None,
) -> dict[str, Any]:
    """Create a governed evidence snapshot for one supported candidate."""
    sport = str(req.sport or "").upper()
    stat_type = str(req.stat_type or "").upper()
    if sport != "MLB" or stat_type != "PITCHER_STRIKEOUTS":
        raise PropAutoHydrationError(
            "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
            "automatic evidence hydration is not certified for this sport/stat route",
            detail={"sport": sport, "stat_type": stat_type},
        )
    player = str(req.player or "").strip()
    if not player:
        raise PropAutoHydrationError("PROP_PLAYER_IDENTITY_REQUIRED", "player is required for prop hydration")

    captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = _aware(req.event_start_time)
    if event_start <= captured_at:
        raise PropAutoHydrationError("EVENT_ALREADY_STARTED", "pregame evidence cannot be hydrated after event start")

    player_id, official_name = _resolve_player_id(player, http_get=http_get)
    game_log, box_score_log = _game_log(
        player_id,
        season=event_start.year,
        event_start=event_start,
        http_get=http_get,
    )
    schedule = _schedule_context(player_id, event_start=event_start, http_get=http_get)

    snapshot_id = str(uuid.uuid4())
    timestamp = captured_at.isoformat()
    role_status = {
        "status": schedule["starter_status"],
        "role": "STARTING_PITCHER",
        "confirmation_strength": "OFFICIAL_PROBABLE_PITCHER",
        "team": schedule["team"],
        "opponent": schedule["opponent"],
        "venue": schedule["venue"],
        "official_game_pk": schedule["official_game_pk"],
        "official_game_date": schedule["official_game_date"],
        "schedule_status": schedule["schedule_status"],
        "source": "MLB StatsAPI official schedule/probablePitcher",
    }
    opportunity_ledger = {
        "status": "READY",
        "game_log_stat": "pitcher strikeouts",
        "box_score_alignment": "1:1",
        "regular_season_prior_starts": len(box_score_log),
        "starter_confirmation": "OFFICIAL_PROBABLE_PITCHER",
        "model_opponent_context": "NEUTRAL_UNTIL_LINEUP_HYDRATED",
    }
    source_timestamps = {
        "auto_hydration_provider": AUTO_HYDRATION_PROVIDER,
        "official_player_search_source": f"{MLB_STATS_API_BASE}/people/search",
        "official_game_log_source": f"{MLB_STATS_API_BASE}/people/{player_id}/stats",
        "official_schedule_source": f"{MLB_STATS_API_BASE}/schedule",
        "official_sources_checked_at": timestamp,
        "board_source": board_source or "UNSPECIFIED_NORMALIZED_INPUT",
        "board_capture": board_capture or timestamp,
    }
    payload = {
        "source_snapshot_id": snapshot_id,
        "captured_at": timestamp,
        "event_id": req.event_id,
        "event_start_time": event_start.isoformat(),
        "sport": sport,
        "player": official_name,
        "stat_type": stat_type,
        "line": float(req.line),
        "game_log": game_log,
        "box_score_log": box_score_log,
        "role_status": role_status,
        "role_timestamp": timestamp,
        "opportunity_ledger": opportunity_ledger,
        "source_timestamps": source_timestamps,
        "hydration_status": "PASS",
        "blockers": [],
        "evidence_version": AUTO_HYDRATION_EVIDENCE_VERSION,
        "can_execute": False,
    }
    persisted_id = _insert_snapshot(client, payload)
    if persisted_id != snapshot_id:
        # Keep caller/server identity deterministic; a mismatched returned ID is
        # treated as an unproven write rather than silently switching evidence.
        raise PropAutoHydrationError(
            "PROP_EVIDENCE_WRITE_IDENTITY_MISMATCH",
            "persisted evidence snapshot ID differed from the requested ID",
        )
    return {
        "ok": True,
        "code": "PROP_AUTO_HYDRATION_WRITTEN",
        "source_snapshot_id": persisted_id,
        "provider": AUTO_HYDRATION_PROVIDER,
        "player_id": player_id,
        "official_player_name": official_name,
        "official_game_pk": schedule["official_game_pk"],
        "starter_status": schedule["starter_status"],
        "historical_start_count": len(box_score_log),
        "captured_at": timestamp,
        "probability_publishable": False,
        "can_execute": False,
    }
