"""Automatic raw-evidence acquisition for certified MLB pitcher prop routes.

The producing layer owns official MLB identity, starter/event resolution, prior
start history, and target-specific evidence construction.  It never computes a
probability and never authorizes execution.

Supported governed fitted routes:
- PITCHER_STRIKEOUTS
- PITCHING_OUTS
- STRIKES_THROWN
- BALLS_THROWN

For each route the same official prior-start rows are frozen, while game_log is
target-specific.  Pitch-composition routes require official per-start pitches
and strikes; missing composition fails closed rather than being estimated.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
import time
import unicodedata

import httpx

MLB_STATS_API_BASE = "https://statsapi.mlb.com/api/v1"
AUTO_HYDRATION_PROVIDER = "MLB_STATS_API_OFFICIAL_V1"
AUTO_HYDRATION_EVIDENCE_VERSION = "PROP_EVIDENCE_V1"
HTTP_TIMEOUT_SECONDS = 8.0
HTTP_ATTEMPTS = 2
MIN_STARTS = 10
SUPPORTED_STATS = {
    "PITCHER_STRIKEOUTS",
    "PITCHING_OUTS",
    "STRIKES_THROWN",
    "BALLS_THROWN",
}


class PropAutoHydrationError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _aware(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PropAutoHydrationError(
            "PROP_EVENT_START_INVALID",
            "event_start_time must be timezone-aware ISO 8601",
        ) from exc
    if parsed.utcoffset() is None:
        raise PropAutoHydrationError(
            "PROP_EVENT_START_INVALID",
            "event_start_time must include a timezone",
        )
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
        raise PropAutoHydrationError(
            "MLB_GAME_LOG_INVALID",
            f"invalid inningsPitched fraction: {text}",
        )
    try:
        return int(whole) * 3 + int(frac)
    except ValueError as exc:
        raise PropAutoHydrationError(
            "MLB_GAME_LOG_INVALID",
            f"invalid inningsPitched: {text}",
        ) from exc


def _request_json(url: str, *, params: dict[str, Any], http_get: Callable[..., Any]) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            response = http_get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("response JSON was not an object")
            return payload
        except Exception as exc:
            errors.append(type(exc).__name__)
            if attempt < HTTP_ATTEMPTS:
                time.sleep(0.05)
    raise PropAutoHydrationError(
        "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE",
        "official MLB evidence source could not be retrieved",
        detail={"provider": AUTO_HYDRATION_PROVIDER, "attempts": HTTP_ATTEMPTS, "errors": errors},
    )


def _resolve_player_id(player: str, *, http_get: Callable[..., Any]) -> tuple[int, str]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/people/search",
        params={"names": player, "active": "true", "sportIds": "1"},
        http_get=http_get,
    )
    people = payload.get("people") if isinstance(payload.get("people"), list) else []
    exact = [
        p for p in people
        if isinstance(p, dict) and _name_key(p.get("fullName")) == _name_key(player)
    ]
    if len(exact) != 1:
        raise PropAutoHydrationError(
            "PROP_PLAYER_IDENTITY_UNRESOLVED",
            "official MLB player search did not produce one exact active match",
            detail={"player": player, "exact_match_count": len(exact)},
        )
    player_id = _int(exact[0].get("id"))
    if player_id <= 0:
        raise PropAutoHydrationError("PROP_PLAYER_IDENTITY_UNRESOLVED", "official MLB player ID was missing")
    return player_id, str(exact[0].get("fullName") or player)


def _schedule_context(
    player_id: int,
    *,
    event_start: datetime,
    http_get: Callable[..., Any],
) -> dict[str, Any]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/schedule",
        params={
            "sportId": "1",
            "startDate": (event_start.date() - timedelta(days=1)).isoformat(),
            "endDate": event_start.date().isoformat(),
            "hydrate": "probablePitcher,team,venue",
        },
        http_get=http_get,
    )
    candidates: list[tuple[float, dict[str, Any], str]] = []
    dates = payload.get("dates") if isinstance(payload.get("dates"), list) else []
    for block in dates:
        games = block.get("games") if isinstance(block, dict) and isinstance(block.get("games"), list) else []
        for game in games:
            if not isinstance(game, dict):
                continue
            teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
            for side in ("home", "away"):
                side_data = teams.get(side) if isinstance(teams.get(side), dict) else {}
                probable = side_data.get("probablePitcher") if isinstance(side_data.get("probablePitcher"), dict) else {}
                if _int(probable.get("id")) != player_id:
                    continue
                try:
                    game_start = _aware(str(game.get("gameDate") or ""))
                except PropAutoHydrationError:
                    continue
                candidates.append((abs((game_start - event_start).total_seconds()), game, side))
    if not candidates:
        raise PropAutoHydrationError(
            "MLB_STARTER_STATUS_UNRESOLVED",
            "official MLB schedule did not list the player as a probable pitcher for the target event window",
            detail={"player_id": player_id},
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
    other = "away" if side == "home" else "home"
    team_node = teams.get(side) if isinstance(teams.get(side), dict) else {}
    opp_node = teams.get(other) if isinstance(teams.get(other), dict) else {}
    team = team_node.get("team") if isinstance(team_node.get("team"), dict) else {}
    opponent = opp_node.get("team") if isinstance(opp_node.get("team"), dict) else {}
    venue = game.get("venue") if isinstance(game.get("venue"), dict) else {}
    status = game.get("status") if isinstance(game.get("status"), dict) else {}
    return {
        "official_game_pk": game.get("gamePk"),
        "official_game_date": game.get("gameDate"),
        "side": side.upper(),
        "team": team.get("abbreviation") or team.get("name") or "UNKNOWN",
        "team_id": _int(team.get("id")),
        "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
        "opponent_team_id": _int(opponent.get("id")),
        "venue": venue.get("name") or "UNKNOWN",
        "schedule_status": status.get("detailedState") or status.get("abstractGameState") or "UNKNOWN",
        "starter_status": "STARTER_PROBABLE_OFFICIAL_SCHEDULE",
    }


def _prior_start_rows(
    player_id: int,
    *,
    season: int,
    event_start: datetime,
    stat_type: str,
    http_get: Callable[..., Any],
) -> tuple[list[float], list[dict[str, Any]]]:
    payload = _request_json(
        f"{MLB_STATS_API_BASE}/people/{player_id}/stats",
        params={"stats": "gameLog", "group": "pitching", "season": str(season), "gameType": "R"},
        http_get=http_get,
    )
    blocks = payload.get("stats") if isinstance(payload.get("stats"), list) else []
    splits: list[Any] = []
    for block in blocks:
        if isinstance(block, dict) and isinstance(block.get("splits"), list):
            splits.extend(block["splits"])

    parsed: list[tuple[str, dict[str, Any], float]] = []
    for split in splits:
        if not isinstance(split, dict):
            continue
        stat = split.get("stat") if isinstance(split.get("stat"), dict) else {}
        if _int(stat.get("gamesStarted")) < 1:
            continue
        date_value = str(split.get("date") or "")
        try:
            game_date = datetime.fromisoformat(date_value).date()
        except ValueError:
            continue
        if game_date >= event_start.date():
            continue
        try:
            outs = _outs_from_ip(stat.get("inningsPitched"))
        except PropAutoHydrationError:
            continue
        strikeouts = _int(stat.get("strikeOuts"), default=-1)
        pitches = _int(stat.get("numberOfPitches"), default=-1)
        strikes = _int(stat.get("strikes"), default=-1)
        if stat_type == "PITCHER_STRIKEOUTS":
            if strikeouts < 0:
                continue
            target = float(strikeouts)
        elif stat_type == "PITCHING_OUTS":
            target = float(outs)
        elif stat_type in {"STRIKES_THROWN", "BALLS_THROWN"}:
            if pitches < 0 or strikes < 0 or pitches < strikes:
                continue
            target = float(strikes if stat_type == "STRIKES_THROWN" else pitches - strikes)
        else:
            continue
        opponent = split.get("opponent") if isinstance(split.get("opponent"), dict) else {}
        row = {
            "date": date_value,
            "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
            "ip": str(stat.get("inningsPitched")),
            "outs": outs,
            "bf": _int(stat.get("battersFaced")),
            "so": max(strikeouts, 0),
            "bb": _int(stat.get("baseOnBalls")),
            "er": _int(stat.get("earnedRuns")),
        }
        if pitches >= 0:
            row["pitches"] = pitches
        if strikes >= 0:
            row["strikes"] = strikes
        parsed.append((date_value, row, target))

    parsed.sort(key=lambda item: item[0], reverse=True)
    recent = parsed[:MIN_STARTS]
    if len(recent) < MIN_STARTS:
        code = "MLB_PITCH_COMPOSITION_INSUFFICIENT" if stat_type in {"STRIKES_THROWN", "BALLS_THROWN"} else "MLB_RECENT_STARTS_INSUFFICIENT"
        raise PropAutoHydrationError(
            code,
            "fewer than ten official regular-season starts with required target evidence were available before the event",
            detail={"starts_found": len(recent), "required": MIN_STARTS, "stat_type": stat_type},
        )
    return [target for _, _, target in recent], [row for _, row, _ in recent]


def auto_hydrate_prop_evidence(
    *,
    sport: str,
    player: str,
    stat_type: str,
    event_start_time: str,
    http_get: Callable[..., Any] = httpx.get,
    now: datetime | None = None,
    source_capture_timestamp: str | None = None,
    source_label: str | None = None,
) -> dict[str, Any]:
    sport_key = str(sport or "").strip().upper()
    stat_key = str(stat_type or "").strip().upper()
    if sport_key != "MLB" or stat_key not in SUPPORTED_STATS:
        raise PropAutoHydrationError(
            "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
            "no certified server-owned auto-hydration provider exists for this route",
            detail={"sport": sport_key, "stat_type": stat_key},
        )

    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = _aware(event_start_time)
    if event_start <= now_utc:
        raise PropAutoHydrationError("EVENT_ALREADY_STARTED", "automatic evidence acquisition is pregame-only")

    captured = _aware(source_capture_timestamp) if source_capture_timestamp else now_utc
    if captured > now_utc:
        raise PropAutoHydrationError("CAPTURE_TIMESTAMP_IN_FUTURE", "source capture timestamp is in the future")
    if captured >= event_start:
        raise PropAutoHydrationError("CAPTURE_NOT_PREGAME", "source capture timestamp is not pregame")

    player_id, official_name = _resolve_player_id(player, http_get=http_get)
    schedule = _schedule_context(player_id, event_start=event_start, http_get=http_get)
    game_log, box_score_log = _prior_start_rows(
        player_id,
        season=event_start.year,
        event_start=event_start,
        stat_type=stat_key,
        http_get=http_get,
    )

    source_name = (source_label or "AUTO_HYDRATION").strip() or "AUTO_HYDRATION"
    source_timestamps = {
        "MLB_STATS_API_OFFICIAL": captured.isoformat(),
        f"INPUT_CAPTURE_{source_name}": captured.isoformat(),
    }
    role_status = {
        "status": "CONFIRMED_PROBABLE_STARTER",
        "role": "STARTING_PITCHER",
        "player_id": player_id,
        "player": official_name,
        **schedule,
    }
    opportunity_ledger = {
        "status": "READY",
        "gate_label": "READY",
        "regular_season_prior_starts": len(game_log),
        "required_prior_starts": MIN_STARTS,
        "target_stat_type": stat_key,
        "official_game_pk": schedule.get("official_game_pk"),
        "starter_status": schedule.get("starter_status"),
    }
    return {
        "captured_at": captured.isoformat(),
        "game_log": game_log,
        "box_score_log": box_score_log,
        "role_status": role_status,
        "role_timestamp": captured.isoformat(),
        "opportunity_ledger": opportunity_ledger,
        "source_timestamps": source_timestamps,
        "evidence_version": AUTO_HYDRATION_EVIDENCE_VERSION,
        "rate_provenance": f"{AUTO_HYDRATION_PROVIDER}:{stat_key}:OFFICIAL_PRIOR_STARTS",
        "opponent_context": None,
    }
