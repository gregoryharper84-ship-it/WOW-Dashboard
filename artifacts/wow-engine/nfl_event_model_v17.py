"""Certified WOW V17 NFL outright-winner fitted model and publication adapter.

This module owns fitted NFL sporting probability only. It never consumes market
prices and never executes wagers. A champion artifact is promoted only after a
chronological train/calibration/validation split passes deterministic gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

from nfl_event_features_p2 import FEATURE_ORDER, FEATURE_SCHEMA_VERSION

CAN_EXECUTE = False
PROVIDER_IDENTITY = "WOW_NFL_EVENT_FITTED_MODEL_V1"
MODEL_FAMILY = "NFL_OUTRIGHT_WIN_LOGREG_V1"
FEATURE_TRANSFORM_VERSION = "STANDARD_SCALER_V1"
ARTIFACT_FORMAT = "JSON_COEFFICIENT_BUNDLE_V1"
CALIBRATION_METHOD = "PLATT_TIME_SPLIT_V1"
BOUNDS_METHOD_VERSION = "CALIBRATION_HOLDOUT_ERROR_MARGIN_V1"
TRAIN_SEASONS = (2021, 2022, 2023)
CALIBRATION_SEASON = 2024
VALIDATION_SEASON = 2025
MIN_TRAIN_N = 750
MIN_CALIBRATION_N = 250
MIN_VALIDATION_N = 250


class NFLModelUnavailable(RuntimeError):
    code = "MODEL_UNAVAILABLE"


class NFLModelInputsInsufficient(RuntimeError):
    code = "MODEL_INPUTS_INSUFFICIENT"


class NFLModelOutputInvalid(RuntimeError):
    code = "MODEL_OUTPUT_INVALID"


class NFLModelScorerFailed(RuntimeError):
    code = "MODEL_SCORER_FAILED"


@dataclass(frozen=True)
class CertifiedNFLModel:
    artifact_id: str
    model_artifact_version: str
    model_family: str
    feature_schema_version: str
    feature_order: tuple[str, ...]
    feature_order_hash: str
    scaler_mean: np.ndarray
    scaler_scale: np.ndarray
    coefficients: np.ndarray
    intercept: float
    platt_a: float
    platt_b: float
    calibration_version: str
    calibration_training_n: int
    uncertainty_margin: float
    bounds_method_version: str
    artifact_checksum: str
    training_dataset_hash: str
    validation_metrics: dict[str, Any]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def feature_order_hash() -> str:
    return _sha256_json(list(FEATURE_ORDER))


def _paginate(table_query: Any, *, page_size: int = 500) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        result = table_query.range(start, start + page_size - 1).execute()
        chunk = result.data if isinstance(result.data, list) else []
        rows.extend(dict(row) for row in chunk)
        if len(chunk) < page_size:
            break
        start += page_size
    return rows


def _load_training_feature_rows(db: Any) -> list[dict[str, Any]]:
    query = (
        db.table("wow_nfl_pregame_feature_rows")
        .select("game_id,season,gameday,feature_schema_version,feature_order,feature_vector,target_outcome,training_eligible,row_inputs_hash")
        .in_("season", [*TRAIN_SEASONS, CALIBRATION_SEASON, VALIDATION_SEASON])
        .eq("training_eligible", True)
        .order("season")
        .order("gameday")
        .order("game_id")
    )
    rows = _paginate(query)
    usable: list[dict[str, Any]] = []
    expected_order = list(FEATURE_ORDER)
    for row in rows:
        if row.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
            continue
        if list(row.get("feature_order") or []) != expected_order:
            continue
        target = str(row.get("target_outcome") or "")
        if target not in {"HOME_WIN", "AWAY_WIN"}:
            continue
        vector = row.get("feature_vector")
        if not isinstance(vector, list) or len(vector) != len(FEATURE_ORDER):
            continue
        try:
            parsed = [float(v) for v in vector]
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in parsed):
            continue
        copy = dict(row)
        copy["feature_vector"] = parsed
        usable.append(copy)
    return usable


def _dataset_hash(rows: Iterable[dict[str, Any]]) -> str:
    identity = [
        {
            "game_id": str(row["game_id"]),
            "season": int(row["season"]),
            "target_outcome": str(row["target_outcome"]),
            "row_inputs_hash": str(row.get("row_inputs_hash") or ""),
        }
        for row in rows
    ]
    return _sha256_json(identity)


def _ece(y_true: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> float:
    total = len(y_true)
    if total == 0:
        return float("inf")
    error = 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    for idx in range(bins):
        lo, hi = edges[idx], edges[idx + 1]
        mask = (probabilities >= lo) & (
            probabilities <= hi if idx == bins - 1 else probabilities < hi
        )
        count = int(mask.sum())
        if count:
            error += (count / total) * abs(float(probabilities[mask].mean()) - float(y_true[mask].mean()))
    return float(error)


def _sigmoid(value: np.ndarray | float) -> np.ndarray:
    arr = np.asarray(value, dtype=float)
    arr = np.clip(arr, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-arr))


def _split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    train = [r for r in rows if int(r["season"]) in TRAIN_SEASONS]
    calibration = [r for r in rows if int(r["season"]) == CALIBRATION_SEASON]
    validation = [r for r in rows if int(r["season"]) == VALIDATION_SEASON]
    return train, calibration, validation


def _xy(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([row["feature_vector"] for row in rows], dtype=float)
    y = np.asarray([1 if row["target_outcome"] == "HOME_WIN" else 0 for row in rows], dtype=int)
    return x, y


def fit_candidate(rows: list[dict[str, Any]], *, training_code_sha: str) -> dict[str, Any]:
    train, calibration, validation = _split(rows)
    if len(train) < MIN_TRAIN_N or len(calibration) < MIN_CALIBRATION_N or len(validation) < MIN_VALIDATION_N:
        raise NFLModelInputsInsufficient(
            f"NFL_TEMPORAL_SPLIT_TOO_THIN:{len(train)}:{len(calibration)}:{len(validation)}"
        )

    x_train, y_train = _xy(train)
    x_cal, y_cal = _xy(calibration)
    x_val, y_val = _xy(validation)
    if len(set(y_train.tolist())) != 2 or len(set(y_cal.tolist())) != 2 or len(set(y_val.tolist())) != 2:
        raise NFLModelInputsInsufficient("NFL_TEMPORAL_SPLIT_CLASS_IMBALANCE")

    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    base = LogisticRegression(solver="lbfgs", max_iter=4000, random_state=17)
    base.fit(x_train_scaled, y_train)

    cal_logits = base.decision_function(scaler.transform(x_cal)).reshape(-1, 1)
    platt = LogisticRegression(solver="lbfgs", max_iter=2000, random_state=17, penalty=None)
    platt.fit(cal_logits, y_cal)
    platt_a = float(platt.coef_[0, 0])
    platt_b = float(platt.intercept_[0])

    val_logits = base.decision_function(scaler.transform(x_val))
    raw_val = _sigmoid(val_logits)
    calibrated_val = _sigmoid(platt_a * val_logits + platt_b)

    brier = float(brier_score_loss(y_val, calibrated_val))
    ll = float(log_loss(y_val, np.column_stack([1.0 - calibrated_val, calibrated_val]), labels=[0, 1]))
    auc = float(roc_auc_score(y_val, calibrated_val))
    ece = _ece(y_val, calibrated_val)

    baseline_p = float(y_cal.mean())
    baseline_predictions = np.full(len(y_val), baseline_p, dtype=float)
    baseline_brier = float(brier_score_loss(y_val, baseline_predictions))
    baseline_ll = float(log_loss(y_val, np.column_stack([1.0 - baseline_predictions, baseline_predictions]), labels=[0, 1]))

    # This is deliberately named an error margin, not a confidence interval.
    uncertainty_margin = float(max(0.015, min(0.08, 1.96 * math.sqrt(max(brier, 1e-9) / len(y_val)))))

    metrics = {
        "train_n": len(train),
        "calibration_n": len(calibration),
        "validation_n": len(validation),
        "validation_brier_score": brier,
        "validation_log_loss": ll,
        "validation_auc": auc,
        "validation_ece_10": ece,
        "baseline_probability": baseline_p,
        "baseline_brier_score": baseline_brier,
        "baseline_log_loss": baseline_ll,
        "uncertainty_margin": uncertainty_margin,
        "raw_validation_brier_score": float(brier_score_loss(y_val, raw_val)),
        "train_seasons": list(TRAIN_SEASONS),
        "calibration_season": CALIBRATION_SEASON,
        "validation_season": VALIDATION_SEASON,
    }
    certification_checks = {
        "train_n": len(train) >= MIN_TRAIN_N,
        "calibration_n": len(calibration) >= MIN_CALIBRATION_N,
        "validation_n": len(validation) >= MIN_VALIDATION_N,
        "beats_baseline_brier": brier < baseline_brier,
        "beats_baseline_log_loss": ll < baseline_ll,
        "auc_not_below_chance": auc >= 0.50,
        "ece_within_limit": ece <= 0.12,
        "platt_positive_slope": platt_a > 0.0,
    }
    metrics["certification_checks"] = certification_checks
    certified = all(certification_checks.values())

    dataset_hash = _dataset_hash(rows)
    payload = {
        "provider_identity": PROVIDER_IDENTITY,
        "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_order": list(FEATURE_ORDER),
        "feature_order_hash": feature_order_hash(),
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "scaler_mean": [float(v) for v in scaler.mean_.tolist()],
        "scaler_scale": [float(v) for v in scaler.scale_.tolist()],
        "coefficients": [float(v) for v in base.coef_[0].tolist()],
        "intercept": float(base.intercept_[0]),
        "platt_a": platt_a,
        "platt_b": platt_b,
        "calibration_method": CALIBRATION_METHOD,
        "bounds_method_version": BOUNDS_METHOD_VERSION,
        "uncertainty_margin": uncertainty_margin,
        "temporal_split": {"train_seasons": list(TRAIN_SEASONS), "calibration_season": CALIBRATION_SEASON, "validation_season": VALIDATION_SEASON},
    }
    artifact_checksum = _sha256_json(payload)
    split_hash = _sha256_json(payload["temporal_split"])
    bundle_fingerprint = _sha256_json({"artifact_checksum": artifact_checksum, "training_dataset_hash": dataset_hash, "feature_order_hash": payload["feature_order_hash"], "calibration": [platt_a, platt_b]})
    model_version = f"NFL_EVENT_LOGREG_PLATT_V1_{dataset_hash[:12]}"
    calibration_version = f"NFL_PLATT_TIME_SPLIT_V1_{dataset_hash[:12]}"

    return {
        "certified": certified,
        "dataset_hash": dataset_hash,
        "split_hash": split_hash,
        "model_artifact_version": model_version,
        "calibration_version": calibration_version,
        "artifact_checksum": artifact_checksum,
        "bundle_fingerprint": bundle_fingerprint,
        "artifact_payload": payload,
        "metrics": metrics,
        "training_rows": len(train),
        "calibration_rows": len(calibration),
        "validation_rows": len(validation),
        "training_code_sha": training_code_sha,
        "fit_start": min(str(row["gameday"]) for row in train),
        "fit_end": max(str(row["gameday"]) for row in calibration),
    }


def _training_code_sha() -> str:
    env_sha = str(os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT_SHA") or "").strip().lower()
    if len(env_sha) in range(40, 65) and all(ch in "0123456789abcdef" for ch in env_sha):
        return env_sha
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _active_artifact_rows(db: Any) -> list[dict[str, Any]]:
    result = db.table("wow_nfl_event_fitted_model_artifacts").select("*").eq("active", True).eq("promoted", True).eq("lifecycle_state", "CHAMPION").limit(2).execute()
    return [dict(row) for row in (result.data or [])]


def ensure_champion_model(db: Any) -> dict[str, Any]:
    rows = _load_training_feature_rows(db)
    if not rows:
        raise NFLModelInputsInsufficient("NFL_TRAINING_FEATURE_ROWS_EMPTY")
    dataset_hash = _dataset_hash(rows)
    active = _active_artifact_rows(db)
    if len(active) > 1:
        raise NFLModelOutputInvalid("MULTIPLE_ACTIVE_NFL_CHAMPION_ARTIFACTS")
    if active and active[0].get("training_dataset_hash") == dataset_hash:
        return {"status": "EXISTING_CHAMPION", "artifact_id": active[0]["artifact_id"], "model_artifact_version": active[0]["model_artifact_version"], "training_dataset_hash": dataset_hash, "probability_publishable": False, "can_execute": False}

    fitted = fit_candidate(rows, training_code_sha=_training_code_sha())
    if not fitted["certified"]:
        version = fitted["model_artifact_version"]
        existing = db.table("wow_nfl_event_fitted_model_artifacts").select("artifact_id").eq("model_artifact_version", version).limit(1).execute()
        if not (existing.data or []):
            db.table("wow_nfl_event_fitted_model_artifacts").insert({
                "provider_identity": PROVIDER_IDENTITY,
                "model_family": MODEL_FAMILY,
                "model_artifact_version": version,
                "artifact_format": ARTIFACT_FORMAT,
                "artifact_payload": fitted["artifact_payload"],
                "artifact_checksum": fitted["artifact_checksum"],
                "bundle_fingerprint": fitted["bundle_fingerprint"],
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "feature_transform_version": FEATURE_TRANSFORM_VERSION,
                "training_code_sha": fitted["training_code_sha"],
                "training_dataset_hash": fitted["dataset_hash"],
                "training_rows": fitted["training_rows"],
                "validation_metrics": fitted["metrics"],
                "lifecycle_state": "BLOCKED",
                "active": False,
                "promoted": False,
                "probability_publishable": False,
                "can_execute": False,
            }).execute()
        return {"status": "CERTIFICATION_BLOCKED", "model_artifact_version": version, "validation_metrics": fitted["metrics"], "probability_publishable": False, "can_execute": False}

    calibrator = {
        "phase": "PHASE_B",
        "calibration_method": CALIBRATION_METHOD,
        "calibration_version": fitted["calibration_version"],
        "parent_cohort": "NFL_OUTRIGHT_WINNER_2024_TIME_SPLIT",
        "training_n": fitted["calibration_rows"],
        "fit_start": f"{CALIBRATION_SEASON}-01-01T00:00:00+00:00",
        "fit_end": f"{CALIBRATION_SEASON}-12-31T23:59:59+00:00",
        "fit_metrics_json": fitted["metrics"],
        "platt_a": fitted["artifact_payload"]["platt_a"],
        "platt_b": fitted["artifact_payload"]["platt_b"],
        "fold_train_audit_json": {"train_seasons": list(TRAIN_SEASONS), "calibration_season": CALIBRATION_SEASON, "validation_season": VALIDATION_SEASON, "validation_untouched_until_final_assessment": True},
        "bounds_method_version": BOUNDS_METHOD_VERSION,
        "sport": "NFL",
        "market_family": "OUTRIGHT_WINNER",
        "model_family": MODEL_FAMILY,
        "source_data_hash": fitted["dataset_hash"],
        "split_hash": fitted["split_hash"],
        "brier_score": fitted["metrics"]["validation_brier_score"],
        "log_loss": fitted["metrics"]["validation_log_loss"],
        "calibration_error": fitted["metrics"]["validation_ece_10"],
        "live_bounds_json": {"method": BOUNDS_METHOD_VERSION, "global_margin": fitted["metrics"]["uncertainty_margin"], "formal_confidence_interval": False},
    }
    artifact = {
        "provider_identity": PROVIDER_IDENTITY,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": fitted["model_artifact_version"],
        "artifact_format": ARTIFACT_FORMAT,
        "artifact_payload": fitted["artifact_payload"],
        "artifact_checksum": fitted["artifact_checksum"],
        "bundle_fingerprint": fitted["bundle_fingerprint"],
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_transform_version": FEATURE_TRANSFORM_VERSION,
        "training_code_sha": fitted["training_code_sha"],
        "training_dataset_hash": fitted["dataset_hash"],
        "training_rows": fitted["training_rows"],
        "validation_metrics": fitted["metrics"],
        "certification_id": f"NFL_V17_TEMPORAL_CERT_{fitted['dataset_hash'][:16]}",
        "can_execute": False,
    }
    result = db.rpc("wow_v17_promote_nfl_model_bundle", {"p_calibrator": calibrator, "p_artifact": artifact}).execute()
    if not isinstance(result.data, dict) or result.data.get("status") != "PROMOTED":
        raise NFLModelOutputInvalid("NFL_MODEL_PROMOTION_INVALID_RESPONSE")
    return {**result.data, "validation_metrics": fitted["metrics"], "training_dataset_hash": fitted["dataset_hash"], "probability_publishable": False, "can_execute": False}


def load_champion_model(db: Any) -> CertifiedNFLModel:
    active = _active_artifact_rows(db)
    if len(active) != 1:
        raise NFLModelUnavailable("NFL_CHAMPION_ARTIFACT_NOT_AVAILABLE")
    artifact = active[0]
    calibrator_id = artifact.get("calibrator_id")
    if not calibrator_id:
        raise NFLModelOutputInvalid("NFL_CHAMPION_CALIBRATOR_ID_MISSING")
    calibrator_result = db.table("wow_calibrators").select("*").eq("calibrator_id", calibrator_id).eq("active", True).eq("promoted", True).eq("validation_status", "PASS").eq("health_status", "PASS").limit(1).execute()
    calibrators = calibrator_result.data or []
    if len(calibrators) != 1:
        raise NFLModelUnavailable("NFL_ACTIVE_CALIBRATOR_NOT_AVAILABLE")
    calibrator = dict(calibrators[0])
    payload = dict(artifact.get("artifact_payload") or {})
    if payload.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise NFLModelOutputInvalid("NFL_FEATURE_SCHEMA_ARTIFACT_MISMATCH")
    if tuple(payload.get("feature_order") or ()) != tuple(FEATURE_ORDER):
        raise NFLModelOutputInvalid("NFL_FEATURE_ORDER_ARTIFACT_MISMATCH")
    if payload.get("feature_order_hash") != feature_order_hash():
        raise NFLModelOutputInvalid("NFL_FEATURE_ORDER_HASH_MISMATCH")
    if _sha256_json(payload) != artifact.get("artifact_checksum"):
        raise NFLModelOutputInvalid("NFL_ARTIFACT_CHECKSUM_MISMATCH")

    dim = len(FEATURE_ORDER)
    mean = np.asarray(payload.get("scaler_mean"), dtype=float)
    scale = np.asarray(payload.get("scaler_scale"), dtype=float)
    coef = np.asarray(payload.get("coefficients"), dtype=float)
    if mean.shape != (dim,) or scale.shape != (dim,) or coef.shape != (dim,):
        raise NFLModelOutputInvalid("NFL_ARTIFACT_DIMENSION_MISMATCH")
    if not np.all(np.isfinite(mean)) or not np.all(np.isfinite(scale)) or not np.all(np.isfinite(coef)):
        raise NFLModelOutputInvalid("NFL_ARTIFACT_NONFINITE_PARAMETERS")
    if np.any(scale <= 0):
        raise NFLModelOutputInvalid("NFL_ARTIFACT_INVALID_SCALER")

    bounds = dict(calibrator.get("live_bounds_json") or {})
    margin = float(bounds.get("global_margin"))
    if not 0.0 < margin <= 0.20:
        raise NFLModelOutputInvalid("NFL_BOUNDS_MARGIN_INVALID")

    return CertifiedNFLModel(
        artifact_id=str(artifact["artifact_id"]),
        model_artifact_version=str(artifact["model_artifact_version"]),
        model_family=str(artifact["model_family"]),
        feature_schema_version=str(artifact["feature_schema_version"]),
        feature_order=tuple(FEATURE_ORDER),
        feature_order_hash=feature_order_hash(),
        scaler_mean=mean,
        scaler_scale=scale,
        coefficients=coef,
        intercept=float(payload["intercept"]),
        platt_a=float(calibrator["platt_a"]),
        platt_b=float(calibrator["platt_b"]),
        calibration_version=str(calibrator["calibration_version"]),
        calibration_training_n=int(calibrator["training_n"]),
        uncertainty_margin=margin,
        bounds_method_version=str(calibrator.get("bounds_method_version") or BOUNDS_METHOD_VERSION),
        artifact_checksum=str(artifact["artifact_checksum"]),
        training_dataset_hash=str(artifact["training_dataset_hash"]),
        validation_metrics=dict(artifact.get("validation_metrics") or {}),
    )


def score_feature_row(model: CertifiedNFLModel, feature_row: dict[str, Any]) -> dict[str, Any]:
    if feature_row.get("feature_schema_version") != model.feature_schema_version:
        raise NFLModelInputsInsufficient("NFL_FEATURE_SCHEMA_INPUT_MISMATCH")
    if list(feature_row.get("feature_order") or []) != list(model.feature_order):
        raise NFLModelInputsInsufficient("NFL_FEATURE_ORDER_INPUT_MISMATCH")
    if feature_row.get("training_eligible") is False:
        raise NFLModelInputsInsufficient("NFL_FEATURE_ROW_INELIGIBLE:" + ",".join(feature_row.get("exclusion_reasons") or []))
    vector = feature_row.get("feature_vector")
    if not isinstance(vector, list) or len(vector) != len(model.feature_order):
        raise NFLModelInputsInsufficient("NFL_FEATURE_VECTOR_DIMENSION_MISMATCH")
    try:
        x = np.asarray([float(v) for v in vector], dtype=float)
    except (TypeError, ValueError) as exc:
        raise NFLModelInputsInsufficient("NFL_FEATURE_VECTOR_NONNUMERIC") from exc
    if not np.all(np.isfinite(x)):
        raise NFLModelInputsInsufficient("NFL_FEATURE_VECTOR_NONFINITE")

    standardized = (x - model.scaler_mean) / model.scaler_scale
    logit = float(np.dot(model.coefficients, standardized) + model.intercept)
    raw_home = float(_sigmoid(logit))
    calibrated_home = float(_sigmoid(model.platt_a * logit + model.platt_b))
    raw_away = 1.0 - raw_home
    calibrated_away = 1.0 - calibrated_home
    margin = model.uncertainty_margin
    home_lower = max(0.001, calibrated_home - margin)
    home_upper = min(0.999, calibrated_home + margin)
    away_lower = max(0.001, 1.0 - home_upper)
    away_upper = min(0.999, 1.0 - home_lower)

    values = [raw_home, raw_away, calibrated_home, calibrated_away, home_lower, home_upper, away_lower, away_upper]
    if not all(math.isfinite(v) and 0.0 < v < 1.0 for v in values):
        raise NFLModelOutputInvalid("NFL_PROBABILITY_PACKAGE_NONFINITE_OR_OUT_OF_RANGE")
    if abs(raw_home + raw_away - 1.0) > 1e-9 or abs(calibrated_home + calibrated_away - 1.0) > 1e-9:
        raise NFLModelOutputInvalid("NFL_PROBABILITY_PAIR_NOT_NORMALIZED")

    return {
        "raw_home_probability": raw_home,
        "raw_away_probability": raw_away,
        "independent_home_probability": raw_home,
        "independent_away_probability": raw_away,
        "calibrated_home_probability": calibrated_home,
        "calibrated_away_probability": calibrated_away,
        "calibrated_home_lower_bound": home_lower,
        "calibrated_home_upper_bound": home_upper,
        "calibrated_away_lower_bound": away_lower,
        "calibrated_away_upper_bound": away_upper,
        "calibration_method": CALIBRATION_METHOD,
        "calibration_version": model.calibration_version,
        "calibration_sample_scope": "NFL_2024_TIME_SPLIT_WITH_2025_HOLDOUT",
        "calibration_training_n": model.calibration_training_n,
        "calibration_health_status": "PASS",
        "uncertainty_method": model.bounds_method_version,
        "model_type": "FITTED_LOGISTIC_REGRESSION",
        "model_version": model.model_artifact_version,
        "model_label": PROVIDER_IDENTITY,
        "model_artifact_id": model.artifact_id,
        "model_timestamp": datetime.now(timezone.utc).isoformat(),
        "feature_schema_version": model.feature_schema_version,
        "feature_order_hash": model.feature_order_hash,
        "training_dataset_hash": model.training_dataset_hash,
        "artifact_checksum": model.artifact_checksum,
        "market_prior_weight": 0.0,
        "blend_publishable": False,
        "probability_publishable": True,
        "provenance_complete": True,
        "can_execute": False,
    }
