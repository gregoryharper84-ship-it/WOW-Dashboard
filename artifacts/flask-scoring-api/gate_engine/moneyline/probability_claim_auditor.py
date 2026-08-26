"""Fail-closed audit for published LLP moneyline probability claims."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

can_execute = False

@dataclass(frozen=True)
class ProbabilityClaimAudit:
    audit_result: str
    rank_eligible: bool
    confidence_ceiling: str | None = None
    blockers: tuple[str, ...] = field(default_factory=tuple)
    can_execute: bool = False
    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_result": self.audit_result,
            "rank_eligible": self.rank_eligible,
            "confidence_ceiling": self.confidence_ceiling,
            "blockers": list(self.blockers),
            "can_execute": False,
        }

def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None

def audit_probability_claim(
    *,
    raw_probability: Any,
    independent_probability: Any,
    market_prior_probability: Any,
    market_prior_weight: Any,
    calibrated_probability: Any,
    lower_bound: Any,
    upper_bound: Any,
    model_status: str,
    model_timestamp: Any = None,
    latest_material_update_timestamp: Any = None,
    source_snapshot_id: Any = None,
    source_coverage_status: str = "COMPLETE",
    outcome_probabilities: list[Any] | None = None,
    normalization_tolerance: float = 0.01,
) -> ProbabilityClaimAudit:
    blockers: list[str] = []
    raw = _as_float(raw_probability)
    independent = _as_float(independent_probability)
    market = _as_float(market_prior_probability)
    weight = _as_float(market_prior_weight)
    calibrated = _as_float(calibrated_probability)
    lower = _as_float(lower_bound)
    upper = _as_float(upper_bound)

    if model_status == "UNAVAILABLE":
        return ProbabilityClaimAudit("NOT_RANK_ELIGIBLE", False, blockers=("MODEL_UNAVAILABLE",))
    if raw is None or independent is None:
        return ProbabilityClaimAudit("UNCALIBRATED_PROSE_ONLY", False, blockers=("INDEPENDENT_PROBABILITY_MISSING",))
    if calibrated is None or lower is None or upper is None:
        return ProbabilityClaimAudit("NOT_RANK_ELIGIBLE", False, blockers=("CALIBRATED_BOUNDS_MISSING",))
    if not (0.0 < lower <= calibrated <= upper < 1.0):
        return ProbabilityClaimAudit("PROBABILITY_AUDIT_FAILURE", False, blockers=("PROBABILITY_ORDER_OR_DOMAIN_INVALID",))

    if outcome_probabilities is not None:
        vals = [_as_float(v) for v in outcome_probabilities]
        if any(v is None or v < 0.0 or v > 1.0 for v in vals):
            blockers.append("OUTCOME_SPACE_NOT_NORMALIZED")
        elif abs(sum(vals) - 1.0) > normalization_tolerance:
            blockers.append("OUTCOME_SPACE_NOT_NORMALIZED")

    model_ts = _parse_timestamp(model_timestamp)
    update_ts = _parse_timestamp(latest_material_update_timestamp)
    if model_ts and update_ts and model_ts < update_ts:
        blockers.append("STALE_MODEL_INVALIDATED")

    coverage = str(source_coverage_status or "").upper()
    if coverage in {"SOURCE_CONFLICT", "DATA_UNOBTAINABLE"}:
        blockers.append("SOURCE_SNAPSHOT_UNRESOLVED")
    if source_snapshot_id in (None, ""):
        blockers.append("SOURCE_SNAPSHOT_UNRESOLVED")

    if blockers:
        primary = blockers[0]
        result = primary if primary in {
            "STALE_MODEL_INVALIDATED", "OUTCOME_SPACE_NOT_NORMALIZED"
        } else "SOURCE_SNAPSHOT_UNRESOLVED"
        return ProbabilityClaimAudit(result, False, blockers=tuple(blockers))

    if weight is not None and weight > 0.50:
        return ProbabilityClaimAudit(
            "PASS_WITH_CONFIDENCE_CEILING", True,
            confidence_ceiling="MODEL_QUALIFIED_HOLD",
            blockers=("MARKET_DEPENDENT_MODEL",),
        )
    return ProbabilityClaimAudit("PASS_PROBABILITY_AUDIT", True)
