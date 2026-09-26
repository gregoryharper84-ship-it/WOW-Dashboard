"""Evidence-only NCAAF publication-bound challenger for issue #665.

This experiment never registers, certifies, promotes, publishes, ranks, or
executes a sporting probability. It operates only on an exact candidate whose
candidate-bound source review and deterministic replay already PASS.

The uncertainty family mirrors the existing V17 multisport publication bridge:
use a calibration-history absolute-residual Q90 and cap the probability-width at
0.40. This module tests whether that family is conservative/useful for the exact
NCAAF dynamic-team-state candidate; it does not authorize production use.
"""
from __future__ import annotations

from hashlib import sha256
import json
from math import exp, isfinite
from typing import Any, Mapping, Sequence

import numpy as np

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
EXPERIMENT_VERSION = "NCAAF_RESIDUAL_Q90_PUBLICATION_BOUND_CHALLENGER_V1"
BOUND_METHOD = "HISTORICAL_CALIBRATION_ABSOLUTE_RESIDUAL_Q90_CAPPED_040"
SUPPORTED_MODEL_FAMILY = "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2"


class NCAAFPublicationBoundExperimentError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def _hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _paginate_rows(db: Any, candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = (
            db.table("wow_d1_training_rows")
            .select("official_event_id,event_start_time,features,outcome_json,can_execute,market_features_used")
            .eq("sport", candidate["sport"])
            .eq("league", candidate["league"])
            .eq("model_family", candidate["model_family"])
            .eq("feature_schema_version", candidate["feature_schema_version"])
            .order("event_start_time")
            .order("official_event_id")
            .range(offset, offset + 999)
            .execute()
        )
        batch = [dict(row) for row in (getattr(page, "data", None) or [])]
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def _exact_candidate(db: Any, candidate_id: str) -> dict[str, Any]:
    result = (
        db.table("wow_d1_candidate_artifacts")
        .select("*")
        .eq("candidate_id", candidate_id)
        .limit(2)
        .execute()
    )
    rows = [dict(row) for row in (getattr(result, "data", None) or [])]
    if len(rows) != 1:
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_CANDIDATE_NOT_FOUND")
    candidate = rows[0]
    if str(candidate.get("model_family") or "") != SUPPORTED_MODEL_FAMILY:
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_MODEL_FAMILY_UNSUPPORTED")
    if str(candidate.get("lifecycle_state") or "").upper() != "CANDIDATE":
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_CANDIDATE_NOT_INERT")
    for flag in (
        "promoted",
        "active",
        "automatic_certification",
        "automatic_promotion",
        "probability_publishable",
    ):
        if candidate.get(flag) is True:
            raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_CANDIDATE_INERTNESS_VIOLATION")
    if candidate.get("can_execute") is not False:
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_CANDIDATE_EXECUTION_VIOLATION")
    return candidate


def _verified_receipt(db: Any, candidate: Mapping[str, Any]) -> dict[str, Any]:
    result = (
        db.table("wow_d1_certification_evidence_receipts")
        .select("*")
        .eq("candidate_id", candidate["candidate_id"])
        .eq("model_artifact_version", candidate["model_artifact_version"])
        .eq("training_dataset_hash", candidate["training_dataset_hash"])
        .eq("artifact_checksum", candidate["artifact_checksum"])
        .eq("source_review_status", "PASS")
        .eq("replay_status", "PASS")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = [dict(row) for row in (getattr(result, "data", None) or [])]
    if len(rows) != 1:
        raise NCAAFPublicationBoundExperimentError(
            "NCAAF_BOUND_SOURCE_REPLAY_NOT_VERIFIED"
        )
    receipt = rows[0]
    if receipt.get("probability_publishable") is not False or receipt.get("can_execute") is not False:
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_EVIDENCE_GOVERNANCE_INVALID")
    return receipt


def _sigmoid(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(value, dtype=float), -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _artifact_probabilities(candidate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    artifact = candidate.get("artifact_payload") or {}
    names = tuple(str(v) for v in (artifact.get("feature_names") or []))
    means = np.asarray(artifact.get("scaler_mean") or [], dtype=float)
    scales = np.asarray(artifact.get("scaler_scale") or [], dtype=float)
    coefficients = np.asarray(artifact.get("coefficients") or [], dtype=float)
    intercept = float(artifact.get("intercept"))
    width = len(names)
    if not width or any(len(v) != width for v in (means, scales, coefficients)):
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_ARTIFACT_SHAPE_INVALID")
    if np.any(~np.isfinite(means)) or np.any(~np.isfinite(scales)) or np.any(~np.isfinite(coefficients)):
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_ARTIFACT_NONFINITE")
    if np.any(scales <= 0.0) or not isfinite(intercept):
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_ARTIFACT_SCALE_INVALID")

    matrix: list[list[float]] = []
    for row in rows:
        if row.get("market_features_used") is not False or row.get("can_execute") is not False:
            raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_TRAINING_ROW_GOVERNANCE_INVALID")
        features = row.get("features")
        if not isinstance(features, Mapping):
            raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_FEATURE_ROW_INVALID")
        try:
            values = [float(features[name]) for name in names]
        except (KeyError, TypeError, ValueError) as exc:
            raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_FEATURE_ROW_INVALID") from exc
        if not all(isfinite(value) for value in values):
            raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_FEATURE_ROW_NONFINITE")
        matrix.append(values)
    x = np.asarray(matrix, dtype=float)
    logits = ((x - means) / scales) @ coefficients + intercept
    return np.clip(_sigmoid(logits), 1e-6, 1.0 - 1e-6)


def _calibrate(probabilities: np.ndarray, calibrator: Mapping[str, Any]) -> np.ndarray:
    method = str(calibrator.get("method") or "")
    values = np.asarray(probabilities, dtype=float)
    if method == "IDENTITY_RAW_PROBABILITY_V1":
        return np.clip(values, 1e-6, 1.0 - 1e-6)
    if method != "EMPIRICAL_WILSON_BINS_V1":
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_CALIBRATOR_UNSUPPORTED")
    bins = calibrator.get("bins")
    if not isinstance(bins, list) or not bins:
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_CALIBRATOR_EMPTY")
    centers = np.asarray([float(row["raw_mean"]) for row in bins], dtype=float)
    points = np.asarray([float(row["calibrated_probability"]) for row in bins], dtype=float)
    mapped = [points[int(np.argmin(np.abs(centers - value)))] for value in values]
    return np.clip(np.asarray(mapped, dtype=float), 1e-6, 1.0 - 1e-6)


def _outcomes(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    result: list[int] = []
    for row in rows:
        outcome = row.get("outcome_json")
        value = outcome.get("home_win") if isinstance(outcome, Mapping) else None
        if not isinstance(value, bool):
            raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_OUTCOME_INVALID")
        result.append(1 if value else 0)
    return np.asarray(result, dtype=int)


def _rank_slice(lower: np.ndarray, correct: np.ndarray, threshold: float) -> dict[str, Any]:
    mask = lower >= float(threshold)
    n = int(np.sum(mask))
    if n == 0:
        return {
            "threshold": threshold,
            "n": 0,
            "mean_lower_bound": None,
            "observed_hit_rate": None,
            "reliability_margin": None,
        }
    mean_lower = float(np.mean(lower[mask]))
    hit_rate = float(np.mean(correct[mask]))
    return {
        "threshold": threshold,
        "n": n,
        "mean_lower_bound": mean_lower,
        "observed_hit_rate": hit_rate,
        "reliability_margin": hit_rate - mean_lower,
    }


def _counterexample_bins(lower: np.ndarray, correct: np.ndarray, bins: int = 5) -> list[dict[str, Any]]:
    order = np.argsort(lower, kind="stable")
    output: list[dict[str, Any]] = []
    for index, chunk in enumerate(np.array_split(order, bins), start=1):
        if len(chunk) == 0:
            continue
        mean_lower = float(np.mean(lower[chunk]))
        hit_rate = float(np.mean(correct[chunk]))
        output.append(
            {
                "bin": index,
                "n": int(len(chunk)),
                "lower_bound_min": float(np.min(lower[chunk])),
                "lower_bound_max": float(np.max(lower[chunk])),
                "mean_lower_bound": mean_lower,
                "observed_hit_rate": hit_rate,
                "reliability_margin": hit_rate - mean_lower,
            }
        )
    return output


def run_ncaaf_publication_bound_challenger(db: Any, candidate_id: str) -> dict[str, Any]:
    candidate = _exact_candidate(db, candidate_id)
    receipt = _verified_receipt(db, candidate)
    rows = _paginate_rows(db, candidate)
    train_n = int(candidate.get("training_rows") or 0)
    calibration_n = int(candidate.get("calibration_rows") or 0)
    test_n = int(candidate.get("test_rows") or 0)
    if len(rows) != train_n + calibration_n + test_n or min(train_n, calibration_n, test_n) <= 0:
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_PARTITION_IDENTITY_INVALID")

    raw = _artifact_probabilities(candidate, rows)
    calibrated = _calibrate(raw, candidate.get("calibrator_payload") or {})
    y = _outcomes(rows)
    cal_start = train_n
    cal_end = train_n + calibration_n
    p_cal = calibrated[cal_start:cal_end]
    y_cal = y[cal_start:cal_end]
    p_test = calibrated[cal_end:]
    y_test = y[cal_end:]

    # Existing multisport V17 uses the 90th percentile of historical absolute
    # probability residuals as the base dynamic uncertainty component. Derive it
    # ONLY from the calibration block; the untouched test block is evaluation-only.
    residuals = np.abs(y_cal - p_cal)
    try:
        residual_q90 = float(np.quantile(residuals, 0.90, method="higher"))
    except TypeError:  # numpy compatibility for older environments
        residual_q90 = float(np.quantile(residuals, 0.90, interpolation="higher"))
    width = min(0.40, max(0.005, residual_q90))

    home_selected = p_test >= 0.5
    selected_probability = np.where(home_selected, p_test, 1.0 - p_test)
    correct = np.where(home_selected, y_test, 1 - y_test).astype(float)
    lower = np.maximum(0.001, selected_probability - width)
    upper = np.minimum(0.999, selected_probability + width)

    mean_lower = float(np.mean(lower))
    hit_rate = float(np.mean(correct))
    counterexamples = _counterexample_bins(lower, correct)
    output = {
        "status": "EXPERIMENT_COMPLETE",
        "experiment_version": EXPERIMENT_VERSION,
        "candidate_id": str(candidate["candidate_id"]),
        "model_family": str(candidate["model_family"]),
        "model_artifact_version": str(candidate["model_artifact_version"]),
        "source_replay_receipt_id": str(receipt.get("receipt_id") or ""),
        "source_replay_evidence_sha256": str(receipt.get("evidence_sha256") or ""),
        "training_dataset_hash": str(candidate["training_dataset_hash"]),
        "artifact_checksum": str(candidate["artifact_checksum"]),
        "calibrator_method": str((candidate.get("calibrator_payload") or {}).get("method") or ""),
        "split": {"train_n": train_n, "calibration_n": calibration_n, "untouched_test_n": test_n},
        "bound_method": BOUND_METHOD,
        "calibration_residual_quantile_90": residual_q90,
        "historical_base_width": width,
        "untouched_test": {
            "n": test_n,
            "selected_side_accuracy": hit_rate,
            "mean_selected_probability": float(np.mean(selected_probability)),
            "mean_selected_lower_bound": mean_lower,
            "mean_selected_upper_bound": float(np.mean(upper)),
            "overall_lower_bound_reliability_margin": hit_rate - mean_lower,
            "rankable_thresholds": [
                _rank_slice(lower, correct, threshold)
                for threshold in (0.50, 0.55, 0.60)
            ],
            "counterexample_bins": counterexamples,
            "worst_bin_reliability_margin": min(
                row["reliability_margin"] for row in counterexamples
            ),
        },
        "methodological_notes": [
            "Calibration residual Q90 is fit only on the chronological calibration partition.",
            "Untouched test rows are used only for bound reliability/counterexample evaluation.",
            "Static Wilson-bin diagnostic endpoints are not used as publication bounds.",
            "No current-status freshness or model-disagreement additive is applied in historical replay.",
            "Production adoption requires separate governed review plus current-history/final-refresh gates.",
        ],
        "recommendation_state": "GOVERNED_REVIEW_REQUIRED",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "database_mutated": False,
        "production_registry_mutated": False,
        "global_terminal_reducer": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }
    output["experiment_sha256"] = _hash(output)
    return output


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "BOUND_METHOD",
    "CAN_EXECUTE",
    "EXPERIMENT_VERSION",
    "NCAAFPublicationBoundExperimentError",
    "PROBABILITY_PUBLISHABLE",
    "run_ncaaf_publication_bound_challenger",
]
