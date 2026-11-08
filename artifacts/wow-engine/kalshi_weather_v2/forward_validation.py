"""Forward-validation scorecard for Kalshi Weather V2 research candidates.

The gates are fixed constants copied from ``EMPIRICAL_ACCEPTANCE_THRESHOLDS.md``
and are deliberately **not** function parameters: a caller must not be able to
lower a threshold that was precommitted before any validation result existed.

This scorecard reports chronological holdout MAE, holdout bias and empirical
one-sigma coverage against those gates. Passing them makes a candidate
review-eligible for the separate probability-level acceptance review; it never
certifies a profile, publishes a probability, or authorizes execution.
"""
from __future__ import annotations

import math
from statistics import mean, stdev
from typing import Any, Iterable, Mapping

from .calibration_fit import ForecastResidual
from .calibration_registry import collect_unique_residual_samples


SCHEMA = "KALSHI_WEATHER_FORWARD_VALIDATION_SCORECARD_V1"
REPORT_SCHEMA = "KALSHI_WEATHER_FORWARD_VALIDATION_REPORT_V1"

MINIMUM_TOTAL_RESIDUALS = 50
MINIMUM_TRAIN_RESIDUALS = 30
MINIMUM_HOLDOUT_RESIDUALS = 20
MAXIMUM_HOLDOUT_ABSOLUTE_MEAN_ERROR_F = 0.75
MAXIMUM_HOLDOUT_MAE_F = 2.5
MINIMUM_ONE_SIGMA_COVERAGE = 0.50
MAXIMUM_ONE_SIGMA_COVERAGE = 0.85

THRESHOLDS: Mapping[str, Any] = {
    "minimum_total_residuals": MINIMUM_TOTAL_RESIDUALS,
    "minimum_train_residuals": MINIMUM_TRAIN_RESIDUALS,
    "minimum_holdout_residuals": MINIMUM_HOLDOUT_RESIDUALS,
    "maximum_holdout_absolute_mean_error_f": MAXIMUM_HOLDOUT_ABSOLUTE_MEAN_ERROR_F,
    "maximum_holdout_mae_f": MAXIMUM_HOLDOUT_MAE_F,
    "minimum_one_sigma_coverage": MINIMUM_ONE_SIGMA_COVERAGE,
    "maximum_one_sigma_coverage": MAXIMUM_ONE_SIGMA_COVERAGE,
    "chronological_split": True,
    "bias_correction": "TRAIN_BIAS_SUBTRACTED_FROM_HOLDOUT",
}

STATUS_BLOCKED = "FORWARD_VALIDATION_BLOCKED"
STATUS_INSUFFICIENT = "FORWARD_VALIDATION_INSUFFICIENT"
STATUS_FAILED = "FORWARD_VALIDATION_FAILED"
STATUS_PASSED = "FORWARD_VALIDATION_PASS_RESEARCH_ONLY"


