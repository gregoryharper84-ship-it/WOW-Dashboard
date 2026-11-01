"""Offline research-to-shadow adapter validation; no scorer, rights or provider IO."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.universal_sports_intelligence_research import (
    SPORT_DOMAINS, compile_intelligence_research_snapshot,
)
from v17.universal_intelligence_shadow_handoff import (
    ShadowFeatureHold, prepare_shadow_feature_handoff,
)


def observation(*, evidence_kind="SOURCE_OBSERVATION", value=3.5):
    return {
        "domain": "quality", "metric": "adjusted_rating",
        "entity_id": "team-123", "value": value,
        "evidence_kind": evidence_kind,
        "source_record_id": "official-row-123",
        "source_url": "https://research.example.org/records/123",
        "observed_at": "2026-10-24T12:00:00Z",
        "first_available_at": "2026-10-24T13:00:00Z",
        "retrieved_at": "2026-10-25T11:00:00Z",
    }


def snapshot(sport="NBA", facts=None):
    return compile_intelligence_research_snapshot(
        sport=sport, official_event_id="candidate-game-17",
        event_start_utc="2026-10-25T20:00:00Z",
        as_of_utc="2026-10-25T15:00:00Z",
        facts=[observation()] if facts is None else facts,
    )


@pytest.mark.parametrize("sport", sorted(SPORT_DOMAINS))
def test_thirteen_sports_support_offline_candidate_only_handoff(sport):
    snap = snapshot(sport)
    ref = snap["research_facts"][0]["source_reference"]
    out = prepare_shadow_feature_handoff(
        snap, proposed_targets={ref: ["LLP_TEAM_EVENT", "WOW_PROP_LANE"]})
    assert out["sport"] == sport
    assert out["received"] == out["shadow_candidates"] == 1
    assert out["held_or_unassigned"] == 0
    assert out["reconciled_exactly_once"] is True
    row = out["rows"][0]
    assert row["status"] == "SHADOW_FEATURE_CANDIDATE_UNVERIFIED"
    assert row["research_target_lanes"] == ["LLP_TEAM_EVENT", "WOW_PROP_LANE"]
    assert row["fitted_feature_consumed"] is False
    assert out["verified_model_features"] == out["model_consumption_receipts"] == []
    assert out["probability_publishable"] is False and out["can_execute"] is False


def test_missing_target_is_visible_not_a_silent_model_feature():
    out = prepare_shadow_feature_handoff(snapshot(), proposed_targets={})
    assert out["shadow_candidates"] == 0 and out["held_or_unassigned"] == 1
    assert out["rows"][0]["status"] == "NO_EXPLICIT_RESEARCH_TARGET"


def test_context_hypotheses_and_categorical_observations_stay_research_only():
    for kind, value, expected in (
        ("CONTEXT_HYPOTHESIS", 3.5, "HYPOTHESIS_ONLY_NO_NUMERIC_FEATURE"),
        ("SOURCE_OBSERVATION", "starter questionable", "CATEGORICAL_RESEARCH_ONLY"),
    ):
        snap = snapshot(facts=[observation(evidence_kind=kind, value=value)])
        ref = snap["research_facts"][0]["source_reference"]
        out = prepare_shadow_feature_handoff(
            snap, proposed_targets={ref: ["LLP_TEAM_EVENT"]})
        assert out["rows"][0]["status"] == expected
        assert out["shadow_candidates"] == 0


@pytest.mark.parametrize("change,code", (
    ({"probability_publishable": True}, "INTEL_SHADOW_AUTHORITY_CONFLICT"),
    ({"event_identity_attested": True}, "INTEL_SHADOW_AUTHORITY_CONFLICT"),
    ({"verified_model_features": [{"forged": 1}]}, "INTEL_SHADOW_AUTHORITY_CONFLICT"),
    ({"can_execute": True}, "INTEL_SHADOW_AUTHORITY_CONFLICT"),
    ({"fact_count": 9}, "INTEL_SHADOW_ROWS_INVALID"),
    ({"event_start_utc": "2026-10-25T10:00:00Z"}, "INTEL_SHADOW_NOT_PREGAME"),
))
def test_snapshot_forgery_and_poststart_blocked(change, code):
    snap = snapshot()
    snap.update(change)
    with pytest.raises(ShadowFeatureHold, match=code):
        prepare_shadow_feature_handoff(snap, proposed_targets={})


@pytest.mark.parametrize("change,code", (
    ({"pregame_model_feature_eligible": True}, "INTEL_SHADOW_ROW_AUTHORITY_CONFLICT"),
    ({"source_attestation": "CERTIFIED"}, "INTEL_SHADOW_ROW_AUTHORITY_CONFLICT"),
    ({"retrieved_at": "2026-10-25T21:00:00+00:00"}, "INTEL_SHADOW_CHRONOLOGY_INVALID"),
    ({"source_reference": "forged"}, "INTEL_SHADOW_SOURCE_REFERENCE_INVALID"),
    ({"source_reference": "b" * 64}, "INTEL_SHADOW_SOURCE_REFERENCE_INVALID"),
    ({"source_record_id": "different-original"}, "INTEL_SHADOW_SOURCE_REFERENCE_INVALID"),
    ({"source_url": "https://altered.example.net/records/123"},
     "INTEL_SHADOW_SOURCE_REFERENCE_INVALID"),
))
def test_bad_row_authority_provenance_and_chronology(change, code):
    snap = snapshot()
    snap["research_facts"][0].update(change)
    with pytest.raises(ShadowFeatureHold, match=code):
        prepare_shadow_feature_handoff(snap, proposed_targets={})


def test_duplicate_reference_fails_closed():
    snap = snapshot()
    snap["research_facts"].append(copy.deepcopy(snap["research_facts"][0]))
    snap["fact_count"] = 2
    with pytest.raises(ShadowFeatureHold, match="INTEL_SHADOW_SOURCE_REFERENCE_INVALID"):
        prepare_shadow_feature_handoff(snap, proposed_targets={})


def test_extra_target_reference_and_unknown_lane_fails_closed():
    snap = snapshot()
    ref = snap["research_facts"][0]["source_reference"]
    with pytest.raises(ShadowFeatureHold, match="INTEL_SHADOW_UNKNOWN_TARGET_REF"):
        prepare_shadow_feature_handoff(snap, proposed_targets={"a" * 64: ["LLP_TEAM_EVENT"]})
    with pytest.raises(ShadowFeatureHold, match="INTEL_SHADOW_TARGETS_INVALID"):
        prepare_shadow_feature_handoff(snap, proposed_targets={ref: ["MARKET_AS_PROBABILITY"]})
    with pytest.raises(ShadowFeatureHold, match="INTEL_SHADOW_TARGETS_INVALID"):
        prepare_shadow_feature_handoff(
            snap, proposed_targets={ref: ["WOW_PROP_LANE", "WOW_PROP_LANE"]})


def test_empty_snapshot_has_exact_zero_reconciliation():
    result = prepare_shadow_feature_handoff(
        snapshot(facts=[]), proposed_targets={})
    assert result["received"] == result["shadow_candidates"] == result["held_or_unassigned"] == 0
    assert result["rows"] == [] and result["reconciled_exactly_once"] is True
