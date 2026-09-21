"""Shadow-only calibration monotonicity diagnostics for LLP V17.1.

This module exists because cohort/bin lower bounds can become non-monotone from
finite-sample noise: a higher fitted/calibrated probability bin can receive a
lower empirical confidence bound than a lower-probability bin. When the final
leaderboard sorts exclusively by that bound, sampling noise can reverse winner
ordering.

Nothing here mutates production probabilities, bounds, rank eligibility,
terminal authority, or execution posture. It produces challenger diagnostics
and monotone/shrunk calibration targets for historical/forward replay only.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Sequence

SERVING_MODE = "SHADOW_ONLY"
CAN_EXECUTE = False
AUTOMATIC_PROMOTION_ALLOWED = False
PRODUCTION_BOUND_MUTATION_ALLOWED = False
PRODUCTION_RANKING_MUTATION_ALLOWED = False


class CalibrationMonotonicityError(ValueError):
    pass


@dataclass(frozen=True)
class CalibrationBin:
    side: str
    bin_index: int
    n: int
    p_mean: float
    observed_rate: float
    lower_bound: float


@dataclass(frozen=True)
class BoundInversion:
    side: str
    prior_bin_index: int
    current_bin_index: int
    prior_p_mean: float
    current_p_mean: float
    prior_lower_bound: float
    current_lower_bound: float
    inversion_size: float


@dataclass(frozen=True)
class MonotoneCalibrationTarget:
    side: str
    bin_index: int
    n: int
    p_mean: float
    observed_rate: float
    original_lower_bound: float
    shrunk_observed_target: float
    monotone_observed_target: float
    local_weight: float


def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise CalibrationMonotonicityError(f"MODEL_INPUTS_INSUFFICIENT: {field}_boolean")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CalibrationMonotonicityError(
            f"MODEL_INPUTS_INSUFFICIENT: {field}_non_numeric"
        ) from exc
    if not isfinite(parsed):
        raise CalibrationMonotonicityError(f"MODEL_INPUTS_INSUFFICIENT: {field}_non_finite")
    return parsed


def _unit(value: Any, *, field: str) -> float:
    parsed = _finite(value, field=field)
    if not 0.0 <= parsed <= 1.0:
        raise CalibrationMonotonicityError(f"MODEL_INPUTS_INSUFFICIENT: {field}_out_of_range")
    return parsed


def _validate_bin(row: CalibrationBin) -> None:
    if not row.side:
        raise CalibrationMonotonicityError("MODEL_INPUTS_INSUFFICIENT: missing_side")
    if int(row.bin_index) < 1:
        raise CalibrationMonotonicityError("MODEL_INPUTS_INSUFFICIENT: invalid_bin_index")
    if int(row.n) < 1:
        raise CalibrationMonotonicityError("MODEL_INPUTS_INSUFFICIENT: invalid_bin_n")
    _unit(row.p_mean, field="p_mean")
    _unit(row.observed_rate, field="observed_rate")
    _unit(row.lower_bound, field="lower_bound")


def audit_lower_bound_monotonicity(
    bins: Sequence[CalibrationBin],
    *,
    tolerance: float = 1e-12,
) -> dict[str, Any]:
    """Find adjacent lower-bound inversions after sorting by p_mean per side."""
    tol = _finite(tolerance, field="tolerance")
    if tol < 0.0:
        raise CalibrationMonotonicityError("MODEL_INPUTS_INSUFFICIENT: negative_tolerance")
    for row in bins:
        _validate_bin(row)

    inversions: list[BoundInversion] = []
    sides = sorted({row.side for row in bins})
    side_summaries: dict[str, dict[str, Any]] = {}

    for side in sides:
        ordered = sorted(
            (row for row in bins if row.side == side),
            key=lambda row: (row.p_mean, row.bin_index),
        )
        previous: CalibrationBin | None = None
        side_inversions = 0
        total_inversion = 0.0
        for row in ordered:
            if previous is not None and row.p_mean < previous.p_mean - tol:
                raise CalibrationMonotonicityError(
                    f"MODEL_INPUTS_CONFLICT: non_monotone_probability_order={side}"
                )
            if previous is not None and row.lower_bound < previous.lower_bound - tol:
                size = previous.lower_bound - row.lower_bound
                inversions.append(
                    BoundInversion(
                        side=side,
                        prior_bin_index=previous.bin_index,
                        current_bin_index=row.bin_index,
                        prior_p_mean=previous.p_mean,
                        current_p_mean=row.p_mean,
                        prior_lower_bound=previous.lower_bound,
                        current_lower_bound=row.lower_bound,
                        inversion_size=size,
                    )
                )
                side_inversions += 1
                total_inversion += size
            previous = row
        side_summaries[side] = {
            "bins": len(ordered),
            "inversions": side_inversions,
            "total_inversion_size": total_inversion,
        }

    return {
        "monotone": not inversions,
        "inversion_count": len(inversions),
        "inversions": tuple(inversions),
        "side_summaries": side_summaries,
        "production_bound_mutated": False,
        "production_ranking_mutated": False,
        "can_execute": CAN_EXECUTE,
    }


def weighted_pava(values: Sequence[float], weights: Sequence[float]) -> tuple[float, ...]:
    """Weighted pool-adjacent-violators algorithm for a non-decreasing fit."""
    if len(values) != len(weights) or not values:
        raise CalibrationMonotonicityError(
            "MODEL_INPUTS_INSUFFICIENT: pava_length_mismatch_or_empty"
        )

    blocks: list[dict[str, Any]] = []
    for index, (raw_value, raw_weight) in enumerate(zip(values, weights)):
        value = _unit(raw_value, field=f"pava_value_{index}")
        weight = _finite(raw_weight, field=f"pava_weight_{index}")
        if weight <= 0.0:
            raise CalibrationMonotonicityError(
                f"MODEL_INPUTS_INSUFFICIENT: pava_weight_nonpositive={index}"
            )
        blocks.append(
            {
                "start": index,
                "end": index,
                "weight": weight,
                "weighted_sum": weight * value,
            }
        )

        while len(blocks) >= 2:
            left = blocks[-2]
            right = blocks[-1]
            left_mean = left["weighted_sum"] / left["weight"]
            right_mean = right["weighted_sum"] / right["weight"]
            if left_mean <= right_mean:
                break
            merged = {
                "start": left["start"],
                "end": right["end"],
                "weight": left["weight"] + right["weight"],
                "weighted_sum": left["weighted_sum"] + right["weighted_sum"],
            }
            blocks[-2:] = [merged]

    fitted = [0.0] * len(values)
    for block in blocks:
        mean = block["weighted_sum"] / block["weight"]
        for index in range(block["start"], block["end"] + 1):
            fitted[index] = mean
    return tuple(fitted)


def monotone_shrunk_calibration_targets(
    bins: Sequence[CalibrationBin],
    *,
    global_observed_rate: float,
    prior_strength: float = 30.0,
) -> tuple[MonotoneCalibrationTarget, ...]:
    """Create shrinkage + monotonic challenger targets for replay only.

    Step 1: shrink each noisy cohort observed rate toward a global rate.
    Step 2: apply weighted isotonic regression in p_mean order within each side.

    These outputs are calibration targets, not production lower bounds. A future
    bound challenger must separately derive/validate interval coverage.
    """
    global_rate = _unit(global_observed_rate, field="global_observed_rate")
    strength = _finite(prior_strength, field="prior_strength")
    if strength < 0.0:
        raise CalibrationMonotonicityError("MODEL_INPUTS_INSUFFICIENT: prior_strength_negative")
    for row in bins:
        _validate_bin(row)

    output: list[MonotoneCalibrationTarget] = []
    for side in sorted({row.side for row in bins}):
        ordered = sorted(
            (row for row in bins if row.side == side),
            key=lambda row: (row.p_mean, row.bin_index),
        )
        shrunk: list[float] = []
        weights: list[float] = []
        local_weights: list[float] = []
        for row in ordered:
            local_weight = row.n / (row.n + strength) if (row.n + strength) > 0 else 0.0
            target = local_weight * row.observed_rate + (1.0 - local_weight) * global_rate
            shrunk.append(target)
            weights.append(float(row.n))
            local_weights.append(local_weight)

        monotone = weighted_pava(shrunk, weights)
        for row, target, monotone_target, local_weight in zip(
            ordered, shrunk, monotone, local_weights
        ):
            output.append(
                MonotoneCalibrationTarget(
                    side=side,
                    bin_index=row.bin_index,
                    n=row.n,
                    p_mean=row.p_mean,
                    observed_rate=row.observed_rate,
                    original_lower_bound=row.lower_bound,
                    shrunk_observed_target=target,
                    monotone_observed_target=monotone_target,
                    local_weight=local_weight,
                )
            )

    return tuple(output)


def challenger_manifest() -> dict[str, Any]:
    return {
        "serving_mode": SERVING_MODE,
        "automatic_promotion_allowed": AUTOMATIC_PROMOTION_ALLOWED,
        "production_bound_mutation_allowed": PRODUCTION_BOUND_MUTATION_ALLOWED,
        "production_ranking_mutation_allowed": PRODUCTION_RANKING_MUTATION_ALLOWED,
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "AUTOMATIC_PROMOTION_ALLOWED",
    "BoundInversion",
    "CAN_EXECUTE",
    "CalibrationBin",
    "CalibrationMonotonicityError",
    "MonotoneCalibrationTarget",
    "PRODUCTION_BOUND_MUTATION_ALLOWED",
    "PRODUCTION_RANKING_MUTATION_ALLOWED",
    "SERVING_MODE",
    "audit_lower_bound_monotonicity",
    "challenger_manifest",
    "monotone_shrunk_calibration_targets",
    "weighted_pava",
]
