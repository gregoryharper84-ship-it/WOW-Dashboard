from __future__ import annotations

import math

import pytest

from v17.prop_calibration_certification import (
    CALIBRATION_CERTIFICATION_BLOCKED,
    CALIBRATION_EVIDENCE_REQUIRED,
    PROMOTION_PACKAGE_BLOCKED,
    PROMOTION_PACKAGE_READY,
    PropCalibrationCertificationPolicy,
    PropCalibrationObservation,
    PropPromotionRequest,
    build_calibration_certification_packet,
    validate_promotion_request,
)
from v17.prop_route_lifecycle import CALIBRATION_CERTIFIED_PASS


def _policy(**overrides) -> PropCalibrationCertificationPolicy:
    values = {
        "policy_id": "TEST_WNBA_POINTS_CERT_POLICY_V1",
        "min_settled_predictions": 4,
        "max_brier_score": 0.30,
        "max_log_loss": 0.80,
        "max_ece": 0.30,
        "max_abs_calibration_bias": 0.30,
        "min_lower_bound_reliability_margin": -0.05,
        "ece_bins": 5,
        "eligible_calibration_statuses": ("PLATT_TIME_SPLIT_V1",),
    }
    values.update(overrides)
    return PropCalibrationCertificationPolicy(**values)


def _row(index: int, *, outcome: int = 1, p: float = 0.70, lb: float = 0.60, calibration_status: str = "PLATT_TIME_SPLIT_V1", artifact_checksum: str = "a" * 64) -> PropCalibrationObservation:
    return PropCalibrationObservation(
        prediction_id=f"pred-{index}",
        sport="WNBA",
        stat_type="POINTS",
        feature_schema_version="PROP_FEATURES_V1",
        model_family="WNBA_PROP_POISSON_LOGGLM_V1",
        model_artifact_version="WNBA_PTS_V2_FORWARD_CALIBRATED",
        artifact_checksum=artifact_checksum,
        calibrator_version="WNBA_POINTS_PLATT_V2",
        calibration_status=calibration_status,
        calibrated_probability=p,
        calibrated_lower_bound=lb,
        outcome=outcome,
        model_timestamp=f"2026-09-{10 + index:02d}T17:00:00+00:00",
        event_start_timestamp=f"2026-09-{10 + index:02d}T20:00:00+00:00",
    )


def _passing_packet():
    rows = [
        _row(1, outcome=1, p=0.70, lb=0.60),
        _row(2, outcome=1, p=0.68, lb=0.59),
        _row(3, outcome=0, p=0.55, lb=0.51),
        _row(4, outcome=1, p=0.72, lb=0.62),
    ]
    return build_calibration_certification_packet(rows, policy=_policy())


def test_forward_calibration_packet_is_artifact_pinned_and_deterministic() -> None:
    packet = _passing_packet()
    assert packet.status == CALIBRATION_CERTIFIED_PASS
    assert packet.settled_prediction_n == 4
    assert packet.artifact_checksum == "a" * 64
    assert packet.policy_id == "TEST_WNBA_POINTS_CERT_POLICY_V1"
    assert len(packet.evidence_hash) == 64
    assert packet.can_execute is False
    assert 0 <= packet.brier_score <= 1
    assert packet.log_loss is not None and packet.log_loss > 0
    assert packet.expected_calibration_error is not None

    again = _passing_packet()
    assert again.evidence_hash == packet.evidence_hash


def test_phase_a_rows_cannot_satisfy_forward_certification() -> None:
    policy = _policy(eligible_calibration_statuses=("PLATT_TIME_SPLIT_V1",))
    rows = [_row(i, calibration_status="PRECALIBRATION_SHRINKAGE") for i in range(1, 5)]
    packet = build_calibration_certification_packet(rows, policy=policy)
    assert packet.status == CALIBRATION_EVIDENCE_REQUIRED
    assert "CALIBRATION_STATUS_NOT_CERTIFICATION_ELIGIBLE" in packet.blockers


def test_policy_cannot_make_phase_a_certification_eligible() -> None:
    with pytest.raises(ValueError, match="Phase-A/precalibration"):
        _policy(eligible_calibration_statuses=("PRECALIBRATION_SHRINKAGE",)).validate()


