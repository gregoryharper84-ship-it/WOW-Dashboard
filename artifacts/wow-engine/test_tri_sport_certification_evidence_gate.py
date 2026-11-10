"""Adversarial proof contract: no source or market shortcut can mint a model."""
from __future__ import annotations

from copy import deepcopy
import pytest

from v17.tri_sport_certification_evidence_gate import (
    assess_tri_sport_evidence, TARGET_SPECIALISTS, SCHEMA,
)

HEX = "b" * 64

def package(sport="NFL", stat="PASSING_YARDS"):
    return {
        "sport": sport,
        "stat_type": stat,
        "historical_outcomes": {
            "source_rights_verified": True,
            "official_event_player_stat_reconciled": True,
            "dataset_sha256": HEX,
            "outcome_count": 250,
        },
        "event": {
            "official_event_id": "game123",
            "official_player_id": "player456",
            "event_start_utc": "2026-10-30T00:00:00Z",
        },
        "pregame_features": {
            "snapshot_sha256": HEX,
            "immutable_source_snapshot_id": "snapshot789",
            "source_first_observed_at": "2026-10-29T20:00:00Z",
            "feature_as_of": "2026-10-29T21:00:00Z",
            "player_level_verified": True,
            "uses_target_event_outcome": False,
        },
        "fitted_model": {
            "controlling_specialist": TARGET_SPECIALISTS[(sport, stat)],
            "artifact_checksum": HEX,
            "training_dataset_hash": HEX,
            "training_code_sha": HEX,
            "model_artifact_version": "synthetic-test-artifact",
            "independent_validation_passed": True,
            "untouched_holdout_rows": 350,
            "chronological_split_verified": True,
            "model_family_fitted": True,
        },
        "calibration": {
            "calibrated_probability": 0.60,
            "calibrated_lower_bound": 0.52,
            "calibrated_upper_bound": 0.69,
            "independently_calibrated_exact_line": True,
            "calibration_artifact_id": "test-calibration",
            "calibration_artifact_checksum": HEX,
        },
        "exact_line": 255.5,
        "direction": "MORE",
        "scorer_receipt": {
            "status": "SUCCEEDED",
            "prediction_id": "test-prediction",
            "specialist": TARGET_SPECIALISTS[(sport, stat)],
            "official_event_id": "game123",
            "official_player_id": "player456",
            "stat_type": stat,
            "direction": "MORE",
            "exact_line": 255.5,
            "model_probability": 0.63,
            "calibration_artifact_id": "test-calibration",
            "can_execute": False,
        },
        "terminal_receipt": {
            "publisher": "V17_TERMINAL_REDUCER",
            "prediction_id": "test-prediction",
            "decision": "FINAL_APPROVED",
            "receipt_id": "test-terminal",
            "can_execute": False,
        },
    }


@pytest.mark.parametrize("sport,stat", sorted(TARGET_SPECIALISTS))
def test_synthetic_complete_package_never_self_certifies_or_publishes(sport, stat):
    p = package(sport, stat)
    x = assess_tri_sport_evidence(p)
    assert x["schema"] == SCHEMA
    assert x["blockers"] == []
    assert x["evidence_check_count"] >= 17
    assert x["status"] == "EVIDENCE_COMPLETE_REQUIRES_INDEPENDENT_CERTIFICATION"
    assert x["ready_for_independent_certification"] is True
    assert x["model_certified_by_this_gate"] is False
    assert x["probability_publishable"] is False and x["can_execute"] is False
    assert "model_probability" not in x


