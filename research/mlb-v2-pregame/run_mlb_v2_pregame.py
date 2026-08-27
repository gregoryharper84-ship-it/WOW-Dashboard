from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

SEED = 20260827
YEARS = (2023, 2024, 2025)
SOURCE_URL = "https://raw.githubusercontent.com/chadwickbureau/retrosplits/refs/heads/master/daybyday/playing-{year}.csv"

GOVERNANCE = {
    "PROMOTED": False,
    "ACTIVE": False,
    "probability_publishable": False,
    "governed_probability_capability": "UNAVAILABLE",
    "can_execute": False,
    "research_only": True,
}

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


def iv(v: Any) -> int:
    try:
        if v in (None, ""):
            return 0
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return 0


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download_sources(raw_dir: Path) -> dict[int, Path]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for year in YEARS:
        path = raw_dir / f"playing-{year}.csv"
        if not path.exists() or path.stat().st_size < 1_000_000:
            url = SOURCE_URL.format(year=year)
            print(f"DOWNLOAD {url}")
            urllib.request.urlretrieve(url, path)
        paths[year] = path
        print(f"SOURCE year={year} bytes={path.stat().st_size} sha256={sha256_file(path)}")
    return paths


def new_team_game(row: dict[str, str]) -> dict[str, Any]:
    return {
        "game_key": row["game.key"],
        "game_date": row["game.date"],
        "game_number": iv(row.get("game.number")),
        "season_phase": row.get("season.phase"),
        "site": row.get("site.key"),
        "is_home": row.get("team.alignment") == "1",
        "team": row["team.key"],
        "opponent": row["opponent.key"],
        "runs": 0,
        "hits": 0,
        "hr": 0,
        "bb": 0,
        "so": 0,
        "tb": 0,
        "bp_out": 0,
        "bp_er": 0,
        "bp_so": 0,
        "bp_bb": 0,
        "bp_pitch": 0,
        "bp_relief_appearances": 0,
        "starter_id": None,
        "starter_out": 0,
        "starter_er": 0,
        "starter_so": 0,
        "starter_bb": 0,
        "starter_hr": 0,
        "starter_h": 0,
        "starter_pitch": 0,
        "starter_tbf": 0,
    }


def parse_season(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["game.key"], row["team.key"])
            if key not in agg:
                agg[key] = new_team_game(row)
            a = agg[key]
            a["runs"] += iv(row.get("B_R"))
            a["hits"] += iv(row.get("B_H"))
            a["hr"] += iv(row.get("B_HR"))
            a["bb"] += iv(row.get("B_BB"))
            a["so"] += iv(row.get("B_SO"))
            a["tb"] += iv(row.get("B_TB"))

            if row.get("P_G") == "1":
                if row.get("P_GS") == "1":
                    a["starter_id"] = row.get("person.key")
                    a["starter_out"] += iv(row.get("P_OUT"))
                    a["starter_er"] += iv(row.get("P_ER"))
                    a["starter_so"] += iv(row.get("P_SO"))
                    a["starter_bb"] += iv(row.get("P_BB"))
                    a["starter_hr"] += iv(row.get("P_HR"))
                    a["starter_h"] += iv(row.get("P_H"))
                    a["starter_pitch"] += iv(row.get("P_PITCH"))
                    a["starter_tbf"] += iv(row.get("P_TBF"))
                else:
                    a["bp_out"] += iv(row.get("P_OUT"))
                    a["bp_er"] += iv(row.get("P_ER"))
                    a["bp_so"] += iv(row.get("P_SO"))
                    a["bp_bb"] += iv(row.get("P_BB"))
                    a["bp_pitch"] += iv(row.get("P_PITCH"))
                    a["bp_relief_appearances"] += 1
    return agg


