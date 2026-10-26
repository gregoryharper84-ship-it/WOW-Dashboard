"""Real-schema-shaped synthetic source version audit cases; no DB mutation."""
from hashlib import sha256
import pytest
from v17.nhl_total_goals_challenger import NHLTotalsResearchError, build_total_rows
from v17.nhl_total_goals_source_adapter import adapt_persisted_nhl_games, CAN_EXECUTE, PROBABILITY_PUBLISHABLE


def src(row_id="2023021048", home=1, away=4, hash_name="v1", retrieved="2026-09-15T19:49:52+00:00"):
    return {"season": "20232024", "official_event_id": row_id,
            "event_start_time": "2024-03-14T23:00:00+00:00",
            "home_participant": "DET", "away_participant": "ARI",
            "outcome_json": {"home_score": home, "away_score": away},
            "source_provider": "NHL_PUBLIC_WEB_API",
            "source_payload_sha256": sha256(hash_name.encode()).hexdigest(),
            "source_retrieved_at": retrieved,
            "historical_reconstruction": True, "can_execute": False}


def test_unique_persisted_record_adapts_to_candidate_input():
    converted, audit = adapt_persisted_nhl_games([src()])
    assert len(converted) == 1
    assert converted[0]["home_score"] == 1
    assert converted[0]["away_score"] == 4
    assert audit["version_drift_events"] == 0
    # Evidence insufficient for fitting; adapter must not bypass the model's source/candidate checks.
    _, feature_audit = build_total_rows(converted)
    assert feature_audit["source_rows"] == 1
    assert not CAN_EXECUTE and not PROBABILITY_PUBLISHABLE


def test_hash_version_change_needs_explicit_review():
    samples = [src(), src(hash_name="v2", retrieved="2026-10-23T00:27:11+00:00")]
    with pytest.raises(NHLTotalsResearchError) as err:
        adapt_persisted_nhl_games(samples)
    assert err.value.code == "NHL_TOTALS_SOURCE_VERSION_REVIEW_REQUIRED"
    games, audit = adapt_persisted_nhl_games(samples, allow_matching_source_versions=True)
    assert len(games) == 1
    assert audit["source_instances"] == 2
    assert audit["canonical_games"] == 1
    assert audit["version_drift_events"] == 1
    assert len(audit["version_drift_ledger"][0]["source_hashes"]) == 2
    assert games[0]["source_payload_sha256"] == src(hash_name="v2")["source_payload_sha256"]
    assert audit["probability_publishable"] is False


def test_conflicting_final_score_fails_even_with_version_override():
    with pytest.raises(NHLTotalsResearchError) as err:
        adapt_persisted_nhl_games([src(), src(home=2, hash_name="v2")], allow_matching_source_versions=True)
    assert err.value.code == "NHL_TOTALS_SOURCE_IDENTITY_CONFLICT"


def test_same_hash_double_insert_still_requires_duplicate_review():
    with pytest.raises(NHLTotalsResearchError) as err:
        adapt_persisted_nhl_games([src(), src()])
    assert err.value.code == "NHL_TOTALS_SOURCE_VERSION_REVIEW_REQUIRED"


@pytest.mark.parametrize("override", [
    {"historical_reconstruction": False},
    {"can_execute": True},
    {"source_provider": "MARKET"},
    {"source_payload_sha256": "fake"},
    {"outcome_json": {"home_score": None, "away_score": 4}},
])
def test_invalid_persisted_source_fails(override):
    record = src()
    record.update(override)
    with pytest.raises(NHLTotalsResearchError) as err:
        adapt_persisted_nhl_games([record])
    assert err.value.code == "NHL_TOTALS_PERSISTED_ROW_INVALID"
