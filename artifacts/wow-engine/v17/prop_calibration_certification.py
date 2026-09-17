"""Artifact-pinned forward calibration evidence and promotion validation for V17 props.

This module standardizes the evidence boundary between fitted candidate research
and governed promotion.  It deliberately does not define universal performance
thresholds: every route must provide an explicit, versioned certification
policy.  It also never mutates the production registry.  Promotion remains a
separate reviewed lifecycle action and ``can_execute`` is always false.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
from typing import Any, Iterable

from v17.prop_route_lifecycle import (
    CALIBRATION_CERTIFIED_PASS,
    CERTIFICATION_APPROVED,
    PHASE_A_CALIBRATION_STATES,
)

CAN_EXECUTE = False
CALIBRATION_EVIDENCE_REQUIRED = "CALIBRATION_EVIDENCE_REQUIRED"
CALIBRATION_CERTIFICATION_BLOCKED = "CALIBRATION_CERTIFICATION_BLOCKED"
PROMOTION_PACKAGE_READY = "PROMOTION_PACKAGE_READY"
PROMOTION_PACKAGE_BLOCKED = "PROMOTION_PACKAGE_BLOCKED"


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def _canonical_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PropCalibrationObservation:
    prediction_id: str
    sport: str
    stat_type: str
    feature_schema_version: str
    model_family: str
    model_artifact_version: str
    artifact_checksum: str
    calibrator_version: str
    calibration_status: str
    calibrated_probability: float
    calibrated_lower_bound: float
    outcome: int
    model_timestamp: str
    event_start_timestamp: str
    can_execute: bool = CAN_EXECUTE


@dataclass(frozen=True)
class PropCalibrationCertificationPolicy:
    """Versioned lane-specific acceptance policy supplied by governance.

    Values here are not defaults for V17.  A route's reviewed certification
    process must explicitly choose and version them.
    """

    policy_id: str
    min_settled_predictions: int
    max_brier_score: float
    max_log_loss: float
    max_ece: float
    max_abs_calibration_bias: float
    min_lower_bound_reliability_margin: float
    ece_bins: int = 10
    eligible_calibration_statuses: tuple[str, ...] = ("PLATT_TIME_SPLIT_V1", "ISOTONIC_V1")
    can_execute: bool = CAN_EXECUTE

    def validate(self) -> None:
        if self.can_execute:
            raise ValueError("certification policy cannot grant execution authority")
        if not self.policy_id.strip():
            raise ValueError("policy_id is required")
        if self.min_settled_predictions <= 0:
            raise ValueError("min_settled_predictions must be positive")
        if self.ece_bins <= 1:
            raise ValueError("ece_bins must be greater than one")
        bounded = (self.max_brier_score, self.max_ece, self.max_abs_calibration_bias)
        if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in bounded):
            raise ValueError("bounded certification thresholds must be finite within [0,1]")
        if not math.isfinite(self.max_log_loss) or self.max_log_loss < 0:
            raise ValueError("max_log_loss must be finite and non-negative")
        if not math.isfinite(self.min_lower_bound_reliability_margin) or not -1 <= self.min_lower_bound_reliability_margin <= 1:
            raise ValueError("min_lower_bound_reliability_margin must be within [-1,1]")
        eligible = tuple(_norm(value) for value in self.eligible_calibration_statuses if _norm(value))
        if not eligible:
            raise ValueError("at least one eligible forward calibration status is required")
        if any(value in PHASE_A_CALIBRATION_STATES for value in eligible):
            raise ValueError("Phase-A/precalibration status cannot be certification-eligible")


@dataclass(frozen=True)
class PropCalibrationCertificationPacket:
    sport: str
    stat_type: str
    feature_schema_version: str
    model_family: str
    model_artifact_version: str
    artifact_checksum: str
    calibrator_version: str
    policy_id: str
    settled_prediction_n: int
    mean_calibrated_probability: float | None
    mean_calibrated_lower_bound: float | None
    observed_hit_rate: float | None
    brier_score: float | None
    log_loss: float | None
    expected_calibration_error: float | None
    calibration_bias: float | None
    lower_bound_reliability_margin: float | None
    status: str
    blockers: tuple[str, ...]
    evidence_hash: str
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PropPromotionRequest:
    sport: str
    stat_type: str
    feature_schema_version: str
    model_family: str
    model_artifact_version: str
    artifact_checksum: str
    calibrator_version: str
    certification_id: str
    calibration_evidence_hash: str
    certification_review_status: str
    target_lifecycle_state: str = "PROSPECTIVE_CERTIFIED"
    can_execute: bool = CAN_EXECUTE


@dataclass(frozen=True)
class PropPromotionValidation:
    status: str
    promotion_ready: bool
    blockers: tuple[str, ...]
    certification_id: str
    model_artifact_version: str
    artifact_checksum: str
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_observation(row: PropCalibrationObservation) -> tuple[str, ...]:
    blockers: list[str] = []
    if row.can_execute:
        blockers.append("CAN_EXECUTE_MUST_BE_FALSE")
    required_text = (
        row.prediction_id,
        row.sport,
        row.stat_type,
        row.feature_schema_version,
        row.model_family,
        row.model_artifact_version,
        row.artifact_checksum,
        row.calibrator_version,
        row.calibration_status,
    )
    if not all(str(value or "").strip() for value in required_text):
        blockers.append("IMMUTABLE_OBSERVATION_IDENTITY_INCOMPLETE")
    p = float(row.calibrated_probability)
    lb = float(row.calibrated_lower_bound)
    if not (math.isfinite(p) and math.isfinite(lb) and 0.0 < lb <= p < 1.0):
        blockers.append("CALIBRATED_PACKAGE_INVALID")
    if row.outcome not in (0, 1):
        blockers.append("BINARY_SETTLED_OUTCOME_REQUIRED")
    try:
        model_ts = _aware(row.model_timestamp)
        start_ts = _aware(row.event_start_timestamp)
        if model_ts >= start_ts:
            blockers.append("PREGAME_TIMESTAMP_INVALID")
    except (TypeError, ValueError):
        blockers.append("TIMESTAMP_INVALID")
    return tuple(dict.fromkeys(blockers))


def _ece(probabilities: list[float], outcomes: list[int], bins: int) -> float:
    n = len(probabilities)
    total = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        members = [i for i, p in enumerate(probabilities) if low <= p < high or (index == bins - 1 and p == 1.0)]
        if not members:
            continue
        mean_p = sum(probabilities[i] for i in members) / len(members)
        mean_y = sum(outcomes[i] for i in members) / len(members)
        total += (len(members) / n) * abs(mean_y - mean_p)
    return total


def build_calibration_certification_packet(
    observations: Iterable[PropCalibrationObservation],
    *,
    policy: PropCalibrationCertificationPolicy,
) -> PropCalibrationCertificationPacket:
    """Build deterministic prospective calibration evidence for one artifact.

    Rows from different artifacts/routes/calibrators are rejected rather than
    pooled.  Phase-A observations may be retained in the historical ledger but
    cannot satisfy a policy that correctly excludes Phase A from certification.
    """
    policy.validate()
    rows = list(observations)
    if not rows:
        identity = {
            "sport": "",
            "stat_type": "",
            "feature_schema_version": "",
            "model_family": "",
            "model_artifact_version": "",
            "artifact_checksum": "",
            "calibrator_version": "",
        }
        payload = {**identity, "policy_id": policy.policy_id, "prediction_ids": [], "status": CALIBRATION_EVIDENCE_REQUIRED}
        return PropCalibrationCertificationPacket(
            **identity,
            policy_id=policy.policy_id,
            settled_prediction_n=0,
            mean_calibrated_probability=None,
            mean_calibrated_lower_bound=None,
            observed_hit_rate=None,
            brier_score=None,
            log_loss=None,
            expected_calibration_error=None,
            calibration_bias=None,
            lower_bound_reliability_margin=None,
            status=CALIBRATION_EVIDENCE_REQUIRED,
            blockers=("NO_SETTLED_IMMUTABLE_PREDICTIONS",),
            evidence_hash=_canonical_hash(payload),
        )

    first = rows[0]
    identity = {
        "sport": _norm(first.sport),
        "stat_type": _norm(first.stat_type),
        "feature_schema_version": str(first.feature_schema_version).strip(),
        "model_family": _norm(first.model_family),
        "model_artifact_version": str(first.model_artifact_version).strip(),
        "artifact_checksum": str(first.artifact_checksum).strip(),
        "calibrator_version": str(first.calibrator_version).strip(),
    }
    blockers: list[str] = []
    seen_prediction_ids: set[str] = set()
    eligible_statuses = {_norm(value) for value in policy.eligible_calibration_statuses}

    for row in rows:
        blockers.extend(_validate_observation(row))
        current_identity = {
            "sport": _norm(row.sport),
            "stat_type": _norm(row.stat_type),
            "feature_schema_version": str(row.feature_schema_version).strip(),
            "model_family": _norm(row.model_family),
            "model_artifact_version": str(row.model_artifact_version).strip(),
            "artifact_checksum": str(row.artifact_checksum).strip(),
            "calibrator_version": str(row.calibrator_version).strip(),
        }
        if current_identity != identity:
            blockers.append("MIXED_ROUTE_OR_ARTIFACT_COHORT")
        if row.prediction_id in seen_prediction_ids:
            blockers.append("DUPLICATE_PREDICTION_ID")
        seen_prediction_ids.add(row.prediction_id)
        if _norm(row.calibration_status) not in eligible_statuses:
            blockers.append("CALIBRATION_STATUS_NOT_CERTIFICATION_ELIGIBLE")

    probabilities = [float(row.calibrated_probability) for row in rows]
    lower_bounds = [float(row.calibrated_lower_bound) for row in rows]
    outcomes = [int(row.outcome) for row in rows]
    n = len(rows)
    mean_p = sum(probabilities) / n
    mean_lb = sum(lower_bounds) / n
    observed = sum(outcomes) / n
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / n
    eps = 1e-15
    log_loss = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for p, y in zip(probabilities, outcomes)) / n
    ece = _ece(probabilities, outcomes, policy.ece_bins)
    bias = observed - mean_p
    lb_margin = observed - mean_lb

    if n < policy.min_settled_predictions:
        blockers.append("MIN_SETTLED_CALIBRATION_COHORT_NOT_MET")
    if brier > policy.max_brier_score:
        blockers.append("BRIER_THRESHOLD_FAILED")
    if log_loss > policy.max_log_loss:
        blockers.append("LOG_LOSS_THRESHOLD_FAILED")
    if ece > policy.max_ece:
        blockers.append("ECE_THRESHOLD_FAILED")
    if abs(bias) > policy.max_abs_calibration_bias:
        blockers.append("CALIBRATION_BIAS_THRESHOLD_FAILED")
    if lb_margin < policy.min_lower_bound_reliability_margin:
        blockers.append("LOWER_BOUND_RELIABILITY_THRESHOLD_FAILED")

    blockers = list(dict.fromkeys(blockers))
    if "MIN_SETTLED_CALIBRATION_COHORT_NOT_MET" in blockers or "CALIBRATION_STATUS_NOT_CERTIFICATION_ELIGIBLE" in blockers:
        status = CALIBRATION_EVIDENCE_REQUIRED
    elif blockers:
        status = CALIBRATION_CERTIFICATION_BLOCKED
    else:
        status = CALIBRATION_CERTIFIED_PASS

    hash_payload = {
        **identity,
        "policy": asdict(policy),
        "prediction_rows": [
            {
                "prediction_id": row.prediction_id,
                "calibration_status": _norm(row.calibration_status),
                "calibrated_probability": float(row.calibrated_probability),
                "calibrated_lower_bound": float(row.calibrated_lower_bound),
                "outcome": int(row.outcome),
                "model_timestamp": row.model_timestamp,
                "event_start_timestamp": row.event_start_timestamp,
            }
            for row in sorted(rows, key=lambda item: item.prediction_id)
        ],
        "metrics": {
            "n": n,
            "mean_p": mean_p,
            "mean_lb": mean_lb,
            "observed": observed,
            "brier": brier,
            "log_loss": log_loss,
            "ece": ece,
            "bias": bias,
            "lower_bound_reliability_margin": lb_margin,
        },
        "status": status,
        "blockers": blockers,
        "can_execute": False,
    }

    return PropCalibrationCertificationPacket(
        **identity,
        policy_id=policy.policy_id,
        settled_prediction_n=n,
        mean_calibrated_probability=mean_p,
        mean_calibrated_lower_bound=mean_lb,
        observed_hit_rate=observed,
        brier_score=brier,
        log_loss=log_loss,
        expected_calibration_error=ece,
        calibration_bias=bias,
        lower_bound_reliability_margin=lb_margin,
        status=status,
        blockers=tuple(blockers),
        evidence_hash=_canonical_hash(hash_payload),
    )


def validate_promotion_request(
    packet: PropCalibrationCertificationPacket,
    request: PropPromotionRequest,
) -> PropPromotionValidation:
    """Validate a reviewed exact-artifact promotion package without applying it."""
    blockers: list[str] = []
    if request.can_execute or packet.can_execute:
        blockers.append("CAN_EXECUTE_MUST_BE_FALSE")

    expected_identity = (
        packet.sport,
        packet.stat_type,
        packet.feature_schema_version,
        packet.model_family,
        packet.model_artifact_version,
        packet.artifact_checksum,
        packet.calibrator_version,
    )
    requested_identity = (
        _norm(request.sport),
        _norm(request.stat_type),
        str(request.feature_schema_version).strip(),
        _norm(request.model_family),
        str(request.model_artifact_version).strip(),
        str(request.artifact_checksum).strip(),
        str(request.calibrator_version).strip(),
    )
    if requested_identity != expected_identity:
        blockers.append("PROMOTION_ARTIFACT_IDENTITY_MISMATCH")
    if packet.status != CALIBRATION_CERTIFIED_PASS:
        blockers.append("CALIBRATION_CERTIFICATION_NOT_PASS")
    if str(request.calibration_evidence_hash).strip() != packet.evidence_hash:
        blockers.append("CALIBRATION_EVIDENCE_HASH_MISMATCH")
    if _norm(request.certification_review_status) != CERTIFICATION_APPROVED:
        blockers.append("INDEPENDENT_CERTIFICATION_REVIEW_NOT_APPROVED")
    if not str(request.certification_id or "").strip():
        blockers.append("CERTIFICATION_ID_REQUIRED")
    if _norm(request.target_lifecycle_state) not in {"PROSPECTIVE_CERTIFIED", "CHAMPION"}:
        blockers.append("PROMOTION_TARGET_LIFECYCLE_INVALID")

    blockers = list(dict.fromkeys(blockers))
    ready = not blockers
    return PropPromotionValidation(
        status=PROMOTION_PACKAGE_READY if ready else PROMOTION_PACKAGE_BLOCKED,
        promotion_ready=ready,
        blockers=tuple(blockers),
        certification_id=str(request.certification_id or "").strip(),
        model_artifact_version=str(request.model_artifact_version or "").strip(),
        artifact_checksum=str(request.artifact_checksum or "").strip(),
    )


__all__ = [
    "CALIBRATION_CERTIFICATION_BLOCKED",
    "CALIBRATION_EVIDENCE_REQUIRED",
    "CAN_EXECUTE",
    "PROMOTION_PACKAGE_BLOCKED",
    "PROMOTION_PACKAGE_READY",
    "PropCalibrationCertificationPacket",
    "PropCalibrationCertificationPolicy",
    "PropCalibrationObservation",
    "PropPromotionRequest",
    "PropPromotionValidation",
    "build_calibration_certification_packet",
    "validate_promotion_request",
]
