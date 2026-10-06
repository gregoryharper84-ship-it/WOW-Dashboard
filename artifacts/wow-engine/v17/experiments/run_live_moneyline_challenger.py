"""Execute chronological Class-C MLB/NFL live-moneyline replay."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss

from v17.experiments.live_moneyline_challenger import (
    ResearchBundle,
    fit_research_bundle,
    research_receipt,
)
from v17.experiments.live_moneyline_replay_data import (
    MLB_FEATURES,
    NFL_FEATURES,
    build_mlb_replay,
    build_nfl_replay,
)


def _vector_probabilities(bundle: ResearchBundle, frame: pd.DataFrame) -> np.ndarray:
    x = frame.loc[:, bundle.feature_names].to_numpy(float)
    mean = np.asarray(bundle.scaler_mean)
    scale = np.asarray(bundle.scaler_scale)
    coef = np.asarray(bundle.coefficients)
    logits = ((x - mean) / scale) @ coef + bundle.intercept
    logits = np.clip(bundle.platt_a * logits + bundle.platt_b, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-logits))


def _metric_block(frame: pd.DataFrame, p_home: np.ndarray) -> dict[str, Any]:
    y = frame["home_win"].to_numpy(int)
    return {
        "n": int(len(frame)),
        "game_n": int(frame["game_id"].nunique()),
        "brier": float(brier_score_loss(y, p_home)),
        "log_loss": float(log_loss(y, p_home, labels=[0, 1])),
    }


def _slice_metrics(sport: str, validation: pd.DataFrame, p_home: np.ndarray) -> dict[str, Any]:
    out: dict[str, Any] = {}
    score_abs = validation["score_diff"].abs()
    masks: dict[str, pd.Series]
    if sport == "NFL":
        seconds = validation["seconds_remaining"]
        masks = {
            "early": seconds > 2400,
            "middle": (seconds <= 2400) & (seconds > 900),
            "late": seconds <= 900,
            "one_score_or_less": score_abs <= 8,
            "lead_9_plus": score_abs >= 9,
            "home_possession": validation["possession_home"] == 1,
            "away_possession": validation["possession_home"] == 0,
        }
    else:
        inning = validation["inning"]
        masks = {
            "early": inning <= 3,
            "middle": (inning >= 4) & (inning <= 6),
            "late": inning >= 7,
            "one_run_or_tied": score_abs <= 1,
            "lead_2_plus": score_abs >= 2,
            "bases_empty": validation["runners_on"] == 0,
            "runners_on": validation["runners_on"] >= 1,
        }
    for name, mask in masks.items():
        indexes = np.flatnonzero(mask.to_numpy(bool))
        if len(indexes) < 50:
            out[name] = {"n": int(len(indexes)), "status": "SAMPLE_TOO_THIN"}
            continue
        out[name] = _metric_block(validation.iloc[indexes], p_home[indexes])
    return out


def _lower_bound_replay(bundle: ResearchBundle, validation: pd.DataFrame, p_home: np.ndarray) -> dict[str, Any]:
    bins = list(bundle.calibration_bins)
    y = validation["home_win"].to_numpy(int)
    selected_home = p_home >= 0.5
    selected_probability = np.where(selected_home, p_home, 1.0 - p_home)
    selected_hit = np.where(selected_home, y, 1 - y)
    lower = np.zeros(len(validation), dtype=float)
    for idx, probability in enumerate(selected_probability):
        nearest = min(bins, key=lambda b: abs(float(b["predicted_mean"]) - float(probability)))
        # Never allow the empirical reliability bound to exceed the fitted
        # selected-side point probability.
        lower[idx] = min(float(probability), float(nearest["wilson90_lower"]))
    qualified = lower >= 0.55
    return {
        "method": "SIDE_NEUTRAL_LOCAL_WILSON90_12_EQUAL_COUNT_BINS",
        "validation_n": int(len(validation)),
        "qualified_n_at_0_55": int(qualified.sum()),
        "qualified_hit_rate": float(selected_hit[qualified].mean()) if qualified.any() else None,
        "all_selected_hit_rate": float(selected_hit.mean()),
        "mean_selected_probability": float(selected_probability.mean()),
        "mean_lower_bound": float(lower.mean()),
        "probability_publishable": False,
    }


def _split(frame: pd.DataFrame, sport: str):
    if sport == "NFL":
        train_seasons, calibration_season, validation_season = {2021, 2022, 2023}, 2024, 2025
    else:
        train_seasons, calibration_season, validation_season = {2022, 2023}, 2024, 2025
    train = frame[frame["season"].isin(train_seasons)].copy()
    calibration = frame[frame["season"] == calibration_season].copy()
    validation = frame[frame["season"] == validation_season].copy()
    return train, calibration, validation, {
        "train_seasons": sorted(train_seasons),
        "calibration_season": calibration_season,
        "validation_season": validation_season,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    sport = args.sport.upper()
    if sport == "NFL":
        frame, source = build_nfl_replay([2021, 2022, 2023, 2024, 2025], args.cache_dir)
        features = NFL_FEATURES
    elif sport == "MLB":
        frame, source = build_mlb_replay(
            [2022, 2023, 2024, 2025],
            max_games_per_season=args.mlb_games_per_season,
            workers=args.workers,
        )
        features = MLB_FEATURES
    else:
        raise SystemExit("sport must be NFL or MLB")

    train, calibration, validation, split = _split(frame, sport)
    bundle = fit_research_bundle(
        sport=sport,
        train=train,
        calibration=calibration,
        validation=validation,
        feature_names=features,
    )
    p_val = _vector_probabilities(bundle, validation)
    receipt = research_receipt(bundle, source_manifest=source)
    receipt["temporal_split"] = split
    receipt["slice_metrics"] = _slice_metrics(sport, validation, p_val)
    receipt["lower_bound_replay"] = _lower_bound_replay(bundle, validation, p_val)
    receipt["counterexample_review_queue"] = {
        "definition": "largest absolute calibration residual among validation states",
        "status": "GENERATED",
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, choices=["NFL", "MLB"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache-dir", default=".cache/wow-live-replay")
    parser.add_argument("--mlb-games-per-season", type=int, default=350)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    receipt = run(args)
    print(json.dumps({
        "status": receipt["status"],
        "sport": receipt["sport"],
        "validation_metrics": receipt["validation_metrics"],
        "lower_bound_replay": receipt["lower_bound_replay"],
        "probability_publishable": False,
        "promotion_authorized": False,
        "can_execute": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
