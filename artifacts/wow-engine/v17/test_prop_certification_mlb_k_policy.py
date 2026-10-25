"""MLB PITCHER_STRIKEOUTS proposed forward-certification policy + no-skill floor.

Covers: the proposed registry entry, today's Phase-A cohort staying
evidence-required with its upstream blocker intact, no-skill cohorts being
typed-blocked, a skilled calibrated cohort passing the policy without gaining
any publication or release authority, and default-off backward compatibility.
"""
from __future__ import annotations

import math

import pytest

from calibration import PHASE_B_MIN_N
from v17.prop_calibration_certification import (
    CALIBRATION_CERTIFICATION_BLOCKED,
    PropCalibrationCertificationPolicy,
    PropCalibrationObservation,
    build_calibration_certification_packet,
)
from v17.prop_certification_runtime import (
    POLICY_REVIEW_REQUIRED,
    REVIEWED_ROUTE_POLICIES,
    ROUTE_POLICY_STATUS,
    audit_artifact_certification,
    validate_reviewed_route_policies,
)
from v17.prop_production_registration import REVIEWED_CERTIFICATION_RELEASES
from v17.prop_route_lifecycle import (
    CALIBRATION_CERTIFIED_PASS,
    CALIBRATION_EVIDENCE_REQUIRED,
    PHASE_A_CALIBRATION_STATES,
)

K = ("MLB", "PITCHER_STRIKEOUTS")
ARTIFACT = {
    "sport": "MLB",
    "stat_type": "PITCHER_STRIKEOUTS",
    "feature_schema_version": "PROP_FEATURES_V1",
    "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB",
    "model_artifact_version": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1_2026_08_29",
    "artifact_checksum": "b" * 64,
    "calibrator_version": "MLB_PITCHER_SO_CAL_V1",
}


def _obs(i: int, p: float, lb: float, hit: int, status: str = "PLATT_TIME_SPLIT_V1") -> PropCalibrationObservation:
    return PropCalibrationObservation(
        prediction_id=f"k-{i:05d}",
        sport=ARTIFACT["sport"],
        stat_type=ARTIFACT["stat_type"],
        feature_schema_version=ARTIFACT["feature_schema_version"],
        model_family=ARTIFACT["model_family"],
        model_artifact_version=ARTIFACT["model_artifact_version"],
        artifact_checksum=ARTIFACT["artifact_checksum"],
        calibrator_version=ARTIFACT["calibrator_version"],
        calibration_status=status,
        calibrated_probability=p,
        calibrated_lower_bound=lb,
        outcome=hit,
        model_timestamp="2027-04-10T16:00:00+00:00",
        event_start_timestamp="2027-04-10T23:05:00+00:00",
    )


def _skilled_cohort(n_each: int = 100) -> list[PropCalibrationObservation]:
    """Exactly calibrated two-level forecasts: 80% of p=0.8 rows hit, 30% of p=0.3 rows hit."""
    rows = []
    for i in range(n_each):
        rows.append(_obs(i, 0.80, 0.75, int(i < int(0.8 * n_each))))
    for i in range(n_each):
        rows.append(_obs(n_each + i, 0.30, 0.25, int(i < int(0.3 * n_each))))
    return rows


