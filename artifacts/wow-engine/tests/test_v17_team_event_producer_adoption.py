"""Prove producer diagnostics are observational, cross-sport and fail-closed."""
from __future__ import annotations

import pytest

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS
from v17.cross_sport_evidence_integrity import (
    CRITICAL_STATUS_CONTRACT,
    TEAM_ROSTER_SPORTS,
    audit_cross_sport_evidence_integrity,
)
from v17.team_event_producer_adoption import inspect_team_event_producer_adoption


def _inspect(sport, result=None, receipt=None):
    return inspect_team_event_producer_adoption(
        sport=sport,
        result=result or {},
        consumption_receipt=receipt or {},
    )


@pytest.mark.parametrize("sport", EXPECTED_TEAM_EVENT_SPORTS)
def test_every_declared_sport_reports_unearned_producer_proof(sport):
    got = _inspect(sport)
    assert got["sport"] == sport
    assert got["status"] == "PRODUCER_PROOF_INCOMPLETE"
    assert got["publication_authority"] is False
    assert got["can_execute"] is False
    assert got["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert "PRODUCER_ATTESTATION_NOT_ENROLLED_OR_NOT_PASS" in got["blockers"]
    assert got["components"]["sport_specific_critical_status"]
    assert len(got["diagnostic_receipt_id"]) == 64


def test_nfl_real_response_components_do_not_self_certify_producer():
    result = {
        "canonical_acquisition": {
            "status": "PASS",
            "source_snapshot_id": "nfl-canonical-snapshot-1",
        },
        "model_timestamp": "2026-10-26T15:00:00+00:00",
        "feature_row_hash": "a" * 64,
        "llp_governance": {
            "final_refresh": {
                "final_refresh_status": "PASS",
                "checked_at": "2026-10-26T15:05:00+00:00",
                "refresh_source_snapshot_id": "nfl-canonical-snapshot-1",
            },
        },
        "nfl_stage_receipt": {"status": "PASS"},
        "calibrated_probability": 0.72,
        "calibrated_lower_bound": 0.66,
    }
    receipt = {
        "receipt_complete": True,
        "numerical_consumption_proven": True,
        "feature_consumption_receipt_id": "numerical-1",
    }
    got = _inspect("NFL", result, receipt)
    assert got["components"]["nfl_schedule_final_refresh"] == "SCHEDULE_ONLY_PASS"
    assert got["components"]["nfl_durable_stage_receipt"] == "PASS"
    assert got["components"]["fitted_numerical_feature_consumption"] == "RECEIPT_COMPLETE"
    assert got["components"]["fitted_scorer_row_hash"] == "HASH_PRESENT_NOT_VECTOR_EQUIVALENCE"
    assert got["status"] == "PRODUCER_PROOF_INCOMPLETE"
    assert "EVENT_EFFECTIVE_PERSONNEL_STATUS_NOT_PROVEN" in got["blockers"]
    assert "SCORER_FROZEN_VECTOR_EQUIVALENCE_NOT_PROVEN" in got["blockers"]
    assert "AUTHENTICATED_SOURCE_AND_PARTICIPANT_SNAPSHOTS_NOT_PROVEN" in got["blockers"]
    assert result["calibrated_probability"] == 0.72


def test_mlb_existing_source_snapshot_reference_is_not_source_attestation():
    got = _inspect("MLB", {
        "source_snapshot_id": "frozen-mlb-1",
        "source_snapshot_timestamp": "2026-10-26T15:00:00+00:00",
        "model_timestamp": "2026-10-26T15:05:00+00:00",
        "model_version": "mlb-synthetic-v1",
    })
    assert got["components"]["backend_score_source_reference"] == "PRESENT_NOT_INDEPENDENTLY_AUTHENTICATED"
    assert got["status"] == "PRODUCER_PROOF_INCOMPLETE"
    assert "EVENT_EFFECTIVE_PERSONNEL_STATUS_NOT_PROVEN" in got["blockers"]
    assert got["can_execute"] is False


def test_caller_supplied_proof_cannot_count_as_backend_source():
    got = _inspect("NFL", {
        "sport_specific_evidence": {
            "source_snapshot_id": "caller-forged-source",
            "verification_status": "VERIFIED",
            "final_refresh_status": "PASS",
            "feature_row_hash": "b" * 64,
        },
    })
    assert got["source_snapshot_id"] is None
    assert got["components"]["nfl_schedule_final_refresh"] == "NOT_PROVEN"
    assert got["components"]["fitted_scorer_row_hash"] == "NOT_PROVEN"
    assert got["status"] == "PRODUCER_PROOF_INCOMPLETE"


def test_receipt_identity_is_deterministic_and_model_values_unmodified():
    result = {"source_snapshot_id": "mlb-frozen-1", "model_timestamp": "2026-10-26T15:00:00+00:00"}
    a = _inspect("MLB", result)
    b = _inspect("MLB", result)
    assert a == b
    assert result == {"source_snapshot_id": "mlb-frozen-1", "model_timestamp": "2026-10-26T15:00:00+00:00"}


def test_admitted_scorer_evidence_is_read_only_not_a_second_publisher():
    got = _inspect("NBA", {
        "cross_sport_evidence_required": True,
        "cross_sport_evidence_attested": True,
        "cross_sport_evidence_audit": {"evidence_status": "PASS"},
    })
    # Raw proof flags alone are not a source/feature package.
    assert got["status"] == "PRODUCER_PROOF_INCOMPLETE"
    assert got["publication_authority"] is False
    assert got["can_execute"] is False
    assert got["blockers"][:2] == [
        "BACKEND_SOURCE_REFERENCE_NOT_RETURNED",
        "FITTED_INFERENCE_TIME_NOT_RETURNED",
    ]


def test_unknown_sport_is_not_silently_supported():
    with pytest.raises(ValueError, match="SPORT_CONTRACT_UNRESOLVED"):
        _inspect("UNKNOWN")


def test_nfl_scorer_vector_equality_only_closes_vector_gap():
    result = {
        "canonical_acquisition": {
            "status": "PASS", "source_snapshot_id": "nfl-source-2",
        },
        "model_timestamp": "2026-10-26T15:00:00+00:00",
        "feature_row_hash": "a" * 64,
        "feature_snapshot_hash": "b" * 64,
        "model_consumed_feature_hash": "b" * 64,
        "feature_vector_hash_policy": "NFL_CANONICAL_NUMERIC_VECTOR_SCHEMA_ORDER_V1",
    }
    got = _inspect("NFL", result)
    assert got["components"]["scorer_numeric_vector_equivalence"] == (
        "SCORER_VECTOR_MATCH_NOT_SOURCE_AUTHENTICATION"
    )
    assert "SCORER_FROZEN_VECTOR_EQUIVALENCE_NOT_PROVEN" not in got["blockers"]
    assert "AUTHENTICATED_SOURCE_AND_PARTICIPANT_SNAPSHOTS_NOT_PROVEN" in got["blockers"]
    assert got["status"] == "PRODUCER_PROOF_INCOMPLETE"


def test_vector_digest_requires_matching_hash_and_fitted_policy():
    for consumed, policy in [
        ("c" * 64, "NFL_CANONICAL_NUMERIC_VECTOR_SCHEMA_ORDER_V1"),
        ("b" * 64, "UNTRUSTED_POLICY"),
    ]:
        got = _inspect("NFL", {
            "feature_snapshot_hash": "b" * 64,
            "model_consumed_feature_hash": consumed,
            "feature_vector_hash_policy": policy,
        })
        assert got["components"]["scorer_numeric_vector_equivalence"] == "NOT_PROVEN"
        assert "SCORER_FROZEN_VECTOR_EQUIVALENCE_NOT_PROVEN" in got["blockers"]


def _admitted_fixture(sport):
    """Synthetic trusted-bridge output, not a live certified specialist."""
    from datetime import datetime, timedelta, timezone

    tip = datetime(2026, 10, 26, 19, tzinfo=timezone.utc)

    def participant(n):
        item = {
            "participant_id": f"side-{n}",
            "snapshot_id": f"source-side-{n}",
            "source_id": f"synthetic-source-{n}",
            "source_timestamp": (tip-timedelta(hours=2)).isoformat(),
            "effective_at": (tip-timedelta(days=1)).isoformat(),
            "latest_material_update_at": (tip-timedelta(hours=2)).isoformat(),
            "verification_status": "VERIFIED",
            "critical_status_contract": CRITICAL_STATUS_CONTRACT[sport],
            "critical_status": "PASS",
            "critical_status_snapshot_id": f"critical-{n}",
        }
        if sport in TEAM_ROSTER_SPORTS:
            item.update(roster_reconciliation_status="PASS", personnel_snapshot_id=f"roster-{n}")
        return item

    envelope = {
        "sport": sport,
        "official_event_id": "event-100",
        "official_event_snapshot_id": "snapshot-event-100",
        "prediction_id": "prediction-100",
        "scorer_official_event_id": "event-100",
        "official_participant_ids": ["side-1", "side-2"],
        "participants": [participant(1), participant(2)],
        "event_start_utc": tip.isoformat(),
        "model_timestamp": (tip-timedelta(hours=1)).isoformat(),
        "official_event_updated_at": (tip-timedelta(hours=2)).isoformat(),
        "final_refresh_status": "PASS",
        "final_refresh_at": (tip-timedelta(minutes=15)).isoformat(),
        "current_event_status": "PREGAME",
        "current_material_update_at": (tip-timedelta(hours=2)).isoformat(),
        "feature_snapshot_hash": "a" * 64,
        "model_consumed_feature_hash": "a" * 64,
        "feature_consumption_verification_status": "VERIFIED",
        "critical_feature_consumption_complete": True,
        "feature_consumption_receipt_id": "receipt-100",
        "model_artifact_version": "synthetic-artifact-v1",
        "model_invoked": True,
        "required_modules": ["hydrate", "fitted_score", "calibrate"],
        "completed_modules": ["hydrate", "fitted_score", "calibrate"],
        "stage_errors": [],
    }
    audit = audit_cross_sport_evidence_integrity(envelope)
    assert audit["evidence_status"] == "PASS", audit
    result = {
        "sport": sport,
        "official_event_id": envelope["official_event_id"],
        "prediction_id": envelope["prediction_id"],
        "model_artifact_version": envelope["model_artifact_version"],
        "model_timestamp": envelope["model_timestamp"],
        "cross_sport_evidence": envelope,
        "cross_sport_evidence_required": True,
        "cross_sport_evidence_attested": True,
        "cross_sport_evidence_audit": audit,
        "can_execute": False,
    }
    if sport == "NFL":
        result["nfl_stage_receipt"] = {"status": "PASS"}
    receipt = {
        "receipt_complete": True,
        "numerical_consumption_proven": True,
        "critical_consumption_complete": True,
        "consumption_verification_status": "VERIFIED",
        "feature_consumption_receipt_id": "receipt-100",
    }
    return result, receipt


@pytest.mark.parametrize("sport", EXPECTED_TEAM_EVENT_SPORTS)
def test_each_sport_has_one_clean_attested_producer_diagnostic(sport):
    result, receipt = _admitted_fixture(sport)
    got = _inspect(sport, result, receipt)
    assert got["status"] == "PRODUCER_ADMISSION_PASS", (sport, got)
    assert got["blockers"] == []
    assert got["components"]["scorer_numeric_vector_equivalence"] == "ATTESTED_FROZEN_VECTOR_MATCH"
    assert got["components"]["backend_score_source_reference"] == "ATTESTED_SOURCE_SNAPSHOT_REFERENCE"
    assert got["source_snapshot_id"] == "snapshot-event-100"
    assert got["scorer_numeric_vector_hash"] == "a" * 64
    if sport != "NFL":
        assert got["components"]["fitted_scorer_row_hash"] == "NOT_APPLICABLE"
        assert got["components"]["nfl_schedule_final_refresh"] == "NOT_APPLICABLE"
    else:
        assert got["components"]["nfl_schedule_final_refresh"] == "ATTESTED_FINAL_REFRESH"
    assert got["publication_authority"] is False
    assert got["can_execute"] is False
    assert got["terminal_authority"] == "V17_TERMINAL_REDUCER"


@pytest.mark.parametrize("sport", EXPECTED_TEAM_EVENT_SPORTS)
def test_attestation_without_exact_consumption_receipt_is_held(sport):
    result, receipt = _admitted_fixture(sport)
    result["cross_sport_evidence"]["feature_consumption_receipt_id"] = "wrong-receipt"
    got = _inspect(sport, result, receipt)
    assert got["status"] == "PRODUCER_PROOF_INCOMPLETE"
    assert "PRODUCER_ATTESTATION_NOT_ENROLLED_OR_NOT_PASS" in got["blockers"]
    assert got["components"]["backend_score_source_reference"] == "MISSING"
    assert got["publication_authority"] is False


@pytest.mark.parametrize("sport", EXPECTED_TEAM_EVENT_SPORTS)
def test_attestation_does_not_override_missing_numerical_proof(sport):
    result, receipt = _admitted_fixture(sport)
    receipt["numerical_consumption_proven"] = False
    got = _inspect(sport, result, receipt)
    assert got["status"] == "PRODUCER_PROOF_INCOMPLETE"
    assert "NUMERICAL_CONSUMPTION_RECEIPT_NOT_COMPLETE" in got["blockers"]


@pytest.mark.parametrize("sport", EXPECTED_TEAM_EVENT_SPORTS)
def test_wrong_prediction_binding_held_across_sports(sport):
    result, receipt = _admitted_fixture(sport)
    result["prediction_id"] = "another-prediction"
    got = _inspect(sport, result, receipt)
    assert got["status"] == "PRODUCER_PROOF_INCOMPLETE"
    assert "PRODUCER_ATTESTATION_NOT_ENROLLED_OR_NOT_PASS" in got["blockers"]


def test_nfl_admitted_producer_still_requires_durable_stage_receipt():
    result, receipt = _admitted_fixture("NFL")
    del result["nfl_stage_receipt"]
    got = _inspect("NFL", result, receipt)
    assert got["status"] == "PRODUCER_PROOF_INCOMPLETE"
    assert "NFL_DURABLE_STAGE_RECEIPT_NOT_PROVEN" in got["blockers"]


@pytest.mark.parametrize("sport", ("NBA", "NHL", "SOCCER"))
def test_unauthenticated_source_envelope_never_creates_positive(sport):
    result, receipt = _admitted_fixture(sport)
    result["cross_sport_evidence_attested"] = False
    got = _inspect(sport, result, receipt)
    assert got["source_snapshot_id"] is None
    assert got["status"] == "PRODUCER_PROOF_INCOMPLETE"
    assert got["scorer_numeric_vector_hash"] is None
