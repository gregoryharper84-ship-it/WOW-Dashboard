from __future__ import annotations

"""Pure, market-free MLB V2 pregame feature construction.

This module mirrors the validated research feature semantics.  All dynamic state
must contain results from dates strictly earlier than the target game date.
"""

import math
from datetime import date
from typing import Any

FEATURE_NAMES = [
    "elo_diff_100",
    "season_win_pct_diff",
    "season_run_diff_pg_diff",
    "t10_win_pct_diff",
    "t10_runs_pg_diff",
    "t10_runs_allowed_pg_diff",
    "t10_run_diff_pg_diff",
    "t10_hits_pg_diff",
    "t10_hr_pg_diff",
    "t10_bb_pg_diff",
    "t10_so_pg_diff",
    "t10_tb_pg_diff",
    "t20_run_diff_pg_diff",
    "venue_split_win_pct_diff",
    "venue_split_run_diff_pg_diff",
    "rest_days_diff",
    "games_last7_diff",
    "bp_season_era_diff",
    "bp_season_k9_diff",
    "bp_season_bb9_diff",
    "bp_l10_era_diff",
    "bp_l10_k9_diff",
    "bp_l10_bb9_diff",
    "bp_last3_pitches_diff",
    "bp_last3_outs_diff",
    "bp_last3_relief_appearances_diff",
    "starter_l5_era_diff",
    "starter_l5_k9_diff",
    "starter_l5_bb9_diff",
    "starter_l5_hr9_diff",
    "starter_l5_whip_diff",
    "starter_l5_outs_per_start_diff",
    "starter_l5_pitches_per_start_diff",
    "starter_season_era_diff",
    "starter_season_k9_diff",
    "starter_season_bb9_diff",
    "starter_rest_days_diff",
    "starter_missing_home",
    "starter_missing_away",
    "starter_log_prior_starts_home",
    "starter_log_prior_starts_away",
]

STARTER_DEFAULTS = {
    "era": 4.30,
    "k9": 8.50,
    "bb9": 3.30,
    "hr9": 1.20,
    "whip": 1.30,
    "outs_per_start": 15.0,
    "pitches_per_start": 85.0,
    "rest_days": 5.0,
}


def _mean(rows: list[dict[str, Any]], key: str, default: float = 0.0) -> float:
    if not rows:
        return float(default)
    return float(sum(float(r.get(key, 0.0)) for r in rows) / len(rows))


def _bp_rates(rows: list[dict[str, Any]]) -> tuple[float, float, float]:
    outs = sum(float(r.get("bp_out", 0.0)) for r in rows)
    er = sum(float(r.get("bp_er", 0.0)) for r in rows)
    so = sum(float(r.get("bp_so", 0.0)) for r in rows)
    bb = sum(float(r.get("bp_bb", 0.0)) for r in rows)
    if outs <= 0.0:
        return 4.30, 8.50, 3.30
    return 27.0 * er / outs, 27.0 * so / outs, 27.0 * bb / outs


def team_summary(history: list[dict[str, Any]], current_date: date, current_is_home: bool) -> dict[str, float]:
    # Defensive leak check: this function must never receive same/future-date outcomes.
    if any(r["date"] >= current_date for r in history):
        raise ValueError("MLB_V2_LOOKAHEAD_BLOCKED:team_history_contains_same_or_future_date")
    year = current_date.year
    season = [r for r in history if r["date"].year == year]
    t10 = history[-10:]
    t20 = history[-20:]
    venue = [r for r in season if bool(r.get("is_home")) == current_is_home][-40:]
    last_date = history[-1]["date"] if history else None
    rest_days = min(10.0, max(0.0, float((current_date - last_date).days))) if last_date else 5.0
    games_last7 = float(sum(1 for r in history if 0 < (current_date - r["date"]).days <= 7))
    bp_season_era, bp_season_k9, bp_season_bb9 = _bp_rates(season)
    bp_l10_era, bp_l10_k9, bp_l10_bb9 = _bp_rates(t10)
    last3 = [r for r in history if 0 < (current_date - r["date"]).days <= 3]
    return {
        "season_win_pct": _mean(season, "win", 0.5),
        "season_run_diff_pg": _mean(season, "run_diff", 0.0),
        "t10_win_pct": _mean(t10, "win", 0.5),
        "t10_runs_pg": _mean(t10, "runs", 0.0),
        "t10_runs_allowed_pg": _mean(t10, "runs_allowed", 0.0),
        "t10_run_diff_pg": _mean(t10, "run_diff", 0.0),
        "t10_hits_pg": _mean(t10, "hits", 0.0),
        "t10_hr_pg": _mean(t10, "hr", 0.0),
        "t10_bb_pg": _mean(t10, "bb", 0.0),
        "t10_so_pg": _mean(t10, "so", 0.0),
        "t10_tb_pg": _mean(t10, "tb", 0.0),
        "t20_run_diff_pg": _mean(t20, "run_diff", 0.0),
        "venue_split_win_pct": _mean(venue, "win", 0.5),
        "venue_split_run_diff_pg": _mean(venue, "run_diff", 0.0),
        "rest_days": rest_days,
        "games_last7": games_last7,
        "bp_season_era": bp_season_era,
        "bp_season_k9": bp_season_k9,
        "bp_season_bb9": bp_season_bb9,
        "bp_l10_era": bp_l10_era,
        "bp_l10_k9": bp_l10_k9,
        "bp_l10_bb9": bp_l10_bb9,
        "bp_last3_pitches": float(sum(float(r.get("bp_pitch", 0.0)) for r in last3)),
        "bp_last3_outs": float(sum(float(r.get("bp_out", 0.0)) for r in last3)),
        "bp_last3_relief_appearances": float(sum(float(r.get("bp_relief_appearances", 0.0)) for r in last3)),
    }


