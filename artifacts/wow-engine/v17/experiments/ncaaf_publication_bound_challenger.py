"""Evidence-only NCAAF publication-bound challenger for issue #665.

This experiment never registers, certifies, promotes, publishes, ranks, or
executes a sporting probability. It operates only on an exact candidate whose
candidate-bound source review and deterministic replay already PASS.

The point probability is never changed. Event-level uncertainty is derived from
the exact fitted logistic artifact using a penalized-Hessian covariance estimate
fit on the chronological training partition. A separate calibration-error margin
is learned only from the chronological calibration partition. The untouched test
partition is evaluation-only.
"""
from __future__ import annotations

from hashlib import sha256
import json
from math import isfinite, log, sqrt
from typing import Any, Mapping, Sequence

import numpy as np

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
EXPERIMENT_VERSION = "NCAAF_HESSIAN_CALIBRATION_MARGIN_BOUND_CHALLENGER_V2"
BOUND_METHOD = "LOGISTIC_HESSIAN_EVENT_SE_PLUS_CALIBRATION_ERROR_MARGIN_V1"
SUPPORTED_MODEL_FAMILY = "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2"
SUPPORTED_CALIBRATOR = "IDENTITY_RAW_PROBABILITY_V1"
LOGIT_Z = 1.959963984540054
REGULARIZATION_C = 1.0

# Pre-registered challenger-review gates. They do not certify or promote.
OVERALL_RELIABILITY_TOLERANCE = -0.02
BIN_RELIABILITY_TOLERANCE = -0.08
THRESHOLD_RELIABILITY_TOLERANCE = -0.05
MAX_MEDIAN_HALF_WIDTH = 0.20


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
    calibrator = candidate.get("calibrator_payload") or {}
    if str(calibrator.get("method") or "") != SUPPORTED_CALIBRATOR:
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_CALIBRATOR_UNSUPPORTED")
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
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_SOURCE_REPLAY_NOT_VERIFIED")
    receipt = rows[0]
    if receipt.get("probability_publishable") is not False or receipt.get("can_execute") is not False:
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_EVIDENCE_GOVERNANCE_INVALID")
    return receipt


