"""Contract regression across every cataloged V17 team/event sport."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest

from v17.cross_sport_evidence_integrity import (
    TEAM_ROSTER_SPORTS,
    audit_cross_sport_evidence_integrity,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS


def envelope(sport: str) -> dict:
    t = datetime(2026, 10, 26, 19, tzinfo=timezone.utc)

    def participant(i):
        return {
            "participant_id": f"p-{i}",
            "snapshot_id": f"snap-{i}",
            "verification_status": "VERIFIED",
            "source_id": f"authoritative-{i}",
            "source_timestamp": (t-timedelta(hours=2)).isoformat(),
            "effective_at": (t-timedelta(days=1)).isoformat(),
            "latest_material_update_at": (t-timedelta(hours=2)).isoformat(),
            **({"roster_reconciliation_status": "PASS", "personnel_snapshot_id": f"roster-{i}"}
               if sport in TEAM_ROSTER_SPORTS else {}),
        }
    return {
        "sport": sport,
        "official_event_id": "event-123",
        "official_event_snapshot_id": "official-schedule-123",
        "official_participant_ids": ["p-1", "p-2"],
        "official_event_updated_at": (t-timedelta(hours=2)).isoformat(),
        "prediction_id": "prediction-123",
        "scorer_official_event_id": "event-123",
        "event_start_utc": t.isoformat(),
        "model_timestamp": (t-timedelta(hours=1)).isoformat(),
        "participants": [participant(1), participant(2)],
        "feature_snapshot_hash": "a" * 64,
        "model_consumed_feature_hash": "a" * 64,
        "feature_consumption_verification_status": "VERIFIED",
        "critical_feature_consumption_complete": True,
        "model_artifact_version": "test-artifact",
        "model_invoked": True,
        "required_modules": ["hydrate", "fit", "calibrate"],
        "completed_modules": ["hydrate", "fit", "calibrate"],
        "stage_errors": [],
    }


@pytest.mark.parametrize("sport", EXPECTED_TEAM_EVENT_SPORTS)
def test_all_cataloged_sports_have_single_admission_contract(sport):
    decision = audit_cross_sport_evidence_integrity(envelope(sport))
    assert decision["evidence_status"] == "PASS", decision
    assert decision["can_execute"] is False


@pytest.mark.parametrize("sport", EXPECTED_TEAM_EVENT_SPORTS)
def test_missing_second_participant_blocks_every_sport(sport):
    item = envelope(sport)
    item["participants"].pop()
    assert "PARTICIPANT_SNAPSHOTS_INCOMPLETE" in audit_cross_sport_evidence_integrity(item)["blockers"]


@pytest.mark.parametrize("sport", sorted(TEAM_ROSTER_SPORTS))
def test_stale_roster_and_unreconciled_trades_hold_team_sports(sport):
    item = envelope(sport)
    item["participants"][1]["roster_reconciliation_status"] = "UNVERIFIED"
    item["participants"][1]["latest_material_update_at"] = "2026-10-26T18:30:00+00:00"
    blockers = audit_cross_sport_evidence_integrity(item)["blockers"]
    assert "PARTICIPANT_1_ROSTER_NOT_RECONCILED" in blockers
    assert "PARTICIPANT_1_LATEST_MATERIAL_UPDATE_AT_AFTER_MODEL" in blockers


def test_scorer_must_consume_exact_frozen_feature_vector():
    item = envelope("NBA")
    item["model_consumed_feature_hash"] = "b" * 64
    assert "MODEL_FEATURE_SNAPSHOT_MISMATCH" in audit_cross_sport_evidence_integrity(item)["blockers"]


def test_script_presence_is_not_script_execution():
    item = envelope("NFL")
    item["completed_modules"] = ["hydrate", "fit"]
    assert "REQUIRED_MODULE_EXECUTION_UNPROVEN" in audit_cross_sport_evidence_integrity(item)["blockers"]


def test_missing_proof_never_promotes_and_never_invents_model_unavailability():
    item = envelope("NHL")
    del item["model_consumed_feature_hash"]
    item["feature_consumption_verification_status"] = "UNVERIFIED"
    decision = audit_cross_sport_evidence_integrity(item)
    assert decision["evidence_status"] == "HELD"
    assert "NUMERICAL_FEATURE_CONSUMPTION_UNVERIFIED" in decision["blockers"]
    assert "MODEL_UNAVAILABLE" not in decision["blockers"]
    assert decision["can_execute"] is False


def test_timestamp_needs_explicit_timezone_and_must_be_pregame():
    item = envelope("TENNIS")
    item["model_timestamp"] = item["event_start_utc"]
    assert "NOT_A_PREGAME_MODEL_SNAPSHOT" in audit_cross_sport_evidence_integrity(item)["blockers"]
    item["model_timestamp"] = "2026-10-26T18:00:00"
    assert "PREGAME_TIMESTAMP_PROOF_MISSING" in audit_cross_sport_evidence_integrity(item)["blockers"]


def test_golf_field_may_have_more_than_two_participants():
    item = envelope("PGA")
    third = deepcopy(item["participants"][1])
    third.update(participant_id="p-3", snapshot_id="snap-3")
    item["participants"].append(third)
    assert audit_cross_sport_evidence_integrity(item)["evidence_status"] == "PASS"


def test_canonical_event_identity_must_match_both_sides():
    item = envelope("NBA")
    item["official_participant_ids"] = ["p-1", "other-team"]
    decision = audit_cross_sport_evidence_integrity(item)
    assert "OFFICIAL_PARTICIPANT_SET_MISMATCH" in decision["blockers"]


def test_mismatched_prediction_event_and_late_schedule_update_hold():
    item = envelope("NHL")
    item["scorer_official_event_id"] = "another-event"
    item["official_event_updated_at"] = "2026-10-26T18:30:00+00:00"
    blockers = audit_cross_sport_evidence_integrity(item)["blockers"]
    assert "SCORER_EVENT_BINDING_UNPROVEN" in blockers
    assert "OFFICIAL_EVENT_CHANGED_AFTER_MODEL" in blockers
