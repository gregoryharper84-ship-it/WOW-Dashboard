"""Prove producer diagnostics are observational, cross-sport and fail-closed."""
from __future__ import annotations

import pytest

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS
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
    assert got["status"] == "PRODUCER_ADMISSION_PASS"
    assert got["publication_authority"] is False
    assert got["can_execute"] is False
    assert got["blockers"][:2] == [
        "BACKEND_SOURCE_REFERENCE_NOT_RETURNED",
        "FITTED_INFERENCE_TIME_NOT_RETURNED",
    ]


def test_unknown_sport_is_not_silently_supported():
    with pytest.raises(ValueError, match="SPORT_CONTRACT_UNRESOLVED"):
        _inspect("UNKNOWN")
