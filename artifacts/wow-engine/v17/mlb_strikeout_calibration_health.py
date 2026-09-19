"""MLB pitcher-strikeouts calibration/drift health monitor (V17, Gap #9).

Read-only diagnostic over immutable pregame strikeout predictions and their
settled outcomes. Reports whether the already-shipped MLB_STRIKEOUT_EXPERT
lane is showing calibration or feature drift -- it never retrains, never
recalibrates, never mutates a stored probability, and never disables the
specialist. This module produces evidence for the existing postmortem
monitoring path; it is not itself a gate and holds no execution authority.

can_execute=false unconditionally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import log, sqrt
from statistics import fmean, pstdev
from typing import Any, Iterable, Mapping, Sequence

CAN_EXECUTE = False

PROP_KEY = ("MLB", "PITCHER_STRIKEOUTS")
CONTROLLING_SPECIALIST = "wow.mlb-pitcher-failure-path-expert"

# Canonical V17 failure taxonomy -- reused, never re-invented.
MODEL_INPUTS_INSUFFICIENT = "MODEL_INPUTS_INSUFFICIENT"
MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"

# Health states owned by this monitor (distinct from scoring failure codes).
HEALTH_HEALTHY = "HEALTHY"
HEALTH_WATCH = "WATCH"
HEALTH_INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"

MIN_SAMPLE_FOR_HEALTH = 20
RELIABILITY_BUCKETS = 5

# Watch thresholds. These flag degradation for human/postmortem review only;
# they never feed back into model_probability, calibrated_probability, or
# calibrated_lower_bound.
BRIER_WATCH_THRESHOLD = 0.26
RELIABILITY_ERROR_WATCH_THRESHOLD = 0.08
LOWER_BOUND_COVERAGE_WATCH_THRESHOLD = 0.90
FEATURE_DRIFT_Z_WATCH_THRESHOLD = 2.0

MORE_DIRECTIONS = {"MORE", "OVER", "YES_MORE"}
LESS_DIRECTIONS = {"LESS", "UNDER", "YES_LESS"}
WIN_RESULTS = {"WIN", "WON", "SETTLED_WIN"}
LOSS_RESULTS = {"LOSS", "LOST", "SETTLED_LOSS"}


@dataclass(frozen=True)
class SettledStrikeoutRecord:
    """One immutable pregame prediction joined to its settled outcome.

    All fields are read from already-persisted, already-immutable rows.
    This dataclass never writes back to prediction/outcome storage.
    """

    prediction_id: str
    direction: str
    model_probability: float
    calibrated_probability: float
    calibrated_lower_bound: float
    official_result: str
    feature_snapshot: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureDriftFinding:
    feature_name: str
    baseline_mean: float
    window_mean: float
    z_score: float
    watch: bool


@dataclass(frozen=True)
class StrikeoutCalibrationHealthResult:
    prop_key: tuple[str, str]
    controlling_specialist: str
    state: str
    sample_count: int
    brier_score: float | None
    log_loss: float | None
    observed_hit_rate: float | None
    predicted_hit_rate: float | None
    reliability_error: float | None
    lower_bound_coverage: float | None
    feature_drift: tuple[FeatureDriftFinding, ...]
    reason_codes: tuple[str, ...]
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        out = {
            "prop_key": list(self.prop_key),
            "controlling_specialist": self.controlling_specialist,
            "state": self.state,
            "sample_count": self.sample_count,
            "brier_score": self.brier_score,
            "log_loss": self.log_loss,
            "observed_hit_rate": self.observed_hit_rate,
            "predicted_hit_rate": self.predicted_hit_rate,
            "reliability_error": self.reliability_error,
            "lower_bound_coverage": self.lower_bound_coverage,
            "feature_drift": [
                {
                    "feature_name": f.feature_name,
                    "baseline_mean": f.baseline_mean,
                    "window_mean": f.window_mean,
                    "z_score": f.z_score,
                    "watch": f.watch,
                }
                for f in self.feature_drift
            ],
            "reason_codes": list(self.reason_codes),
            "can_execute": self.can_execute,
        }
        return out


def _outcome_indicator(record: SettledStrikeoutRecord) -> float | None:
    """1.0 if the selected direction cleared, 0.0 if it lost, None if push/void."""
    result = str(record.official_result or "").strip().upper()
    if result in WIN_RESULTS:
        return 1.0
    if result in LOSS_RESULTS:
        return 0.0
    return None


def _validated_probability(value: float) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    value = float(value)
    if not (0.0 < value < 1.0):
        return None
    return value


def _brier(pairs: Sequence[tuple[float, float]]) -> float:
    return fmean((p - y) ** 2 for p, y in pairs)


def _log_loss(pairs: Sequence[tuple[float, float]]) -> float:
    eps = 1e-9
    total = 0.0
    for p, y in pairs:
        p = min(max(p, eps), 1 - eps)
        total += -(y * log(p) + (1 - y) * log(1 - p))
    return total / len(pairs)


def _reliability_error(pairs: Sequence[tuple[float, float]], buckets: int) -> float:
    """Mean |predicted - observed| across equal-width probability buckets."""
    bucketed: dict[int, list[tuple[float, float]]] = {}
    for p, y in pairs:
        idx = min(int(p * buckets), buckets - 1)
        bucketed.setdefault(idx, []).append((p, y))
    errors = []
    weights = []
    for members in bucketed.values():
        mean_p = fmean(m[0] for m in members)
        mean_y = fmean(m[1] for m in members)
        errors.append(abs(mean_p - mean_y))
        weights.append(len(members))
    total_weight = sum(weights)
    return sum(e * w for e, w in zip(errors, weights)) / total_weight


def _feature_drift(
    records: Sequence[SettledStrikeoutRecord],
    baseline_feature_means: Mapping[str, float] | None,
    baseline_feature_stddevs: Mapping[str, float] | None,
) -> tuple[FeatureDriftFinding, ...]:
    if not baseline_feature_means:
        return ()
    findings: list[FeatureDriftFinding] = []
    for feature_name, baseline_mean in baseline_feature_means.items():
        values = [
            r.feature_snapshot[feature_name]
            for r in records
            if isinstance(r.feature_snapshot.get(feature_name), (int, float))
            and not isinstance(r.feature_snapshot.get(feature_name), bool)
        ]
        if len(values) < MIN_SAMPLE_FOR_HEALTH:
            continue
        window_mean = fmean(values)
        stddev = None
        if baseline_feature_stddevs:
            stddev = baseline_feature_stddevs.get(feature_name)
        if not stddev:
            stddev = pstdev(values) or None
        if not stddev:
            z_score = 0.0
        else:
            n = len(values)
            standard_error = stddev / sqrt(n)
            z_score = (window_mean - baseline_mean) / standard_error if standard_error else 0.0
        watch = abs(z_score) >= FEATURE_DRIFT_Z_WATCH_THRESHOLD
        findings.append(
            FeatureDriftFinding(
                feature_name=feature_name,
                baseline_mean=baseline_mean,
                window_mean=window_mean,
                z_score=z_score,
                watch=watch,
            )
        )
    return tuple(findings)


def evaluate_strikeout_calibration_health(
    records: Iterable[SettledStrikeoutRecord],
    *,
    min_sample: int = MIN_SAMPLE_FOR_HEALTH,
    baseline_feature_means: Mapping[str, float] | None = None,
    baseline_feature_stddevs: Mapping[str, float] | None = None,
) -> StrikeoutCalibrationHealthResult:
    """Evaluate rolling calibration/drift health for MLB pitcher strikeouts.

    Input records must already carry a settled ``official_result`` and the
    original immutable pregame ``model_probability`` / ``calibrated_probability``
    / ``calibrated_lower_bound``. This function performs no writes and returns
    no value capable of altering those stored fields.
    """
    records = list(records)

    usable: list[SettledStrikeoutRecord] = []
    reason_codes: set[str] = set()
    for record in records:
        outcome = _outcome_indicator(record)
        if outcome is None:
            continue
        cp = _validated_probability(record.calibrated_probability)
        lb = _validated_probability(record.calibrated_lower_bound)
        if cp is None or lb is None:
            reason_codes.add(MODEL_OUTPUT_INVALID)
            continue
        usable.append(record)

    if len(usable) < min_sample:
        reason_codes.add(MODEL_INPUTS_INSUFFICIENT)
        return StrikeoutCalibrationHealthResult(
            prop_key=PROP_KEY,
            controlling_specialist=CONTROLLING_SPECIALIST,
            state=HEALTH_INSUFFICIENT_SAMPLE,
            sample_count=len(usable),
            brier_score=None,
            log_loss=None,
            observed_hit_rate=None,
            predicted_hit_rate=None,
            reliability_error=None,
            lower_bound_coverage=None,
            feature_drift=(),
            reason_codes=tuple(sorted(reason_codes)),
        )

    pairs = [(_validated_probability(r.calibrated_probability), _outcome_indicator(r)) for r in usable]
    brier = _brier(pairs)
    logloss = _log_loss(pairs)
    reliability_error = _reliability_error(pairs, RELIABILITY_BUCKETS)
    observed_hit_rate = fmean(y for _, y in pairs)
    predicted_hit_rate = fmean(p for p, _ in pairs)

    # A calibrated lower bound should, in aggregate, understate the true
    # long-run hit rate. We check that property directly rather than per-row
    # (a per-row bound is not a per-row guarantee): coverage is how much of
    # the mean published lower bound the observed hit rate actually delivered.
    mean_lower_bound = fmean(_validated_probability(r.calibrated_lower_bound) for r in usable)
    lower_bound_coverage = (
        min(observed_hit_rate / mean_lower_bound, 1.0) if mean_lower_bound else 1.0
    )

    feature_drift = _feature_drift(usable, baseline_feature_means, baseline_feature_stddevs)

    watch_reasons: list[str] = []
    if brier > BRIER_WATCH_THRESHOLD:
        watch_reasons.append("BRIER_ABOVE_WATCH_THRESHOLD")
    if reliability_error > RELIABILITY_ERROR_WATCH_THRESHOLD:
        watch_reasons.append("RELIABILITY_ERROR_ABOVE_WATCH_THRESHOLD")
    if lower_bound_coverage < LOWER_BOUND_COVERAGE_WATCH_THRESHOLD:
        watch_reasons.append("LOWER_BOUND_COVERAGE_BELOW_WATCH_THRESHOLD")
    if any(f.watch for f in feature_drift):
        watch_reasons.append("FEATURE_DRIFT_DETECTED")

    state = HEALTH_WATCH if watch_reasons else HEALTH_HEALTHY
    reason_codes.update(watch_reasons)

    return StrikeoutCalibrationHealthResult(
        prop_key=PROP_KEY,
        controlling_specialist=CONTROLLING_SPECIALIST,
        state=state,
        sample_count=len(usable),
        brier_score=brier,
        log_loss=logloss,
        observed_hit_rate=observed_hit_rate,
        predicted_hit_rate=predicted_hit_rate,
        reliability_error=reliability_error,
        lower_bound_coverage=lower_bound_coverage,
        feature_drift=feature_drift,
        reason_codes=tuple(sorted(reason_codes)),
    )