@pytest.mark.parametrize("path,value,code", [
    (("historical_outcomes","source_rights_verified"), False, "TRI_SPORT_SOURCE_RIGHTS_UNVERIFIED"),
    (("historical_outcomes","official_event_player_stat_reconciled"), False, "TRI_SPORT_OUTCOME_IDENTITY_UNVERIFIED"),
    (("historical_outcomes","dataset_sha256"), "bad", "TRI_SPORT_DATASET_HASH_MISSING"),
    (("historical_outcomes","outcome_count"), 9, "TRI_SPORT_HISTORICAL_OUTCOME_COHORT_INSUFFICIENT"),
    (("event","official_player_id"), None, "TRI_SPORT_EVENT_IDENTITY_OR_START_MISSING"),
    (("pregame_features","snapshot_sha256"), None, "TRI_SPORT_PREGAME_SNAPSHOT_UNFROZEN"),
    (("pregame_features","source_first_observed_at"), "2026-11-01T00:00:00Z", "TRI_SPORT_PREGAME_POINT_IN_TIME_UNVERIFIED"),
    (("pregame_features","feature_as_of"), "2026-10-30T00:00:00Z", "TRI_SPORT_PREGAME_POINT_IN_TIME_UNVERIFIED"),
    (("pregame_features","player_level_verified"), False, "TRI_SPORT_PREGAME_PLAYER_FEATURES_UNVERIFIED"),
    (("pregame_features","uses_target_event_outcome"), True, "TRI_SPORT_TARGET_LEAKAGE_NOT_EXCLUDED"),
    (("fitted_model","controlling_specialist"), "wow.nba-player-prop-probability-expert", "TRI_SPORT_CONTROLLING_SPECIALIST_INCORRECT"),
    (("fitted_model","training_dataset_hash"), "", "TRI_SPORT_FITTED_ARTIFACT_PROVENANCE_MISSING"),
    (("fitted_model","independent_validation_passed"), False, "TRI_SPORT_UNTOUCHED_HOLDOUT_UNVERIFIED"),
    (("fitted_model","model_family_fitted"), False, "TRI_SPORT_FITTED_MODEL_NOT_VERIFIED"),
    (("calibration","calibrated_lower_bound"), 0.78, "TRI_SPORT_EXACT_LINE_CALIBRATION_OR_BOUNDS_INVALID"),
    (("calibration","independently_calibrated_exact_line"), False, "TRI_SPORT_EXACT_LINE_CALIBRATION_OR_BOUNDS_INVALID"),
    (("scorer_receipt","prediction_id"), None, "TRI_SPORT_GENUINE_SCORER_RECEIPT_MISSING"),
    (("scorer_receipt","status"), "MODEL_UNAVAILABLE", "TRI_SPORT_GENUINE_SCORER_RECEIPT_MISSING"),
    (("scorer_receipt","model_probability"), 1.2, "TRI_SPORT_GENUINE_SCORER_RECEIPT_MISSING"),
    (("terminal_receipt","publisher"), "OTHER_SPECIALIST", "TRI_SPORT_TERMINAL_RECEIPT_MISSING"),
    (("terminal_receipt","decision"), "HELD", "TRI_SPORT_TERMINAL_RECEIPT_MISSING"),
    (("terminal_receipt","can_execute"), True, "TRI_SPORT_TERMINAL_RECEIPT_MISSING"),
])
def test_each_proof_failure_holds_typed_without_reranking(path, value, code):
    x = package()
    x[path[0]][path[1]] = value
    result = assess_tri_sport_evidence(x)
    assert code in result["blockers"]
    assert result["status"] == "BLOCKED_WITH_EXACT_REASONS"
    assert not result["ready_for_independent_certification"]
    assert not result["probability_publishable"]
    assert result["can_execute"] is False


def test_unsupported_route_and_empty_payload_hold_without_guessing():
    result = assess_tri_sport_evidence(package("NFL", "PASSING_YARDS") | {"stat_type": "RECEPTIONS"})
    assert result["blockers"] == ["TRI_SPORT_EXACT_ROUTE_UNSUPPORTED"]
    assert result["can_execute"] is False
    assert assess_tri_sport_evidence(None)["status"] == "INVALID_REQUEST"


def test_empty_evidence_never_passes_and_does_not_infer_from_implied_market():
    x = {"sport": "NBA", "stat_type": "POINTS", "book_implied_probability": 0.99}
    result = assess_tri_sport_evidence(x)
    assert result["status"] == "BLOCKED_WITH_EXACT_REASONS"
    assert "TRI_SPORT_GENUINE_SCORER_RECEIPT_MISSING" in result["blockers"]
    assert "TRI_SPORT_EXACT_LINE_CALIBRATION_OR_BOUNDS_INVALID" in result["blockers"]
    assert result["model_certified_by_this_gate"] is False


def test_market_only_held_terminal_preserves_sporting_proof_not_card():
    p = package()
    p["terminal_receipt"].update({
        "decision": "HELD",
        "hold_domain": "MARKET",
        "sporting_probability_preserved": True,
    })
    x = assess_tri_sport_evidence(p)
    assert x["blockers"] == []
    assert x["status"] == "SPORTING_EVIDENCE_COMPLETE_MARKET_HELD"
    assert x["ready_for_independent_certification"] is True
    assert x["sporting_probability_preserved_on_market_hold"] is True
    assert x["full_terminal_approval"] is False
    assert x["probability_publishable"] is False and x["can_execute"] is False


@pytest.mark.parametrize("hold_domain,preserved", [
    ("MODEL", True), ("MARKET", False), ("SPORTING", True), (None, True),
])
def test_model_or_unsubstantiated_terminal_hold_never_counts_as_sporting_proof(
    hold_domain, preserved
):
    p = package()
    p["terminal_receipt"].update({
        "decision": "HELD",
        "hold_domain": hold_domain,
        "sporting_probability_preserved": preserved,
    })
    result = assess_tri_sport_evidence(p)
    assert result["status"] == "BLOCKED_WITH_EXACT_REASONS"
    assert "TRI_SPORT_TERMINAL_RECEIPT_MISSING" in result["blockers"]
    assert result["sporting_probability_preserved_on_market_hold"] is False
