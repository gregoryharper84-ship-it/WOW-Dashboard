"""Wave 3 FB-NFL-ML: forward-grade readiness ledger + metric replay (§A).

Pure, read-only helpers. No database, no network, no promotion. The caller
supplies already-graded in-memory rows; this module recomputes the paired
point-in-time metrics (Brier, log loss, ECE, calibration bias, reliability
curve, lower-bound gap) and emits the daily readiness ledger and the
certification decision package.

Thresholds mirror ``v17.nfl_forward_shadow.calibration_health`` (Brier < 0.25,
log loss < log 2, ECE <= 0.12, |bias| <= 0.08, lower-bound gap >= -0.03,
minimum 100). Reaching 100 is necessary, not sufficient.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

CAN_EXECUTE = False

MIN_FORWARD_GRADED = 100
BRIER_MAX = 0.25
LOGLOSS_MAX = math.log(2)
ECE_MAX = 0.12
BIAS_MAX = 0.08
LOWER_BOUND_GAP_MIN = -0.03


@dataclass(frozen=True)
class GradedRow:
    forecast: float
    outcome: int  # 0 or 1 for the selected side
    lower_bound: float

    def __post_init__(self) -> None:
        if self.outcome not in (0, 1):
            raise ValueError("outcome must be 0 or 1")
        for name, value in (
            ("forecast", self.forecast),
            ("lower_bound", self.lower_bound),
        ):
            if not isinstance(value, (int, float)) or not 0.0 < float(value) < 1.0:
                raise ValueError(f"{name} must be strictly inside (0, 1)")


@dataclass(frozen=True)
class ReadinessLedger:
    valid_n: int
    missing_to_100: int
    decision: str
    blockers: tuple
    mean_forecast: float | None
    observed_rate: float | None
    brier: float | None
    log_loss: float | None
    ece: float | None
    calibration_bias: float | None
    mean_lower_bound: float | None
    lower_bound_gap: float | None
    can_execute: bool = False
    probability_publishable: bool = False


def _ece(forecasts: Sequence[float], outcomes: Sequence[int], bins: int = 10) -> float:
    n = len(forecasts)
    if n == 0:
        return 0.0
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [
            i
            for i, p in enumerate(forecasts)
            if (lo <= p < hi) or (b == bins - 1 and p == hi)
        ]
        if not idx:
            continue
        bin_forecast = sum(forecasts[i] for i in idx) / len(idx)
        bin_observed = sum(outcomes[i] for i in idx) / len(idx)
        total += (len(idx) / n) * abs(bin_forecast - bin_observed)
    return total


def compute_metrics(rows: Sequence[GradedRow]) -> dict:
    """Recompute paired point-in-time metrics for one cohort."""
    if not rows:
        raise ValueError("empty cohort")
    forecasts = [float(r.forecast) for r in rows]
    outcomes = [int(r.outcome) for r in rows]
    lowers = [float(r.lower_bound) for r in rows]
    n = len(rows)
    brier = sum((p - y) ** 2 for p, y in zip(forecasts, outcomes)) / n
    log_loss = sum(
        -(y * math.log(p) + (1 - y) * math.log(1 - p)) for p, y in zip(forecasts, outcomes)
    ) / n
    mean_forecast = sum(forecasts) / n
    observed_rate = sum(outcomes) / n
    bias = mean_forecast - observed_rate
    mean_lower = sum(lowers) / n
    gap = observed_rate - mean_lower
    return {
        "n": n,
        "mean_forecast": mean_forecast,
        "observed_rate": observed_rate,
        "brier": brier,
        "log_loss": log_loss,
        "ece": _ece(forecasts, outcomes),
        "calibration_bias": bias,
        "mean_lower_bound": mean_lower,
        "lower_bound_gap": gap,
    }


def build_readiness_ledger(rows: Sequence[GradedRow]) -> ReadinessLedger:
    """Emit the daily forward-grade readiness ledger. Fail closed below 100.

    Metrics are always recomputed and reported when rows exist, even below
    the 100-sample minimum, so reviewers can see the diagnostic trajectory.
    Below-minimum metrics are observation only: the decision stays
    ``DO_NOT_CERTIFY_YET`` and no metric gate is evaluated as passing.
    """
    valid_n = len(rows)
    missing = max(0, MIN_FORWARD_GRADED - valid_n)
    metrics = compute_metrics(rows) if valid_n else None

    def _field(name: str) -> float | None:
        return metrics[name] if metrics is not None else None

    if valid_n < MIN_FORWARD_GRADED:
        return ReadinessLedger(
            valid_n=valid_n,
            missing_to_100=missing,
            decision="DO_NOT_CERTIFY_YET",
            blockers=(f"FORWARD_GRADED_N_{valid_n}_LT_100",),
            mean_forecast=_field("mean_forecast"),
            observed_rate=_field("observed_rate"),
            brier=_field("brier"),
            log_loss=_field("log_loss"),
            ece=_field("ece"),
            calibration_bias=_field("calibration_bias"),
            mean_lower_bound=_field("mean_lower_bound"),
            lower_bound_gap=_field("lower_bound_gap"),
        )
    metrics = compute_metrics(rows)
    blockers: list[str] = []
    if not metrics["brier"] < BRIER_MAX:
        blockers.append("FORWARD_BRIER_NOT_BELOW_0_25")
    if not metrics["log_loss"] < LOGLOSS_MAX:
        blockers.append("FORWARD_LOG_LOSS_NOT_BELOW_LOG2")
    if not metrics["ece"] <= ECE_MAX:
        blockers.append("FORWARD_ECE_ABOVE_0_12")
    if not abs(metrics["calibration_bias"]) <= BIAS_MAX:
        blockers.append("FORWARD_CALIBRATION_BIAS_ABOVE_0_08")
    if not metrics["lower_bound_gap"] >= LOWER_BOUND_GAP_MIN:
        blockers.append("FORWARD_LOWER_BOUND_RELIABILITY_FAILED")
    decision = "FORWARD_EVIDENCE_PASS" if not blockers else "DO_NOT_CERTIFY_YET"
    return ReadinessLedger(
        valid_n=valid_n,
        missing_to_100=0,
        decision=decision,
        blockers=tuple(blockers),
        mean_forecast=metrics["mean_forecast"],
        observed_rate=metrics["observed_rate"],
        brier=metrics["brier"],
        log_loss=metrics["log_loss"],
        ece=metrics["ece"],
        calibration_bias=metrics["calibration_bias"],
        mean_lower_bound=metrics["mean_lower_bound"],
        lower_bound_gap=metrics["lower_bound_gap"],
    )


def certification_package(ledger: ReadinessLedger) -> Mapping[str, object]:
    """Wrap the ledger in the §A.5 decision package. Never self-certifies."""
    ready = ledger.decision == "FORWARD_EVIDENCE_PASS" and ledger.valid_n >= MIN_FORWARD_GRADED
    return {
        "certification_decision": (
            "CERTIFICATION_EVIDENCE_READY_FOR_INDEPENDENT_REVIEW"
            if ready
            else "REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED"
        ),
        "certified": False,
        "probability_publishable": False,
        "can_execute": False,
        "valid_n": ledger.valid_n,
        "missing_to_100": ledger.missing_to_100,
        "blockers": list(ledger.blockers),
    }


__all__ = [
    "BIAS_MAX",
    "BRIER_MAX",
    "CAN_EXECUTE",
    "ECE_MAX",
    "LOGLOSS_MAX",
    "LOWER_BOUND_GAP_MIN",
    "MIN_FORWARD_GRADED",
    "GradedRow",
    "ReadinessLedger",
    "build_readiness_ledger",
    "certification_package",
    "compute_metrics",
]
