#!/usr/bin/env python3
"""Offset-Poisson WNBA prop trainer built on the audited data contract."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize
from sklearn.metrics import mean_absolute_error, mean_poisson_deviance

from scripts import train_wnba_props as base

MODEL_ALPHA = 0.02
MIN_GLM_BLEND_WEIGHT = 0.10
INNER_TRAIN_FRACTION = 0.80


def correction_design(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    eps = 0.25
    baseline = np.maximum(x[:, 0], 0.05)
    design = np.column_stack([
        np.log((x[:, 1] + eps) / (x[:, 0] + eps)),
        np.log((x[:, 2] + eps) / (x[:, 0] + eps)),
        np.log(np.maximum(x[:, 4], 0.1) / np.maximum(x[:, 3], 0.1)),
        np.log(np.maximum(x[:, 5], 0.1) / np.maximum(x[:, 3], 0.1)),
    ])
    return baseline, design


def fit_offset_glm(x: np.ndarray, y: np.ndarray) -> tuple[float, np.ndarray]:
    baseline, design = correction_design(x)
    matrix = np.column_stack([np.ones(len(design)), design])

    def value_grad(beta: np.ndarray) -> tuple[float, np.ndarray]:
        eta = np.clip(matrix @ beta, -4.0, 4.0)
        mu = baseline * np.exp(eta)
        value = float(np.mean(mu - y * np.log(np.maximum(mu, 1e-12))))
        value += MODEL_ALPHA * float(np.dot(beta[1:], beta[1:]))
        grad = (matrix.T @ (mu - y)) / len(y)
        grad[1:] += 2.0 * MODEL_ALPHA * beta[1:]
        return value, grad

    result = minimize(
        fun=lambda b: value_grad(b)[0],
        x0=np.zeros(matrix.shape[1], dtype=float),
        jac=lambda b: value_grad(b)[1],
        method="L-BFGS-B",
        bounds=[(-0.50, 0.50)] + [(-1.50, 1.50)] * 4,
        options={"maxiter": 2000, "ftol": 1e-12},
    )
    if not result.success or not np.isfinite(result.x).all():
        raise base.TrainingUnavailable(f"OFFSET_POISSON_FIT_FAILED:{result.message}")
    return float(result.x[0]), np.asarray(result.x[1:], dtype=float)


def offset_predict(x: np.ndarray, intercept: float, coef: np.ndarray) -> np.ndarray:
    baseline, design = correction_design(x)
    eta = np.clip(intercept + design @ coef, -4.0, 4.0)
    return np.maximum(baseline * np.exp(eta), 1e-9)


def choose_blend_weight(train: list[base.FeaturedRow]) -> tuple[float, dict[str, Any]]:
    dates = sorted({r.game_date for r in train})
    cut_idx = min(max(int(len(dates) * INNER_TRAIN_FRACTION), 1), len(dates) - 1)
    cutoff = dates[cut_idx]
    inner_train = [r for r in train if r.game_date < cutoff]
    inner_val = [r for r in train if r.game_date >= cutoff]
    if len(inner_train) < 200 or len(inner_val) < 100:
        raise base.TrainingUnavailable(f"INNER_SPLIT_TOO_SMALL train={len(inner_train)} validation={len(inner_val)}")
    x_fit, y_fit = base._matrix(inner_train)
    x_val, y_val = base._matrix(inner_val)
    intercept, coef = fit_offset_glm(x_fit, y_fit)
    glm_pred = offset_predict(x_val, intercept, coef)
    baseline = np.maximum(x_val[:, 0], 1e-9)
    choices: list[tuple[float, float]] = []
    for weight in np.arange(MIN_GLM_BLEND_WEIGHT, 1.0001, 0.10):
        pred = (1.0 - weight) * baseline + weight * glm_pred
        dev = float(mean_poisson_deviance(y_val, np.maximum(pred, 1e-9)))
        choices.append((dev, float(round(weight, 2))))
    best_dev, best_weight = min(choices, key=lambda item: (item[0], item[1]))
    return best_weight, {
        "inner_cutoff": cutoff,
        "inner_train_rows": len(inner_train),
        "inner_validation_rows": len(inner_val),
        "selected_blend_weight": best_weight,
        "selected_inner_deviance": best_dev,
        "candidate_deviance_by_weight": {format(w, ".2f"): d for d, w in choices},
    }


def fit_one(route: str, games: list[base.Game], source_meta: dict[str, Any]):
    featured = base.build_featured_rows(games)
    train, holdout, cutoff = base._split(featured)
    x_train, y_train = base._matrix(train)
    x_holdout, y_holdout = base._matrix(holdout)
    raw_mean = x_train.mean(axis=0)
    raw_scale = np.where(x_train.std(axis=0) < 1e-9, 1.0, x_train.std(axis=0))
    z_holdout = (x_holdout - raw_mean) / raw_scale

    blend_weight, selection = choose_blend_weight(train)
    intercept, coef = fit_offset_glm(x_train, y_train)
    glm_pred = offset_predict(x_holdout, intercept, coef)
    baseline = np.maximum(x_holdout[:, 0], 1e-9)
    pred = (1.0 - blend_weight) * baseline + blend_weight * glm_pred

    dev = float(mean_poisson_deviance(y_holdout, pred))
    baseline_dev = float(mean_poisson_deviance(y_holdout, baseline))
    ratio = dev / baseline_dev if baseline_dev > 0 else float("inf")
    training_max = int(max(r.actual for r in train))
    holdout_max = int(max(r.actual for r in holdout))
    q999 = float(np.quantile(y_train, 0.999))
    max_support = max(training_max, holdout_max, int(math.ceil(q999 + 6.0))) + 2
    finite_rate = float(np.isfinite(pred).mean())
    ood_rate = float((np.max(np.abs(z_holdout), axis=1) > base.MAX_Z_OOD).mean())
    validation_pass = (
        finite_rate == 1.0
        and len(holdout) >= base.MIN_HOLDOUT_ROWS
        and math.isfinite(dev)
        and math.isfinite(baseline_dev)
        and blend_weight >= MIN_GLM_BLEND_WEIGHT
        and ratio <= base.VALIDATION_DEVIANCE_RATIO_MAX
    )
    blockers = []
    if finite_rate != 1.0:
        blockers.append("WNBA_MODEL_NONFINITE_HOLDOUT_PREDICTIONS")
    if ratio > base.VALIDATION_DEVIANCE_RATIO_MAX:
        blockers.append("WNBA_MODEL_FAILS_NAIVE_BASELINE_DEVIANCE_GATE")

    stat_short = base.STAT_ROUTES[route][0]
    training_code_sha = os.environ.get("GITHUB_SHA", "UNRESOLVED_TRAINING_CODE_SHA")
    dataset_hash = hashlib.sha256(
        (base.PLAYER_LOG_SHA256 + "|" + route + "|" + cutoff + "|" + str(len(featured))).encode()
    ).hexdigest()
    payload = {
        "model_family": base.MODEL_FAMILY,
        "model_kind": "OFFSET_POISSON_BLEND_V1",
        "stat_type": route,
        "feature_names": list(base.FEATURE_NAMES),
        "feature_mean": raw_mean.tolist(),
        "feature_scale": raw_scale.tolist(),
        "correction_feature_names": [
            "log_l5_to_l10_stat", "log_last_to_l10_stat",
            "log_l5_to_l10_minutes", "log_last_to_l10_minutes",
        ],
        "coef": coef.tolist(),
        "intercept": intercept,
        "blend_weight_glm": blend_weight,
        "alpha": MODEL_ALPHA,
        "min_prior_games": base.MIN_PRIOR_GAMES,
        "max_support_k": max_support,
        "max_abs_z_for_coverage": base.MAX_Z_OOD,
        "feature_transform_version": base.FEATURE_TRANSFORM_VERSION,
        "source_sha256": base.PLAYER_LOG_SHA256,
        "temporal_cutoff": cutoff,
    }
    checksum = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    metrics = {
        "validation_status": "PASS" if validation_pass else "BLOCKED",
        "blockers": blockers,
        "train_rows": len(train), "holdout_rows": len(holdout), "featured_rows": len(featured),
        "unique_players_featured": len({r.player_id for r in featured}),
        "temporal_cutoff": cutoff, "train_end": max(r.game_date for r in train),
        "holdout_start": min(r.game_date for r in holdout), "holdout_end": max(r.game_date for r in holdout),
        "mean_poisson_deviance": dev, "naive_l10_mean_poisson_deviance": baseline_dev,
        "deviance_ratio_vs_naive": ratio, "deviance_ratio_gate_max": base.VALIDATION_DEVIANCE_RATIO_MAX,
        "mae": float(mean_absolute_error(y_holdout, pred)),
        "naive_l10_mae": float(mean_absolute_error(y_holdout, baseline)),
        "finite_prediction_rate": finite_rate, "holdout_ood_rate_z_gt_6": ood_rate,
        "training_target_max": training_max, "holdout_target_max": holdout_max, "max_support_k": max_support,
        "inner_model_selection": selection, "fitted_component_weight": blend_weight,
        "source": source_meta, "probability_publishable": False, "can_execute": False,
    }
    artifact = {
        "provider_identity": "WOW_PROP_FITTED_MODEL_V1", "model_family": base.MODEL_FAMILY,
        "model_artifact_version": f"WNBA_{stat_short}_POISSON_LOGGLM_V1_{base.ARTIFACT_DATE}",
        "calibrator_version": base.CALIBRATOR_VERSION, "sport": "WNBA", "stat_type": route,
        "feature_schema_version": base.FEATURE_SCHEMA_VERSION,
        "feature_transform_version": base.FEATURE_TRANSFORM_VERSION,
        "specialist_version": base.SPECIALIST_VERSION,
        "certification_id": f"WNBA-{stat_short}-OFFLINE-2026-08-30",
        "lifecycle_state": "CANDIDATE", "training_dataset_hash": dataset_hash,
        "training_code_sha": training_code_sha, "artifact_checksum": checksum,
        "artifact_format": "JSON_POISSON_LOGGLM_V1", "artifact_payload": payload,
        "supported_line_min": 0.0, "supported_line_max": float(max_support - 1),
        "training_rows": len(train), "validation_metrics": metrics,
        "certification_eligible": bool(validation_pass), "promoted": False, "active": False,
        "probability_publishable": False, "can_execute": False,
    }
    return artifact, metrics


def main() -> int:
    out_dir = Path(os.environ.get("WNBA_ARTIFACT_OUT_DIR", Path(__file__).resolve().parent.parent / "data"))
    out_dir.mkdir(parents=True, exist_ok=True)
    source = base._download(base.PLAYER_LOG_URL, base.PLAYER_LOG_SHA256)
    artifacts = []
    report = {
        "model_family": base.MODEL_FAMILY, "source_url": base.PLAYER_LOG_URL,
        "source_sha256": base.PLAYER_LOG_SHA256,
        "training_code_sha": os.environ.get("GITHUB_SHA", "UNRESOLVED_TRAINING_CODE_SHA"),
        "routes": {}, "probability_publishable": False, "can_execute": False,
    }
    all_pass = True
    for route, (_, aliases) in base.STAT_ROUTES.items():
        games, source_meta = base._extract_games(source, aliases)
        artifact, metrics = fit_one(route, games, source_meta)
        artifacts.append(artifact)
        report["routes"][route] = metrics
        all_pass = all_pass and metrics["validation_status"] == "PASS"
    report["training_status"] = "PASS" if all_pass else "BLOCKED"
    report["artifact_registration_status"] = "NOT_ATTEMPTED"
    report["runtime_model_status"] = "MODEL_UNAVAILABLE"
    (out_dir / "wow_wnba_prop_artifacts_v1.json").write_text(json.dumps(artifacts, indent=2, sort_keys=True) + "\n")
    (out_dir / "wow_wnba_prop_training_report_v1.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if all_pass else 3


if __name__ == "__main__":
    sys.exit(main())
