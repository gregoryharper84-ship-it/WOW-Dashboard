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


def test_canary_store_failure_is_typed_not_missing_receipts():
    class FailingQuery:
        def select(self, *_): return self
        def eq(self, *_): return self
        def order(self, *_, **__): return self
        def limit(self, *_): return self
        def execute(self): raise ConnectionError("simulated receipt-store outage")

    class DB:
        def table(self, name):
            assert name == registration.CANARY_TABLE
            return FailingQuery()

    import pytest
    with pytest.raises(registration.CanaryReceiptStoreUnavailable) as err:
        registration._canary_receipts(DB(), _artifact(), _release())
    assert str(err.value) == "CANARY_RECEIPT_STORE_UNAVAILABLE"
    assert isinstance(err.value.__cause__, ConnectionError)
    assert registration._canary_receipts(DB(), _artifact(), None) == []


def test_receipt_store_failure_never_grants_production_authority(monkeypatch):
    class DB:
        pass

    monkeypatch.setattr(registration, "run_prop_certification_audit", lambda *_args, **_kwargs: {
        "artifact_rows": [{**_artifact(), "status": "CALIBRATION_CERTIFIED_PASS", "evidence_hash": "evidence-hash"}],
        "certified_pass_n": 1,
        "reviewed_policy_count": 0,
    })
    monkeypatch.setattr(registration, "_artifact_rows", lambda _db: [_artifact()])
    monkeypatch.setattr(registration, "REVIEWED_CERTIFICATION_RELEASES", {
        registration._release_key("WNBA", "POINTS", "WNBA_PTS_FORWARD_V2", "a" * 64): _release()
    })
    def fail_receipts(*_args):
        raise registration.CanaryReceiptStoreUnavailable("CANARY_RECEIPT_STORE_UNAVAILABLE")
    monkeypatch.setattr(registration, "_canary_receipts", fail_receipts)
    result = registration.run_prop_production_registration_audit(
        registration.PropProductionRegistrationAuditRequest(routes=["WNBA:POINTS"]), db=DB()
    )
    row = result["route_rows"][0]
    assert "CANARY_RECEIPT_STORE_UNAVAILABLE" in row["blockers"]
    assert row["action_canary_verified"] is False
    assert row["production_numerical_authority"] is False
    assert row["status"] != PRODUCTION_REGISTERED
    assert row["can_execute"] is False


def test_canary_store_failure_preserves_upstream_lifecycle_status(monkeypatch):
    monkeypatch.setattr(registration, "run_prop_certification_audit", lambda *_a, **_kw: {
        "artifact_rows": [], "certified_pass_n": 0, "reviewed_policy_count": 0,
    })
    monkeypatch.setattr(registration, "_artifact_rows", lambda _db: [_artifact()])
    monkeypatch.setattr(registration, "REVIEWED_CERTIFICATION_RELEASES", {
        registration._release_key("WNBA", "POINTS", "WNBA_PTS_FORWARD_V2", "a" * 64): _release()
    })

    def unavailable(*_args):
        raise registration.CanaryReceiptStoreUnavailable("CANARY_RECEIPT_STORE_UNAVAILABLE")

    monkeypatch.setattr(registration, "_canary_receipts", unavailable)
    for upstream in (
        "CALIBRATION_EVIDENCE_REQUIRED",
        "CERTIFICATION_REVIEW_REQUIRED",
        "GOVERNED_PROMOTION_REQUIRED",
        "HYDRATION_OR_RUNTIME_ADAPTER_REQUIRED",
        PRODUCTION_REGISTERED,
    ):
        monkeypatch.setattr(registration, "audit_registration_for_artifact", lambda *_a, **_kw: {
            "sport": "WNBA", "stat_type": "POINTS",
            "status": upstream,
            "blockers": [], "action_canary_blockers": [],
            "action_canary_verified": True,
            "production_numerical_authority": True,
            "can_execute": False,
        })
        result = registration.run_prop_production_registration_audit(
            registration.PropProductionRegistrationAuditRequest(routes=["WNBA:POINTS"]), db=object()
        )
        row = result["route_rows"][0]
        expected = ("HYDRATION_OR_RUNTIME_ADAPTER_REQUIRED"
                    if upstream == PRODUCTION_REGISTERED else upstream)
        assert row["status"] == expected
        assert row["status"] != PRODUCTION_REGISTERED
        assert row["production_numerical_authority"] is False
        assert row["action_canary_verified"] is False
        assert "CANARY_RECEIPT_STORE_UNAVAILABLE" in row["blockers"]
        assert "CANARY_RECEIPT_STORE_UNAVAILABLE" in row["action_canary_blockers"]
        assert result["production_registered_n"] == 0
        assert result["can_execute"] is False