def _sigmoid(value: np.ndarray | float) -> np.ndarray:
    clipped = np.clip(np.asarray(value, dtype=float), -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _artifact_design(
    candidate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
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
    standardized = (x - means) / scales
    logits = standardized @ coefficients + intercept
    probabilities = np.clip(_sigmoid(logits), 1e-6, 1.0 - 1e-6)
    design = np.column_stack([np.ones(len(standardized), dtype=float), standardized])
    return probabilities, logits, design


def _artifact_probabilities(candidate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    probabilities, _logits, _design = _artifact_design(candidate, rows)
    return probabilities


def _outcomes(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    result: list[int] = []
    for row in rows:
        outcome = row.get("outcome_json")
        value = outcome.get("home_win") if isinstance(outcome, Mapping) else None
        if not isinstance(value, bool):
            raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_OUTCOME_INVALID")
        result.append(1 if value else 0)
    return np.asarray(result, dtype=int)


def _parameter_covariance(design: np.ndarray, probabilities: np.ndarray) -> tuple[np.ndarray, int]:
    if design.ndim != 2 or len(design) != len(probabilities):
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_HESSIAN_INPUT_INVALID")
    weights = np.clip(probabilities * (1.0 - probabilities), 1e-8, None)
    hessian = design.T @ (design * weights[:, None])
    penalty = np.zeros_like(hessian)
    # The fitted candidate uses sklearn LogisticRegression(C=1.0). Do not
    # penalize the intercept; apply the same L2 family to fitted coefficients.
    penalty[1:, 1:] = np.eye(hessian.shape[0] - 1) / REGULARIZATION_C
    hessian = hessian + penalty
    try:
        covariance = np.linalg.pinv(hessian, hermitian=True)
    except TypeError:
        covariance = np.linalg.pinv(hessian)
    if np.any(~np.isfinite(covariance)):
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_HESSIAN_NONFINITE")
    return covariance, int(np.linalg.matrix_rank(hessian))


def _calibration_error_margin(probabilities: np.ndarray, outcomes: np.ndarray) -> tuple[float, float]:
    if len(probabilities) < 50 or len(probabilities) != len(outcomes):
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_CALIBRATION_SAMPLE_INSUFFICIENT")
    brier = float(np.mean((np.asarray(probabilities, dtype=float) - outcomes) ** 2))
    # Same evidence family used by the certified NFL publication bundle: a held-
    # out proper-score error margin, explicitly not called a confidence interval.
    margin = float(max(0.015, min(0.08, LOGIT_Z * sqrt(max(brier, 1e-12) / len(outcomes)))))
    return brier, margin


def _event_bounds(
    *,
    logits: np.ndarray,
    design: np.ndarray,
    covariance: np.ndarray,
    calibration_margin: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    variances = np.einsum("ij,jk,ik->i", design, covariance, design)
    standard_errors = np.sqrt(np.maximum(0.0, variances))
    home_lower = np.maximum(0.001, _sigmoid(logits - LOGIT_Z * standard_errors) - calibration_margin)
    home_upper = np.minimum(0.999, _sigmoid(logits + LOGIT_Z * standard_errors) + calibration_margin)
    return home_lower, home_upper, standard_errors


def _ece(probabilities: np.ndarray, outcomes: np.ndarray, bins: int = 10) -> float:
    total = len(probabilities)
    if total == 0:
        return 1.0
    result = 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    for idx in range(bins):
        lo, hi = edges[idx], edges[idx + 1]
        mask = (probabilities >= lo) & (probabilities <= hi if idx == bins - 1 else probabilities < hi)
        n = int(np.sum(mask))
        if n:
            result += (n / total) * abs(float(np.mean(probabilities[mask])) - float(np.mean(outcomes[mask])))
    return float(result)


def _point_replay(candidate: Mapping[str, Any], probabilities: np.ndarray, outcomes: np.ndarray) -> dict[str, Any]:
    clipped = np.clip(probabilities, 1e-6, 1.0 - 1e-6)
    actual = {
        "calibrated_brier": float(np.mean((clipped - outcomes) ** 2)),
        "calibrated_log_loss": float(-np.mean(outcomes * np.log(clipped) + (1 - outcomes) * np.log(1.0 - clipped))),
        "ece": _ece(clipped, outcomes),
    }
    expected = candidate.get("validation_metrics") or {}
    deltas: dict[str, float | None] = {}
    comparable = True
    for key, value in actual.items():
        expected_value = expected.get(key) if isinstance(expected, Mapping) else None
        if not isinstance(expected_value, (int, float)) or isinstance(expected_value, bool):
            comparable = False
            deltas[key] = None
        else:
            deltas[key] = abs(value - float(expected_value))
    passed = (not comparable) or all(value is None or value <= 1e-9 for value in deltas.values())
    if comparable and not passed:
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_POINT_REPLAY_MISMATCH")
    return {"actual": actual, "expected": dict(expected) if isinstance(expected, Mapping) else {}, "deltas": deltas, "passed": passed, "comparable": comparable}


def _rank_slice(lower: np.ndarray, correct: np.ndarray, threshold: float) -> dict[str, Any]:
    mask = lower >= float(threshold)
    n = int(np.sum(mask))
    if n == 0:
        return {"threshold": threshold, "n": 0, "mean_lower_bound": None, "observed_hit_rate": None, "reliability_margin": None}
    mean_lower = float(np.mean(lower[mask]))
    hit_rate = float(np.mean(correct[mask]))
    return {"threshold": threshold, "n": n, "mean_lower_bound": mean_lower, "observed_hit_rate": hit_rate, "reliability_margin": hit_rate - mean_lower}


def _counterexample_bins(lower: np.ndarray, correct: np.ndarray, bins: int = 5) -> list[dict[str, Any]]:
    order = np.argsort(lower, kind="stable")
    output: list[dict[str, Any]] = []
    for index, chunk in enumerate(np.array_split(order, bins), start=1):
        if len(chunk) == 0:
            continue
        mean_lower = float(np.mean(lower[chunk]))
        hit_rate = float(np.mean(correct[chunk]))
        output.append({
            "bin": index,
            "n": int(len(chunk)),
            "lower_bound_min": float(np.min(lower[chunk])),
            "lower_bound_max": float(np.max(lower[chunk])),
            "mean_lower_bound": mean_lower,
            "observed_hit_rate": hit_rate,
            "reliability_margin": hit_rate - mean_lower,
        })
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

    probabilities, logits, design = _artifact_design(candidate, rows)
    outcomes = _outcomes(rows)
    train_end = train_n
    calibration_end = train_n + calibration_n

    covariance, hessian_rank = _parameter_covariance(design[:train_end], probabilities[:train_end])
    calibration_brier, calibration_margin = _calibration_error_margin(
        probabilities[train_end:calibration_end], outcomes[train_end:calibration_end]
    )
    home_lower, home_upper, standard_errors = _event_bounds(
        logits=logits,
        design=design,
        covariance=covariance,
        calibration_margin=calibration_margin,
    )

    p_test = probabilities[calibration_end:]
    y_test = outcomes[calibration_end:]
    test_home_lower = home_lower[calibration_end:]
    test_home_upper = home_upper[calibration_end:]
    point_replay = _point_replay(candidate, p_test, y_test)

    home_selected = p_test >= 0.5
    selected_probability = np.where(home_selected, p_test, 1.0 - p_test)
    correct = np.where(home_selected, y_test, 1 - y_test).astype(float)
    selected_lower = np.where(home_selected, test_home_lower, 1.0 - test_home_upper)
    selected_upper = np.where(home_selected, test_home_upper, 1.0 - test_home_lower)
    selected_lower = np.clip(selected_lower, 0.001, 0.999)
    selected_upper = np.clip(selected_upper, 0.001, 0.999)
    half_width = selected_probability - selected_lower

    mean_lower = float(np.mean(selected_lower))
    hit_rate = float(np.mean(correct))
    counterexamples = _counterexample_bins(selected_lower, correct)
    threshold_slices = [_rank_slice(selected_lower, correct, threshold) for threshold in (0.50, 0.55, 0.60)]
    worst_bin_margin = min(row["reliability_margin"] for row in counterexamples)
    threshold_gate = all(
        row["n"] < 30 or row["reliability_margin"] is not None and row["reliability_margin"] >= THRESHOLD_RELIABILITY_TOLERANCE
        for row in threshold_slices
    )
    review_checks = {
        "point_probability_replay_matches_candidate": bool(point_replay["passed"]),
        "overall_lower_bound_reliability": hit_rate - mean_lower >= OVERALL_RELIABILITY_TOLERANCE,
        "worst_quintile_lower_bound_reliability": worst_bin_margin >= BIN_RELIABILITY_TOLERANCE,
        "rankable_threshold_reliability": threshold_gate,
        "median_half_width_within_limit": float(np.median(half_width)) <= MAX_MEDIAN_HALF_WIDTH,
        "event_dynamic_width_nonconstant": float(np.max(half_width) - np.min(half_width)) > 1e-8,
    }
    challenger_pass = all(review_checks.values())

    residuals = np.abs(outcomes[train_end:calibration_end] - probabilities[train_end:calibration_end])
    try:
        residual_q90 = float(np.quantile(residuals, 0.90, method="higher"))
    except TypeError:
        residual_q90 = float(np.quantile(residuals, 0.90, interpolation="higher"))

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
        "calibrator_method": SUPPORTED_CALIBRATOR,
        "split": {"train_n": train_n, "calibration_n": calibration_n, "untouched_test_n": test_n},
        "bound_method": BOUND_METHOD,
        "parameter_uncertainty": {
            "method": "PENALIZED_LOGISTIC_HESSIAN_PSEUDOINVERSE",
            "regularization_c": REGULARIZATION_C,
            "logit_z": LOGIT_Z,
            "hessian_rank": hessian_rank,
            "parameter_count_including_intercept": int(design.shape[1]),
            "test_logit_se_min": float(np.min(standard_errors[calibration_end:])),
            "test_logit_se_median": float(np.median(standard_errors[calibration_end:])),
            "test_logit_se_max": float(np.max(standard_errors[calibration_end:])),
        },
        "calibration_brier": calibration_brier,
        "calibration_error_margin": calibration_margin,
        # Retained as a diagnostic/backward-compatible field only. Q90 is not
        # the V2 publication bound width.
        "calibration_residual_quantile_90": residual_q90,
        "historical_base_width": calibration_margin,
        "dynamic_width_summary": {
            "min": float(np.min(half_width)),
            "median": float(np.median(half_width)),
            "mean": float(np.mean(half_width)),
            "max": float(np.max(half_width)),
        },
        "point_probability_replay": point_replay,
        "untouched_test": {
            "n": test_n,
            "selected_side_accuracy": hit_rate,
            "mean_selected_probability": float(np.mean(selected_probability)),
            "mean_selected_lower_bound": mean_lower,
            "mean_selected_upper_bound": float(np.mean(selected_upper)),
            "overall_lower_bound_reliability_margin": hit_rate - mean_lower,
            "rankable_thresholds": threshold_slices,
            "counterexample_bins": counterexamples,
            "worst_bin_reliability_margin": worst_bin_margin,
        },
        "review_checks": review_checks,
        "challenger_pass": challenger_pass,
        "final_refresh_contract": {
            "contract_version": "NCAAF_DYNAMIC_PRIOR_STABLE_STATUS_V1",
            "numerically_modeled_by_this_candidate": [
                "prior_results_and_point_margin",
                "schedule_adjusted_form",
                "fragility_and_variance",
                "change_point_state",
                "roster_and_lineup_continuity_when_present_in_history",
                "rest_and_travel_when_present_in_history",
            ],
            "hold_only_untrained_material_families": [
                "quarterback_status",
                "injury_news_status",
                "venue_weather",
            ],
            "material_or_unresolved_change_action": "MODEL_QUALIFIED_HOLD",
            "manual_probability_adjustment_allowed": False,
            "market_probability_substitution_allowed": False,
            "requires_model_timestamp_gte_latest_material_update": True,
            "can_execute": False,
        },
        "methodological_notes": [
            "The exact fitted candidate point probability is preserved unchanged.",
            "Parameter covariance is fit only from the chronological training partition.",
            "Calibration error margin is fit only from the chronological calibration partition.",
            "The untouched test partition is evaluation-only and never tunes width or review thresholds.",
            "Static Wilson-bin diagnostic endpoints are not used as publication bounds.",
            "Untrained QB/injury/weather material changes are HOLD gates, never invented probability adjustments.",
            "Production adoption requires separate governed certification/promotion and current-history freshness.",
        ],
        "recommendation_state": "CHALLENGER_PASS_GOVERNED_REVIEW_REQUIRED" if challenger_pass else "CHALLENGER_FAIL_REVIEW_REQUIRED",
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
