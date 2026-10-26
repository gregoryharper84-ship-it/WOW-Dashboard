"""Fixture-only checks: no test here touches Render, Supabase or the live Action."""
from __future__ import annotations

import pytest

from v17.all_sport_recovery_proof_matrix import (
    build_all_sport_recovery_proof_matrix as build,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS


def _team(sport="NFL", ready=True, **updates):
    result = {
        "sport": sport,
        "model_capability_ready": ready,
        "operational_readiness_status": "READY" if ready else "REGISTERED_NOT_READY",
        "readiness_blockers": [] if ready else ["MODEL_ARTIFACT_UNAVAILABLE"],
    }
    result.update(updates)
    return result


def _prop(sport="NBA", stat="REBOUNDS", ready=False, **updates):
    result = {
        "sport": sport,
        "stat_type": stat,
        "production_numerical_authority": ready,
        "status": "PRODUCTION_REGISTERED" if ready else "CANDIDATE_ONLY",
        "blockers": [] if ready else ["FORWARD_CALIBRATION_CERTIFICATION_REQUIRED"],
    }
    result.update(updates)
    return result


def _receipt(sport="NFL", lane="TEAM_EVENT_WINNER", stat_type=None, **updates):
    result = {
        "sport": sport, "lane": lane, "stat_type": stat_type,
        "official_event_id": "evt-123",
        "exact_selection": "HOME",
        "model_artifact_id": "fitted-champion-1",
        "model_timestamp": "2026-10-26T18:00:00Z",
        "calibrated_probability": 0.67,
        "calibrated_lower_bound": 0.59,
        "action_receipt_id": "action-123",
        "prediction_id": "pred-123",
        "source_sha": "a" * 40,
        "deployed_sha": "a" * 40,
        "source_snapshot_id": "snapshot-123",
        "terminal_disposition": "HELD",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }
    result.update(updates)
    return result


def _row(matrix, sport, lane="TEAM_EVENT_WINNER", stat=None):
    return next(x for x in matrix["rows"] if (
        x["sport"], x["lane"], x["stat_type"]
    ) == (sport, lane, stat))


def test_all_thirteen_sports_remain_visible_without_proof():
    result = build(team_rows=[], prop_rows=[])
    assert result["sports_reconciled"] == len(EXPECTED_TEAM_EVENT_SPORTS) == 13
    assert len(result["rows"]) == 26
    assert result["status_counts"] == {"INVENTORY_NOT_ESTABLISHED": 26}
    assert all(x["production_verified"] is False for x in result["rows"])
    assert result["can_execute"] is False


def test_registered_candidate_not_automatically_model_ready():
    result = build(team_rows=[{"sport": "NFL", "registered_capability": True,
                               "certification_status": "CERTIFIED"}], prop_rows=[])
    assert _row(result, "NFL")["readiness_status"] == "CAPABILITY_NOT_PROVEN"
    assert _row(result, "NFL")["first_unproven_stage"] == "FITTED_MODEL_OR_CERTIFICATION"


def test_ready_team_without_action_is_not_production_verified():
    result = build(team_rows=[_team()], prop_rows=[])
    row = _row(result, "NFL")
    assert row["readiness_status"] == "ACTION_NOT_TESTED"
    assert row["blocker"] == "AUTHENTICATED_ACTION_RECEIPT_MISSING"


def test_valid_looking_packet_is_only_ready_for_independent_review():
    result = build(team_rows=[_team()], prop_rows=[], receipt_rows=[_receipt()])
    row = _row(result, "NFL")
    assert row["readiness_status"] == "READY_FOR_INDEPENDENT_REVIEW"
    assert row["production_verified"] is False
    assert row["first_unproven_stage"] == "INDEPENDENT_PRODUCTION_VERIFICATION"


def test_sha_mismatch_and_invalid_bounds_are_explicit():
    for altered, reason in (
        ({"deployed_sha": "b" * 40}, "PRODUCTION_SHA_MISMATCH"),
        ({"calibrated_lower_bound": 0.9}, "INVALID_CALIBRATION_BOUNDS"),
        ({"calibrated_probability": float("nan")}, "INVALID_CALIBRATION_BOUNDS"),
        ({"action_receipt_id": None}, "MISSING_PROOF_FIELD:action_receipt_id"),
    ):
        result = build(team_rows=[_team()], prop_rows=[],
                       receipt_rows=[_receipt(**altered)])
        row = _row(result, "NFL")
        assert row["readiness_status"] == "PROOF_PACKET_INCOMPLETE"
        assert row["blocker"] == reason


def test_unpromoted_nba_prop_is_not_reclassified_by_fake_receipt():
    proof = _receipt(sport="NBA", lane="PLAYER_PROP", stat_type="REBOUNDS",
                     exact_line="5+", direction="MORE", period="FULL_GAME")
    result = build(team_rows=[], prop_rows=[_prop()], receipt_rows=[proof])
    row = _row(result, "NBA", "PLAYER_PROP", "REBOUNDS")
    assert row["readiness_status"] == "CAPABILITY_NOT_PROVEN"
    assert row["blocker"] == "FORWARD_CALIBRATION_CERTIFICATION_REQUIRED"
    assert row["production_verified"] is False


def test_prop_exact_offer_identity_and_action_proof_required():
    proof = _receipt(sport="NBA", lane="PLAYER_PROP", stat_type="REBOUNDS",
                     period="FULL_GAME", exact_line="5+", direction="MORE")
    result = build(team_rows=[], prop_rows=[_prop(ready=True)], receipt_rows=[proof])
    assert _row(result, "NBA", "PLAYER_PROP", "REBOUNDS")["readiness_status"] == (
        "READY_FOR_INDEPENDENT_REVIEW"
    )
    changed = dict(proof, direction=None)
    result = build(team_rows=[], prop_rows=[_prop(ready=True)], receipt_rows=[changed])
    assert _row(result, "NBA", "PLAYER_PROP", "REBOUNDS")["blocker"] == (
        "MISSING_PROOF_FIELD:direction"
    )


def test_duplicate_route_receipts_fail_closed_not_cherry_picked():
    result = build(team_rows=[_team()], prop_rows=[],
                   receipt_rows=[_receipt(), _receipt(prediction_id="pred-other")])
    assert _row(result, "NFL")["readiness_status"] == "PROOF_PACKET_AMBIGUOUS"


def test_duplicate_manifest_route_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        build(team_rows=[_team(), _team()], prop_rows=[])


def test_receipt_missing_from_manifest_reported_not_promoted():
    result = build(team_rows=[], prop_rows=[],
                   receipt_rows=[_receipt(sport="NFL")])
    assert result["receipt_routes_not_in_inventory"] == [
        {"sport": "NFL", "lane": "TEAM_EVENT_WINNER", "stat_type": None}
    ]
    assert _row(result, "NFL")["readiness_status"] == "INVENTORY_NOT_ESTABLISHED"
