"""Research-only NCAAF publication-bound challenger V3 for issue #665.

V2 established that the controlling candidate's lower bounds were reliable but
failed the pre-registered usefulness gate because its two-sided 95% Hessian
interval was too wide. V3 does not relax that gate or tune on the exposed test
block. It changes the inferential target: publication ranking needs a one-sided
lower bound, so V3 uses the standard one-sided 95% normal quantile (z=1.64485)
for fitted-parameter uncertainty and a calibration-only proper-score margin.

The original test block is now explicitly historical replay evidence because it
was observed during V2. Production adoption requires a genuinely later forward
cohort scored by the frozen candidate. This module never certifies, promotes,
publishes, ranks, mutates the registry, or executes a wager.
"""
from __future__ import annotations

from hashlib import sha256
import json
from math import sqrt
from typing import Any, Mapping

import numpy as np

from v17.experiments.ncaaf_publication_bound_challenger import (
    BIN_RELIABILITY_TOLERANCE,
    MAX_MEDIAN_HALF_WIDTH,
    NCAAFPublicationBoundExperimentError,
    OVERALL_RELIABILITY_TOLERANCE,
    REGULARIZATION_C,
    SUPPORTED_CALIBRATOR,
    THRESHOLD_RELIABILITY_TOLERANCE,
    _artifact_design,
    _counterexample_bins,
    _exact_candidate,
    _outcomes,
    _paginate_rows,
    _parameter_covariance,
    _point_replay,
    _rank_slice,
    _sigmoid,
    _verified_receipt,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
RANK_ELIGIBLE = False
EXPERIMENT_VERSION = "NCAAF_ONE_SIDED_95_BOUND_FORWARD_CHALLENGER_V3"
BOUND_METHOD = "LOGISTIC_HESSIAN_ONE_SIDED_95_PLUS_CALIBRATION_MARGIN_V1"
ONE_SIDED_Z = 1.6448536269514722
MIN_FORWARD_N = 100
MIN_THRESHOLD_N = 30


def _hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _calibration_margin(probabilities: np.ndarray, outcomes: np.ndarray) -> tuple[float, float]:
    if len(probabilities) < 50 or len(probabilities) != len(outcomes):
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_CALIBRATION_SAMPLE_INSUFFICIENT")
    brier = float(np.mean((np.asarray(probabilities, dtype=float) - outcomes) ** 2))
    margin = float(max(0.015, min(0.08, ONE_SIDED_Z * sqrt(max(brier, 1e-12) / len(outcomes)))))
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
    lower = np.maximum(0.001, _sigmoid(logits - ONE_SIDED_Z * standard_errors) - calibration_margin)
    upper = np.minimum(0.999, _sigmoid(logits + ONE_SIDED_Z * standard_errors) + calibration_margin)
    return lower, upper, standard_errors


def _selected_metrics(
    probabilities: np.ndarray,
    outcomes: np.ndarray,
    home_lower: np.ndarray,
    home_upper: np.ndarray,
) -> dict[str, Any]:
    if not (len(probabilities) == len(outcomes) == len(home_lower) == len(home_upper)):
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_EVALUATION_SHAPE_INVALID")
    if len(probabilities) == 0:
        return {"n": 0, "review_checks": {}, "review_pass": False}

    home_selected = probabilities >= 0.5
    selected_probability = np.where(home_selected, probabilities, 1.0 - probabilities)
    correct = np.where(home_selected, outcomes, 1 - outcomes).astype(float)
    selected_lower = np.where(home_selected, home_lower, 1.0 - home_upper)
    selected_upper = np.where(home_selected, home_upper, 1.0 - home_lower)
    selected_lower = np.clip(selected_lower, 0.001, 0.999)
    selected_upper = np.clip(selected_upper, 0.001, 0.999)
    half_width = selected_probability - selected_lower

    mean_lower = float(np.mean(selected_lower))
    hit_rate = float(np.mean(correct))
    bins = _counterexample_bins(selected_lower, correct)
    thresholds = [_rank_slice(selected_lower, correct, value) for value in (0.50, 0.55, 0.60)]
    worst_bin_margin = min(row["reliability_margin"] for row in bins)
    threshold_gate = all(
        row["n"] < MIN_THRESHOLD_N
        or (
            row["reliability_margin"] is not None
            and row["reliability_margin"] >= THRESHOLD_RELIABILITY_TOLERANCE
        )
        for row in thresholds
    )
    checks = {
        "overall_lower_bound_reliability": hit_rate - mean_lower >= OVERALL_RELIABILITY_TOLERANCE,
        "worst_quintile_lower_bound_reliability": worst_bin_margin >= BIN_RELIABILITY_TOLERANCE,
        "rankable_threshold_reliability": threshold_gate,
        "median_half_width_within_limit": float(np.median(half_width)) <= MAX_MEDIAN_HALF_WIDTH,
        "event_dynamic_width_nonconstant": float(np.max(half_width) - np.min(half_width)) > 1e-8,
    }
    return {
        "n": int(len(probabilities)),
        "selected_side_accuracy": hit_rate,
        "mean_selected_probability": float(np.mean(selected_probability)),
        "mean_selected_lower_bound": mean_lower,
        "mean_selected_upper_bound": float(np.mean(selected_upper)),
        "overall_lower_bound_reliability_margin": hit_rate - mean_lower,
        "rankable_thresholds": thresholds,
        "counterexample_bins": bins,
        "worst_bin_reliability_margin": worst_bin_margin,
        "dynamic_width_summary": {
            "min": float(np.min(half_width)),
            "median": float(np.median(half_width)),
            "mean": float(np.mean(half_width)),
            "max": float(np.max(half_width)),
        },
        "review_checks": checks,
        "review_pass": all(checks.values()),
    }


def run_ncaaf_publication_bound_challenger_v3(db: Any, candidate_id: str) -> dict[str, Any]:
    candidate = _exact_candidate(db, candidate_id)
    receipt = _verified_receipt(db, candidate)
    rows = _paginate_rows(db, candidate)
    train_n = int(candidate.get("training_rows") or 0)
    calibration_n = int(candidate.get("calibration_rows") or 0)
    test_n = int(candidate.get("test_rows") or 0)
    frozen_total = train_n + calibration_n + test_n
    if len(rows) < frozen_total or min(train_n, calibration_n, test_n) <= 0:
        raise NCAAFPublicationBoundExperimentError("NCAAF_BOUND_PARTITION_IDENTITY_INVALID")

    probabilities, logits, design = _artifact_design(candidate, rows)
    outcomes = _outcomes(rows)
    train_end = train_n
    calibration_end = train_n + calibration_n
    test_end = frozen_total

    covariance, hessian_rank = _parameter_covariance(design[:train_end], probabilities[:train_end])
    calibration_brier, calibration_error_margin = _calibration_margin(
        probabilities[train_end:calibration_end], outcomes[train_end:calibration_end]
    )
    home_lower, home_upper, standard_errors = _event_bounds(
        logits=logits,
        design=design,
        covariance=covariance,
        calibration_margin=calibration_error_margin,
    )

    p_test = probabilities[calibration_end:test_end]
    y_test = outcomes[calibration_end:test_end]
    historical = _selected_metrics(
        p_test,
        y_test,
        home_lower[calibration_end:test_end],
        home_upper[calibration_end:test_end],
    )
    point_replay = _point_replay(candidate, p_test, y_test)
    historical_checks = dict(historical.get("review_checks") or {})
    historical_checks["point_probability_replay_matches_candidate"] = bool(point_replay["passed"])
    historical_replay_pass = all(historical_checks.values())

    forward_rows = rows[test_end:]
    forward_n = len(forward_rows)
    if forward_n:
        forward = _selected_metrics(
            probabilities[test_end:],
            outcomes[test_end:],
            home_lower[test_end:],
            home_upper[test_end:],
        )
    else:
        forward = {"n": 0, "review_checks": {}, "review_pass": False}
    forward_sample_pass = forward_n >= MIN_FORWARD_N
    forward_reliability_pass = bool(forward.get("review_pass")) if forward_sample_pass else False

    frozen_test_cutoff = str(rows[test_end - 1].get("event_start_time") or "")
    forward_first_event = str(forward_rows[0].get("event_start_time") or "") if forward_rows else None
    genuinely_later = bool(forward_rows and forward_first_event > frozen_test_cutoff)
    forward_validation_pass = forward_sample_pass and forward_reliability_pass and genuinely_later

    if not historical_replay_pass:
        recommendation_state = "CHALLENGER_FAIL_REVIEW_REQUIRED"
    elif not forward_validation_pass:
        recommendation_state = "FORWARD_VALIDATION_REQUIRED"
    else:
        recommendation_state = "GOVERNED_REVIEW_REQUIRED"

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
        "split": {
            "train_n": train_n,
            "calibration_n": calibration_n,
            "historical_test_n": test_n,
            "new_forward_n": forward_n,
        },
        "bound_method": BOUND_METHOD,
        "one_sided_z": ONE_SIDED_Z,
        "calibration_brier": calibration_brier,
        "calibration_error_margin": calibration_error_margin,
        "parameter_uncertainty": {
            "method": "PENALIZED_LOGISTIC_HESSIAN_PSEUDOINVERSE",
            "regularization_c": REGULARIZATION_C,
            "hessian_rank": hessian_rank,
            "parameter_count_including_intercept": int(design.shape[1]),
            "historical_test_logit_se_min": float(np.min(standard_errors[calibration_end:test_end])),
            "historical_test_logit_se_median": float(np.median(standard_errors[calibration_end:test_end])),
            "historical_test_logit_se_max": float(np.max(standard_errors[calibration_end:test_end])),
        },
        "historical_test_replay": {
            **historical,
            "point_probability_replay": point_replay,
            "review_checks": historical_checks,
            "review_pass": historical_replay_pass,
            "prior_exposure": True,
            "promotion_evidence_allowed": False,
            "reason": "TEST_BLOCK_WAS_ALREADY_OBSERVED_BY_V2",
        },
        "forward_validation": {
            **forward,
            "minimum_required_n": MIN_FORWARD_N,
            "frozen_test_cutoff": frozen_test_cutoff,
            "first_forward_event_start": forward_first_event,
            "genuinely_later_than_frozen_test": genuinely_later,
            "sample_size_pass": forward_sample_pass,
            "reliability_pass": forward_reliability_pass,
            "forward_validation_pass": forward_validation_pass,
        },
        "final_refresh_contract": {
            "contract_version": "NCAAF_DYNAMIC_PRIOR_STABLE_STATUS_V1",
            "hold_only_untrained_material_families": [
                "quarterback_status",
                "offensive_line_health",
                "skill_position_health",
                "weather_materiality",
            ],
            "stable_or_unchanged_required_for_publication": True,
            "material_or_unresolved_change_terminal": "MODEL_QUALIFIED_HOLD",
            "numeric_adjustment_without_trained_artifact_allowed": False,
        },
        "review_policy": {
            "v2_gate_relaxation_allowed": False,
            "historical_test_retuning_allowed": False,
            "new_forward_cohort_required": True,
            "minimum_forward_n": MIN_FORWARD_N,
        },
        "recommendation_state": recommendation_state,
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
    "MIN_FORWARD_N",
    "ONE_SIDED_Z",
    "PROBABILITY_PUBLISHABLE",
    "RANK_ELIGIBLE",
    "run_ncaaf_publication_bound_challenger_v3",
]
