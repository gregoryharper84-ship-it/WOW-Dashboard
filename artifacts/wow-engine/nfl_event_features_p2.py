"""WOW v16 NFL ML P2: deterministic prior-only pregame feature rows.

The current game's score/outcome is a label only and is never included in the
feature vector. A contributing historical game must have gameday < target
Gameday. Ties are preserved as a third target state. No probability is fit or
published in P2.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
import json
from typing import Any, Iterable

FEATURE_SCHEMA_VERSION = "NFL_EVENT_PREGAME_PRIOR_V1"
ROLLING_GAMES = 8
MIN_PRIOR_GAMES = 4

FEATURE_ORDER = (
    "week",
    "home_prior_games",
    "away_prior_games",
    "home_season_prior_games",
    "away_season_prior_games",
    "home_rest_days",
    "away_rest_days",
    "home_recent_off_epa_pp",
    "away_recent_off_epa_pp",
    "off_epa_edge",
    "home_recent_def_epa_pp",
    "away_recent_def_epa_pp",
    "def_epa_edge",
    "home_recent_success_rate",
    "away_recent_success_rate",
    "success_rate_edge",
    "home_recent_turnovers_pg",
    "away_recent_turnovers_pg",
    "turnover_edge",
    "home_recent_sacks_allowed_pg",
    "away_recent_sacks_allowed_pg",
    "sack_edge",
    "home_recent_st_epa_pg",
    "away_recent_st_epa_pg",
    "st_epa_edge",
    "home_prior_win_rate",
    "away_prior_win_rate",
    "win_rate_edge",
    "home_prior_point_diff_pg",
    "away_prior_point_diff_pg",
    "point_diff_edge",
)


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _d(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x


def _team_game_record(game: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    team = str(summary["team"])
    is_home = bool(summary["is_home"])
    pf = int(game["home_score"] if is_home else game["away_score"])
    pa = int(game["away_score"] if is_home else game["home_score"])
    return {
        "game_id": str(game["game_id"]),
        "season": int(game["season"]),
        "gameday": _d(game["gameday"]),
        "team": team,
        "offensive_plays": int(summary.get("offensive_plays") or 0),
        "offensive_epa_sum": _f(summary.get("offensive_epa_sum")) or 0.0,
        "defensive_epa_mean": _f(summary.get("defensive_epa_mean")),
        "success_plays": int(summary.get("success_plays") or 0),
        "turnovers": int(summary.get("turnovers") or 0),
        "sacks_allowed": int(summary.get("sacks_allowed") or 0),
        "special_teams_epa_sum": _f(summary.get("special_teams_epa_sum")) or 0.0,
        "points_for": pf,
        "points_against": pa,
        "win_value": 0.5 if pf == pa else (1.0 if pf > pa else 0.0),
        "schedule_content_sha256": str(game.get("schedule_content_sha256") or ""),
        "pbp_content_sha256": str(summary.get("pbp_content_sha256") or ""),
    }


def _prior_metrics(records: list[dict[str, Any]], *, target_date: date, season: int) -> dict[str, Any]:
    prior = [r for r in records if r["gameday"] < target_date]
    prior.sort(key=lambda r: (r["gameday"], r["game_id"]))
    recent = prior[-ROLLING_GAMES:]
    season_prior = [r for r in prior if r["season"] == season]
    max_prior = prior[-1]["gameday"] if prior else None
    rest_days = (target_date - max_prior).days if max_prior else None

    plays = sum(r["offensive_plays"] for r in recent)
    off_epa_pp = (sum(r["offensive_epa_sum"] for r in recent) / plays) if plays else None
    success_rate = (sum(r["success_plays"] for r in recent) / plays) if plays else None
    def_values = [r["defensive_epa_mean"] for r in recent if r["defensive_epa_mean"] is not None]
    def_epa_pp = (sum(def_values) / len(def_values)) if def_values else None
    n = len(recent)

    return {
        "prior_games": len(prior),
        "season_prior_games": len(season_prior),
        "max_prior_gameday": max_prior.isoformat() if max_prior else None,
        "rest_days": float(rest_days) if rest_days is not None else None,
        "recent_off_epa_pp": off_epa_pp,
        "recent_def_epa_pp": def_epa_pp,
        "recent_success_rate": success_rate,
        "recent_turnovers_pg": (sum(r["turnovers"] for r in recent) / n) if n else None,
        "recent_sacks_allowed_pg": (sum(r["sacks_allowed"] for r in recent) / n) if n else None,
        "recent_st_epa_pg": (sum(r["special_teams_epa_sum"] for r in recent) / n) if n else None,
        "prior_win_rate": (sum(r["win_value"] for r in recent) / n) if n else None,
        "prior_point_diff_pg": (sum(r["points_for"] - r["points_against"] for r in recent) / n) if n else None,
        "source_hashes": sorted({h for r in prior for h in (r["schedule_content_sha256"], r["pbp_content_sha256"]) if h}),
    }


def _edge(home: float | None, away: float | None) -> float | None:
    return None if home is None or away is None else float(home - away)


def build_prior_feature_rows(
    training_games: Iterable[dict[str, Any]],
    team_summaries: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    games = {str(g["game_id"]): dict(g) for g in training_games}
    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    summaries_by_game: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for summary in team_summaries:
        game = games.get(str(summary.get("game_id")))
        if game is None:
            continue
        record = _team_game_record(game, dict(summary))
        history[record["team"]].append(record)
        summaries_by_game[str(game["game_id"])][record["team"]] = dict(summary)

    output: list[dict[str, Any]] = []
    ordered_games = sorted(games.values(), key=lambda g: (_d(g["gameday"]), str(g["game_id"])))
    for game in ordered_games:
        target_date = _d(game["gameday"])
        home = str(game["home_team"])
        away = str(game["away_team"])
        hm = _prior_metrics(history.get(home, []), target_date=target_date, season=int(game["season"]))
        am = _prior_metrics(history.get(away, []), target_date=target_date, season=int(game["season"]))
        max_prior_dates = [d for d in (hm["max_prior_gameday"], am["max_prior_gameday"]) if d]
        max_prior = max(max_prior_dates) if max_prior_dates else None
        exclusions: list[str] = []
        if hm["prior_games"] < MIN_PRIOR_GAMES:
            exclusions.append("HOME_PRIOR_SAMPLE_THIN")
        if am["prior_games"] < MIN_PRIOR_GAMES:
            exclusions.append("AWAY_PRIOR_SAMPLE_THIN")
        required_rates = (
            hm["recent_off_epa_pp"], am["recent_off_epa_pp"],
            hm["recent_def_epa_pp"], am["recent_def_epa_pp"],
            hm["recent_success_rate"], am["recent_success_rate"],
        )
        if any(v is None for v in required_rates):
            exclusions.append("PRIOR_RATE_FEATURE_MISSING")
        if max_prior is not None and _d(max_prior) >= target_date:
            exclusions.append("TEMPORAL_LEAKAGE_DETECTED")

        features = {
            "week": float(game["week"]),
            "home_prior_games": float(hm["prior_games"]),
            "away_prior_games": float(am["prior_games"]),
            "home_season_prior_games": float(hm["season_prior_games"]),
            "away_season_prior_games": float(am["season_prior_games"]),
            "home_rest_days": hm["rest_days"],
            "away_rest_days": am["rest_days"],
            "home_recent_off_epa_pp": hm["recent_off_epa_pp"],
            "away_recent_off_epa_pp": am["recent_off_epa_pp"],
            "off_epa_edge": _edge(hm["recent_off_epa_pp"], am["recent_off_epa_pp"]),
            "home_recent_def_epa_pp": hm["recent_def_epa_pp"],
            "away_recent_def_epa_pp": am["recent_def_epa_pp"],
            "def_epa_edge": _edge(am["recent_def_epa_pp"], hm["recent_def_epa_pp"]),
            "home_recent_success_rate": hm["recent_success_rate"],
            "away_recent_success_rate": am["recent_success_rate"],
            "success_rate_edge": _edge(hm["recent_success_rate"], am["recent_success_rate"]),
            "home_recent_turnovers_pg": hm["recent_turnovers_pg"],
            "away_recent_turnovers_pg": am["recent_turnovers_pg"],
            "turnover_edge": _edge(am["recent_turnovers_pg"], hm["recent_turnovers_pg"]),
            "home_recent_sacks_allowed_pg": hm["recent_sacks_allowed_pg"],
            "away_recent_sacks_allowed_pg": am["recent_sacks_allowed_pg"],
            "sack_edge": _edge(am["recent_sacks_allowed_pg"], hm["recent_sacks_allowed_pg"]),
            "home_recent_st_epa_pg": hm["recent_st_epa_pg"],
            "away_recent_st_epa_pg": am["recent_st_epa_pg"],
            "st_epa_edge": _edge(hm["recent_st_epa_pg"], am["recent_st_epa_pg"]),
            "home_prior_win_rate": hm["prior_win_rate"],
            "away_prior_win_rate": am["prior_win_rate"],
            "win_rate_edge": _edge(hm["prior_win_rate"], am["prior_win_rate"]),
            "home_prior_point_diff_pg": hm["prior_point_diff_pg"],
            "away_prior_point_diff_pg": am["prior_point_diff_pg"],
            "point_diff_edge": _edge(hm["prior_point_diff_pg"], am["prior_point_diff_pg"]),
        }
        vector = [features[name] for name in FEATURE_ORDER]
        if any(v is None for v in vector):
            exclusions.append("FEATURE_VECTOR_INCOMPLETE")

        target = "TIE" if bool(game.get("tie")) else ("HOME_WIN" if bool(game.get("home_win")) else "AWAY_WIN")
        source_hashes = sorted(set(hm["source_hashes"] + am["source_hashes"] + [str(game.get("schedule_content_sha256") or "")]) - {""})
        row = {
            "game_id": str(game["game_id"]),
            "season": int(game["season"]),
            "week": int(game["week"]),
            "gameday": target_date.isoformat(),
            "home_team": home,
            "away_team": away,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_cutoff_date": target_date.isoformat(),
            "max_prior_gameday": max_prior,
            "feature_order": list(FEATURE_ORDER),
            "features": features,
            "feature_vector": vector,
            "target_outcome": target,
            "home_prior_games": hm["prior_games"],
            "away_prior_games": am["prior_games"],
            "training_eligible": not exclusions,
            "exclusion_reasons": sorted(set(exclusions)),
            "source_content_sha256s": source_hashes,
            "probability_publishable": False,
            "can_execute": False,
        }
        row["row_inputs_hash"] = _hash(row)
        output.append(row)
    return output