def _no_skill_cohort(n: int = 200) -> list[PropCalibrationObservation]:
    """Spread-out forecasts whose outcomes ignore them (50% hit in every bin)."""
    levels = (0.45, 0.55, 0.65, 0.75)
    return [_obs(i, levels[i % 4], levels[i % 4] - 0.05, (i // 4) % 2) for i in range(n)]


def test_proposed_policy_is_registered_strict_and_validated():
    policy = REVIEWED_ROUTE_POLICIES[K]
    policy.validate()
    validate_reviewed_route_policies(REVIEWED_ROUTE_POLICIES)
    assert policy.require_skill_over_base_rate is True
    assert policy.min_settled_predictions >= PHASE_B_MIN_N
    assert policy.max_brier_score <= 0.25
    assert policy.max_log_loss <= math.log(2) + 1e-4
    assert not set(policy.eligible_calibration_statuses) & set(PHASE_A_CALIBRATION_STATES)
    assert policy.can_execute is False
    assert ROUTE_POLICY_STATUS[K].review_status == "APPROVED"
    assert ROUTE_POLICY_STATUS[K].blocker is None


def test_only_strikeouts_gained_a_policy():
    assert set(REVIEWED_ROUTE_POLICIES) == {K}
    others = [s for key, s in ROUTE_POLICY_STATUS.items() if key != K]
    assert others and all(s.review_status == "REVIEW_REQUIRED" for s in others)
    assert all(s.blocker == POLICY_REVIEW_REQUIRED for s in others)


def test_registry_guard_rejects_lenient_or_undeclared_policies():
    lenient = PropCalibrationCertificationPolicy(
        policy_id="TOO_SMALL", min_settled_predictions=PHASE_B_MIN_N - 1, max_brier_score=0.25,
        max_log_loss=0.69, max_ece=0.1, max_abs_calibration_bias=0.07, min_lower_bound_reliability_margin=0.0,
    )
    with pytest.raises(ValueError, match="Phase-B"):
        validate_reviewed_route_policies({K: lenient})
    ok = REVIEWED_ROUTE_POLICIES[K]
    with pytest.raises(ValueError, match="undeclared"):
        validate_reviewed_route_policies({("MLB", "NOT_A_DECLARED_LANE"): ok})


def test_todays_phase_a_cohort_stays_evidence_required_with_upstream_blocker_intact():
    rows = [_obs(i, 0.62, 0.55, i % 2, status="PRECALIBRATION_SHRINKAGE") for i in range(666)]
    audit = audit_artifact_certification(ARTIFACT, rows)
    assert audit["status"] == CALIBRATION_EVIDENCE_REQUIRED
    assert "PHASE_A_PRECALIBRATION_NOT_FORWARD_CERTIFICATION" in audit["blockers"]
    assert "CALIBRATION_STATUS_NOT_CERTIFICATION_ELIGIBLE" in audit["blockers"]
    assert POLICY_REVIEW_REQUIRED not in audit["blockers"]
    assert audit["promotion_package_ready"] is False
    assert audit["can_execute"] is False


def test_no_skill_forward_cohort_is_typed_blocked():
    audit = audit_artifact_certification(ARTIFACT, _no_skill_cohort())
    assert audit["status"] == CALIBRATION_CERTIFICATION_BLOCKED
    assert "BRIER_NO_SKILL_VS_BASE_RATE" in audit["blockers"]
    assert "LOG_LOSS_NO_SKILL_VS_BASE_RATE" in audit["blockers"]
    assert audit["brier_score"] > audit["base_rate_brier_score"]
    assert audit["promotion_package_ready"] is False


def test_constant_base_rate_forecast_ties_reference_and_is_no_skill():
    # The shape that passes the pre-existing WNBA test policy: perfect
    # calibration, zero discrimination.  The floor treats a tie as no skill.
    rows = [_obs(i, 0.80, 0.70, int(i % 5 != 0)) for i in range(200)]
    audit = audit_artifact_certification(ARTIFACT, rows)
    assert audit["status"] == CALIBRATION_CERTIFICATION_BLOCKED
    assert audit["blockers"] == ["BRIER_NO_SKILL_VS_BASE_RATE", "LOG_LOSS_NO_SKILL_VS_BASE_RATE"]


def test_skilled_cohort_passes_policy_but_gains_no_publication_authority():
    audit = audit_artifact_certification(ARTIFACT, _skilled_cohort())
    assert audit["status"] == CALIBRATION_CERTIFIED_PASS, audit["blockers"]
    assert audit["blockers"] == []
    assert audit["brier_score"] < audit["base_rate_brier_score"]
    assert audit["log_loss"] < audit["base_rate_log_loss"]
    assert audit["promotion_package_ready"] is True
    # Certification evidence is not a release: publication stays owner-gated.
    assert REVIEWED_CERTIFICATION_RELEASES == {}
    assert audit["can_execute"] is False


def test_upstream_minimum_cannot_be_erased_by_a_lenient_explicit_policy():
    lenient = PropCalibrationCertificationPolicy(
        policy_id="LENIENT_TEST_ONLY", min_settled_predictions=50, max_brier_score=0.25,
        max_log_loss=0.6931, max_ece=0.10, max_abs_calibration_bias=0.07,
        min_lower_bound_reliability_margin=0.0, eligible_calibration_statuses=("PLATT_TIME_SPLIT_V1",),
        require_skill_over_base_rate=True,
    )
    rows = _skilled_cohort(n_each=30)
    packet = build_calibration_certification_packet(rows, policy=lenient)
    assert packet.status == CALIBRATION_CERTIFIED_PASS
    audit = audit_artifact_certification(ARTIFACT, rows, reviewed_policies={K: lenient})
    assert audit["status"] == CALIBRATION_EVIDENCE_REQUIRED
    assert "MIN_SETTLED_CALIBRATION_COHORT_NOT_MET" in audit["blockers"]
    assert audit["promotion_package_ready"] is False


def test_floor_is_off_by_default_so_existing_policies_are_unchanged():
    legacy = PropCalibrationCertificationPolicy(
        policy_id="LEGACY_SHAPE", min_settled_predictions=200, max_brier_score=0.25,
        max_log_loss=0.70, max_ece=0.05, max_abs_calibration_bias=0.05,
        min_lower_bound_reliability_margin=0.0, eligible_calibration_statuses=("PLATT_TIME_SPLIT_V1",),
    )
    rows = [_obs(i, 0.80, 0.70, int(i % 5 != 0)) for i in range(200)]
    packet = build_calibration_certification_packet(rows, policy=legacy)
    assert packet.status == CALIBRATION_CERTIFIED_PASS
    assert packet.base_rate_brier_score == pytest.approx(0.16)
    assert packet.base_rate_log_loss == pytest.approx(-(0.8 * math.log(0.8) + 0.2 * math.log(0.2)))


def test_degenerate_all_hit_cohort_is_no_skill():
    rows = [_obs(i, 0.90, 0.85, 1) for i in range(200)]
    packet = build_calibration_certification_packet(rows, policy=REVIEWED_ROUTE_POLICIES[K])
    assert packet.base_rate_brier_score == 0.0
    assert "BRIER_NO_SKILL_VS_BASE_RATE" in packet.blockers
    assert packet.status != CALIBRATION_CERTIFIED_PASS
