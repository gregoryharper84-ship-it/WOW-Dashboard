"""Official MLB evidence hydrator for batter plate-appearance props.

Builds auditable pregame evidence for PLATE_APPEARANCES from MLB StatsAPI.
Historical PA values are never padded or estimated. Current batting slot is
used only when the official batting order is present; otherwise hydration still
succeeds with an explicit lineup-unconfirmed state so the fitted-model coverage
gate can hold publication without misclassifying the row as an acquisition
failure.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import httpx


PA_STAT = "PLATE_APPEARANCES"


def _aware(value: str, *, error_type: type[RuntimeError]) -> datetime:
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


def _team_id_for_player(
    player_id: int,
    *,
    request_json: Callable[..., dict[str, Any]],
    http_get: Callable[..., Any],
    mlb_stats_api_base: str,
    int_value: Callable[..., int],
    error_type: type[RuntimeError],
) -> int:
    payload = request_json(
        f"{mlb_stats_api_base}/people/{player_id}",
        params={"hydrate": "currentTeam"},
        http_get=http_get,
    )
    people = payload.get("people") if isinstance(payload.get("people"), list) else []
    person = people[0] if people and isinstance(people[0], dict) else {}
    current_team = person.get("currentTeam") if isinstance(person.get("currentTeam"), dict) else {}
    team_id = int_value(current_team.get("id"))
    if team_id <= 0:
        raise error_type(
            "MLB_BATTER_TEAM_UNRESOLVED",
            "official MLB player record did not resolve a current team",
            detail={"player_id": player_id},
        )
    return team_id


def _target_game(
    team_id: int,
    *,
    event_start: datetime,
    request_json: Callable[..., dict[str, Any]],
    http_get: Callable[..., Any],
    mlb_stats_api_base: str,
    int_value: Callable[..., int],
    error_type: type[RuntimeError],
) -> dict[str, Any]:
    payload = request_json(
        f"{mlb_stats_api_base}/schedule",
        params={
            "sportId": "1",
            "teamId": str(team_id),
            "startDate": (event_start.date() - timedelta(days=1)).isoformat(),
            "endDate": event_start.date().isoformat(),
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
                if int_value(team.get("id")) == team_id:
                    side = candidate_side
                    break
            if side is None:
                continue
            game_date = str(game.get("gameDate") or "")
            try:
                game_start = _aware(game_date, error_type=error_type)
            except RuntimeError:
                continue
            candidates.append((abs((game_start - event_start).total_seconds()), game, side))

    if not candidates:
        raise error_type(
            "MLB_BATTER_EVENT_UNRESOLVED",
            "official MLB schedule did not resolve the batter's target game",
            detail={"team_id": team_id},
        )
    candidates.sort(key=lambda item: item[0])
    delta, game, side = candidates[0]
    if delta > 12 * 60 * 60:
        raise error_type(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "official MLB game was too far from the requested event start",
            detail={"start_delta_seconds": delta, "team_id": team_id},
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
        "official_game_pk": int_value(game.get("gamePk")),
        "official_game_date": game.get("gameDate"),
        "side": side.upper(),
        "team": team.get("abbreviation") or team.get("name") or "UNKNOWN",
        "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
        "venue": venue.get("name") or "UNKNOWN",
        "schedule_status": status.get("detailedState") or status.get("abstractGameState") or "UNKNOWN",
        "team_alignment": 1 if side == "home" else 0,
    }


def _official_batting_slot(
    player_id: int,
    *,
    game_pk: int,
    side: str,
    request_json: Callable[..., dict[str, Any]],
    http_get: Callable[..., Any],
    mlb_stats_api_base: str,
    int_value: Callable[..., int],
) -> int | None:
    if game_pk <= 0:
        return None
    try:
        payload = request_json(
            f"{mlb_stats_api_base}/game/{game_pk}/boxscore",
            params={},
            http_get=http_get,
        )
    except Exception:
        return None
    teams = payload.get("teams") if isinstance(payload.get("teams"), dict) else {}
    node = teams.get(side.lower()) if isinstance(teams.get(side.lower()), dict) else {}
    batting_order = node.get("battingOrder") if isinstance(node.get("battingOrder"), list) else []
    ids = [int_value(value) for value in batting_order]
    try:
        return ids.index(player_id) + 1
    except ValueError:
        return None


def _recent_pa_history(
    player_id: int,
    *,
    event_start: datetime,
    request_json: Callable[..., dict[str, Any]],
    http_get: Callable[..., Any],
    mlb_stats_api_base: str,
    int_value: Callable[..., int],
    min_games: int,
) -> tuple[list[float], list[dict[str, Any]], list[int]]:
    parsed: list[tuple[str, int, dict[str, Any], float]] = []
    seasons_queried: list[int] = []
    for season in range(event_start.year, max(event_start.year - 3, 1900), -1):
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
                stat = split.get("stat") if isinstance(split.get("stat"), dict) else {}
                date_value = str(split.get("date") or "")
                try:
                    game_date = datetime.fromisoformat(date_value).date()
                except ValueError:
                    continue
                if game_date >= event_start.date():
                    continue
                pa = int_value(stat.get("plateAppearances"), default=-1)
                if pa < 0:
                    continue
                opponent = split.get("opponent") if isinstance(split.get("opponent"), dict) else {}
                row = {
                    "date": date_value,
                    "season": season,
                    "opponent": opponent.get("abbreviation") or opponent.get("name") or "UNKNOWN",
                    "pa": pa,
                    "ab": int_value(stat.get("atBats")),
                    "h": int_value(stat.get("hits")),
                    "bb": int_value(stat.get("baseOnBalls")),
                    "so": int_value(stat.get("strikeOuts")),
                }
                game_number = int_value(split.get("gameNumber"))
                parsed.append((date_value, game_number, row, float(pa)))
        if len(parsed) >= min_games:
            break

    parsed.sort(key=lambda item: (item[0], item[1]), reverse=True)
    recent = parsed[:min_games]
    return (
        [target for _, _, _, target in recent],
        [row for _, _, row, _ in recent],
        seasons_queried,
    )


def hydrate_mlb_plate_appearance_evidence(
    *,
    player: str,
    event_start_time: str,
    resolve_player_id: Callable[..., tuple[int, str]],
    request_json: Callable[..., dict[str, Any]],
    int_value: Callable[..., int],
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
    event_start = _aware(event_start_time, error_type=error_type)
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

    team_id = _team_id_for_player(
        player_id,
        request_json=request_json,
        http_get=http_get,
        mlb_stats_api_base=mlb_stats_api_base,
        int_value=int_value,
        error_type=error_type,
    )
    schedule = _target_game(
        team_id,
        event_start=event_start,
        request_json=request_json,
        http_get=http_get,
        mlb_stats_api_base=mlb_stats_api_base,
        int_value=int_value,
        error_type=error_type,
    )
    batting_slot = _official_batting_slot(
        player_id,
        game_pk=int_value(schedule.get("official_game_pk")),
        side=str(schedule.get("side") or ""),
        request_json=request_json,
        http_get=http_get,
        mlb_stats_api_base=mlb_stats_api_base,
        int_value=int_value,
    )
    game_log, box_score_log, seasons_queried = _recent_pa_history(
        player_id,
        event_start=event_start,
        request_json=request_json,
        http_get=http_get,
        mlb_stats_api_base=mlb_stats_api_base,
        int_value=int_value,
        min_games=min_games,
    )
    if len(game_log) < min_games:
        raise error_type(
            "MLB_RECENT_GAMES_INSUFFICIENT",
            "fewer than ten official regular-season games with plate-appearance evidence were available across the supported history window",
            detail={
                "games_found": len(game_log),
                "required": min_games,
                "stat_type": PA_STAT,
                "seasons_queried": seasons_queried,
            },
        )

    timestamp = captured_at.isoformat()
    lineup_confirmed = batting_slot is not None
    selected_seasons = sorted({int(row["season"]) for row in box_score_log}, reverse=True)
    source_timestamps = {
        "MLB_STATS_API_PLAYER_IDENTITY": timestamp,
        "MLB_STATS_API_HITTING_GAME_LOG": timestamp,
        "MLB_STATS_API_TEAM_SCHEDULE": timestamp,
    }
    if lineup_confirmed:
        source_timestamps["MLB_STATS_API_OFFICIAL_BATTING_ORDER"] = timestamp
    if len(selected_seasons) > 1:
        source_timestamps["MLB_STATS_API_CROSS_SEASON_HISTORY"] = timestamp
    if source_capture_timestamp:
        source_timestamps[f"INPUT_CAPTURE_{str(source_label).strip().upper()}"] = source_capture_timestamp

    return {
        "captured_at": timestamp,
        "game_log": game_log,
        "box_score_log": box_score_log,
        "role_status": {
            "status": "STARTER_CONFIRMED" if lineup_confirmed else "LINEUP_UNCONFIRMED",
            "role": "BATTER",
            "confirmation_strength": "OFFICIAL_BATTING_ORDER" if lineup_confirmed else "OFFICIAL_SCHEDULE_ONLY",
            "player_id": player_id,
            "official_name": official_name,
            "team": schedule["team"],
            "opponent": schedule["opponent"],
            "venue": schedule["venue"],
            "official_game_pk": schedule["official_game_pk"],
            "official_game_date": schedule["official_game_date"],
            "schedule_status": schedule["schedule_status"],
            "source": "MLB StatsAPI official player/schedule/battingOrder",
        },
        "role_timestamp": timestamp,
        "opportunity_ledger": {
            "status": "READY",
            "game_log_stat": "plate appearances",
            "box_score_alignment": "1:1",
            "regular_season_prior_games": len(box_score_log),
            "target_stat_type": PA_STAT,
            "batting_slot": batting_slot,
            "team_alignment": schedule["team_alignment"],
            "team_alignment_encoding": "0=AWAY,1=HOME",
            "lineup_confirmation": "OFFICIAL_BATTING_ORDER" if lineup_confirmed else "UNCONFIRMED_HOLD_AT_MODEL_COVERAGE",
            "history_seasons_queried": seasons_queried,
            "history_seasons_used": selected_seasons,
            "history_selection": "MOST_RECENT_OFFICIAL_GAMES_NO_IMPUTATION",
        },
        "source_timestamps": source_timestamps,
        "evidence_version": evidence_version,
        "rate_provenance": "MLB StatsAPI official hitting gameLog; target=PLATE_APPEARANCES; current batting slot only from official battingOrder; no PA or lineup values imputed",
    }