def build_forward_validation_scorecard(
    residuals: Iterable[ForecastResidual],
    *,
    station_id: str,
    lane: str,
    lead_time_bucket: str,
) -> dict[str, Any]:
    """Score one station/lane/lead-time group against the precommitted gates.

    The split is chronological by settlement time and strictly separated: every
    training residual settles before the first holdout residual. Residuals that
    share a settlement timestamp with the boundary are kept in the holdout, so a
    tie can never leak a later sample into training.
    """
    rows = [row for row in residuals if isinstance(row, ForecastResidual)]

    blockers: list[str] = []
    usable: list[ForecastResidual] = []
    lookahead = 0
    unnormalized = 0
    non_finite = 0
    for row in rows:
        if not (row.forecast_as_of.endswith("Z") and row.settlement_time.endswith("Z")):
            unnormalized += 1
            continue
        if not (math.isfinite(row.forecast_value_f) and math.isfinite(row.settled_value_f)):
            non_finite += 1
            continue
        if row.forecast_as_of >= row.settlement_time:
            lookahead += 1
            continue
        usable.append(row)

    if lookahead:
        blockers.append(f"LOOKAHEAD_RESIDUALS_EXCLUDED:{lookahead}")
    if unnormalized:
        blockers.append(f"UNNORMALIZED_TIMESTAMPS_EXCLUDED:{unnormalized}")
    if non_finite:
        blockers.append(f"NON_FINITE_RESIDUALS_EXCLUDED:{non_finite}")

    ordered = sorted(usable, key=lambda row: (row.settlement_time, row.forecast_as_of))
    total_n = len(ordered)

    result: dict[str, Any] = {
        "schema": SCHEMA,
        "station_id": station_id,
        "lane": lane,
        "lead_time_bucket": lead_time_bucket,
        "thresholds": dict(THRESHOLDS),
        "counts": {
            "total": total_n,
            "train": 0,
            "holdout": 0,
            "excluded_lookahead": lookahead,
            "excluded_unnormalized": unnormalized,
            "excluded_non_finite": non_finite,
        },
        "train": {"bias_f": None, "sigma_f": None},
        "holdout": {
            "mean_error_f": None,
            "absolute_mean_error_f": None,
            "mae_f": None,
            "one_sigma_coverage": None,
        },
        "gates": [],
        "blockers": list(blockers),
        "residual_gates_passed": False,
        "certification_eligible": False,
        "certification_blockers": [
            "PROBABILITY_LEVEL_ACCEPTANCE_REQUIRED",
            "GOVERNED_PROMOTION_REQUIRED",
        ],
        "probability_publishable": False,
        "capability_promotion_allowed": False,
        "can_execute": False,
    }

    count_gates = [
        ("TOTAL_RESIDUALS", f">= {MINIMUM_TOTAL_RESIDUALS}", total_n, total_n >= MINIMUM_TOTAL_RESIDUALS),
    ]
    if total_n < MINIMUM_TOTAL_RESIDUALS:
        result["gates"] = [_gate(*args) for args in count_gates]
        result["status"] = STATUS_BLOCKED if blockers else STATUS_INSUFFICIENT
        if not blockers:
            result["blockers"].append("FORWARD_VALIDATION_SAMPLE_INSUFFICIENT")
        return result

    boundary_index = total_n - MINIMUM_HOLDOUT_RESIDUALS
    boundary_time = ordered[boundary_index].settlement_time
    train = [row for row in ordered if row.settlement_time < boundary_time]
    holdout = [row for row in ordered if row.settlement_time >= boundary_time]
    train_n, holdout_n = len(train), len(holdout)

    count_gates.extend(
        [
            ("TRAIN_RESIDUALS", f">= {MINIMUM_TRAIN_RESIDUALS}", train_n, train_n >= MINIMUM_TRAIN_RESIDUALS),
            ("HOLDOUT_RESIDUALS", f">= {MINIMUM_HOLDOUT_RESIDUALS}", holdout_n, holdout_n >= MINIMUM_HOLDOUT_RESIDUALS),
        ]
    )
    result["counts"]["train"] = train_n
    result["counts"]["holdout"] = holdout_n
    result["gates"] = [_gate(*args) for args in count_gates]

    if train_n < MINIMUM_TRAIN_RESIDUALS or holdout_n < MINIMUM_HOLDOUT_RESIDUALS:
        # Too many residuals share the boundary timestamp to split cleanly.
        result["status"] = STATUS_BLOCKED if blockers else STATUS_INSUFFICIENT
        result["blockers"].append("CHRONOLOGICAL_SPLIT_INSUFFICIENT")
        return result

    train_values = [row.residual_f for row in train]
    bias = mean(train_values)
    sigma = stdev(train_values)
    result["train"] = {"bias_f": bias, "sigma_f": sigma}

    if sigma <= 0:
        result["status"] = STATUS_BLOCKED
        result["blockers"].append("TRAIN_RESIDUAL_VARIANCE_ZERO")
        return result

    corrected = [row.residual_f - bias for row in holdout]
    absolute_mean_error = abs(mean(corrected))
    mae = mean(abs(value) for value in corrected)
    coverage = sum(1.0 for value in corrected if abs(value) <= sigma) / holdout_n

    result["holdout"] = {
        "mean_error_f": mean(corrected),
        "absolute_mean_error_f": absolute_mean_error,
        "mae_f": mae,
        "one_sigma_coverage": coverage,
    }

    metric_gates = [
        (
            "HOLDOUT_ABSOLUTE_MEAN_ERROR_F",
            f"<= {MAXIMUM_HOLDOUT_ABSOLUTE_MEAN_ERROR_F}",
            absolute_mean_error,
            absolute_mean_error <= MAXIMUM_HOLDOUT_ABSOLUTE_MEAN_ERROR_F,
        ),
        (
            "HOLDOUT_MAE_F",
            f"<= {MAXIMUM_HOLDOUT_MAE_F}",
            mae,
            mae <= MAXIMUM_HOLDOUT_MAE_F,
        ),
        (
            "ONE_SIGMA_COVERAGE",
            f"{MINIMUM_ONE_SIGMA_COVERAGE}..{MAXIMUM_ONE_SIGMA_COVERAGE}",
            coverage,
            MINIMUM_ONE_SIGMA_COVERAGE <= coverage <= MAXIMUM_ONE_SIGMA_COVERAGE,
        ),
    ]
    result["gates"].extend(_gate(*args) for args in metric_gates)

    failed = [gate["gate"] for gate in result["gates"] if not gate["passed"]]
    result["blockers"].extend(f"HOLDOUT_GATE_FAILED:{name}" for name in failed)

    if blockers:
        result["status"] = STATUS_BLOCKED
    elif failed:
        result["status"] = STATUS_FAILED
    else:
        result["status"] = STATUS_PASSED
        result["residual_gates_passed"] = True
    return result


def build_forward_validation_report(
    predictions: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one scorecard per station/lane/lead-time group from immutable rows."""
    groups = collect_unique_residual_samples(predictions, outcomes)
    scorecards = [
        build_forward_validation_scorecard(
            (sample.residual for sample in samples),
            station_id=station_id,
            lane=lane,
            lead_time_bucket=bucket,
        )
        for (station_id, lane, bucket), samples in sorted(groups.items())
    ]
    return {
        "schema": REPORT_SCHEMA,
        "status": "FORWARD_VALIDATION_REPORT",
        "thresholds": dict(THRESHOLDS),
        "groups": scorecards,
        "groups_passing_residual_gates": sum(
            1 for card in scorecards if card["residual_gates_passed"]
        ),
        "certification_eligible": False,
        "probability_publishable": False,
        "capability_promotion_allowed": False,
        "can_execute": False,
    }


def _gate(name: str, requirement: str, observed: Any, passed: bool) -> dict[str, Any]:
    return {
        "gate": name,
        "requirement": requirement,
        "observed": observed,
        "passed": bool(passed),
    }