def pair_games(all_team_games: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[bool, dict[str, Any]]] = defaultdict(dict)
    for a in all_team_games.values():
        grouped[a["game_key"]][bool(a["is_home"])] = a

    games: list[dict[str, Any]] = []
    skipped_missing_side = 0
    skipped_tie = 0
    for game_key, sides in grouped.items():
        if True not in sides or False not in sides:
            skipped_missing_side += 1
            continue
        h, a = sides[True], sides[False]
        if h["runs"] == a["runs"]:
            skipped_tie += 1
            continue
        games.append(
            {
                "game_key": game_key,
                "game_date": h["game_date"],
                "year": int(h["game_date"][:4]),
                "game_number": h["game_number"],
                "season_phase": h["season_phase"],
                "site": h["site"],
                "home": h,
                "away": a,
                "y": int(h["runs"] > a["runs"]),
            }
        )
    games.sort(key=lambda g: (g["game_date"], g["game_key"]))
    print(
        f"PAIR games={len(games)} missing_side={skipped_missing_side} ties={skipped_tie} "
        f"date_range={games[0]['game_date']}..{games[-1]['game_date']}"
    )
    return games


def safe_rate(num: float, den: float, default: float) -> float:
    return float(num / den) if den else float(default)


def mean_stat(rows: list[dict[str, Any]], key: str, default: float = 0.0) -> float:
    if not rows:
        return float(default)
    return float(sum(float(r[key]) for r in rows) / len(rows))


def team_summary(
    history: list[dict[str, Any]], current_date: date, current_is_home: bool
) -> dict[str, float]:
    year = current_date.year
    season = [r for r in history if r["date"].year == year]
    t10 = history[-10:]
    t20 = history[-20:]
    venue = [r for r in season if bool(r["is_home"]) == current_is_home][-40:]

    def win_pct(rows: list[dict[str, Any]]) -> float:
        return mean_stat(rows, "win", 0.5)

    def rpg(rows: list[dict[str, Any]], key: str, default: float = 0.0) -> float:
        return mean_stat(rows, key, default)

    last_date = history[-1]["date"] if history else None
    rest_days = min(10.0, max(0.0, float((current_date - last_date).days))) if last_date else 5.0
    games_last7 = float(sum(1 for r in history if 0 < (current_date - r["date"]).days <= 7))

    def bp_rates(rows: list[dict[str, Any]]) -> tuple[float, float, float]:
        outs = sum(r["bp_out"] for r in rows)
        er = sum(r["bp_er"] for r in rows)
        so = sum(r["bp_so"] for r in rows)
        bb = sum(r["bp_bb"] for r in rows)
        if outs <= 0:
            return 4.30, 8.50, 3.30
        return 27.0 * er / outs, 27.0 * so / outs, 27.0 * bb / outs

    bp_season_era, bp_season_k9, bp_season_bb9 = bp_rates(season)
    bp_l10_era, bp_l10_k9, bp_l10_bb9 = bp_rates(t10)
    last3 = [r for r in history if 0 < (current_date - r["date"]).days <= 3]

    return {
        "prior_games": float(len(history)),
        "season_games": float(len(season)),
        "season_win_pct": win_pct(season),
        "season_run_diff_pg": rpg(season, "run_diff", 0.0),
        "t10_win_pct": win_pct(t10),
        "t10_runs_pg": rpg(t10, "runs", 0.0),
        "t10_runs_allowed_pg": rpg(t10, "runs_allowed", 0.0),
        "t10_run_diff_pg": rpg(t10, "run_diff", 0.0),
        "t10_hits_pg": rpg(t10, "hits", 0.0),
        "t10_hr_pg": rpg(t10, "hr", 0.0),
        "t10_bb_pg": rpg(t10, "bb", 0.0),
        "t10_so_pg": rpg(t10, "so", 0.0),
        "t10_tb_pg": rpg(t10, "tb", 0.0),
        "t20_run_diff_pg": rpg(t20, "run_diff", 0.0),
        "venue_split_win_pct": win_pct(venue),
        "venue_split_run_diff_pg": rpg(venue, "run_diff", 0.0),
        "rest_days": rest_days,
        "games_last7": games_last7,
        "bp_season_era": bp_season_era,
        "bp_season_k9": bp_season_k9,
        "bp_season_bb9": bp_season_bb9,
        "bp_l10_era": bp_l10_era,
        "bp_l10_k9": bp_l10_k9,
        "bp_l10_bb9": bp_l10_bb9,
        "bp_last3_pitches": float(sum(r["bp_pitch"] for r in last3)),
        "bp_last3_outs": float(sum(r["bp_out"] for r in last3)),
        "bp_last3_relief_appearances": float(sum(r["bp_relief_appearances"] for r in last3)),
    }


