"""V17 development diagnostics: observe, audit, and review without suppression."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Iterable, Mapping


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"TIMEZONE_REQUIRED:{name}")
    return value


@dataclass(frozen=True)
class MarketComparison:
    event_id: str
    model_probability: float
    opener_probability: float
    decision_consensus_probability: float
    close_probability: float | None
    observed_at: datetime

    def __post_init__(self) -> None:
        _utc(self.observed_at, "observed_at")
        for name in ("model_probability", "opener_probability", "decision_consensus_probability"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"PROBABILITY_OUT_OF_RANGE:{name}")
        if self.close_probability is not None and not 0 <= self.close_probability <= 1:
            raise ValueError("PROBABILITY_OUT_OF_RANGE:close_probability")


def monitor_model_disagreement(
    row: MarketComparison,
    *,
    review_gap: float = 0.10,
    persistent_prior_gaps: Iterable[float] = (),
    persistence_count: int = 3,
) -> dict:
    """Return a review signal; this function can never suppress a prediction."""
    gaps = {
        "opener": row.model_probability - row.opener_probability,
        "decision_consensus": row.model_probability - row.decision_consensus_probability,
        "close": None if row.close_probability is None else row.model_probability - row.close_probability,
    }
    current_large = abs(gaps["decision_consensus"]) >= review_gap
    prior_large = sum(abs(float(gap)) >= review_gap for gap in persistent_prior_gaps)
    persistent = current_large and prior_large + 1 >= persistence_count
    return {
        **asdict(row),
        "gaps": gaps,
        "review_status": "REVIEW_REQUIRED" if persistent else "OBSERVE",
        "persistent_large_gap": persistent,
        "automatic_suppression": False,
        "probability_unchanged": True,
        "can_execute": False,
    }


@dataclass(frozen=True)
class TemporalFeatureProvenance:
    feature_name: str
    feature_value_hash: str
    source_snapshot_id: str
    source_published_at: datetime
    first_knowable_at: datetime
    captured_at: datetime
    used_at: datetime
    availability_basis: str

    def validate(self) -> None:
        for name in ("source_published_at", "first_knowable_at", "captured_at", "used_at"):
            _utc(getattr(self, name), name)
        if self.source_published_at > self.first_knowable_at:
            raise ValueError("SOURCE_PUBLISHED_AFTER_FIRST_KNOWABLE")
        if self.first_knowable_at > self.captured_at:
            raise ValueError("CAPTURE_PRECEDES_KNOWABILITY")
        if self.first_knowable_at > self.used_at or self.captured_at > self.used_at:
            raise ValueError("TEMPORAL_FEATURE_LEAKAGE")
        if len(self.feature_value_hash) != 64:
            raise ValueError("FEATURE_HASH_INVALID")


@dataclass(frozen=True)
class HypothesisChange:
    change_id: str
    sporting_rationale: str
    affected_feature: str
    expected_direction: str
    training_start: datetime
    training_end: datetime
    holdout_start: datetime
    holdout_end: datetime
    calibration_before: Mapping[str, float]
    calibration_after: Mapping[str, float]

    def validate(self) -> None:
        if self.expected_direction not in {"INCREASE", "DECREASE", "NON_MONOTONIC"}:
            raise ValueError("EXPECTED_DIRECTION_INVALID")
        for name in ("training_start", "training_end", "holdout_start", "holdout_end"):
            _utc(getattr(self, name), name)
        if not self.training_start < self.training_end < self.holdout_start < self.holdout_end:
            raise ValueError("TRAIN_HOLDOUT_OVERLAP_OR_ORDER_INVALID")
        if not self.sporting_rationale.strip() or not self.affected_feature.strip():
            raise ValueError("HYPOTHESIS_JUSTIFICATION_REQUIRED")
        if not self.calibration_before or not self.calibration_after:
            raise ValueError("BEFORE_AFTER_CALIBRATION_REQUIRED")

    def ledger_record(self) -> dict:
        self.validate()
        return {**asdict(self), "holdout_untouched": True, "automatic_promotion": False, "can_execute": False}
