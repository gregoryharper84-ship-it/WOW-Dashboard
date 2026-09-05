"""Official pregame evidence hydration for MLB batter plate appearances.

The fitted PA model was trained on three point-in-time inputs:
- the batter's own strictly prior game plate-appearance history,
- the batter's current batting slot, and
- team alignment (1=home, 0=away).

This hydrator reproduces those semantics from MLB StatsAPI only.  It fails
closed when the current MLB team, target game, official batting order, batter
slot, or ten prior regular-season PA rows cannot be resolved.  Same-day prior
games are conservatively excluded because the gameLog payload exposes a date
but not a trustworthy per-split pregame timestamp; this prevents doubleheader
look-ahead leakage at the cost of occasionally using an older tenth game.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import httpx

PA_STAT_TYPE = "PLATE_APPEARANCES"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _event_start(value: str, *, error_type: type[RuntimeError]) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise error_type(
            "PROP_EVENT_START_INVALID",
            "event_start_time must be timezone-aware ISO 8601",
        ) from exc
    if parsed.utcoffset() is None:
        raise error_type(
            "PROP_EVENT_START_INVALID",
            "event_start_time must include a timezone",
        )
    return parsed.astimezone(timezone.utc)


def _current_mlb_team(
    player_id: int,
    *,
    request_json: Callable[..., dict[str, Any]],
    http_get: Callable[..., Any],
    mlb_stats_api_base: str,
    error_type: type[RuntimeError],
) -> tuple[int, str]:
    payload = request_json(
        f"{mlb_stats_api_base}/people/{player_id}",
        params={"hydrate": "currentTeam", "appContext": "majorLeague"},
        http_get=http_get,
    )
    people = payload.get("people") if isinstance(payload.get("people"), list) else []
    person = people[0] if people and isinstance(people[0], dict) else {}
    team = person.get("currentTeam") if isinstance(person.get("currentTeam"), dict) else {}
    team_id = _int(team.get("id"))
    if team_id <= 0:
        raise error_type(
            "MLB_BATTER_TEAM_UNRESOLVED",
            "official MLB player profile did not expose a current major-league team",
            detail={"player_id": player_id},
        )
    return team_id, str(team.get("name") or team.get("abbreviation") or team_id)


def _target_game(
    team_id: int,
    *,
    event_start: datetime,
    request_json: Callable[..., dict[str, Any]],
    http_get: Callable[..., Any],
    mlb_stats_api_base: str,
    error_type: type[RuntimeError],
) -> dict[str, Any]:
    payload = request_json(
        f"{mlb_stats_api_base}/schedule",
        params={
            "sportId": "1",
            "teamId": str(team_id),
            "startDate": (event_start.date() - timedelta(days=1)).isoformat(),
            "endDate": (event_start.date() + timedelta(days=1)).isoformat(),
            "hydrate": "team,venue",
        },
        http_get=http_get,
    )
    candidates: list[tuple[float, dict[str, Any], str]] = []
    dates = payload.get("dates") if isinstance(payload.get("dates"), list) else []
    for date_block in dates:
        games = date_block.get("games") if isinstance(date_block, dict) else None
        if not isinstance(games, list):
            continue
        for game in games:
            if not isinstance(game, dict):
                continue
            teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
            side = None
            for candidate_side in ("home", "away"):
                node = teams.get(candidate_side) if isinstance(teams.get(candidate_side), dict) else {}
                team = node.get("team") if isinstance(node.get("team"), dict) else {}
                if _int(team.get("id")) == team_id:
                    side = candidate_side
                    break
            if side is None:
                continue
            try:
                game_start = datetime.fromisoformat(str(game.get("gameDate") or "").replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if game_start.utcoffset() is None:
                continue
            delta = abs((game_start.astimezone(timezone.utc) - event_start).total_seconds())
            candidates.append((delta, game, side))

    if not candidates:
        raise error_type(
            "PROP_EVENT_IDENTITY_UNRESOLVED",
            "official MLB schedule did not contain a target game for the batter's current team",
            detail={"team_id": team_id},
        )
    candidates.sort(key=lambda item: item[0])
    delta, game, side = candidates[0]
    if delta > 12 * 60 * 60:
        raise error_type(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "closest official MLB game was too far from the requested event start",
            detail={"team_id": team_id, "start_delta_seconds": delta},
        )

    teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
    other = "away" if side == "home" else "home"
    team_node = teams.get(side) if isinstance(teams.get(side), dict) else {}
    opp_node = teams.get(other) if isinstance(teams.get(other), dict) else {}
    team = team_node.get("team") if isinstance(team_node.get("team"), dict) else {}
    opponent = opp_node.get("team") if isinstance(opp_node.get("team"), dict) else {}
    venue = game.get("venue") if isinstance(game.get("venue"), dict) else {}
    status = game.get("status") if isinstance(game.get("status"), dict) else {}
    game_pk = _int(game.get("gamePk"))
    if game_pk <= 0:
        raise error_type(
            "PROP_EVENT_IDENTITY_UNRESOLVED",
            "official MLB schedule game did not expose gamePk",
        )
    return {
        "official_game_pk": game_pk,
        "official_game_date": str(game.get("gameDate") or ""),
        "side": side.upper(),
        "team_id": team_id,
        "team": team.get("abbreviation") or team.get("name") or "UNKNOWN",
        "opponent_team_id": _int(opponent.get("id")),
        "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
        "venue": venue.get("name") or "UNKNOWN",
        "schedule_status": status.get("detailedState") or status.get("abstractGameState") or "UNKNOWN",
    }


def _official_batting_slot(
    *,
    player_id: int,
    game_pk: int,
    side: str,
    request_json: Callable[..., dict[str, Any]],
    http_get: Callable[..., Any],
    mlb_stats_api_base: str,
    error_type: type[RuntimeError],
) -> tuple[int, list[int]]:
    payload = request_json(
        f"{mlb_stats_api_base}/game/{game_pk}/boxscore",
        params={},
        http_get=http_get,
    )
    teams = payload.get("teams") if isinstance(payload.get("teams"), dict) else {}
    node = teams.get(side.lower()) if isinstance(teams.get(side.lower()), dict) else {}
    raw_order = node.get("battingOrder") if isinstance(node.get("battingOrder"), list) else []
    batting_order = [_int(value) for value in raw_order if _int(value) > 0]
    if len(batting_order) < 9:
        raise error_type(
            "MLB_LINEUP_NOT_CONFIRMED",
            "official MLB boxscore did not yet expose a complete batting order",
            detail={"game_pk": game_pk, "side": side, "lineup_size": len(batting_order)},
        )
    first_nine = batting_order[:9]
    if player_id not in first_nine:
        raise error_type(
            "MLB_BATTER_NOT_IN_CONFIRMED_LINEUP",
            "batter was not present in the official starting batting order",
            detail={"game_pk": game_pk, "player_id": player_id},
        )
    return first_nine.index(player_id) + 1, first_nine


def _prior_pa_history(
    player_id: int,
    *,
    event_start: datetime,
    request_json: Callable[..., dict[str, Any]],
    http_get: Callable[..., Any],
    mlb_stats_api_base: str,
    min_games: int,
    error_type: type[RuntimeError],
) -> tuple[list[float], list[dict[str, Any]], list[int]]:
    parsed: list[tuple[str, int, dict[str, Any]]] = []
    seasons_queried: list[int] = []
    for season in (event_start.year, event_start.year - 1):
        seasons_queried.append(season)
        payload = request_json(
            f"{mlb_stats_api_base}/people/{player_id}/stats",
            params={
                "stats": "gameLog",
                "group": "hitting",
                "season": str(season),
                "gameType": "R",
            },
            http_get=http_get,
        )
        blocks = payload.get("stats") if isinstance(payload.get("stats"), list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            splits = block.get("splits") if isinstance(block.get("splits"), list) else []
            for split in splits:
                if not isinstance(split, dict):
                    continue
                date_value = str(split.get("date") or "")
                try:
                    game_date = datetime.fromisoformat(date_value).date()
                except ValueError:
                    continue
                # Conservative anti-leakage rule: exclude all same-day splits.
                if game_date >= event_start.date():
                    continue
                stat = split.get("stat") if isinstance(split.get("stat"), dict) else {}
                pa = _int(stat.get("plateAppearances"), default=-1)
                if pa < 0:
                    continue
                game = split.get("game") if isinstance(split.get("game"), dict) else {}
                opponent = split.get("opponent") if isinstance(split.get("opponent"), dict) else {}
                row = {
                    "date": date_value,
                    "season": season,
                    "game_pk": _int(game.get("gamePk")),
                    "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
                    "pa": pa,
                }
                parsed.append((date_value, _int(game.get("gamePk")), row))

    # Deduplicate the same game when API hydration returns overlapping blocks.
    unique: dict[tuple[str, int], dict[str, Any]] = {}
    for date_value, game_pk, row in parsed:
        unique[(date_value, game_pk)] = row
    rows = sorted(unique.values(), key=lambda row: (row["date"], row["game_pk"]), reverse=True)[:min_games]
    if len(rows) < min_games:
        raise error_type(
            "MLB_RECENT_BATTER_GAMES_INSUFFICIENT",
            "fewer than ten strictly prior official regular-season batting games with PA were available",
            detail={
                "games_found": len(rows),
                "required": min_games,
                "seasons_queried": seasons_queried,
            },
        )
    return [float(row["pa"]) for row in rows], rows, seasons_queried


def hydrate_mlb_plate_appearances_evidence(
    *,
    player: str,
    event_start_time: str,
    resolve_player_id: Callable[..., tuple[int, str]],
    request_json: Callable[..., dict[str, Any]],
    error_type: type[RuntimeError],
    mlb_stats_api_base: str,
    evidence_version: str,
    min_games: int,
    http_get: Callable[..., Any] = httpx.get,
    now: Optional[datetime] = None,
    source_capture_timestamp: Optional[str] = None,
    source_label: str = "NORMALIZED_PICK_REQUEST",
) -> dict[str, Any]:
    normalized_player = " ".join(str(player or "").strip().split())
    if not normalized_player:
        raise error_type("PROP_PLAYER_IDENTITY_REQUIRED", "player is required for prop hydration")

    captured_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = _event_start(event_start_time, error_type=error_type)
    if event_start <= captured_at:
        raise error_type("EVENT_ALREADY_STARTED", "pregame evidence cannot be hydrated after event start")

    try:
        player_id, official_name = resolve_player_id(
            normalized_player,
            event_start=event_start,
            http_get=http_get,
        )
    except TypeError:
        player_id, official_name = resolve_player_id(normalized_player, http_get=http_get)

    team_id, _team_name = _current_mlb_team(
        player_id,
        request_json=request_json,
        http_get=http_get,
        mlb_stats_api_base=mlb_stats_api_base,
        error_type=error_type,
    )
    schedule = _target_game(
        team_id,
        event_start=event_start,
        request_json=request_json,
        http_get=http_get,
        mlb_stats_api_base=mlb_stats_api_base,
        error_type=error_type,
    )
    batting_slot, batting_order = _official_batting_slot(
        player_id=player_id,
        game_pk=schedule["official_game_pk"],
        side=schedule["side"],
        request_json=request_json,
        http_get=http_get,
        mlb_stats_api_base=mlb_stats_api_base,
        error_type=error_type,
    )
    game_log, box_score_log, seasons_queried = _prior_pa_history(
        player_id,
        event_start=event_start,
        request_json=request_json,
        http_get=http_get,
        mlb_stats_api_base=mlb_stats_api_base,
        min_games=min_games,
        error_type=error_type,
    )

    timestamp = captured_at.isoformat()
    team_alignment = 1 if schedule["side"] == "HOME" else 0
    source_timestamps = {
        "MLB_STATS_API_PLAYER_IDENTITY": timestamp,
        "MLB_STATS_API_CURRENT_TEAM": timestamp,
        "MLB_STATS_API_SCHEDULE": timestamp,
        "MLB_STATS_API_OFFICIAL_BATTING_ORDER": timestamp,
        "MLB_STATS_API_HITTING_GAME_LOG": timestamp,
    }
    if len({row["season"] for row in box_score_log}) > 1:
        source_timestamps["MLB_STATS_API_CROSS_SEASON_HISTORY"] = timestamp
    if source_capture_timestamp:
        source_timestamps[f"INPUT_CAPTURE_{str(source_label).strip().upper()}"] = source_capture_timestamp

    return {
        "captured_at": timestamp,
        # The generic frozen-evidence validator requires aligned numeric history
        # plus per-game rows.  For PA these are the exact model's prior-PA series.
        "game_log": game_log,
        "box_score_log": box_score_log,
        "role_status": {
            "status": "LINEUP_CONFIRMED_OFFICIAL_BATTING_ORDER",
            "role": "STARTING_BATTER",
            "confirmation_strength": "OFFICIAL_BATTING_ORDER",
            "player_id": player_id,
            "official_name": official_name,
            "team": schedule["team"],
            "opponent": schedule["opponent"],
            "venue": schedule["venue"],
            "official_game_pk": schedule["official_game_pk"],
            "official_game_date": schedule["official_game_date"],
            "schedule_status": schedule["schedule_status"],
            "source": "MLB StatsAPI official schedule + game boxscore battingOrder",
        },
        "role_timestamp": timestamp,
        "opportunity_ledger": {
            "status": "READY",
            "target_stat_type": PA_STAT_TYPE,
            "prior_pa_log": [int(value) for value in game_log],
            "batting_slot": batting_slot,
            "team_alignment": team_alignment,
            "team_alignment_semantics": "1=HOME,0=AWAY",
            "lineup_status": "CONFIRMED",
            "lineup_source": "MLB_STATS_API_OFFICIAL_BATTING_ORDER",
            "lineup_player_ids": batting_order,
            "regular_season_prior_games": len(box_score_log),
            "history_seasons_queried": seasons_queried,
            "history_seasons_used": sorted({row["season"] for row in box_score_log}, reverse=True),
            "history_selection": "MOST_RECENT_STRICTLY_PRIOR_OFFICIAL_GAMES_NO_SAME_DAY_NO_IMPUTATION",
        },
        "source_timestamps": source_timestamps,
        "evidence_version": evidence_version,
        "rate_provenance": "MLB StatsAPI official hitting gameLog PA; current official battingOrder; team alignment 1=home/0=away; same-day history excluded; no PA values imputed",
    }