def _starter_rates(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {k: STARTER_DEFAULTS[k] for k in ("era", "k9", "bb9", "hr9", "whip", "outs_per_start", "pitches_per_start")}
    outs = sum(float(r.get("out", 0.0)) for r in rows)
    if outs <= 0.0:
        return _starter_rates([])
    er = sum(float(r.get("er", 0.0)) for r in rows)
    so = sum(float(r.get("so", 0.0)) for r in rows)
    bb = sum(float(r.get("bb", 0.0)) for r in rows)
    hr = sum(float(r.get("hr", 0.0)) for r in rows)
    hits = sum(float(r.get("h", 0.0)) for r in rows)
    pitches = sum(float(r.get("pitch", 0.0)) for r in rows)
    n = len(rows)
    return {
        "era": 27.0 * er / outs,
        "k9": 27.0 * so / outs,
        "bb9": 27.0 * bb / outs,
        "hr9": 27.0 * hr / outs,
        "whip": 3.0 * (hits + bb) / outs,
        "outs_per_start": outs / n,
        "pitches_per_start": pitches / n,
    }


def starter_summary(history: list[dict[str, Any]], current_date: date) -> dict[str, float]:
    if any(r["date"] >= current_date for r in history):
        raise ValueError("MLB_V2_LOOKAHEAD_BLOCKED:starter_history_contains_same_or_future_date")
    if not history:
        base = _starter_rates([])
        return {
            **{f"l5_{k}": v for k, v in base.items()},
            "season_era": STARTER_DEFAULTS["era"],
            "season_k9": STARTER_DEFAULTS["k9"],
            "season_bb9": STARTER_DEFAULTS["bb9"],
            "rest_days": STARTER_DEFAULTS["rest_days"],
            "missing": 1.0,
            "prior_starts": 0.0,
        }
    l5 = history[-5:]
    season = [r for r in history if r["date"].year == current_date.year]
    l5r = _starter_rates(l5)
    sr = _starter_rates(season)
    rest_days = min(14.0, max(0.0, float((current_date - history[-1]["date"]).days)))
    return {
        **{f"l5_{k}": v for k, v in l5r.items()},
        "season_era": sr["era"],
        "season_k9": sr["k9"],
        "season_bb9": sr["bb9"],
        "rest_days": rest_days,
        "missing": 0.0,
        "prior_starts": float(len(history)),
    }


def build_feature_vector(home_team: dict[str, float], away_team: dict[str, float], home_starter: dict[str, float], away_starter: dict[str, float], home_elo: float, away_elo: float) -> list[float]:
    d = lambda x, y, key: float(x[key] - y[key])
    x = [
        (float(home_elo) - float(away_elo)) / 100.0,
        d(home_team, away_team, "season_win_pct"),
        d(home_team, away_team, "season_run_diff_pg"),
        d(home_team, away_team, "t10_win_pct"),
        d(home_team, away_team, "t10_runs_pg"),
        d(home_team, away_team, "t10_runs_allowed_pg"),
        d(home_team, away_team, "t10_run_diff_pg"),
        d(home_team, away_team, "t10_hits_pg"),
        d(home_team, away_team, "t10_hr_pg"),
        d(home_team, away_team, "t10_bb_pg"),
        d(home_team, away_team, "t10_so_pg"),
        d(home_team, away_team, "t10_tb_pg"),
        d(home_team, away_team, "t20_run_diff_pg"),
        d(home_team, away_team, "venue_split_win_pct"),
        d(home_team, away_team, "venue_split_run_diff_pg"),
        d(home_team, away_team, "rest_days"),
        d(home_team, away_team, "games_last7"),
        d(home_team, away_team, "bp_season_era"),
        d(home_team, away_team, "bp_season_k9"),
        d(home_team, away_team, "bp_season_bb9"),
        d(home_team, away_team, "bp_l10_era"),
        d(home_team, away_team, "bp_l10_k9"),
        d(home_team, away_team, "bp_l10_bb9"),
        d(home_team, away_team, "bp_last3_pitches"),
        d(home_team, away_team, "bp_last3_outs"),
        d(home_team, away_team, "bp_last3_relief_appearances"),
        d(home_starter, away_starter, "l5_era"),
        d(home_starter, away_starter, "l5_k9"),
        d(home_starter, away_starter, "l5_bb9"),
        d(home_starter, away_starter, "l5_hr9"),
        d(home_starter, away_starter, "l5_whip"),
        d(home_starter, away_starter, "l5_outs_per_start"),
        d(home_starter, away_starter, "l5_pitches_per_start"),
        d(home_starter, away_starter, "season_era"),
        d(home_starter, away_starter, "season_k9"),
        d(home_starter, away_starter, "season_bb9"),
        d(home_starter, away_starter, "rest_days"),
        float(home_starter["missing"]),
        float(away_starter["missing"]),
        math.log1p(float(home_starter["prior_starts"])),
        math.log1p(float(away_starter["prior_starts"])),
    ]
    if len(x) != len(FEATURE_NAMES):
        raise RuntimeError(f"MLB_V2_FEATURE_COUNT_MISMATCH:{len(x)}!={len(FEATURE_NAMES)}")
    return x
