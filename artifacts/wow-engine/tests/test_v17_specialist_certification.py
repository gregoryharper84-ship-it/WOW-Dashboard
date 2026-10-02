from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v17.specialist_certification import (
    CAN_EXECUTE,
    GLOBAL_TERMINAL_REDUCER,
    QualificationStatus,
    QualificationVerificationRequest,
    SpecialistRuntimePayload,
    evaluate_row_publication_gate,
    verify_specialist_qualification,
)


def _request():
    return QualificationVerificationRequest(
        sport="NFL",
        market_type="SPREAD",
        target_specialist_id="nfl_spread_key_number_v2",
        requested_market="POINT_SPREAD",
        current_time=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )


def _payload(**overrides):
    payload = dict(
        identity={
            "specialist_id": "nfl_spread_key_number_v2",
            "sport": "NFL",
            "market_type": "SPREAD",
            "model_family": "KeyNumber_Discrete_Margin",
            "supported_markets": ["POINT_SPREAD"],
            "owner_lane": "LLP_TEAM_BETTING_ENGINE",
            "required_feature_inputs": ["team_rating", "injury_state"],
        },
        certification={
            "certification_id": "cert-1",
            "specialist_id": "nfl_spread_key_number_v2",
            "is_active": True,
            "certified_at": "2026-09-01T00:00:00Z",
            "expires_at": "2026-12-01T00:00:00Z",
            "model_artifact_sha": "a" * 40,
            "feature_contract_version": "nfl-spread-features-v3",
            "calibration_artifact_id": "cal-7",
            "qualification_policy": {
                "policy_id": "nfl-spread-policy-v2",
                "max_ece": 0.04,
                "max_brier": 0.24,
                "minimum_validation_rows": 1000,
            },
            "lower_bound_method": "BOOTSTRAP_PERCENTILE_5TH",
        },
        calibration_artifact={
            "artifact_id": "cal-7",
            "ece_score": 0.03,
            "brier_score": 0.22,
            "validation_rows": 2500,
        },
        model_artifact_sha="a" * 40,
        feature_payload={
            "version": "nfl-spread-features-v3",
            "features": {"team_rating": 1.0, "injury_state": 0.0},
            "is_stale": False,
        },
        lower_bound_artifact={"artifact_id": "lb-1"},
        matching_owner_ids=("nfl_spread_key_number_v2",),
    )
    payload.update(overrides)
    return SpecialistRuntimePayload(**payload)


def test_qualified_specialist_reaches_reducer_but_never_execution():
    status, _ = verify_specialist_qualification(_request(), _payload())
    assert status == QualificationStatus.QUALIFIED
    receipt = evaluate_row_publication_gate(_payload(), status)
    assert receipt["is_publishable"] is True
    assert receipt["terminal_status"] == "QUALIFIED_FOR_REDUCER"
    assert receipt["verification_matrix"]["terminal_authority"] == GLOBAL_TERMINAL_REDUCER
    assert receipt["verification_matrix"]["can_execute"] is False
    assert CAN_EXECUTE is False


def test_missing_calibration_preserves_specific_failure_before_version_check():
    payload = _payload(calibration_artifact={})
    status, _ = verify_specialist_qualification(_request(), payload)
    assert status == QualificationStatus.CALIBRATION_ARTIFACT_MISSING


def test_multiple_matching_owners_fail_closed_without_fallback():
    payload = _payload(matching_owner_ids=("nfl_spread_key_number_v2", "generic_nfl_spread_v1"))
    status, _ = verify_specialist_qualification(_request(), payload)
    assert status == QualificationStatus.SPECIALIST_OWNERSHIP_CONFLICT
    assert evaluate_row_publication_gate(payload, status)["is_publishable"] is False


def test_required_feature_contract_is_enforced():
    feature_payload = {
        "version": "nfl-spread-features-v3",
        "features": {"team_rating": 1.0},
        "is_stale": False,
    }
    status, _ = verify_specialist_qualification(_request(), _payload(feature_payload=feature_payload))
    assert status == QualificationStatus.REQUIRED_FEATURE_MISSING


def test_malformed_expiry_is_typed_not_exception():
    cert = dict(_payload().certification)
    cert["expires_at"] = "not-a-date"
    status, _ = verify_specialist_qualification(_request(), _payload(certification=cert))
    assert status == QualificationStatus.CERTIFICATION_ARTIFACT_INVALID


def test_specialist_specific_calibration_policy_is_enforced():
    calibration = dict(_payload().calibration_artifact)
    calibration["ece_score"] = 0.041
    status, _ = verify_specialist_qualification(_request(), _payload(calibration_artifact=calibration))
    assert status == QualificationStatus.CALIBRATION_ARTIFACT_INVALID


def test_missing_lower_bound_fails_closed():
    status, _ = verify_specialist_qualification(_request(), _payload(lower_bound_artifact={}))
    assert status == QualificationStatus.LOWER_BOUND_ARTIFACT_MISSING
