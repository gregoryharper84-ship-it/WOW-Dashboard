#!/usr/bin/env python3
"""Offline deterministic trainer for governed NFL player-prop candidates.

Builds exact route artifacts for PASSING_YARDS, RUSHING_YARDS,
RECEIVING_YARDS, and ANYTIME_TD from nflverse weekly player statistics.
The script is intentionally offline/control-plane only: it never registers,
promotes, publishes, prices, or executes a wager.

Training contract
-----------------
* nflverse stats_player weekly CSVs, 2021-2025, CC-BY-4.0.
* Whole-week chronological split: 2021-2023 train, 2024 calibration, 2025 holdout.
* Yardage routes: ridge regression over rolling L10/L5 form + opportunity,
  blended against leakage-safe L10 baseline; Gaussian residual distribution.
* Anytime TD: logistic regression over the same leakage-safe rolling features,
  blended against the player's prior L10 touchdown rate.
* Validation must be finite, have a substantial untouched 2025 holdout, and
  remain within a strict no-worse-than-naive tolerance.

Candidate presence is not capability. Production use still requires exact
artifact registration, lifecycle certification/promotion, runtime adapter,
calibration/bounds, current pregame evidence, and V17 terminal governance.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, mean_absolute_error, mean_squared_error
from sklearn.preprocessing import StandardScaler

CAN_EXECUTE = False
PROVIDER_IDENTITY = "WOW_PROP_FITTED_MODEL_V1"
MODEL_FAMILY = "NFL_PROP_ROLLING_FITTED_V1"
CALIBRATOR_VERSION = "NFL_PROP_PRECALIBRATION_BOOTSTRAP_V1"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
FEATURE_TRANSFORM_VERSION = "NFL_PROP_ROLLING_FORM_V1"
SPECIALIST_VERSION = "wow.nfl-player-prop-probability-expert@1"
ARTIFACT_FORMAT = "JSON_NFL_PROP_ROLLING_V1"
SOURCE_PROVIDER = "NFLVERSE_STATS_PLAYER"
SOURCE_LICENSE = "CC-BY-4.0"
TRAIN_SEASONS = (2021, 2022, 2023)
CALIBRATION_SEASON = 2024
HOLDOUT_SEASON = 2025
MIN_PRIOR_GAMES = 10
MIN_TRAIN_ROWS = 1500
MIN_CAL_ROWS = 400
MIN_HOLDOUT_ROWS = 400
MAX_ABS_Z = 6.0
RIDGE_ALPHA = 8.0
BLEND_GRID = tuple(round(v, 2) for v in np.linspace(0.10, 1.0, 10))

ROUTES: dict[str, dict[str, Any]] = {
    "PASSING_YARDS": {
        "target": "passing_yards",
        "opportunity": "attempts",
        "kind": "GAUSSIAN_RIDGE_BLEND_V1",
        "line_min": 0.0,
        "line_max": 600.0,
    },
    "RUSHING_YARDS": {
        "target": "rushing_yards",
        "opportunity": "carries",
        "kind": "GAUSSIAN_RIDGE_BLEND_V1",
        "line_min": 0.0,
        "line_max": 350.0,
    },
    "RECEIVING_YARDS": {
        "target": "receiving_yards",
        "opportunity": "targets",
        "kind": "GAUSSIAN_RIDGE_BLEND_V1",
        "line_min": 0.0,
        "line_max": 350.0,
    },
    "ANYTIME_TD": {
        "target": "anytime_td",
        "opportunity": "touch_opportunity",
        "kind": "BERNOULLI_LOGISTIC_BLEND_V1",
        "line_min": 0.0,
        "line_max": 1.5,
    },
}

FEATURE_NAMES = (
    "l10_stat_mean",
    "l5_stat_mean",
    "last_stat",
    "l10_opportunity_mean",
    "l5_opportunity_mean",
    "last_opportunity",
)


def _url(season: int) -> str:
    return (
        "https://github.com/nflverse/nflverse-data/releases/download/"
        f"stats_player/stats_player_week_{season}.csv"
    )


def _download(season: int) -> tuple[pd.DataFrame, str, str]:
    url = _url(season)
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    payload = response.content
    digest = hashlib.sha256(payload).hexdigest()
    frame = pd.read_csv(io.BytesIO(payload), low_memory=False)
    return frame, digest, url


def _require_columns(frame: pd.DataFrame) -> None:
    required = {
        "player_id",
        "player_display_name",
        "position",
        "team",
        "opponent_team",
        "season",
        "week",
        "game_id",
        "passing_yards",
        "attempts",
        "rushing_yards",
        "carries",
        "receiving_yards",
        "targets",
        "rushing_tds",
        "receiving_tds",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError("NFLVERSE_REQUIRED_COLUMNS_MISSING:" + ",".join(missing))


def _prepare() -> tuple[pd.DataFrame, dict[str, Any]]:
    frames: list[pd.DataFrame] = []
    source_assets: dict[str, Any] = {}
    seasons = (*TRAIN_SEASONS, CALIBRATION_SEASON, HOLDOUT_SEASON)
    for season in seasons:
        frame, digest, url = _download(season)
        _require_columns(frame)
        frames.append(frame)
        source_assets[str(season)] = {"url": url, "sha256": digest, "rows": int(len(frame))}

    all_rows = pd.concat(frames, ignore_index=True, sort=False)
    if "season_type" in all_rows.columns:
        all_rows = all_rows[all_rows["season_type"].astype(str).str.upper().isin({"REG", "REGULAR"})].copy()
    for col in (
        "season",
        "week",
        "passing_yards",
        "attempts",
        "rushing_yards",
        "carries",
        "receiving_yards",
        "targets",
        "rushing_tds",
        "receiving_tds",
        "special_teams_tds",
    ):
        if col not in all_rows.columns:
            all_rows[col] = 0
        all_rows[col] = pd.to_numeric(all_rows[col], errors="coerce").fillna(0.0)

    all_rows["player_id"] = all_rows["player_id"].astype(str).str.strip()
    all_rows["player_display_name"] = all_rows["player_display_name"].astype(str).str.strip()
    all_rows["position"] = all_rows["position"].astype(str).str.upper().str.strip()
    all_rows["team"] = all_rows["team"].astype(str).str.upper().str.strip()
    all_rows["opponent_team"] = all_rows["opponent_team"].astype(str).str.upper().str.strip()
    all_rows["game_id"] = all_rows["game_id"].astype(str).str.strip()
    all_rows["anytime_td"] = (
        all_rows["rushing_tds"] + all_rows["receiving_tds"] + all_rows["special_teams_tds"]
    ).gt(0).astype(float)
    all_rows["touch_opportunity"] = all_rows["carries"] + all_rows["targets"]
    all_rows["event_index"] = all_rows["season"].astype(int) * 100 + all_rows["week"].astype(int)
    all_rows = all_rows[
        all_rows["player_id"].ne("")
        & all_rows["game_id"].ne("")
        & all_rows["season"].isin(seasons)
    ].copy()
    all_rows.sort_values(["player_id", "event_index", "game_id"], inplace=True)

    canonical_source = json.dumps(source_assets, sort_keys=True, separators=(",", ":"))
    source_bundle_hash = hashlib.sha256(canonical_source.encode("utf-8")).hexdigest()
    return all_rows, {
        "provider": SOURCE_PROVIDER,
        "license_id": SOURCE_LICENSE,
        "attribution_required": True,
        "evidence_domain": "SPORTING",
        "grants_model_capability": False,
        "probability_publishable": False,
        "can_execute": False,
        "assets": source_assets,
        "bundle_hash": source_bundle_hash,
    }


def _featured_rows(frame: pd.DataFrame, *, target: str, opportunity: str) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    for player_id, group in frame.groupby("player_id", sort=False):
        group = group.sort_values(["event_index", "game_id"])
        history: list[tuple[float, float]] = []
        for row in group.itertuples(index=False):
            value = float(getattr(row, target))
            opp = float(getattr(row, opportunity))
            if not math.isfinite(value) or not math.isfinite(opp) or opp < 0:
                continue
            # Prop relevance: a player must have a route-specific opportunity.
            # This avoids teaching the model from inactive/statistically absent rows.
            relevant = opp > 0
            if relevant and len(history) >= MIN_PRIOR_GAMES:
                recent10 = history[-10:]
                recent5 = recent10[-5:]
                stats10 = [v for v, _ in recent10]
                opp10 = [o for _, o in recent10]
                stats5 = [v for v, _ in recent5]
                opp5 = [o for _, o in recent5]
                output.append({
                    "player_id": str(player_id),
                    "player_name": str(getattr(row, "player_display_name")),
                    "position": str(getattr(row, "position")),
                    "season": int(getattr(row, "season")),
                    "week": int(getattr(row, "week")),
                    "event_index": int(getattr(row, "event_index")),
                    "game_id": str(getattr(row, "game_id")),
                    "target": value,
                    "l10_stat_mean": float(np.mean(stats10)),
                    "l5_stat_mean": float(np.mean(stats5)),
                    "last_stat": float(stats10[-1]),
                    "l10_opportunity_mean": float(np.mean(opp10)),
                    "l5_opportunity_mean": float(np.mean(opp5)),
                    "last_opportunity": float(opp10[-1]),
                })
            if relevant:
                history.append((value, opp))
    result = pd.DataFrame(output)
    if result.empty:
        raise RuntimeError("NFL_PROP_FEATURE_ROWS_EMPTY")
    return result.sort_values(["event_index", "game_id", "player_id"]).reset_index(drop=True)


def _matrix(rows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    x = rows.loc[:, FEATURE_NAMES].to_numpy(dtype=float)
    y = rows["target"].to_numpy(dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise RuntimeError("NFL_PROP_NONFINITE_TRAINING_MATRIX")
    return x, y


def _split(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = rows[rows["season"].isin(TRAIN_SEASONS)].copy()
    calibration = rows[rows["season"].eq(CALIBRATION_SEASON)].copy()
    holdout = rows[rows["season"].eq(HOLDOUT_SEASON)].copy()
    if len(train) < MIN_TRAIN_ROWS:
        raise RuntimeError(f"NFL_PROP_TRAIN_ROWS_BELOW_MINIMUM:{len(train)}")
    if len(calibration) < MIN_CAL_ROWS:
        raise RuntimeError(f"NFL_PROP_CAL_ROWS_BELOW_MINIMUM:{len(calibration)}")
    if len(holdout) < MIN_HOLDOUT_ROWS:
        raise RuntimeError(f"NFL_PROP_HOLDOUT_ROWS_BELOW_MINIMUM:{len(holdout)}")
    if train["event_index"].max() >= calibration["event_index"].min():
        raise RuntimeError("NFL_PROP_TRAIN_CAL_CHRONOLOGY_INVALID")
    if calibration["event_index"].max() >= holdout["event_index"].min():
        raise RuntimeError("NFL_PROP_CAL_HOLDOUT_CHRONOLOGY_INVALID")
    return train, calibration, holdout


def _best_blend(target: np.ndarray, baseline: np.ndarray, fitted: np.ndarray, *, binary: bool) -> tuple[float, float]:
    best_weight = BLEND_GRID[0]
    best_loss = float("inf")
    for weight in BLEND_GRID:
        pred = (1.0 - weight) * baseline + weight * fitted
        if binary:
            pred = np.clip(pred, 1e-6, 1 - 1e-6)
            loss = float(brier_score_loss(target, pred))
        else:
            loss = float(mean_absolute_error(target, pred))
        if loss < best_loss - 1e-12:
            best_weight, best_loss = weight, loss
    return best_weight, best_loss


def _artifact(route: str, cfg: dict[str, Any], rows: pd.DataFrame, source: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    train, calibration, holdout = _split(rows)
    x_train, y_train = _matrix(train)
    x_cal, y_cal = _matrix(calibration)
    x_test, y_test = _matrix(holdout)

    scaler = StandardScaler()
    z_train = scaler.fit_transform(x_train)
    z_cal = scaler.transform(x_cal)
    z_test = scaler.transform(x_test)
    baseline_cal = x_cal[:, 0]
    baseline_test = x_test[:, 0]
    blockers: list[str] = []

    if cfg["kind"] == "GAUSSIAN_RIDGE_BLEND_V1":
        model = Ridge(alpha=RIDGE_ALPHA, fit_intercept=True)
        model.fit(z_train, y_train)
        fitted_cal = model.predict(z_cal)
        fitted_test = model.predict(z_test)
        blend_weight, _ = _best_blend(y_cal, baseline_cal, fitted_cal, binary=False)
        pred_cal = (1.0 - blend_weight) * baseline_cal + blend_weight * fitted_cal
        pred_test = (1.0 - blend_weight) * baseline_test + blend_weight * fitted_test
        residuals = y_cal - pred_cal
        residual_sigma = float(np.std(residuals, ddof=1))
        residual_sigma = max(residual_sigma, 1.0)
        mae = float(mean_absolute_error(y_test, pred_test))
        baseline_mae = float(mean_absolute_error(y_test, baseline_test))
        rmse = float(mean_squared_error(y_test, pred_test) ** 0.5)
        baseline_rmse = float(mean_squared_error(y_test, baseline_test) ** 0.5)
        ratio = mae / baseline_mae if baseline_mae > 0 else float("inf")
        if not math.isfinite(ratio) or ratio > 1.03:
            blockers.append("NFL_PROP_FAILS_NAIVE_MAE_GATE")
        if blend_weight < 0.10:
            blockers.append("NFL_PROP_FITTED_COMPONENT_WEIGHT_TOO_LOW")
        payload = {
            "model_family": MODEL_FAMILY,
            "model_kind": cfg["kind"],
            "stat_type": route,
            "feature_names": list(FEATURE_NAMES),
            "feature_mean": [round(float(v), 12) for v in scaler.mean_],
            "feature_scale": [round(float(v if v > 1e-12 else 1.0), 12) for v in scaler.scale_],
            "coef": [round(float(v), 12) for v in model.coef_],
            "intercept": round(float(model.intercept_), 12),
            "blend_weight_fitted": round(float(blend_weight), 12),
            "residual_sigma": round(residual_sigma, 12),
            "support_min": -50,
            "support_max": int(math.ceil(max(float(rows["target"].max()), cfg["line_max"]) + 75)),
            "max_abs_z_for_coverage": MAX_ABS_Z,
            "min_prior_games": MIN_PRIOR_GAMES,
            "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        }
        metric_core = {
            "mae": mae,
            "baseline_l10_mae": baseline_mae,
            "mae_ratio_vs_naive": ratio,
            "rmse": rmse,
            "baseline_l10_rmse": baseline_rmse,
            "residual_sigma_calibration": residual_sigma,
            "selected_blend_weight": blend_weight,
        }
    else:
        # TD baseline is the prior L10 touchdown rate, already feature[0].
        model = LogisticRegression(C=0.5, max_iter=2000, solver="lbfgs")
        model.fit(z_train, y_train.astype(int))
        fitted_cal = model.predict_proba(z_cal)[:, 1]
        fitted_test = model.predict_proba(z_test)[:, 1]
        blend_weight, _ = _best_blend(y_cal, baseline_cal, fitted_cal, binary=True)
        pred_test = np.clip((1.0 - blend_weight) * baseline_test + blend_weight * fitted_test, 1e-6, 1 - 1e-6)
        base_test = np.clip(baseline_test, 1e-6, 1 - 1e-6)
        brier = float(brier_score_loss(y_test, pred_test))
        baseline_brier = float(brier_score_loss(y_test, base_test))
        ratio = brier / baseline_brier if baseline_brier > 0 else float("inf")
        if not math.isfinite(ratio) or ratio > 1.03:
            blockers.append("NFL_PROP_FAILS_NAIVE_BRIER_GATE")
        if blend_weight < 0.10:
            blockers.append("NFL_PROP_FITTED_COMPONENT_WEIGHT_TOO_LOW")
        payload = {
            "model_family": MODEL_FAMILY,
            "model_kind": cfg["kind"],
            "stat_type": route,
            "feature_names": list(FEATURE_NAMES),
            "feature_mean": [round(float(v), 12) for v in scaler.mean_],
            "feature_scale": [round(float(v if v > 1e-12 else 1.0), 12) for v in scaler.scale_],
            "coef": [round(float(v), 12) for v in model.coef_[0]],
            "intercept": round(float(model.intercept_[0]), 12),
            "blend_weight_fitted": round(float(blend_weight), 12),
            "max_abs_z_for_coverage": MAX_ABS_Z,
            "min_prior_games": MIN_PRIOR_GAMES,
            "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        }
        metric_core = {
            "brier": brier,
            "baseline_l10_brier": baseline_brier,
            "brier_ratio_vs_naive": ratio,
            "selected_blend_weight": blend_weight,
            "holdout_positive_rate": float(np.mean(y_test)),
        }

    finite = all(math.isfinite(float(v)) for v in metric_core.values())
    if not finite:
        blockers.append("NFL_PROP_VALIDATION_METRICS_NONFINITE")
    max_abs_z_holdout = float(np.max(np.abs(z_test))) if z_test.size else float("inf")
    ood_rate = float((np.max(np.abs(z_test), axis=1) > MAX_ABS_Z).mean())
    if ood_rate > 0.08:
        blockers.append("NFL_PROP_HOLDOUT_OOD_RATE_TOO_HIGH")

    validation_status = "PASS" if not blockers else "BLOCKED"
    training_dataset_hash = hashlib.sha256(
        (source["bundle_hash"] + "|" + route + "|NFL_PROP_SPLIT_2021_2023_2024_2025").encode("utf-8")
    ).hexdigest()
    artifact_checksum = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    short = {
        "PASSING_YARDS": "PASSYDS",
        "RUSHING_YARDS": "RUSHYDS",
        "RECEIVING_YARDS": "RECYDS",
        "ANYTIME_TD": "ATTD",
    }[route]
    artifact_version = f"NFL_{short}_ROLLING_FITTED_V1_2026_09_20"
    metrics = {
        "validation_status": validation_status,
        "blockers": sorted(set(blockers)),
        "train_rows": int(len(train)),
        "calibration_rows": int(len(calibration)),
        "holdout_rows": int(len(holdout)),
        "train_seasons": list(TRAIN_SEASONS),
        "calibration_season": CALIBRATION_SEASON,
        "holdout_season": HOLDOUT_SEASON,
        "whole_week_chronological_split": True,
        "holdout_max_abs_feature_z": max_abs_z_holdout,
        "holdout_ood_rate_z_gt_6": ood_rate,
        "source": {
            "source_snapshot": {
                **source,
                "bundle_id": "nflverse-player-weekly-2021-2025-20260920",
            }
        },
        "probability_publishable": False,
        "can_execute": False,
        **{k: round(float(v), 12) for k, v in metric_core.items()},
    }
    code_sha = os.environ.get("GITHUB_SHA", "0" * 40)
    if len(code_sha) < 40:
        code_sha = hashlib.sha1(code_sha.encode("utf-8")).hexdigest()
    artifact = {
        "provider_identity": PROVIDER_IDENTITY,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": artifact_version,
        "calibrator_version": CALIBRATOR_VERSION,
        "sport": "NFL",
        "stat_type": route,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "specialist_version": SPECIALIST_VERSION,
        "certification_id": f"NFL-{short}-OFFLINE-2026-09-20",
        "lifecycle_state": "CANDIDATE",
        "training_dataset_hash": training_dataset_hash,
        "training_code_sha": code_sha,
        "artifact_checksum": artifact_checksum,
        "artifact_format": ARTIFACT_FORMAT,
        "artifact_payload": payload,
        "supported_line_min": cfg["line_min"],
        "supported_line_max": cfg["line_max"],
        "training_rows": int(len(train)),
        "validation_metrics": metrics,
        "certification_eligible": validation_status == "PASS",
        "source_provider": SOURCE_PROVIDER,
        "source_license_id": SOURCE_LICENSE,
        "source_attribution_required": True,
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    return artifact, metrics


def main() -> int:
    out_dir = Path(os.environ.get("NFL_PROP_ARTIFACT_OUT_DIR", Path(__file__).resolve().parents[1] / "data"))
    out_dir.mkdir(parents=True, exist_ok=True)
    frame, source = _prepare()
    artifacts: list[dict[str, Any]] = []
    routes_report: dict[str, Any] = {}
    all_pass = True
    for route, cfg in ROUTES.items():
        featured = _featured_rows(frame, target=cfg["target"], opportunity=cfg["opportunity"])
        artifact, metrics = _artifact(route, cfg, featured, source)
        artifacts.append(artifact)
        routes_report[route] = metrics
        all_pass = all_pass and metrics["validation_status"] == "PASS"

    report = {
        "schema_version": "WOW_NFL_PROP_TRAINING_REPORT_V1",
        "model_family": MODEL_FAMILY,
        "training_status": "PASS" if all_pass else "BLOCKED",
        "artifact_registration_status": "NOT_ATTEMPTED",
        "runtime_model_status": "MODEL_UNAVAILABLE",
        "routes": routes_report,
        "source": source,
        "probability_publishable": False,
        "can_execute": False,
    }
    (out_dir / "wow_nfl_prop_artifacts_v1.json").write_text(
        json.dumps(artifacts, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "wow_nfl_prop_training_report_v1.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if all_pass else 3


if __name__ == "__main__":
    raise SystemExit(main())
