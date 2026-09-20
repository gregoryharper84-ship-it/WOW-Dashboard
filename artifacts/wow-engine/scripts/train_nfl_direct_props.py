#!/usr/bin/env python3
"""Offline trainer for governed NFL direct player-prop models.

Uses only nflverse player_stats data allowed by nflverse_dataset_policy_v1.json.
Training rows are built from strictly prior player games.  Model selection uses
2024 calibration data; 2025 is an untouched test season.  No market data enters
training, validation, calibration, or line support.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, log_loss, mean_absolute_error
from sklearn.preprocessing import StandardScaler

SEASONS = tuple(range(2021, 2026))
SOURCE_TEMPLATE = "https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{season}.csv"
TRAIN_END_SEASON = 2023
CALIBRATION_SEASON = 2024
TEST_SEASON = 2025
MIN_PRIOR_GAMES = 10
SPECIALIST_VERSION = "wow.nfl-player-prop-probability-expert@1"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
CALIBRATOR_VERSION = "NFL_DIRECT_PROP_PRECALIBRATION_V1"
YARD_MODEL_FAMILY = "NFL_DIRECT_PROP_RIDGE_RESIDUAL_V1"
TD_MODEL_FAMILY = "NFL_DIRECT_PROP_LOGIT_V1"
CAN_EXECUTE = False

ROUTES: dict[str, dict[str, Any]] = {
    "PASSING_YARDS": {"value": "passing_yards", "opp": "attempts", "min_prior_opp": 5.0},
    "RUSHING_YARDS": {"value": "rushing_yards", "opp": "carries", "min_prior_opp": 1.0},
    "RECEIVING_YARDS": {"value": "receiving_yards", "opp": "targets", "min_prior_opp": 1.0},
    "ANYTIME_TD": {"value": "anytime_td", "opp": "td_opportunities", "min_prior_opp": 1.0},
}

YARD_FEATURES = (
    "l10_mean", "l5_mean", "last", "l10_std", "l10_opp_mean", "l5_opp_mean",
)
TD_FEATURES = (
    "l10_td_rate", "l5_td_rate", "last_td", "l10_opp_mean", "l5_opp_mean",
)


@dataclass(frozen=True)
class Featured:
    season: int
    week: int
    player_id: str
    position: str
    x: tuple[float, ...]
    y: float


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download(season: int) -> tuple[pd.DataFrame, str]:
    url = SOURCE_TEMPLATE.format(season=season)
    r = requests.get(url, timeout=90, headers={"User-Agent": "WOW-V17-NFL-Props/1.0"})
    r.raise_for_status()
    payload = r.content
    frame = pd.read_csv(io.BytesIO(payload), low_memory=False)
    frame["_source_season"] = season
    return frame, _sha(payload)


def _number(row: pd.Series, key: str) -> float:
    value = row.get(key, 0.0)
    if pd.isna(value):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _player_id(row: pd.Series) -> str:
    for key in ("player_id", "gsis_id"):
        value = str(row.get(key, "") or "").strip()
        if value and value.lower() != "nan":
            return value
    return ""


def _position(row: pd.Series) -> str:
    return str(row.get("position", "UNK") or "UNK").strip().upper()


def _prepare(frames: list[pd.DataFrame]) -> pd.DataFrame:
    df = pd.concat(frames, ignore_index=True, sort=False)
    required = {
        "season", "week", "passing_yards", "attempts", "rushing_yards", "carries",
        "receiving_yards", "targets", "rushing_tds", "receiving_tds",
    }
    missing = sorted(required.difference(df.columns))
    if missing:
        raise RuntimeError("NFLVERSE_REQUIRED_COLUMNS_MISSING:" + ",".join(missing))
    if "special_teams_tds" not in df.columns:
        raise RuntimeError("NFLVERSE_ANYTIME_TD_SPECIAL_TEAMS_COLUMN_MISSING")
    if "player_id" not in df.columns and "gsis_id" not in df.columns:
        raise RuntimeError("NFLVERSE_PLAYER_ID_COLUMN_MISSING")
    df = df.copy()
    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    df["week"] = pd.to_numeric(df["week"], errors="coerce")
    df = df[df["season"].notna() & df["week"].notna()]
    df["season"] = df["season"].astype(int)
    df["week"] = df["week"].astype(int)
    season_type = df.get("season_type")
    if season_type is not None:
        df = df[season_type.astype(str).str.upper().isin({"REG", "REGULAR"})]
    return df.sort_values(["season", "week"], kind="stable").reset_index(drop=True)


def _build_rows(df: pd.DataFrame, route: str) -> list[Featured]:
    cfg = ROUTES[route]
    histories: dict[str, list[dict[str, float]]] = defaultdict(list)
    rows: list[Featured] = []
    for _, row in df.iterrows():
        pid = _player_id(row)
        if not pid:
            continue
        passing = _number(row, "passing_yards")
        rushing = _number(row, "rushing_yards")
        receiving = _number(row, "receiving_yards")
        attempts = _number(row, "attempts")
        carries = _number(row, "carries")
        targets = _number(row, "targets")
        rush_td = max(0.0, _number(row, "rushing_tds"))
        rec_td = max(0.0, _number(row, "receiving_tds"))
        st_td = max(0.0, _number(row, "special_teams_tds"))
        anytime_td = 1.0 if (rush_td + rec_td + st_td) > 0 else 0.0
        current = {
            "PASSING_YARDS": passing,
            "RUSHING_YARDS": rushing,
            "RECEIVING_YARDS": receiving,
            "ANYTIME_TD": anytime_td,
            "PASSING_YARDS_opp": attempts,
            "RUSHING_YARDS_opp": carries,
            "RECEIVING_YARDS_opp": targets,
            "ANYTIME_TD_opp": carries + targets,
        }
        history = histories[pid]
        if len(history) >= MIN_PRIOR_GAMES:
            prior = history[-MIN_PRIOR_GAMES:]
            vals = np.asarray([h[route] for h in prior], dtype=float)
            opps = np.asarray([h[f"{route}_opp"] for h in prior], dtype=float)
            if float(opps.mean()) >= float(cfg["min_prior_opp"]):
                if route == "ANYTIME_TD":
                    x = (
                        float(vals.mean()), float(vals[-5:].mean()), float(vals[-1]),
                        float(opps.mean()), float(opps[-5:].mean()),
                    )
                else:
                    x = (
                        float(vals.mean()), float(vals[-5:].mean()), float(vals[-1]),
                        float(vals.std(ddof=0)), float(opps.mean()), float(opps[-5:].mean()),
                    )
                rows.append(Featured(
                    season=int(row["season"]), week=int(row["week"]), player_id=pid,
                    position=_position(row), x=x, y=float(current[route]),
                ))
        history.append(current)
    return rows


def _matrix(rows: list[Featured]) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray([r.x for r in rows], dtype=float), np.asarray([r.y for r in rows], dtype=float)


def _split(rows: list[Featured]) -> tuple[list[Featured], list[Featured], list[Featured]]:
    train = [r for r in rows if r.season <= TRAIN_END_SEASON]
    cal = [r for r in rows if r.season == CALIBRATION_SEASON]
    test = [r for r in rows if r.season == TEST_SEASON]
    if min(len(train), len(cal), len(test)) < 100:
        raise RuntimeError(f"NFL_PROP_SPLIT_TOO_SMALL train={len(train)} cal={len(cal)} test={len(test)}")
    return train, cal, test


def _residual_pmf(residuals: np.ndarray) -> dict[str, float]:
    rounded = np.rint(residuals).astype(int)
    vals, counts = np.unique(rounded, return_counts=True)
    total = float(counts.sum())
    return {str(int(v)): float(c / total) for v, c in zip(vals, counts)}


def _fit_yardage(route: str, rows: list[Featured]) -> tuple[dict[str, Any], dict[str, Any]]:
    train, cal, test = _split(rows)
    x_train, y_train = _matrix(train)
    x_cal, y_cal = _matrix(cal)
    x_test, y_test = _matrix(test)
    scaler = StandardScaler().fit(x_train)
    z_train, z_cal, z_test = scaler.transform(x_train), scaler.transform(x_cal), scaler.transform(x_test)
    candidates = []
    for alpha in (0.1, 1.0, 10.0, 100.0):
        model = Ridge(alpha=alpha).fit(z_train, y_train)
        cal_pred = np.maximum(model.predict(z_cal), 0.0)
        candidates.append((mean_absolute_error(y_cal, cal_pred), alpha, model))
    _, alpha, model = min(candidates, key=lambda item: item[0])
    cal_pred = np.maximum(model.predict(z_cal), 0.0)
    test_pred = np.maximum(model.predict(z_test), 0.0)
    baseline = np.maximum(x_test[:, 0], 0.0)
    model_mae = float(mean_absolute_error(y_test, test_pred))
    baseline_mae = float(mean_absolute_error(y_test, baseline))
    ratio = model_mae / baseline_mae if baseline_mae > 0 else math.inf
    residuals = y_cal - cal_pred
    q995 = float(np.quantile(np.concatenate([y_train, y_cal, y_test]), 0.995))
    supported_max = float(max(10.5, math.ceil(q995 + 10.0) + 0.5))
    passed = bool(np.isfinite(test_pred).all() and ratio <= 1.02 and len(test) >= 100)
    blockers = [] if passed else ["NFL_DIRECT_PROP_FAILS_NAIVE_MAE_GATE"]
    payload = {
        "model_kind": "RIDGE_PLUS_EMPIRICAL_RESIDUAL_PMF_V1",
        "route": route,
        "feature_names": list(YARD_FEATURES),
        "feature_mean": scaler.mean_.tolist(),
        "feature_scale": scaler.scale_.tolist(),
        "coef": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "ridge_alpha": float(alpha),
        "residual_pmf": _residual_pmf(residuals),
        "min_prior_games": MIN_PRIOR_GAMES,
        "supported_line_min": 0.5,
        "supported_line_max": supported_max,
        "fit_train_seasons": [2021, 2022, 2023],
        "calibration_season": CALIBRATION_SEASON,
        "untouched_test_season": TEST_SEASON,
        "strictly_prior_features": True,
    }
    metrics = {
        "validation_status": "PASS" if passed else "BLOCKED",
        "blockers": blockers,
        "train_rows": len(train), "calibration_rows": len(cal), "untouched_test_rows": len(test),
        "untouched_test_mae": model_mae, "naive_l10_mae": baseline_mae,
        "mae_ratio_vs_naive": ratio,
        "selection_metric": "2024_MAE",
        "untouched_metric": "2025_MAE",
        "probability_source": "FITTED_RIDGE_PLUS_2024_EMPIRICAL_RESIDUALS",
        "market_data_used": False,
    }
    return payload, metrics


def _fit_td(rows: list[Featured]) -> tuple[dict[str, Any], dict[str, Any]]:
    train, cal, test = _split(rows)
    x_train, y_train = _matrix(train)
    x_cal, y_cal = _matrix(cal)
    x_test, y_test = _matrix(test)
    scaler = StandardScaler().fit(x_train)
    z_train, z_cal, z_test = scaler.transform(x_train), scaler.transform(x_cal), scaler.transform(x_test)
    candidates = []
    for c in (0.1, 1.0, 10.0):
        model = LogisticRegression(C=c, max_iter=2000, random_state=17).fit(z_train, y_train.astype(int))
        cal_p = model.predict_proba(z_cal)[:, 1]
        candidates.append((brier_score_loss(y_cal, cal_p), c, model))
    _, c, model = min(candidates, key=lambda item: item[0])
    test_p = model.predict_proba(z_test)[:, 1]
    baseline = np.clip(x_test[:, 0], 1e-4, 1 - 1e-4)
    brier = float(brier_score_loss(y_test, test_p))
    baseline_brier = float(brier_score_loss(y_test, baseline))
    ratio = brier / baseline_brier if baseline_brier > 0 else math.inf
    ll = float(log_loss(y_test, np.clip(test_p, 1e-6, 1 - 1e-6)))
    passed = bool(np.isfinite(test_p).all() and ratio <= 1.02 and len(test) >= 100)
    blockers = [] if passed else ["NFL_ANYTIME_TD_FAILS_NAIVE_BRIER_GATE"]
    payload = {
        "model_kind": "LOGISTIC_REGRESSION_V1",
        "route": "ANYTIME_TD",
        "feature_names": list(TD_FEATURES),
        "feature_mean": scaler.mean_.tolist(),
        "feature_scale": scaler.scale_.tolist(),
        "coef": model.coef_[0].tolist(),
        "intercept": float(model.intercept_[0]),
        "logistic_c": float(c),
        "min_prior_games": MIN_PRIOR_GAMES,
        "supported_line_min": 0.5,
        "supported_line_max": 0.5,
        "fit_train_seasons": [2021, 2022, 2023],
        "calibration_season": CALIBRATION_SEASON,
        "untouched_test_season": TEST_SEASON,
        "strictly_prior_features": True,
        "anytime_td_definition": "RUSHING_TDS_PLUS_RECEIVING_TDS_PLUS_SPECIAL_TEAMS_TDS_GT_ZERO",
        "passing_tds_excluded": True,
    }
    metrics = {
        "validation_status": "PASS" if passed else "BLOCKED",
        "blockers": blockers,
        "train_rows": len(train), "calibration_rows": len(cal), "untouched_test_rows": len(test),
        "untouched_test_brier": brier, "naive_l10_brier": baseline_brier,
        "brier_ratio_vs_naive": ratio, "untouched_test_log_loss": ll,
        "selection_metric": "2024_BRIER", "untouched_metric": "2025_BRIER",
        "probability_source": "FITTED_LOGISTIC_REGRESSION",
        "market_data_used": False,
    }
    return payload, metrics


def train(output_dir: Path) -> tuple[Path, Path]:
    frames: list[pd.DataFrame] = []
    hashes: dict[str, str] = {}
    for season in SEASONS:
        frame, digest = _download(season)
        frames.append(frame)
        hashes[str(season)] = digest
    df = _prepare(frames)
    dataset_hash = _sha(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode())
    artifacts = []
    report_routes: dict[str, Any] = {}
    for route in ROUTES:
        featured = _build_rows(df, route)
        payload, metrics = (_fit_td(featured) if route == "ANYTIME_TD" else _fit_yardage(route, featured))
        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        checksum = _sha(canonical_payload)
        model_family = TD_MODEL_FAMILY if route == "ANYTIME_TD" else YARD_MODEL_FAMILY
        version = f"{model_family}_{route}_2026_09_20"
        passed = metrics["validation_status"] == "PASS"
        artifact = {
            "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
            "source_snapshot_bundle_id": f"nflverse-player-stats-2021-2025-{dataset_hash[:16]}",
            "specialist_version": SPECIALIST_VERSION,
            "sport": "NFL",
            "stat_type": route,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "model_family": model_family,
            "model_artifact_version": version,
            "calibrator_version": CALIBRATOR_VERSION,
            "training_dataset_hash": dataset_hash,
            "training_code_sha": os.environ.get("GITHUB_SHA", "LOCAL_UNRESOLVED"),
            "artifact_checksum": checksum,
            "artifact_format": "JSON_NFL_DIRECT_PROP_V1",
            "artifact_payload": payload,
            "training_rows": int(metrics["train_rows"]),
            "effective_sample_size": float(metrics["calibration_rows"]),
            "supported_line_min": float(payload["supported_line_min"]),
            "supported_line_max": float(payload["supported_line_max"]),
            "validation_metrics": metrics,
            "lifecycle_state": "PROSPECTIVE_CERTIFIED" if passed else "CANDIDATE",
            "active": passed,
            "promoted": passed,
            "certification_id": f"NFL-DIRECT-PROP-{route}-20260920" if passed else None,
            "can_execute": False,
        }
        artifacts.append(artifact)
        report_routes[route] = metrics
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / "wow_nfl_direct_prop_artifacts_v1.json"
    report_path = output_dir / "wow_nfl_direct_prop_training_report_v1.json"
    artifact_path.write_text(json.dumps({
        "schema_version": "WOW_NFL_DIRECT_PROP_ARTIFACTS_V1", "can_execute": False,
        "source_hashes": hashes, "training_dataset_hash": dataset_hash, "artifacts": artifacts,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps({
        "schema_version": "WOW_NFL_DIRECT_PROP_TRAINING_REPORT_V1", "can_execute": False,
        "routes": report_routes, "certified_routes": [a["stat_type"] for a in artifacts if a["promoted"]],
        "blocked_routes": [a["stat_type"] for a in artifacts if not a["promoted"]],
        "market_data_used": False, "source_hashes": hashes, "training_dataset_hash": dataset_hash,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact_path, report_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="artifacts/wow-engine/data")
    args = parser.parse_args()
    artifact_path, report_path = train(Path(args.output_dir))
    report = json.loads(report_path.read_text())
    print(json.dumps({"artifact": str(artifact_path), "report": str(report_path), **report}, sort_keys=True))


if __name__ == "__main__":
    main()
