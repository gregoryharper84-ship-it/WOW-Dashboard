"""Deterministic NFL P1 historical normalization for future fitted training.

Consumes already-captured CSV rows and produces game-level outcomes plus two
team summaries per game. All hashes bind normalized rows to the exact source
snapshot bytes. No model probability or publication authority exists here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
import json
import math
from typing import Any, Iterable


_ALLOWED_GAME_TYPES = frozenset({"REG", "WC", "DIV", "CON", "SB"})


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _int(value: Any) -> int | None:
    if value in (None, "", "NA", "NaN", "nan"):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value in (None, "", "NA", "NaN", "nan"):
        return None
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _flag(value: Any) -> bool:
    parsed = _float(value)
    return parsed is not None and parsed > 0.5


def _iso_date(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return None


def build_training_games(
    schedule_rows: Iterable[dict[str, Any]],
    *,
    schedule_snapshot_id: str,
    schedule_content_sha256: str,
) -> list[dict[str, Any]]:
    """Normalize completed NFL games from the authoritative schedule dataset."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in schedule_rows:
        game_id = str(row.get("game_id") or "").strip()
        game_type = str(row.get("game_type") or "").upper().strip()
        season = _int(row.get("season"))
        week = _int(row.get("week"))
        gameday = _iso_date(row.get("gameday"))
        home_team = str(row.get("home_team") or "").upper().strip()
        away_team = str(row.get("away_team") or "").upper().strip()
        home_score = _int(row.get("home_score"))
        away_score = _int(row.get("away_score"))

        if not game_id or game_id in seen:
            continue
        if game_type not in _ALLOWED_GAME_TYPES:
            continue
        if None in (season, week, home_score, away_score) or gameday is None:
            continue
        if not home_team or not away_team or home_team == away_team:
            continue
        if home_score < 0 or away_score < 0:
            continue

        normalized = {
            "game_id": game_id,
            "season": season,
            "game_type": game_type,
            "week": week,
            "gameday": gameday,
            "away_team": away_team,
            "home_team": home_team,
            "away_score": away_score,
            "home_score": home_score,
            "home_win": home_score > away_score,
            "tie": home_score == away_score,
            "roof": str(row.get("roof") or "").strip().lower() or None,
            "surface": str(row.get("surface") or "").strip().lower() or None,
            "temp_f": _float(row.get("temp")),
            "wind_mph": _float(row.get("wind")),
            "schedule_snapshot_id": schedule_snapshot_id,
            "schedule_content_sha256": schedule_content_sha256,
            "probability_publishable": False,
            "can_execute": False,
        }
        normalized["row_inputs_hash"] = _canonical_hash(normalized)
        result.append(normalized)
        seen.add(game_id)

    result.sort(key=lambda row: (row["season"], row["week"], row["gameday"], row["game_id"]))
    return result


