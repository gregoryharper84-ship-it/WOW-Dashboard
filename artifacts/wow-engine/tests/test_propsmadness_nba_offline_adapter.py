"""WOW #2018: deterministic PropsMadness NBA interface tests, no provider I/O."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.propsmadness_nba_offline_adapter import (
    SCHEMA, FIXTURE_ORIGIN, PropsMadnessFixtureHold, capability_receipt,
    normalize_synthetic_fixture,
)

PLAYER_MAP = {"synthetic-p-001": "nba-verified-by-external-test-only-p-001"}
EVENT_MAP = {"synthetic-e-001": "nba-verified-by-external-test-only-e-001"}

BASE = {
    "provider_record_id": "row-market-1", "record_type": "MARKET_DISCOVERY",
    "provider_player_id": "synthetic-p-001", "provider_event_id": "synthetic-e-001",
    "team_id": "BOS", "opponent_id": "NYK", "stat_type": "POINTS",
    "period": "FULL_GAME", "event_start_at": "2026-10-24T00:00:00Z",
    "observed_at": "2026-10-23T20:00:00Z",
    "retrieved_at": "2026-10-23T20:00:05Z",
    "source_field": "synthetic_fixture.exact_line",
    "line": 21.5, "direction": "MORE", "sportsbook": "SYNTHETIC_BOOK",
    "american_odds": -110,
}


def market(**kwargs):
    return {**BASE, **kwargs}


def context(**kwargs):
    return {
        **{k: v for k, v in BASE.items()
           if k not in {"line", "direction", "sportsbook", "american_odds"}},
        "provider_record_id": "row-context-1", "record_type": "PREGAME_CONTEXT",
        "source_field": "synthetic_fixture.usage_pct", "metric_name": "USG_PCT",
        "metric_value": 25.0, **kwargs,
    }


def outcome(**kwargs):
    return {
        **{k: v for k, v in BASE.items()
           if k not in {"line", "direction", "sportsbook", "american_odds"}},
        "provider_record_id": "row-final-1", "record_type": "FINAL_OUTCOME",
        "observed_at": "2026-10-24T03:10:00Z",
        "retrieved_at": "2026-10-24T03:15:00Z",
        "game_final_at": "2026-10-24T03:00:00Z",
        "source_field": "synthetic_fixture.final_points",
        "actual_value": 24, **kwargs,
    }


def encode(rows=None, **root_overrides):
    root = {"schema": SCHEMA, "origin": FIXTURE_ORIGIN,
            "provider": "PROPSMADNESS", "sport": "NBA",
            "rows": rows if rows is not None else [market(), context(), outcome()]}
    root.update(root_overrides)
    return json.dumps(root, sort_keys=True).encode("utf-8")


def normalize(rows=None, **root_overrides):
    return normalize_synthetic_fixture(
        encode(rows, **root_overrides),
        player_crosswalk=PLAYER_MAP, event_crosswalk=EVENT_MAP,
    )


def assert_hold(rows, code):
    with pytest.raises(PropsMadnessFixtureHold, match=code):
        normalize(rows)


def test_capability_discloses_no_public_api_or_live_access():
    status = capability_receipt()
    assert status["public_api"] is False
    assert status["live_fetch_supported"] is False
    assert status["vendor_permission"] == "UNVERIFIED"
    assert status["history_storage_authorized"] is False
    assert status["training_authorized"] is False
    assert status["prediction_authority"] is False
    assert status["can_execute"] is False


def test_three_domains_normalize_without_creating_probability_or_grant():
    raw = encode()
    result = normalize_synthetic_fixture(
        raw, player_crosswalk=PLAYER_MAP, event_crosswalk=EVENT_MAP
    )
    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["raw_rows"] == 3
    assert result["counts_by_type"] == {
        "FINAL_OUTCOME": 1, "MARKET_DISCOVERY": 1, "PREGAME_CONTEXT": 1,
    }
    assert [x["evidence_domain"] for x in result["rows"]] == [
        "MARKET", "SPORTING_RESEARCH", "SPORTING_SETTLED_LABEL",
    ]
    assert result["rows"][0]["line"] == 21.5
    assert result["rows"][1]["metric_value"] == 25
    assert result["rows"][2]["actual_value"] == 24
    assert all(x["pregame_feature_eligible"] is False for x in result["rows"])
    assert all(x["can_execute"] is False for x in result["rows"])
    for flag in ("source_provenance_attested", "crosswalk_attested",
                 "storage_write_authorized", "training_ready", "model_certified",
                 "probability_publishable", "can_execute"):
        assert result[flag] is False


def test_real_vendor_payload_never_admitted_as_fixture():
    with pytest.raises(PropsMadnessFixtureHold, match="PM_REAL_VENDOR_DATA_NOT_AUTHORIZED"):
        normalize(origin="PROPSMADNESS_REAL_EXPORT")
    with pytest.raises(PropsMadnessFixtureHold, match="PM_WRONG_PROVIDER_OR_SPORT"):
        normalize(sport="WNBA")


@pytest.mark.parametrize("override,expected", [
    ({"stat_type": "PRA"}, "PM_UNSUPPORTED_NBA_STAT"),
    ({"period": "FIRST_QUARTER"}, "PM_UNSUPPORTED_PERIOD"),
    ({"direction": "OVER"}, "PM_DIRECTION_INVALID"),
    ({"american_odds": -99}, "PM_ODDS_INVALID"),
    ({"american_odds": 0}, "PM_ODDS_INVALID"),
    ({"line": float("inf")}, "PM_EXACT_LINE_INVALID"),
    ({"line": -1.5}, "PM_EXACT_LINE_INVALID"),
    ({"sportsbook": ""}, "PM_BOOK_MISSING"),
    ({"source_field": ""}, "PM_FIELD_PROVENANCE_MISSING"),
    ({"team_id": "NYK"}, "PM_TEAMS_CONFLICT"),
])
def test_market_line_and_period_fails_closed(override, expected):
    assert_hold([market(**override)], expected)


def test_model_probability_and_auth_fields_are_not_accepted():
    assert_hold([market(model_probability=0.77)], "PM_MODEL_AUTHORITY_FIELD_FORBIDDEN")
    assert_hold([context(calibrated_lower_bound=0.63)], "PM_MODEL_AUTHORITY_FIELD_FORBIDDEN")
    assert_hold([market(api_key="secret")], "PM_CREDENTIAL_FIELD_FORBIDDEN")
    with pytest.raises(PropsMadnessFixtureHold, match="PM_MODEL_AUTHORITY_FIELD_FORBIDDEN"):
        normalize(model_probability=0.9)


def test_identity_is_explicit_never_guessed():
    assert_hold([market(provider_player_id="different")],
                "PM_CANONICAL_IDENTITY_UNRESOLVED")
    assert_hold([market(provider_event_id="different")],
                "PM_CANONICAL_IDENTITY_UNRESOLVED")
    assert_hold([market(provider_player_id="")], "PM_PLAYER_ID_MISSING")


def test_pregame_sources_must_precede_tipoff_even_when_fetched_after():
    assert_hold([market(observed_at="2026-10-24T00:01:00Z",
                        retrieved_at="2026-10-24T00:02:00Z")],
                "PM_POST_TIP_MARKET_OBSERVATION")
    assert_hold([context(observed_at="2026-10-23T23:30:00Z",
                         retrieved_at="2026-10-24T03:30:00Z")],
                "PM_POST_TIP_CONTEXT_LEAKAGE")
    assert_hold([market(observed_at="2026-10-23T21:00:00Z",
                        retrieved_at="2026-10-23T20:00:00Z")],
                "PM_AS_OF_CHRONOLOGY_CONFLICT")


def test_timezone_requirement_and_context_field_restrictions():
    assert_hold([context(observed_at="2026-10-23T20:00:00")],
                "PM_OBSERVED_TIME_INVALID")
    assert_hold([context(metric_name="MATCHUP_GRADE")], "PM_UNSUPPORTED_CONTEXT_METRIC")
    assert_hold([context(line=10.5)], "PM_CONTEXT_MARKET_OR_LABEL_MIXED")
    assert_hold([market(actual_value=25)], "PM_MARKET_SPORTING_MIXED")


def test_postgame_labels_never_masquerade_as_pregame_features():
    assert_hold([outcome(observed_at="2026-10-24T01:00:00Z",
                         retrieved_at="2026-10-24T01:02:00Z")],
                "PM_FINAL_OUTCOME_CHRONOLOGY_INVALID")
    assert_hold([outcome(actual_value=12.5)], "PM_COUNTING_STAT_NONINTEGER")
    assert_hold([outcome(line=22.5)], "PM_OUTCOME_MARKET_OR_CONTEXT_MIXED")


def test_duplicate_source_id_and_duplicate_thesis_both_fail():
    assert_hold([market(), context(provider_record_id="row-market-1")],
                "PM_DUPLICATE_SOURCE_RECORD_ID")
    assert_hold([market(), market(provider_record_id="row-market-2")],
                "PM_DUPLICATE_EVIDENCE_THESIS")


def test_empty_or_oversized_or_malformed_payloads_rejected():
    for raw, reason in [
        (b"", "PM_FIXTURE_SIZE_INVALID"),
        (b"not-json", "PM_FIXTURE_JSON_INVALID"),
        (b"x" * 1_000_001, "PM_FIXTURE_SIZE_INVALID"),
    ]:
        with pytest.raises(PropsMadnessFixtureHold, match=reason):
            normalize_synthetic_fixture(raw, player_crosswalk=PLAYER_MAP,
                                        event_crosswalk=EVENT_MAP)
    with pytest.raises(PropsMadnessFixtureHold, match="PM_FIXTURE_ROWS_INVALID"):
        normalize(rows=[])


def test_synthetic_hit_rate_is_not_governed_probability():
    # Refuse a provider-side precomputed probability regardless of label.
    assert_hold([market(implied_probability=0.70)],
                "PM_MODEL_AUTHORITY_FIELD_FORBIDDEN")
    result = normalize([market()])
    assert "model_probability" not in str(result)
    assert "calibrated_probability" not in str(result)
    assert result["probability_publishable"] is False