def starter_rates(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {
            "era": STARTER_DEFAULTS["era"],
            "k9": STARTER_DEFAULTS["k9"],
            "bb9": STARTER_DEFAULTS["bb9"],
            "hr9": STARTER_DEFAULTS["hr9"],
            "whip": STARTER_DEFAULTS["whip"],
            "outs_per_start": STARTER_DEFAULTS["outs_per_start"],
            "pitches_per_start": STARTER_DEFAULTS["pitches_per_start"],
        }
    outs = sum(r["out"] for r in rows)
    er = sum(r["er"] for r in rows)
    so = sum(r["so"] for r in rows)
    bb = sum(r["bb"] for r in rows)
    hr = sum(r["hr"] for r in rows)
    h = sum(r["h"] for r in rows)
    pitches = sum(r["pitch"] for r in rows)
    if outs <= 0:
        return starter_rates([])
    n = len(rows)
    return {
        "era": 27.0 * er / outs,
        "k9": 27.0 * so / outs,
        "bb9": 27.0 * bb / outs,
        "hr9": 27.0 * hr / outs,
        "whip": 3.0 * (h + bb) / outs,
        "outs_per_start": float(outs / n),
        "pitches_per_start": float(pitches / n),
    }


def starter_summary(
    pitcher_history: list[dict[str, Any]], current_date: date
) -> dict[str, float]:
    if not pitcher_history:
        return {
            **{f"l5_{k}": v for k, v in starter_rates([]).items()},
            "season_era": STARTER_DEFAULTS["era"],
            "season_k9": STARTER_DEFAULTS["k9"],
            "season_bb9": STARTER_DEFAULTS["bb9"],
            "rest_days": STARTER_DEFAULTS["rest_days"],
            "missing": 1.0,
            "prior_starts": 0.0,
        }
    l5 = pitcher_history[-5:]
    season = [r for r in pitcher_history if r["date"].year == current_date.year]
    l5r = starter_rates(l5)
    sr = starter_rates(season)
    last_date = pitcher_history[-1]["date"]
    rest_days = min(14.0, max(0.0, float((current_date - last_date).days)))
    return {
        **{f"l5_{k}": v for k, v in l5r.items()},
        "season_era": sr["era"],
        "season_k9": sr["k9"],
        "season_bb9": sr["bb9"],
        "rest_days": rest_days,
        "missing": 0.0,
        "prior_starts": float(len(pitcher_history)),
    }


def difference(h: dict[str, float], a: dict[str, float], key: str) -> float:
    return float(h[key] - a[key])


def build_feature_vector(
    home_team: dict[str, float],
    away_team: dict[str, float],
    home_starter: dict[str, float],
    away_starter: dict[str, float],
    home_elo: float,
    away_elo: float,
) -> list[float]:
    x = [
        (home_elo - away_elo) / 100.0,
        difference(home_team, away_team, "season_win_pct"),
        difference(home_team, away_team, "season_run_diff_pg"),
        difference(home_team, away_team, "t10_win_pct"),
        difference(home_team, away_team, "t10_runs_pg"),
        difference(home_team, away_team, "t10_runs_allowed_pg"),
        difference(home_team, away_team, "t10_run_diff_pg"),
        difference(home_team, away_team, "t10_hits_pg"),
        difference(home_team, away_team, "t10_hr_pg"),
        difference(home_team, away_team, "t10_bb_pg"),
        difference(home_team, away_team, "t10_so_pg"),
        difference(home_team, away_team, "t10_tb_pg"),
        difference(home_team, away_team, "t20_run_diff_pg"),
        difference(home_team, away_team, "venue_split_win_pct"),
        difference(home_team, away_team, "venue_split_run_diff_pg"),
        difference(home_team, away_team, "rest_days"),
        difference(home_team, away_team, "games_last7"),
        difference(home_team, away_team, "bp_season_era"),
        difference(home_team, away_team, "bp_season_k9"),
        difference(home_team, away_team, "bp_season_bb9"),
        difference(home_team, away_team, "bp_l10_era"),
        difference(home_team, away_team, "bp_l10_k9"),
        difference(home_team, away_team, "bp_l10_bb9"),
        difference(home_team, away_team, "bp_last3_pitches"),
        difference(home_team, away_team, "bp_last3_outs"),
        difference(home_team, away_team, "bp_last3_relief_appearances"),
        difference(home_starter, away_starter, "l5_era"),
        difference(home_starter, away_starter, "l5_k9"),
        difference(home_starter, away_starter, "l5_bb9"),
        difference(home_starter, away_starter, "l5_hr9"),
        difference(home_starter, away_starter, "l5_whip"),
        difference(home_starter, away_starter, "l5_outs_per_start"),
        difference(home_starter, away_starter, "l5_pitches_per_start"),
        difference(home_starter, away_starter, "season_era"),
        difference(home_starter, away_starter, "season_k9"),
        difference(home_starter, away_starter, "season_bb9"),
        difference(home_starter, away_starter, "rest_days"),
        home_starter["missing"],
        away_starter["missing"],
        math.log1p(home_starter["prior_starts"]),
        math.log1p(away_starter["prior_starts"]),
    ]
    assert len(x) == len(FEATURE_NAMES)
    return [float(v) for v in x]


def elo_expected(home_elo: float, away_elo: float, home_advantage: float = 35.0) -> float:
    return 1.0 / (1.0 + 10.0 ** (-((home_elo + home_advantage) - away_elo) / 400.0))


def build_pregame_dataset(games: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    team_hist: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pitcher_hist: dict[str, list[dict[str, Any]]] = defaultdict(list)
    elo: dict[str, float] = defaultdict(lambda: 1500.0)
    features: list[dict[str, Any]] = []

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in games:
        by_date[g["game_date"]].append(g)

    current_year: int | None = None
    excluded_team_history = 0
    starter_missing_rows = 0

    for ds in sorted(by_date):
        d = date.fromisoformat(ds)
        if current_year is None:
            current_year = d.year
        elif d.year != current_year:
            for team in list(elo):
                elo[team] = 1500.0 + 0.75 * (elo[team] - 1500.0)
            current_year = d.year

        date_games = sorted(by_date[ds], key=lambda g: g["game_key"])

        # Freeze all features for this calendar date before using any same-date result.
        for g in date_games:
            h = g["home"]
            a = g["away"]
            h_hist = team_hist[h["team"]]
            a_hist = team_hist[a["team"]]
            if len(h_hist) < 10 or len(a_hist) < 10:
                excluded_team_history += 1
                continue

            ht = team_summary(h_hist, d, True)
            at = team_summary(a_hist, d, False)
            hs = starter_summary(pitcher_hist[h["starter_id"]] if h["starter_id"] else [], d)
            ass = starter_summary(pitcher_hist[a["starter_id"]] if a["starter_id"] else [], d)
            if hs["missing"] or ass["missing"]:
                starter_missing_rows += 1

            x = build_feature_vector(ht, at, hs, ass, elo[h["team"]], elo[a["team"]])
            features.append(
                {
                    "game_key": g["game_key"],
                    "game_date": ds,
                    "year": d.year,
                    "season_phase": g["season_phase"],
                    "home_team": h["team"],
                    "away_team": a["team"],
                    "home_starter_id": h["starter_id"],
                    "away_starter_id": a["starter_id"],
                    "x": x,
                    "y": g["y"],
                }
            )

        # Accumulate Elo deltas from the frozen pre-date ratings.
        elo_delta: dict[str, float] = defaultdict(float)
        for g in date_games:
            h, a = g["home"], g["away"]
            exp = elo_expected(elo[h["team"]], elo[a["team"]])
            delta = 20.0 * (float(g["y"]) - exp)
            elo_delta[h["team"]] += delta
            elo_delta[a["team"]] -= delta

        # Only now commit same-date outcomes to state.
        for g in date_games:
            h, a = g["home"], g["away"]
            for side, opp in ((h, a), (a, h)):
                team_hist[side["team"]].append(
                    {
                        "date": d,
                        "is_home": bool(side["is_home"]),
                        "win": float(side["runs"] > opp["runs"]),
                        "runs": float(side["runs"]),
                        "runs_allowed": float(opp["runs"]),
                        "run_diff": float(side["runs"] - opp["runs"]),
                        "hits": float(side["hits"]),
                        "hr": float(side["hr"]),
                        "bb": float(side["bb"]),
                        "so": float(side["so"]),
                        "tb": float(side["tb"]),
                        "bp_out": side["bp_out"],
                        "bp_er": side["bp_er"],
                        "bp_so": side["bp_so"],
                        "bp_bb": side["bp_bb"],
                        "bp_pitch": side["bp_pitch"],
                        "bp_relief_appearances": side["bp_relief_appearances"],
                    }
                )
                if side["starter_id"]:
                    pitcher_hist[side["starter_id"]].append(
                        {
                            "date": d,
                            "out": side["starter_out"],
                            "er": side["starter_er"],
                            "so": side["starter_so"],
                            "bb": side["starter_bb"],
                            "hr": side["starter_hr"],
                            "h": side["starter_h"],
                            "pitch": side["starter_pitch"],
                            "tbf": side["starter_tbf"],
                        }
                    )
        for team, delta in elo_delta.items():
            elo[team] += delta

    features.sort(key=lambda r: (r["game_date"], r["game_key"]))
    audit = {
        "rows": len(features),
        "excluded_insufficient_team_history": excluded_team_history,
        "rows_with_at_least_one_missing_prior_starter_history": starter_missing_rows,
        "date_range": [features[0]["game_date"], features[-1]["game_date"]],
        "same_calendar_date_outcome_leakage": False,
        "date_batching_rule": "ALL_FEATURES_FOR_DATE_FROZEN_BEFORE_ANY_DATE_RESULT_UPDATE",
    }
    return features, audit


def split_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    splits = {"train": [], "calibration": [], "validation": [], "test": []}
    for r in rows:
        ds = r["game_date"]
        if ds <= "2024-06-30":
            splits["train"].append(r)
        elif "2024-07-01" <= ds <= "2024-08-31":
            splits["calibration"].append(r)
        elif "2024-09-01" <= ds <= "2024-12-31":
            splits["validation"].append(r)
        elif ds >= "2025-01-01":
            splits["test"].append(r)
    return splits


def xy(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.asarray([r["x"] for r in rows], dtype=float),
        np.asarray([r["y"] for r in rows], dtype=int),
    )


def reliability_bins(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> list[dict[str, Any]]:
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out = []
    for i in range(n_bins):
        mask = (p >= edges[i]) & ((p <= edges[i + 1]) if i == n_bins - 1 else (p < edges[i + 1]))
        n = int(mask.sum())
        out.append(
            {
                "bin": i + 1,
                "lo": float(edges[i]),
                "hi": float(edges[i + 1]),
                "count": n,
                "mean_pred": float(p[mask].mean()) if n else None,
                "observed": float(y[mask].mean()) if n else None,
            }
        )
    return out


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1.0 - 1e-12)
    y = np.asarray(y, dtype=int)
    bins = reliability_bins(y, p)
    n = len(y)
    ece = sum(
        (b["count"] / n) * abs(float(b["mean_pred"]) - float(b["observed"]))
        for b in bins
        if b["count"]
    )
    return {
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "roc_auc": float(roc_auc_score(y, p)),
        "accuracy_at_0_5": float(accuracy_score(y, p >= 0.5)),
        "ece_10bin": float(ece),
        "probability_stats": {
            "min": float(p.min()),
            "p05": float(np.quantile(p, 0.05)),
            "median": float(np.median(p)),
            "p95": float(np.quantile(p, 0.95)),
            "max": float(p.max()),
            "mean": float(p.mean()),
            "std": float(p.std()),
        },
        "reliability_bins": bins,
    }


def bootstrap_brier_improvement(
    y: np.ndarray, baseline: np.ndarray, candidate: np.ndarray, n_iter: int, seed: int
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    n = len(y)
    diffs = np.empty(n_iter, dtype=float)
    for i in range(n_iter):
        idx = rng.integers(0, n, n)
        b0 = np.mean((baseline[idx] - y[idx]) ** 2)
        b1 = np.mean((candidate[idx] - y[idx]) ** 2)
        diffs[i] = b0 - b1
    return {
        "improvement_mean": float(diffs.mean()),
        "ci95": [float(np.quantile(diffs, 0.025)), float(np.quantile(diffs, 0.975))],
        "p_improvement_gt_0": float(np.mean(diffs > 0)),
    }


@dataclass
class Candidate:
    name: str
    model_factory: Callable[[], Any]


def model_candidates() -> list[Candidate]:
    return [
        Candidate(
            "logreg_c0_1",
            lambda: Pipeline(
                [
                    ("scaler", StandardScaler()),
                    (
                        "model",
                        LogisticRegression(
                            C=0.1, max_iter=5000, solver="lbfgs", random_state=SEED
                        ),
                    ),
                ]
            ),
        ),
        Candidate(
            "logreg_c1_0",
            lambda: Pipeline(
                [
                    ("scaler", StandardScaler()),
                    (
                        "model",
                        LogisticRegression(
                            C=1.0, max_iter=5000, solver="lbfgs", random_state=SEED
                        ),
                    ),
                ]
            ),
        ),
        Candidate(
            "histgb_depth3",
            lambda: HistGradientBoostingClassifier(
                learning_rate=0.05,
                max_iter=250,
                max_depth=3,
                min_samples_leaf=30,
                l2_regularization=1.0,
                random_state=SEED,
            ),
        ),
    ]


def fit_calibrators(p_cal: np.ndarray, y_cal: np.ndarray) -> dict[str, Any]:
    platt = LogisticRegression(C=1.0, max_iter=5000, solver="lbfgs", random_state=SEED)
    platt.fit(p_cal.reshape(-1, 1), y_cal)
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(p_cal, y_cal)
    return {"raw": None, "platt": platt, "isotonic": iso}


def apply_calibrator(cal: Any, name: str, p: np.ndarray) -> np.ndarray:
    if name == "raw":
        return np.asarray(p, dtype=float)
    if name == "platt":
        return cal.predict_proba(np.asarray(p).reshape(-1, 1))[:, 1]
    if name == "isotonic":
        return np.asarray(cal.predict(p), dtype=float)
    raise ValueError(name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("research/mlb-v2-pregame-out"))
    ap.add_argument("--bootstraps", type=int, default=10000)
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    source_paths = download_sources(out / "raw")
    source_manifest = {
        str(year): {
            "url": SOURCE_URL.format(year=year),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for year, path in source_paths.items()
    }

    all_team_games: dict[tuple[str, str], dict[str, Any]] = {}
    for year in YEARS:
        parsed = parse_season(source_paths[year])
        all_team_games.update(parsed)
        print(f"PARSED year={year} team_game_rows={len(parsed)}")

    games = pair_games(all_team_games)
    rows, feature_audit = build_pregame_dataset(games)
    splits = split_rows(rows)
    for name, items in splits.items():
        if not items:
            raise RuntimeError(f"Empty split: {name}")
        print(
            f"SPLIT {name} n={len(items)} dates={items[0]['game_date']}..{items[-1]['game_date']} "
            f"home_win_rate={np.mean([r['y'] for r in items]):.6f}"
        )

    Xtr, ytr = xy(splits["train"])
    Xcal, ycal = xy(splits["calibration"])
    Xval, yval = xy(splits["validation"])
    Xtest, ytest = xy(splits["test"])

    valid_results: list[dict[str, Any]] = []
    fitted: dict[str, tuple[Any, dict[str, Any]]] = {}
    for c in model_candidates():
        model = c.model_factory()
        model.fit(Xtr, ytr)
        pcal = model.predict_proba(Xcal)[:, 1]
        calibrators = fit_calibrators(pcal, ycal)
        pval_raw = model.predict_proba(Xval)[:, 1]
        fitted[c.name] = (model, calibrators)
        for cal_name, cal in calibrators.items():
            pval = apply_calibrator(cal, cal_name, pval_raw)
            m = metrics(yval, pval)
            valid_results.append(
                {
                    "model": c.name,
                    "calibration": cal_name,
                    "validation_metrics": m,
                }
            )
            print(
                f"VALID model={c.name} cal={cal_name} brier={m['brier']:.6f} "
                f"logloss={m['log_loss']:.6f} auc={m['roc_auc']:.6f}"
            )

    valid_results.sort(
        key=lambda r: (
            r["validation_metrics"]["brier"],
            r["validation_metrics"]["log_loss"],
            r["model"],
            r["calibration"],
        )
    )
    selected = valid_results[0]
    selected_model_name = selected["model"]
    selected_cal_name = selected["calibration"]
    selected_model, selected_calibrators = fitted[selected_model_name]
    selected_cal = selected_calibrators[selected_cal_name]
    print(f"SELECTED model={selected_model_name} calibration={selected_cal_name}")

    ptest_raw = selected_model.predict_proba(Xtest)[:, 1]
    ptest = apply_calibrator(selected_cal, selected_cal_name, ptest_raw)
    train_home_rate = float(ytr.mean())
    p_naive = np.full(len(ytest), train_home_rate, dtype=float)
    p_home54 = np.full(len(ytest), 0.54, dtype=float)

    test_metrics = {
        "naive_training_home_rate": metrics(ytest, p_naive),
        "home_field_0_54": metrics(ytest, p_home54),
        "selected_v2": metrics(ytest, ptest),
        "selected_raw_precalibration": metrics(ytest, ptest_raw),
    }
    boot_naive = bootstrap_brier_improvement(
        ytest, p_naive, ptest, args.bootstraps, SEED
    )
    boot_home54 = bootstrap_brier_improvement(
        ytest, p_home54, ptest, args.bootstraps, SEED + 1
    )

    brier_gain = (
        test_metrics["naive_training_home_rate"]["brier"]
        - test_metrics["selected_v2"]["brier"]
    )
    logloss_gain = (
        test_metrics["naive_training_home_rate"]["log_loss"]
        - test_metrics["selected_v2"]["log_loss"]
    )
    secure_gain = boot_naive["ci95"][0] > 0.0
    research_gate = bool(
        brier_gain > 0.0
        and logloss_gain > 0.0
        and secure_gain
        and test_metrics["selected_v2"]["roc_auc"] > 0.5
    )

    results = {
        "status": "MLB_V2_PREGAME_RESEARCH_VALIDATION",
        "source_manifest": source_manifest,
        "feature_schema_version": "MLB_PREGAME_V2_20260827",
        "feature_names": FEATURE_NAMES,
        "feature_count": len(FEATURE_NAMES),
        "feature_audit": feature_audit,
        "split_policy": {
            "train": "all eligible rows through 2024-06-30",
            "calibration": "2024-07-01 through 2024-08-31",
            "validation_selection": "2024-09-01 through 2024-12-31",
            "locked_forward_test": "all eligible 2025 rows",
            "same_date_batching": "features frozen before any same-date outcome update",
            "test_used_for_model_or_calibrator_selection": False,
        },
        "split_counts": {k: len(v) for k, v in splits.items()},
        "split_date_ranges": {
            k: [v[0]["game_date"], v[-1]["game_date"]] for k, v in splits.items()
        },
        "split_home_win_rates": {
            k: float(np.mean([r["y"] for r in v])) for k, v in splits.items()
        },
        "validation_candidates": valid_results,
        "selected": {
            "model": selected_model_name,
            "calibration": selected_cal_name,
            "validation_metrics": selected["validation_metrics"],
        },
        "test_metrics": test_metrics,
        "paired_bootstrap_brier": {
            "selected_vs_naive_training_home_rate": boot_naive,
            "selected_vs_home_field_0_54": boot_home54,
        },
        "test_point_improvement": {
            "brier_vs_naive": float(brier_gain),
            "log_loss_vs_naive": float(logloss_gain),
        },
        "research_evidence_gate": {
            "directional_brier_gain": bool(brier_gain > 0.0),
            "directional_logloss_gain": bool(logloss_gain > 0.0),
            "bootstrap_brier_ci_lower_gt_zero": bool(secure_gain),
            "auc_gt_0_5": bool(test_metrics["selected_v2"]["roc_auc"] > 0.5),
            "research_gate_pass": research_gate,
            "note": "Even a research gate pass does not promote or activate the governed probability capability.",
        },
        "governance": GOVERNANCE,
    }

    (out / "mlb_v2_results.json").write_text(json.dumps(results, indent=2))
    (out / "mlb_v2_feature_schema.json").write_text(
        json.dumps(
            {
                "version": "MLB_PREGAME_V2_20260827",
                "feature_names": FEATURE_NAMES,
                "count": len(FEATURE_NAMES),
                "leakage_rule": "STRICTLY_PRIOR_CALENDAR_DATES_FOR_DYNAMIC_STATE",
                "same_date_batching": True,
                "governance": GOVERNANCE,
            },
            indent=2,
        )
    )
    with (out / "mlb_v2_features.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")

    with (out / "mlb_v2_test_predictions.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "game_key",
                "game_date",
                "home_team",
                "away_team",
                "home_starter_id",
                "away_starter_id",
                "home_win",
                "naive",
                "homefield_054",
                "selected_raw",
                "selected_probability",
            ]
        )
        for i, r in enumerate(splits["test"]):
            w.writerow(
                [
                    r["game_key"],
                    r["game_date"],
                    r["home_team"],
                    r["away_team"],
                    r["home_starter_id"],
                    r["away_starter_id"],
                    r["y"],
                    p_naive[i],
                    p_home54[i],
                    ptest_raw[i],
                    ptest[i],
                ]
            )

    joblib.dump(selected_model, out / "selected_base_model.joblib")
    joblib.dump(selected_cal, out / "selected_calibrator.joblib")
    (out / "governance.json").write_text(json.dumps(GOVERNANCE, indent=2))

    print("V2_RESULT", json.dumps({
        "selected": results["selected"],
        "test_metrics": {
            "naive_brier": test_metrics["naive_training_home_rate"]["brier"],
            "selected_brier": test_metrics["selected_v2"]["brier"],
            "selected_log_loss": test_metrics["selected_v2"]["log_loss"],
            "selected_auc": test_metrics["selected_v2"]["roc_auc"],
            "selected_ece": test_metrics["selected_v2"]["ece_10bin"],
        },
        "bootstrap_vs_naive": boot_naive,
        "research_gate": results["research_evidence_gate"],
        "governance": GOVERNANCE,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