def build_game_team_summaries(
    pbp_rows: Iterable[dict[str, Any]],
    *,
    training_games: Iterable[dict[str, Any]],
    pbp_snapshot_id: str,
    pbp_content_sha256: str,
) -> list[dict[str, Any]]:
    """Aggregate PBP into two deterministic team summaries per known game.

    EPA attached to an offensive play is counted positively for the offense and
    as EPA allowed for the defense. Missing optional PBP metrics remain neutral
    aggregates rather than being fabricated as league-average estimates.
    """
    games = {str(row["game_id"]): dict(row) for row in training_games}
    stats: dict[tuple[str, str], dict[str, Any]] = {}

    def ensure(game_id: str, team: str, opponent: str, is_home: bool) -> dict[str, Any]:
        key = (game_id, team)
        if key not in stats:
            stats[key] = {
                "game_id": game_id,
                "team": team,
                "opponent": opponent,
                "is_home": is_home,
                "offensive_plays": 0,
                "offensive_epa_sum": 0.0,
                "defensive_plays": 0,
                "defensive_epa_sum": 0.0,
                "success_plays": 0,
                "pass_epa_sum": 0.0,
                "rush_epa_sum": 0.0,
                "turnovers": 0,
                "sacks_allowed": 0,
                "special_teams_epa_sum": 0.0,
                "qb_gsis_ids": set(),
            }
        return stats[key]

    for game_id, game in games.items():
        ensure(game_id, str(game["home_team"]), str(game["away_team"]), True)
        ensure(game_id, str(game["away_team"]), str(game["home_team"]), False)

    for row in pbp_rows:
        game_id = str(row.get("game_id") or "").strip()
        game = games.get(game_id)
        if game is None:
            continue
        posteam = str(row.get("posteam") or "").upper().strip()
        defteam = str(row.get("defteam") or "").upper().strip()
        epa = _float(row.get("epa"))
        if not posteam or not defteam or posteam == defteam or epa is None:
            continue
        valid_teams = {str(game["home_team"]), str(game["away_team"])}
        if posteam not in valid_teams or defteam not in valid_teams:
            continue

        offense = ensure(game_id, posteam, defteam, posteam == game["home_team"])
        defense = ensure(game_id, defteam, posteam, defteam == game["home_team"])
        offense["offensive_plays"] += 1
        offense["offensive_epa_sum"] += epa
        defense["defensive_plays"] += 1
        defense["defensive_epa_sum"] += epa

        if _flag(row.get("success")):
            offense["success_plays"] += 1
        play_type = str(row.get("play_type") or "").lower().strip()
        if _flag(row.get("pass")) or play_type == "pass":
            offense["pass_epa_sum"] += epa
        if _flag(row.get("rush")) or play_type == "run":
            offense["rush_epa_sum"] += epa
        if _flag(row.get("interception")) or _flag(row.get("fumble_lost")):
            offense["turnovers"] += 1
        if _flag(row.get("sack")):
            offense["sacks_allowed"] += 1
        if _flag(row.get("special_teams_play")):
            offense["special_teams_epa_sum"] += epa

        qb_id = str(row.get("passer_player_id") or "").strip()
        if qb_id:
            offense["qb_gsis_ids"].add(qb_id)

    output: list[dict[str, Any]] = []
    for key in sorted(stats):
        row = stats[key]
        offensive_plays = int(row["offensive_plays"])
        defensive_plays = int(row["defensive_plays"])
        normalized = {
            "game_id": row["game_id"],
            "team": row["team"],
            "opponent": row["opponent"],
            "is_home": bool(row["is_home"]),
            "offensive_plays": offensive_plays,
            "offensive_epa_sum": round(float(row["offensive_epa_sum"]), 10),
            "offensive_epa_mean": (
                round(float(row["offensive_epa_sum"]) / offensive_plays, 10)
                if offensive_plays else None
            ),
            "defensive_epa_sum": round(float(row["defensive_epa_sum"]), 10),
            "defensive_epa_mean": (
                round(float(row["defensive_epa_sum"]) / defensive_plays, 10)
                if defensive_plays else None
            ),
            "success_plays": int(row["success_plays"]),
            "success_rate": (
                round(int(row["success_plays"]) / offensive_plays, 10)
                if offensive_plays else None
            ),
            "pass_epa_sum": round(float(row["pass_epa_sum"]), 10),
            "rush_epa_sum": round(float(row["rush_epa_sum"]), 10),
            "turnovers": int(row["turnovers"]),
            "sacks_allowed": int(row["sacks_allowed"]),
            "special_teams_epa_sum": round(float(row["special_teams_epa_sum"]), 10),
            "qb_gsis_ids": sorted(row["qb_gsis_ids"]),
            "pbp_snapshot_id": pbp_snapshot_id,
            "pbp_content_sha256": pbp_content_sha256,
            "probability_publishable": False,
            "can_execute": False,
        }
        normalized["row_inputs_hash"] = _canonical_hash(normalized)
        output.append(normalized)

    return output
