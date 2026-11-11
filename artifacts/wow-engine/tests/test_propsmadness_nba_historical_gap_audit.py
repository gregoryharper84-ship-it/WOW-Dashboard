"""Only synthetic comparisons; no provider feed or training privilege."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.propsmadness_nba_offline_adapter import (
    SCHEMA as PM_SCHEMA, PropsMadnessFixtureHold, normalize_synthetic_fixture,
)
from v17.propsmadness_nba_historical_gap_audit import (
    evaluate_synthetic_historical_gap,
)

COMMON = {
    "provider_player_id": "fake-player", "provider_event_id": "fake-game",
    "team_id": "CHI", "opponent_id": "MIL",
    "stat_type": "POINTS", "period": "FULL_GAME",
    "event_start_at": "2026-10-24T01:00:00Z",
}
CROSSWALK_PLAYER = {"fake-player": "synthetic-nba-player"}
CROSSWALK_EVENT = {"fake-game": "synthetic-nba-game"}


def market():
    return {
        **COMMON, "provider_record_id": "market-1",
        "record_type": "MARKET_DISCOVERY", "source_field": "synthetic.market",
        "observed_at": "2026-10-23T22:00:00Z",
        "retrieved_at": "2026-10-23T22:02:00Z",
        "line": 20.5, "direction": "MORE", "american_odds": -110,
        "sportsbook": "SYNTHETIC_BOOK",
    }


def context():
    return {
        **COMMON, "provider_record_id": "context-1",
        "record_type": "PREGAME_CONTEXT", "source_field": "synthetic.usage",
        "observed_at": "2026-10-23T21:00:00Z",
        "retrieved_at": "2026-10-23T21:01:00Z",
        "metric_name": "USG_PCT", "metric_value": 24.0,
    }


def outcome(actual=22):
    return {
        **COMMON, "provider_record_id": "outcome-1",
        "record_type": "FINAL_OUTCOME", "source_field": "synthetic.final_pts",
        "observed_at": "2026-10-24T04:02:00Z",
        "retrieved_at": "2026-10-24T04:03:00Z",
        "game_final_at": "2026-10-24T04:00:00Z", "actual_value": actual,
    }


def pm(*, actual=22):
    root = {
        "schema": PM_SCHEMA, "origin": "SYNTHETIC_TEST_DATA",
        "provider": "PROPSMADNESS", "sport": "NBA",
        "rows": [market(), context(), outcome(actual)],
    }
    return normalize_synthetic_fixture(
        json.dumps(root).encode(), player_crosswalk=CROSSWALK_PLAYER,
        event_crosswalk=CROSSWALK_EVENT,
    )


def baseline(*, actual=22):
    return [{
        "origin": "SYNTHETIC_TEST_DATA",
        "source_provider": "SPORTSDATAVERSE_ESPN",
        "sport": "NBA", "period": "FULL_GAME",
        "canonical_player_id": "synthetic-nba-player",
        "canonical_event_id": "synthetic-nba-game",
        "stat_type": "POINTS", "actual_value": actual,
        "game_final_at": "2026-10-24T04:00:00Z",
        "observed_at": "2026-10-24T04:05:00Z",
    }]


def test_equal_labels_and_separate_historical_context_and_market():
    result = evaluate_synthetic_historical_gap(pm(), baseline())
    assert result["status"] == "SYNTHETIC_COMPARISON_COMPLETE"
    assert result["shared_labels"] == 1
    assert result["agreed_labels"] == 1
    assert result["conflicted_labels"] == 0
    assert result["pm_only_labels"] == 0
    assert result["baseline_only_labels"] == 0
    assert result["context_field_counts"] == {"USG_PCT": 1}
    assert result["market_offers_seen"] == 1
    for forbidden in ("historical_line_archive_verified",
                      "historical_pregame_availability_verified",
                      "commercial_data_rights_verified",
                      "incremental_predictive_value_verified",
                      "independent_provider_parity_verified",
                      "training_ready", "staging_write_authorized",
                      "model_certified", "probability_publishable",
                      "can_execute"):
        assert result[forbidden] is False


def test_stat_conflict_is_reported_not_silently_resolved():
    result = evaluate_synthetic_historical_gap(pm(actual=23), baseline(actual=22))
    assert result["shared_labels"] == 1
    assert result["agreed_labels"] == 0
    assert result["conflicted_labels"] == 1
    assert result["model_certified"] is False


def test_missing_baseline_or_provider_history_is_not_interpolated():
    only_other_player = baseline()
    only_other_player[0]["canonical_player_id"] = "synthetic-other-player"
    result = evaluate_synthetic_historical_gap(pm(), only_other_player)
    assert result["shared_labels"] == 0
    assert result["pm_only_labels"] == 1
    assert result["baseline_only_labels"] == 1
    assert result["agreed_labels"] == 0


def test_real_vendor_or_unreviewed_baseline_does_not_qualify():
    base = baseline()
    base[0]["origin"] = "VENDOR_REAL_DATA"
    with pytest.raises(PropsMadnessFixtureHold, match="NBA_GAP_UNAPPROVED_BASELINE_SOURCE"):
        evaluate_synthetic_historical_gap(pm(), base)
    base = baseline()
    base[0]["source_provider"] = "PROPSMADNESS"
    with pytest.raises(PropsMadnessFixtureHold, match="NBA_GAP_UNAPPROVED_BASELINE_SOURCE"):
        evaluate_synthetic_historical_gap(pm(), base)


def test_missing_finalization_and_duplicate_baseline_fail():
    rows = baseline()
    rows[0]["observed_at"] = "2026-10-24T01:30:00Z"
    with pytest.raises(PropsMadnessFixtureHold, match="NBA_GAP_LABEL_NOT_FINAL"):
        evaluate_synthetic_historical_gap(pm(), rows)
    with pytest.raises(PropsMadnessFixtureHold, match="NBA_GAP_DUPLICATE_BASELINE_LABEL"):
        evaluate_synthetic_historical_gap(pm(), baseline() + baseline())


def test_synthetic_research_cannot_be_promoted_by_input_flags():
    normalized = pm()
    normalized["training_ready"] = True
    with pytest.raises(PropsMadnessFixtureHold, match="NBA_GAP_PM_REAL_SOURCE_NOT_APPROVED"):
        evaluate_synthetic_historical_gap(normalized, baseline())
    normalized = pm()
    normalized["rows"][1]["pregame_feature_eligible"] = True
    with pytest.raises(PropsMadnessFixtureHold, match="NBA_GAP_PM_CONTEXT_PROMOTED"):
        evaluate_synthetic_historical_gap(normalized, baseline())
    normalized = pm()
    normalized["rows"][0]["evidence_domain"] = "SPORTING_RESEARCH"
    with pytest.raises(PropsMadnessFixtureHold, match="NBA_GAP_PM_MARKET_PROMOTED"):
        evaluate_synthetic_historical_gap(normalized, baseline())


def test_baseline_boolean_or_fractional_outcome_and_unauthorized_row_fail():
    b = baseline()
    b[0]["actual_value"] = True
    with pytest.raises(PropsMadnessFixtureHold, match="NBA_BASELINE_NONINTEGER_LABEL"):
        evaluate_synthetic_historical_gap(pm(), b)
    b = baseline()
    b[0]["actual_value"] = 22.4
    with pytest.raises(PropsMadnessFixtureHold, match="NBA_BASELINE_NONINTEGER_LABEL"):
        evaluate_synthetic_historical_gap(pm(), b)
    x = pm()
    x["rows"][2]["training_label_authorized"] = True
    with pytest.raises(PropsMadnessFixtureHold, match="NBA_GAP_PM_LABEL_PROMOTED"):
        evaluate_synthetic_historical_gap(x, baseline())
