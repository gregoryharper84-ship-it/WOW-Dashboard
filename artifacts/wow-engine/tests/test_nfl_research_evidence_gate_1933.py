"""Test backlog #1933 source audit candidate vs protected production hold."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17.nfl_research_evidence_gate_1933 import (
    EXPECTED_PLAYER_SHA256, EXPECTED_SCHEDULE_SHA256, EXPECTED_PLAYER_URL,
    EXPECTED_SCHEDULE_URL, ROUTES, review_research_evidence,
)


def _receipt():
    return {
        "schema": "WOW_NFL_BACKLOG_CANARY_RECEIPT_V1",
        "season": 2025,
        "pilot_status": "RESEARCH_ONLY",
        "source_rights_state": "LICENSE_REVIEW_REQUIRED",
        "status": "SOURCE_VALIDATION_HOLD",
        "blockers": ["NFL_PILOT_IDENTITY_ROWS_QUARANTINED"],
        "can_execute": False,
        "probability_publishable": False,
        "training_materialized": False,
        "pinned_schedule_expected_sha256": EXPECTED_SCHEDULE_SHA256,
        "reference": {
            "matches_frozen_source": True,
            "expected_sha256": EXPECTED_PLAYER_SHA256,
        },
        "source_manifest": {
            "nflverse_stats_player": {
                "sha256": EXPECTED_PLAYER_SHA256, "url": EXPECTED_PLAYER_URL, "rows": 19422,
            },
            "nflverse_schedule": {
                "sha256": EXPECTED_SCHEDULE_SHA256, "url": EXPECTED_SCHEDULE_URL, "rows": 7548,
            },
        },
        "counts": {
            "quarantined_identity_rows": 18,
            "quarantine_missing_player_id": 18,
            "quarantine_missing_game_id": 0,
            "quarantined_zero_stat_rows": 18,
            "quarantined_nonzero_stat_rows": 0,
            "quarantined_invalid_or_missing_stat_rows": 0,
            "player_rows_in_season": 18540,
            "joined_player_rows": 18522,
            "held_unsettled_or_unjoined": 0,
            "finalized_games_in_scope": 272,
            "normalized_stat_observations": {key: 18522 for key in ROUTES},
            "quarantine_opportunity_audit": {
                "status": "ZERO_OPPORTUNITY_AUDITED",
                "missing_columns": [], "nonzero_rows": 0, "zero_rows": 18,
                "invalid_or_missing_rows": 0,
            },
        },
        "independent_espn_final_score_parity": {
            "status": "MATCHED", "date": "2025-09-07",
            "games_in_scope": 13, "matched_final_games": 13,
            "score_conflict_games": 0, "missing_or_unfinalized_games": 0,
            "full_season_parity_established": False,
            "source": "ESPN_NFL_SCOREBOARD_INDEPENDENT",
            "source_sha256": "a" * 64,
        },
    }


def test_matching_evidence_is_candidate_only_and_preserves_training_hold():
    gate = review_research_evidence(_receipt())
    assert gate["status"] == "RESEARCH_EVIDENCE_AUDIT_CANDIDATE"
    assert gate["audit_coherent"] is True and gate["issues"] == []
    for forbidden in ("storage_write_authorized", "training_ready", "model_certified",
                      "probability_publishable", "can_execute", "attestation_verified"):
        assert gate[forbidden] is False


def test_changed_hash_or_mutable_url_fails_audit():
    for path, value in (
        (("source_manifest", "nflverse_schedule", "sha256"), "b" * 64),
        (("source_manifest", "nflverse_schedule", "url"),
         "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"),
        (("source_manifest", "nflverse_stats_player", "sha256"), "c" * 64),
    ):
        obj = _receipt()
        obj[path[0]][path[1]][path[2]] = value
        gate = review_research_evidence(obj)
        assert gate["status"] == "RESEARCH_EVIDENCE_INCOMPLETE"
        assert gate["storage_write_authorized"] is False


def test_missing_identity_positive_opportunity_or_invalid_stat_never_accepted():
    obj = _receipt()
    obj["counts"]["quarantine_opportunity_audit"]["nonzero_rows"] = 1
    assert "RESEARCH_OPPORTUNITY_QUARANTINE_MATERIALITY" in review_research_evidence(obj)["issues"]
    obj = _receipt()
    obj["counts"]["quarantined_nonzero_stat_rows"] = 1
    assert "RESEARCH_OUTCOME_QUARANTINE_MATERIALITY" in review_research_evidence(obj)["issues"]
    obj = _receipt()
    obj["counts"]["joined_player_rows"] -= 1
    assert "RESEARCH_ROW_RECONCILIATION_CONFLICT" in review_research_evidence(obj)["issues"]


def test_one_day_parity_is_not_full_season_authority():
    obj = _receipt()
    obj["independent_espn_final_score_parity"]["full_season_parity_established"] = True
    result = review_research_evidence(obj)
    assert "RESEARCH_INDEPENDENT_ESPN_PARITY_MISSING" in result["issues"]
    assert result["model_certified"] is False


def test_cannot_silently_remove_fail_closed_hold_or_promote_rights():
    obj = _receipt()
    obj["status"] = "APPROVED_FOR_PRODUCTION"
    obj["blockers"] = []
    result = review_research_evidence(obj)
    assert "RESEARCH_TERMINAL_HOLD_NOT_PRESERVED" in result["issues"]
    assert not result["training_ready"]
    obj = _receipt()
    obj["source_rights_state"] = "V17_APPROVED"
    result = review_research_evidence(obj)
    assert "RESEARCH_RIGHTS_OR_STATUS_CONFLICT" in result["issues"]
    obj = _receipt()
    obj["can_execute"] = True
    result = review_research_evidence(obj)
    assert "RESEARCH_MODE_CONTRACT_CONFLICT" in result["issues"]


def test_not_an_authenticated_attestation_even_with_passing_payload():
    obj = _receipt()
    obj["attested"] = True
    result = review_research_evidence(obj)
    assert result["audit_coherent"] is True
    assert result["attestation_verified"] is False
    assert result["storage_write_authorized"] is False