def test_thin_forward_cohort_remains_evidence_required() -> None:
    packet = build_calibration_certification_packet([_row(1), _row(2)], policy=_policy())
    assert packet.status == CALIBRATION_EVIDENCE_REQUIRED
    assert "MIN_SETTLED_CALIBRATION_COHORT_NOT_MET" in packet.blockers


def test_metric_failure_blocks_certification_without_relabeling_probability() -> None:
    rows = [
        _row(1, outcome=0, p=0.90, lb=0.80),
        _row(2, outcome=0, p=0.88, lb=0.78),
        _row(3, outcome=0, p=0.86, lb=0.76),
        _row(4, outcome=0, p=0.84, lb=0.74),
    ]
    packet = build_calibration_certification_packet(rows, policy=_policy())
    assert packet.status == CALIBRATION_CERTIFICATION_BLOCKED
    assert any(blocker.endswith("THRESHOLD_FAILED") for blocker in packet.blockers)
    assert packet.can_execute is False


def test_mixed_artifact_cohort_is_never_certified() -> None:
    rows = [_row(1), _row(2), _row(3), _row(4, artifact_checksum="b" * 64)]
    packet = build_calibration_certification_packet(rows, policy=_policy())
    assert packet.status == CALIBRATION_CERTIFICATION_BLOCKED
    assert "MIXED_ROUTE_OR_ARTIFACT_COHORT" in packet.blockers


def test_duplicate_prediction_is_not_independent_certification_evidence() -> None:
    row = _row(1)
    packet = build_calibration_certification_packet([row, row, _row(2), _row(3)], policy=_policy())
    assert packet.status == CALIBRATION_CERTIFICATION_BLOCKED
    assert "DUPLICATE_PREDICTION_ID" in packet.blockers


def test_post_start_prediction_blocks_certification_packet() -> None:
    invalid = PropCalibrationObservation(
        **{
            **_row(1).__dict__,
            "model_timestamp": "2026-09-11T21:00:00+00:00",
            "event_start_timestamp": "2026-09-11T20:00:00+00:00",
        }
    )
    packet = build_calibration_certification_packet([invalid, _row(2), _row(3), _row(4)], policy=_policy())
    assert packet.status == CALIBRATION_CERTIFICATION_BLOCKED
    assert "PREGAME_TIMESTAMP_INVALID" in packet.blockers


def test_exact_reviewed_packet_can_become_promotion_ready_but_does_not_apply_promotion() -> None:
    packet = _passing_packet()
    request = PropPromotionRequest(
        sport=packet.sport,
        stat_type=packet.stat_type,
        feature_schema_version=packet.feature_schema_version,
        model_family=packet.model_family,
        model_artifact_version=packet.model_artifact_version,
        artifact_checksum=packet.artifact_checksum,
        calibrator_version=packet.calibrator_version,
        certification_id="PROP-CERT-WNBA-POINTS-FORWARD-V2",
        calibration_evidence_hash=packet.evidence_hash,
        certification_review_status="APPROVED",
        target_lifecycle_state="PROSPECTIVE_CERTIFIED",
    )
    result = validate_promotion_request(packet, request)
    assert result.status == PROMOTION_PACKAGE_READY
    assert result.promotion_ready is True
    assert result.blockers == ()
    assert result.can_execute is False


def test_promotion_fails_closed_on_artifact_or_evidence_hash_mismatch() -> None:
    packet = _passing_packet()
    request = PropPromotionRequest(
        sport=packet.sport,
        stat_type=packet.stat_type,
        feature_schema_version=packet.feature_schema_version,
        model_family=packet.model_family,
        model_artifact_version=packet.model_artifact_version,
        artifact_checksum="b" * 64,
        calibrator_version=packet.calibrator_version,
        certification_id="PROP-CERT-WNBA-POINTS-FORWARD-V2",
        calibration_evidence_hash="wrong",
        certification_review_status="APPROVED",
    )
    result = validate_promotion_request(packet, request)
    assert result.status == PROMOTION_PACKAGE_BLOCKED
    assert result.promotion_ready is False
    assert "PROMOTION_ARTIFACT_IDENTITY_MISMATCH" in result.blockers
    assert "CALIBRATION_EVIDENCE_HASH_MISMATCH" in result.blockers


def test_policy_thresholds_are_explicit_not_hidden_defaults() -> None:
    policy = _policy()
    policy.validate()
    assert math.isfinite(policy.max_brier_score)
    assert policy.policy_id.endswith("V1")
    assert policy.can_execute is False
