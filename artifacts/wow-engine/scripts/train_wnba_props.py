#!/usr/bin/env python3
"""Offline chronological trainer for governed WNBA player-prop count models.

Fits one leakage-safe Poisson log-GLM per supported WNBA counting stat using
pinned WNBA Stats 2026 player game logs. The separate readiness probe certifies
role and exact event-time joins over the companion BoxScoreTraditionalV3 and
ESPN schedule corpora; this trainer intentionally does not use target-game role
or any postgame target information as a feature.

No runtime capability, registry row, probability publication, or execution is
modified by this script. Outputs are candidate artifact JSON + metrics only.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import sys
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from sklearn.linear_model import PoissonRegressor
from sklearn.metrics import mean_absolute_error, mean_poisson_deviance

PLAYER_LOG_URL = (
    "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
    "wnba_stats_player_game_logs/player_game_logs_2026.csv"
)
PLAYER_LOG_SHA256 = "f326bd597a607a574de488b153d76032ee5ec9c4cacd36c8380f229ed96e6288"

MODEL_FAMILY = "WNBA_PROP_POISSON_LOGGLM_V1"
CALIBRATOR_VERSION = "WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
FEATURE_TRANSFORM_VERSION = "WNBA_PROP_ROLLING_FORM_V1"
SPECIALIST_VERSION = "wow.wnba-player-prop-probability-expert@1"
ARTIFACT_DATE = "2026_08_30"
MIN_PRIOR_GAMES = 10
TRAIN_FRACTION = 0.70
MODEL_ALPHA = 0.50
MAX_Z_OOD = 6.0
VALIDATION_DEVIANCE_RATIO_MAX = 1.02
MIN_HOLDOUT_ROWS = 150
CAN_EXECUTE = False

STAT_ROUTES = {
    "POINTS": ("PTS", ("pts", "points")),
    "REBOUNDS": ("REB", ("reb", "rebounds", "rebounds_total", "reboundsTotal")),
    "ASSISTS": ("AST", ("ast", "assists")),
    "THREE_POINTERS_MADE": ("3PM", ("fg3m", "three_pm", "threePointersMade", "three_pointers_made")),
}

FEATURE_NAMES = (
    "l10_stat_mean",
    "l5_stat_mean",
    "last_stat",
    "l10_minutes_mean",
    "l5_minutes_mean",
    "last_minutes",
)


class TrainingUnavailable(RuntimeError):
    pass


def _download(url: str, expected_sha256: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "WOW-Research/1.0"})
    with urllib.request.urlopen(req, timeout=90) as response:
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        raise TrainingUnavailable(f"SOURCE_HASH_MISMATCH expected={expected_sha256} actual={digest}")
    return payload


def _rows(payload: bytes) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    return list(reader.fieldnames or []), list(reader)


def _pick(columns: Iterable[str], aliases: Iterable[str]) -> str | None:
    folded = {c.casefold(): c for c in columns}
    for alias in aliases:
        hit = folded.get(alias.casefold())
        if hit:
            return hit
    return None


def _norm_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _date(value: Any) -> str:
    return str(value or "").strip()[:10]


def _minutes(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("minutes missing")
    if raw.upper().startswith("PT"):
        raw = raw.upper().removeprefix("PT")
        minutes = 0.0
        if "M" in raw:
            m, raw = raw.split("M", 1)
            minutes += float(m or 0)
        if raw.endswith("S"):
            minutes += float(raw[:-1] or 0) / 60.0
        return minutes
    if ":" in raw:
        m, s = raw.split(":", 1)
        return float(m) + float(s) / 60.0
    return float(raw)


def _nonnegative_int(value: Any) -> int:
    parsed = float(value)
    if parsed < 0 or parsed != int(parsed):
        raise ValueError(value)
    return int(parsed)


@dataclass(frozen=True)
class Game:
    player_id: str
    game_id: str
    game_date: str
    minutes: float
    value: int


@dataclass(frozen=True)
class FeaturedRow:
    player_id: str
    game_id: str
    game_date: str
    features: tuple[float, ...]
    actual: int


def _extract_games(payload: bytes, stat_aliases: tuple[str, ...]) -> tuple[list[Game], dict[str, Any]]:
    columns, rows = _rows(payload)
    player_col = _pick(columns, ("player_id", "person_id", "personId"))
    game_col = _pick(columns, ("game_id", "gameId"))
    date_col = _pick(columns, ("game_date", "gameDate"))
    minutes_col = _pick(columns, ("min", "minutes"))
    stat_col = _pick(columns, stat_aliases)
    missing = [name for name, col in {
        "player_id": player_col,
        "game_id": game_col,
        "game_date": date_col,
        "minutes": minutes_col,
        "stat": stat_col,
    }.items() if not col]
    if missing:
        raise TrainingUnavailable("MISSING_COLUMNS:" + ",".join(missing))

    out: list[Game] = []
    rejected = 0
    seen: set[tuple[str, str]] = set()
    duplicates = 0
    for row in rows:
        pid = _norm_id(row[player_col])
        gid = _norm_id(row[game_col])
        if not pid or not gid:
            continue
        key = (pid, gid)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        try:
            minutes = _minutes(row[minutes_col])
            value = _nonnegative_int(row[stat_col])
            game_date = _date(row[date_col])
            if not game_date or minutes <= 0 or minutes > 60:
                raise ValueError("bad chronology/minutes")
        except (TypeError, ValueError):
            rejected += 1
            continue
        out.append(Game(pid, gid, game_date, minutes, value))

    if duplicates:
        raise TrainingUnavailable(f"DUPLICATE_PLAYER_GAME_KEYS:{duplicates}")
    if rejected:
        raise TrainingUnavailable(f"REJECTED_IDENTIFIABLE_GAME_ROWS:{rejected}")
    return out, {
        "source_row_n": len(rows),
        "identified_played_game_n": len(out),
        "duplicate_player_game_n": duplicates,
        "rejected_identifiable_game_n": rejected,
        "columns": columns,
    }


def build_featured_rows(games: list[Game]) -> list[FeaturedRow]:
    by_player: dict[str, list[Game]] = defaultdict(list)
    for game in games:
        by_player[game.player_id].append(game)

    featured: list[FeaturedRow] = []
    for pid, seq in by_player.items():
        seq.sort(key=lambda g: (g.game_date, g.game_id))
        history: list[Game] = []
        for game in seq:
            if len(history) >= MIN_PRIOR_GAMES:
                l10 = history[-10:]
                l5 = history[-5:]
                features = (
                    float(np.mean([g.value for g in l10])),
                    float(np.mean([g.value for g in l5])),
                    float(l10[-1].value),
                    float(np.mean([g.minutes for g in l10])),
                    float(np.mean([g.minutes for g in l5])),
                    float(l10[-1].minutes),
                )
                featured.append(FeaturedRow(pid, game.game_id, game.game_date, features, game.value))
            history.append(game)
    featured.sort(key=lambda r: (r.game_date, r.game_id, r.player_id))
    return featured


def _split(rows: list[FeaturedRow]) -> tuple[list[FeaturedRow], list[FeaturedRow], str]:
    if len(rows) < MIN_HOLDOUT_ROWS * 2:
        raise TrainingUnavailable(f"FEATURED_ROWS_BELOW_MINIMUM:{len(rows)}")
    dates = sorted({r.game_date for r in rows})
    if len(dates) < 10:
        raise TrainingUnavailable("CHRONOLOGY_TOO_SHORT")
    cut_idx = min(max(int(len(dates) * TRAIN_FRACTION), 1), len(dates) - 1)
    cutoff = dates[cut_idx]
    train = [r for r in rows if r.game_date < cutoff]
    holdout = [r for r in rows if r.game_date >= cutoff]
    if len(train) < MIN_HOLDOUT_ROWS or len(holdout) < MIN_HOLDOUT_ROWS:
        raise TrainingUnavailable(f"SPLIT_TOO_SMALL train={len(train)} holdout={len(holdout)}")
    if max(r.game_date for r in train) >= min(r.game_date for r in holdout):
        raise TrainingUnavailable("TEMPORAL_SPLIT_LEAKAGE")
    return train, holdout, cutoff


def _matrix(rows: list[FeaturedRow]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([r.features for r in rows], dtype=float)
    y = np.asarray([r.actual for r in rows], dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise TrainingUnavailable("NONFINITE_TRAINING_MATRIX")
    return x, y


def poisson_pmf(mu: float, max_k: int) -> dict[int, float]:
    """Finite Poisson PMF with the entire upper tail folded into max_k."""
    mu = max(float(mu), 1e-9)
    probs: dict[int, float] = {}
    p0 = math.exp(-mu)
    probs[0] = p0
    running = p0
    pk = p0
    for k in range(1, max_k):
        pk = pk * mu / k
        probs[k] = pk
        running += pk
    probs[max_k] = max(0.0, 1.0 - running)
    total = sum(probs.values())
    return {k: v / total for k, v in probs.items()}


def _fit_one(route: str, games: list[Game], source_meta: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    featured = build_featured_rows(games)
    train, holdout, cutoff = _split(featured)
    x_train, y_train = _matrix(train)
    x_holdout, y_holdout = _matrix(holdout)

    mean = x_train.mean(axis=0)
    scale = x_train.std(axis=0)
    scale = np.where(scale < 1e-9, 1.0, scale)
    z_train = (x_train - mean) / scale
    z_holdout = (x_holdout - mean) / scale

    model = PoissonRegressor(alpha=MODEL_ALPHA, fit_intercept=True, max_iter=2000, tol=1e-8)
    model.fit(z_train, y_train)
    pred = np.maximum(model.predict(z_holdout), 1e-9)
    baseline = np.maximum(x_holdout[:, 0], 1e-9)

    dev = float(mean_poisson_deviance(y_holdout, pred))
    baseline_dev = float(mean_poisson_deviance(y_holdout, baseline))
    ratio = dev / baseline_dev if baseline_dev > 0 else float("inf")
    mae = float(mean_absolute_error(y_holdout, pred))
    baseline_mae = float(mean_absolute_error(y_holdout, baseline))

    training_max = int(max(r.actual for r in train))
    holdout_max = int(max(r.actual for r in holdout))
    q999 = float(np.quantile(y_train, 0.999))
    max_support = max(training_max, holdout_max, int(math.ceil(q999 + 6.0))) + 2

    finite_rate = float(np.isfinite(pred).mean())
    ood_rate = float((np.max(np.abs(z_holdout), axis=1) > MAX_Z_OOD).mean())
    validation_pass = (
        finite_rate == 1.0
        and len(holdout) >= MIN_HOLDOUT_ROWS
        and math.isfinite(dev)
        and math.isfinite(baseline_dev)
        and ratio <= VALIDATION_DEVIANCE_RATIO_MAX
    )
    blockers: list[str] = []
    if finite_rate != 1.0:
        blockers.append("WNBA_MODEL_NONFINITE_HOLDOUT_PREDICTIONS")
    if len(holdout) < MIN_HOLDOUT_ROWS:
        blockers.append("WNBA_MODEL_HOLDOUT_ROWS_BELOW_MINIMUM")
    if not math.isfinite(dev) or not math.isfinite(baseline_dev):
        blockers.append("WNBA_MODEL_HOLDOUT_METRICS_NONFINITE")
    if ratio > VALIDATION_DEVIANCE_RATIO_MAX:
        blockers.append("WNBA_MODEL_FAILS_NAIVE_BASELINE_DEVIANCE_GATE")

    stat_short = STAT_ROUTES[route][0]
    artifact_version = f"WNBA_{stat_short}_POISSON_LOGGLM_V1_{ARTIFACT_DATE}"
    training_code_sha = os.environ.get("GITHUB_SHA", "UNRESOLVED_TRAINING_CODE_SHA")
    dataset_hash = hashlib.sha256(
        (PLAYER_LOG_SHA256 + "|" + route + "|" + cutoff + "|" + str(len(featured))).encode("utf-8")
    ).hexdigest()
    payload = {
        "model_family": MODEL_FAMILY,
        "stat_type": route,
        "feature_names": list(FEATURE_NAMES),
        "feature_mean": mean.tolist(),
        "feature_scale": scale.tolist(),
        "coef": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "alpha": MODEL_ALPHA,
        "min_prior_games": MIN_PRIOR_GAMES,
        "max_support_k": max_support,
        "max_abs_z_for_coverage": MAX_Z_OOD,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "source_sha256": PLAYER_LOG_SHA256,
        "temporal_cutoff": cutoff,
    }
    checksum = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    metrics = {
        "validation_status": "PASS" if validation_pass else "BLOCKED",
        "blockers": blockers,
        "train_rows": len(train),
        "holdout_rows": len(holdout),
        "featured_rows": len(featured),
        "unique_players_featured": len({r.player_id for r in featured}),
        "temporal_cutoff": cutoff,
        "train_end": max(r.game_date for r in train),
        "holdout_start": min(r.game_date for r in holdout),
        "holdout_end": max(r.game_date for r in holdout),
        "mean_poisson_deviance": dev,
        "naive_l10_mean_poisson_deviance": baseline_dev,
        "deviance_ratio_vs_naive": ratio,
        "deviance_ratio_gate_max": VALIDATION_DEVIANCE_RATIO_MAX,
        "mae": mae,
        "naive_l10_mae": baseline_mae,
        "finite_prediction_rate": finite_rate,
        "holdout_ood_rate_z_gt_6": ood_rate,
        "training_target_max": training_max,
        "holdout_target_max": holdout_max,
        "max_support_k": max_support,
        "source": source_meta,
        "probability_publishable": False,
        "can_execute": False,
    }
    artifact = {
        "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
        "model_family": MODEL_FAMILY,
        "model_artifact_version": artifact_version,
        "calibrator_version": CALIBRATOR_VERSION,
        "sport": "WNBA",
        "stat_type": route,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "specialist_version": SPECIALIST_VERSION,
        "certification_id": f"WNBA-{stat_short}-OFFLINE-2026-08-30",
        "lifecycle_state": "CANDIDATE",
        "training_dataset_hash": dataset_hash,
        "training_code_sha": training_code_sha,
        "artifact_checksum": checksum,
        "artifact_format": "JSON_POISSON_LOGGLM_V1",
        "artifact_payload": payload,
        "supported_line_min": 0.0,
        "supported_line_max": float(max_support - 1),
        "training_rows": len(train),
        "validation_metrics": metrics,
        "certification_eligible": bool(validation_pass),
        "promoted": False,
        "active": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    return artifact, metrics


def main() -> int:
    out_dir = Path(os.environ.get("WNBA_ARTIFACT_OUT_DIR", Path(__file__).resolve().parent.parent / "data"))
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = _download(PLAYER_LOG_URL, PLAYER_LOG_SHA256)
    combined: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "model_family": MODEL_FAMILY,
        "source_url": PLAYER_LOG_URL,
        "source_sha256": PLAYER_LOG_SHA256,
        "training_code_sha": os.environ.get("GITHUB_SHA", "UNRESOLVED_TRAINING_CODE_SHA"),
        "routes": {},
        "probability_publishable": False,
        "can_execute": False,
    }
    all_pass = True
    for route, (_, aliases) in STAT_ROUTES.items():
        games, source_meta = _extract_games(payload, aliases)
        artifact, metrics = _fit_one(route, games, source_meta)
        combined.append(artifact)
        report["routes"][route] = metrics
        all_pass = all_pass and metrics["validation_status"] == "PASS"

    report["training_status"] = "PASS" if all_pass else "BLOCKED"
    report["artifact_registration_status"] = "NOT_ATTEMPTED"
    report["runtime_model_status"] = "MODEL_UNAVAILABLE"
    (out_dir / "wow_wnba_prop_artifacts_v1.json").write_text(json.dumps(combined, indent=2, sort_keys=True) + "\n")
    (out_dir / "wow_wnba_prop_training_report_v1.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if all_pass else 3


if __name__ == "__main__":
    sys.exit(main())
