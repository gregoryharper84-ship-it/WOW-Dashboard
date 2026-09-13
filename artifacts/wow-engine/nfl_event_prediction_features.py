"""Build one leakage-safe NFL P2 feature vector for a future team event.

The implementation intentionally reuses P2's exact prior functions and feature
order. The target event has no outcome and cannot enter its own history.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
import json
from typing import Any, Iterable

from nfl_event_features_p2 import (
    FEATURE_ORDER,
    FEATURE_SCHEMA_VERSION,
    MIN_PRIOR_GAMES,
    _d,
    _edge,
    _prior_metrics,
    _team_game_record,
)


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_prediction_feature_row(
    *,
    training_games: Iterable[dict[str, Any]],
    team_summaries: Iterable[dict[str, Any]],
    game_id: str,
    season: int,
    week: int,
    gameday: str | date,
    home_team: str,
    away_team: str,
    schedule_content_sha256: str,
) -> dict[str, Any]:
    games = {str(g["game_id"]): dict(g) for g in training_games}
    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for summary in team_summaries:
        game = games.get(str(summary.get("game_id")))
        if game is None:
            continue
        record = _team_game_record(game, dict(summary))
        history[record["team"]].append(record)

    target_date = _d(gameday)
    home = str(home_team).upper().strip()
    away = str(away_team).upper().strip()
    if not game_id or not home or not away or home == away:
        raise ValueError("NFL_TARGET_EVENT_IDENTITY_INVALID")

    hm = _prior_metrics(history.get(home, []), target_date=target_date, season=int(season))
    am = _prior_metrics(history.get(away, []), target_date=target_date, season=int(season))
    prior_dates = [d for d in (hm["max_prior_gameday"], am["max_prior_gameday"]) if d]
    max_prior = max(prior_dates) if prior_dates else None
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
        "week": float(week),
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

    source_hashes = sorted(set(hm["source_hashes"] + am["source_hashes"] + [str(schedule_content_sha256 or "")]) - {""})
    row = {
        "game_id": str(game_id),
        "season": int(season),
        "week": int(week),
        "gameday": target_date.isoformat(),
        "home_team": home,
        "away_team": away,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_cutoff_date": target_date.isoformat(),
        "max_prior_gameday": max_prior,
        "feature_order": list(FEATURE_ORDER),
        "features": features,
        "feature_vector": vector,
        "target_outcome": None,
        "home_prior_games": hm["prior_games"],
        "away_prior_games": am["prior_games"],
        "training_eligible": not exclusions,
        "exclusion_reasons": sorted(set(exclusions)),
        "source_content_sha256s": source_hashes,
        "probability_publishable": False,
        "can_execute": False,
    }
    row["row_inputs_hash"] = _hash(row)
    return row
