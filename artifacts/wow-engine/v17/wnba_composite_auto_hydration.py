"""Official-source automatic hydration for WNBA P/R/A composite props.

This module reuses the reviewed WNBA identity, roster, schedule, injury-report,
and LeagueGameLog acquisition primitives.  The only new behavior is exact-game
aggregation of PTS/REB/AST for the four declared composite routes.  No model,
probability, calibration, certification, publication, ranking, pricing or execution
logic lives here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Callable, Mapping, Optional

import httpx

import wnba_prop_auto_hydration as base

PROVIDER_ID = "WNBA_OFFICIAL_STATS_CDN_INJURY_COMPOSITE_V1"
EVIDENCE_VERSION = base.EVIDENCE_VERSION
MIN_PRIOR_GAMES = base.MIN_PRIOR_GAMES

COMPONENT_COLUMNS: dict[str, tuple[str, ...]] = {
    "PRA": ("PTS", "REB", "AST"),
    "POINTS_REBOUNDS": ("PTS", "REB"),
    "POINTS_ASSISTS": ("PTS", "AST"),
    "REBOUNDS_ASSISTS": ("REB", "AST"),
}
ALIASES = {
    "PTS+REB+AST": "PRA", "POINTS+REBOUNDS+ASSISTS": "PRA", "POINTS_REBOUNDS_ASSISTS": "PRA", "PTS_REB_AST": "PRA",
    "PTS+REB": "POINTS_REBOUNDS", "POINTS+REBOUNDS": "POINTS_REBOUNDS", "PTS_REB": "POINTS_REBOUNDS",
    "PTS+AST": "POINTS_ASSISTS", "POINTS+ASSISTS": "POINTS_ASSISTS", "PTS_AST": "POINTS_ASSISTS",
    "REB+AST": "REBOUNDS_ASSISTS", "REBOUNDS+ASSISTS": "REBOUNDS_ASSISTS", "REB_AST": "REBOUNDS_ASSISTS",
}


def canonical_stat(value: str) -> str:
    raw = "_".join(str(value or "").strip().upper().replace("-", " ").split())
    raw = ALIASES.get(raw, raw)
    if raw not in COMPONENT_COLUMNS:
        raise base.WNBAPropHydrationError(
            "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
            "WNBA composite automatic hydration is not certified for this stat route",
            detail={"sport": "WNBA", "stat_type": raw},
        )
    return raw


def _composite_game_log(
    player_id: str,
    player_name: str,
    components: tuple[str, ...],
    season: int,
    event_start: datetime,
    *,
    http_get: Callable[..., Any],
) -> tuple[list[float], list[dict[str, Any]]]:
    payload = base._request(
        f"{base.WNBA_STATS_BASE}/leaguegamelog",
        params={
            "LeagueID": "10", "PlayerOrTeam": "P", "Season": str(season),
            "SeasonType": "Regular Season", "Counter": "0", "DateFrom": "", "DateTo": "",
            "Direction": "ASC", "Sorter": "DATE",
        },
        headers=base._stats_headers(),
        http_get=http_get,
    )
    rows = base._result_rows(payload, "LeagueGameLog")
    selected: list[dict[str, Any]] = []
    for row in rows:
        row_pid = str(row.get("PLAYER_ID") or row.get("PERSON_ID") or "").strip()
        row_name = row.get("PLAYER_NAME") or row.get("PLAYER")
        if row_pid and row_pid != str(player_id):
            continue
        if not row_pid and base._name_key(row_name) != base._name_key(player_name):
            continue
        raw_date = str(row.get("GAME_DATE") or "")[:10]
        try:
            game_date = datetime.fromisoformat(raw_date).date()
        except ValueError:
            continue
        if game_date >= event_start.date():
            continue
        try:
            minutes = float(row.get("MIN"))
            values = {column: float(row.get(column)) for column in components}
        except (TypeError, ValueError):
            continue
        if not math.isfinite(minutes) or not 0 < minutes <= 60:
            continue
        if any(not math.isfinite(value) or value < 0 or value != int(value) for value in values.values()):
            continue
        total = int(sum(values.values()))
        selected.append({
            "date": raw_date,
            "game_id": str(row.get("GAME_ID") or ""),
            "team": str(row.get("TEAM_ABBREVIATION") or ""),
            "matchup": str(row.get("MATCHUP") or ""),
            "minutes": minutes,
            "stat": total,
            "components": {column: int(value) for column, value in values.items()},
        })
    selected.sort(key=lambda row: (row["date"], row["game_id"]), reverse=True)
    recent = selected[:MIN_PRIOR_GAMES]
    if len(recent) < MIN_PRIOR_GAMES:
        raise base.WNBAPropHydrationError(
            "WNBA_RECENT_GAMES_INSUFFICIENT",
            "fewer than ten official prior WNBA games with aligned composite components were available",
            detail={"games_found": len(recent), "required": MIN_PRIOR_GAMES},
        )
    return [float(row["stat"]) for row in recent], [
        {
            "date": row["date"], "game_id": row["game_id"], "team": row["team"],
            "matchup": row["matchup"], "minutes": row["minutes"], "components": row["components"],
        }
        for row in recent
    ]


def hydrate_wnba_composite_evidence(
    *,
    player: str,
    stat_type: str,
    event_start_time: str,
    http_get: Callable[..., Any] = httpx.get,
    now: Optional[datetime] = None,
    source_capture_timestamp: Optional[str] = None,
    source_label: str = "NORMALIZED_PICK_REQUEST",
    opponent: Optional[str] = None,
) -> dict[str, Any]:
    stat = canonical_stat(stat_type)
    components = COMPONENT_COLUMNS[stat]
    normalized_player = " ".join(str(player or "").strip().split())
    if not normalized_player:
        raise base.WNBAPropHydrationError("PROP_PLAYER_IDENTITY_REQUIRED", "player is required")

    captured = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event_start = base._aware(event_start_time)
    if event_start <= captured:
        raise base.WNBAPropHydrationError("EVENT_ALREADY_STARTED", "pregame evidence cannot be hydrated after event start")

    game = base._schedule(event_start, http_get=http_get)
    resolved = base._resolve_player_and_team(game, normalized_player, event_start.year, http_get=http_get)
    roster = resolved["roster"]; team = resolved["team"]; opp = resolved["opponent"]
    official_name = str(roster.get("PLAYER") or normalized_player)
    player_id = str(roster.get("PLAYER_ID") or "").strip()
    if not player_id:
        raise base.WNBAPropHydrationError("PROP_PLAYER_IDENTITY_UNRESOLVED", "official WNBA roster player ID missing")

    opponent_name = " ".join(str(opp.get("teamCity") or "").split() + str(opp.get("teamName") or "").split()).strip()
    opponent_tricode = str(opp.get("teamTricode") or "").strip().upper()
    if opponent and base._name_key(opponent) not in {base._name_key(opponent_name), base._name_key(opponent_tricode)}:
        raise base.WNBAPropHydrationError(
            "PROP_EVENT_IDENTITY_CONFLICT",
            "requested opponent did not match the official WNBA schedule event",
            detail={"requested_opponent": opponent, "official_opponent": opponent_name, "official_tricode": opponent_tricode},
        )

    game_log, box_score_log = _composite_game_log(
        player_id, official_name, components, event_start.year, event_start, http_get=http_get,
    )

    away = base._team_node(game, "awayTeam"); home = base._team_node(game, "homeTeam")
    matchup = f"{str(away.get('teamTricode') or '').upper()}@{str(home.get('teamTricode') or '').upper()}"
    game_date = event_start.astimezone(base.ET).strftime("%m/%d/%Y")
    team_name = " ".join(str(team.get("teamCity") or "").split() + str(team.get("teamName") or "").split()).strip()
    injury_url, injury_ts, injury_text = base._latest_injury_report(captured, http_get=http_get)
    availability = base._availability_from_report(
        injury_text,
        player_name=official_name,
        team_name=team_name,
        matchup=matchup,
        game_date=game_date,
    )

    timestamp = captured.isoformat()
    source_timestamps = {
        "WNBA_CDN_SCHEDULE_CURRENT": timestamp,
        "WNBA_STATS_COMMON_TEAM_ROSTER": timestamp,
        "WNBA_STATS_LEAGUE_GAME_LOG": timestamp,
        "WNBA_OFFICIAL_INJURY_REPORT": injury_ts.astimezone(timezone.utc).isoformat(),
    }
    if source_capture_timestamp:
        source_timestamps[f"INPUT_CAPTURE_{str(source_label).strip().upper()}"] = source_capture_timestamp
    l10_minutes = [float(row["minutes"]) for row in box_score_log]
    return {
        "captured_at": timestamp,
        "game_log": game_log,
        "box_score_log": box_score_log,
        "role_status": {
            "status": "CURRENT_ROSTER_CONFIRMED_NO_BLOCKING_INJURY_DESIGNATION",
            "role": str(roster.get("POSITION") or "WNBA_ROTATION_PLAYER"),
            "confirmation_strength": "OFFICIAL_ROSTER_PLUS_FRESH_OFFICIAL_INJURY_REPORT",
            "player_id": player_id,
            "team_id": str(team.get("teamId") or ""),
            "team": team_name,
            "team_tricode": str(team.get("teamTricode") or ""),
            "opponent": opponent_name,
            "opponent_tricode": opponent_tricode,
            "official_game_id": str(game.get("gameId") or ""),
            "official_game_start_utc": str(game.get("gameDateTimeUTC") or ""),
            "schedule_status": str(game.get("gameStatusText") or "Scheduled"),
            "availability": availability["availability"],
            "injury_designation": availability["designation"],
            "injury_report_url": injury_url,
            "source": "WNBA official CDN schedule + WNBA Stats roster/log + official WNBA injury report",
        },
        "role_timestamp": timestamp,
        "opportunity_ledger": {
            "status": "READY",
            "stat_type": stat,
            "stat_source_columns": list(components),
            "composite_method": "EXACT_SAME_GAME_COMPONENT_SUM",
            "box_score_alignment": "1:1",
            "prior_games": len(box_score_log),
            "l10_minutes_mean": sum(l10_minutes) / len(l10_minutes),
            "l5_minutes_mean": sum(l10_minutes[:5]) / 5.0,
            "current_roster_confirmation": "PASS",
            "availability_gate": "PASS",
            "availability_policy": "BLOCK_ANY_EXPLICIT_INJURY_REPORT_DESIGNATION_UNLESS_AVAILABLE",
        },
        "source_timestamps": source_timestamps,
        "evidence_version": EVIDENCE_VERSION,
        "rate_provenance": "Official WNBA LeagueGameLog exact-game P/R/A component sums; current event/team from WNBA CDN; roster and injury report official",
        "hydration_provider": PROVIDER_ID,
    }


__all__ = ["COMPONENT_COLUMNS", "PROVIDER_ID", "canonical_stat", "hydrate_wnba_composite_evidence"]
