from __future__ import annotations

import v17.prop_production_registration as registration
from v17.prop_production_registration import (
    ReviewedCertificationRelease,
    audit_registration_for_artifact,
    validate_action_canary_receipt,
)
from v17.prop_route_lifecycle import PRODUCTION_REGISTERED


def _artifact(certification_id="PROP-CERT-WNBA-POINTS-FORWARD-V2"):
    return {
        "sport": "WNBA",
        "stat_type": "POINTS",
        "feature_schema_version": "PROP_FEATURES_V1",
        "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
        "model_artifact_version": "WNBA_PTS_FORWARD_V2",
        "artifact_checksum": "a" * 64,
        "calibrator_version": "WNBA_POINTS_PLATT_V2",
        "certification_id": certification_id,
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "promoted": True,
        "active": True,
        "candidate_research_active": False,
        "training_dataset_hash": "dataset-hash",
        "training_code_sha": "code-sha",
    }


def _release():
    return ReviewedCertificationRelease(
        sport="WNBA",
        stat_type="POINTS",
        feature_schema_version="PROP_FEATURES_V1",
        model_family="WNBA_PROP_POISSON_LOGGLM_V1",
        model_artifact_version="WNBA_PTS_FORWARD_V2",
        artifact_checksum="a" * 64,
        calibrator_version="WNBA_POINTS_PLATT_V2",
        calibration_evidence_hash="evidence-hash",
        certification_id="PROP-CERT-WNBA-POINTS-FORWARD-V2",
        certification_review_status="APPROVED",
        deterministic_replay_ready=True,
        source_provenance_ready=True,
    )


def _receipt(**overrides):
    values = {
        "action_operation_id": "scoreWowPickRequest",
        "prediction_id": "11111111-1111-1111-1111-111111111111",
        "sport": "WNBA",
        "stat_type": "POINTS",
        "feature_schema_version": "PROP_FEATURES_V1",
        "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
        "model_artifact_version": "WNBA_PTS_FORWARD_V2",
        "artifact_checksum": "a" * 64,
        "calibrator_version": "WNBA_POINTS_PLATT_V2",
        "certification_id": "PROP-CERT-WNBA-POINTS-FORWARD-V2",
        "calibration_evidence_hash": "evidence-hash",
        "raw_model_probability": 0.72,
        "calibrated_probability": 0.70,
        "calibrated_lower_bound": 0.62,
        "reconciliation_status": "PASS",
        "immutable_receipt_hash": "receipt-hash",
        "can_execute": False,
    }
    values.update(overrides)
    return values


def test_real_canary_validator_requires_exact_artifact_and_governed_package():
    valid, blockers = validate_action_canary_receipt(_receipt(), _artifact(), _release())
    assert valid is True
    assert blockers == ()

    invalid, blockers = validate_action_canary_receipt(
        _receipt(artifact_checksum="b" * 64, calibrated_lower_bound=0.80),
        _artifact(), _release(),
    )
    assert invalid is False
    assert "CANARY_EXACT_ARTIFACT_IDENTITY_MISMATCH" in blockers
    assert "CANARY_GOVERNED_PROBABILITY_PACKAGE_INVALID" in blockers


def test_complete_exact_release_runtime_and_real_canary_reaches_production_registered(monkeypatch):
    monkeypatch.setattr(registration, "runtime_registration_snapshot", lambda **_kwargs: {
        "model_adapter_registered": True,
        "calibrator_adapter_registered": True,
        "hydration_route_registered": True,
        "can_execute": False,
    })
    result = audit_registration_for_artifact(
        _artifact(),
        {"status": "CALIBRATION_CERTIFIED_PASS", "evidence_hash": "evidence-hash"},
        release=_release(),
        canary_receipts=[_receipt()],
    )
    assert result["status"] == PRODUCTION_REGISTERED
    assert result["production_numerical_authority"] is True
    assert result["action_canary_verified"] is True
    assert result["can_execute"] is False


def test_legacy_registry_certification_id_cannot_substitute_for_new_reviewed_release(monkeypatch):
    monkeypatch.setattr(registration, "runtime_registration_snapshot", lambda **_kwargs: {
        "model_adapter_registered": True,
        "calibrator_adapter_registered": True,
        "hydration_route_registered": True,
        "can_execute": False,
    })
    result = audit_registration_for_artifact(
        _artifact(certification_id="LEGACY-PRE-FORWARD-CERT"),
        {"status": "CALIBRATION_CERTIFIED_PASS", "evidence_hash": "evidence-hash"},
        release=_release(),
        canary_receipts=[_receipt()],
    )
    assert result["status"] != PRODUCTION_REGISTERED
    assert result["production_numerical_authority"] is False


def test_missing_action_canary_keeps_route_fail_closed(monkeypatch):
    monkeypatch.setattr(registration, "runtime_registration_snapshot", lambda **_kwargs: {
        "model_adapter_registered": True,
        "calibrator_adapter_registered": True,
        "hydration_route_registered": True,
        "can_execute": False,
    })
    result = audit_registration_for_artifact(
        _artifact(),
        {"status": "CALIBRATION_CERTIFIED_PASS", "evidence_hash": "evidence-hash"},
        release=_release(),
        canary_receipts=[],
    )
    assert result["status"] != PRODUCTION_REGISTERED
    assert "CANONICAL_ACTION_CANARY_REQUIRED" in result["blockers"]
    assert result["can_execute"] is False
