"""Fixture-only contract: registered open-license origins -> research, never model."""
import copy
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.universal_intelligence_licensed_source_bridge import (
    SourceBridgeHold, compile_registry_source_shadow_bridge,
)

SPORTSDATAVERSE = (
    "https://raw.githubusercontent.com/sportsdataverse/"
    "sportsdataverse-data/main/fixture/nba_game.json"
)
OPENFOOTBALL = (
    "https://raw.githubusercontent.com/openfootball/"
    "football.json/master/fixture/season.json"
)
START = "2026-10-27T20:00:00Z"
CUTOFF = "2026-10-27T18:00:00Z"


def row(**updates):
    r = {
        "source_id": "SPORTSDATAVERSE_ESPN",
        "sport": "NBA", "event_id_claimed": "nba-game-101",
        "source_url": SPORTSDATAVERSE, "source_record_id": "game-101:team-1",
        "payload_sha256": hashlib.sha256(b"offline fixture").hexdigest(),
        "domain": "quality", "metric": "adjusted_score",
        "entity_id": "nba-team-1", "value": 12.0,
        "evidence_kind": "SOURCE_OBSERVATION",
        "observed_at": "2026-10-27T15:00:00Z",
        "first_available_at": "2026-10-27T15:10:00Z",
        "retrieved_at": "2026-10-27T17:00:00Z",
        "target_lanes": ["LLP_TEAM_EVENT"],
    }
    r.update(updates)
    return r


def compile(rows=None, *, sport="NBA", event="nba-game-101"):
    return compile_registry_source_shadow_bridge(
        sport=sport, official_event_id_claimed=event,
        event_start_utc=START, as_of_utc=CUTOFF,
        observations=[row()] if rows is None else rows,
    )


def test_open_license_candidate_is_research_only_not_an_attested_feature():
    out = compile()
    assert out["received"] == out["candidate_rows"] == 1
    assert out["held"] == 0 and out["reconciled_exactly_once"] is True
    assert out["row_dispositions"][0]["status"] == "SOURCE_CANDIDATE_UNVERIFIED"
    assert out["source_claims"][0]["source_id"] == "SPORTSDATAVERSE_ESPN"
    assert out["source_claims"][0]["independent_rights_review_pending"] is True
    assert out["source_claims"][0]["payload_independently_verified"] is False
    assert out["research_snapshot"]["fact_count"] == 1
    assert out["research_snapshot"]["research_facts"][0]["pregame_model_feature_eligible"] is False
    assert out["shadow_handoff"]["shadow_candidates"] == 1
    assert out["shadow_handoff"]["rows"][0]["status"] == "SHADOW_FEATURE_CANDIDATE_UNVERIFIED"
    assert out["shadow_handoff"]["model_consumption_receipts"] == []
    assert out["model_consumption_receipts"] == []
    for field in ("source_rights_independently_attested", "event_identity_independently_attested",
                  "historical_training_authorized_by_bridge", "probability_publishable",
                  "can_execute"):
        assert out[field] is False
    assert out["calibrated_probability"] is None
    assert out["calibrated_lower_bound"] is None


@pytest.mark.parametrize("sport,source_id,url", [
    ("NBA", "SPORTSDATAVERSE_ESPN", SPORTSDATAVERSE),
    ("WNBA", "SPORTSDATAVERSE_WNBA_STATS", SPORTSDATAVERSE),
    ("NCAAB", "SPORTSDATAVERSE_ESPN", SPORTSDATAVERSE),
    ("SOCCER", "OPENFOOTBALL_CC0", OPENFOOTBALL),
])
def test_registry_supported_sport_research_routes(sport, source_id, url):
    r = row(source_id=source_id, sport=sport, event_id_claimed=f"{sport.lower()}-evt",
            source_url=url)
    out = compile([r], sport=sport, event=f"{sport.lower()}-evt")
    assert out["candidate_rows"] == 1 and out["held"] == 0
    assert out["probability_publishable"] is False


def test_rejects_unapproved_vendor_without_discarding_other_evidence():
    denied = row(source_id="PROPSMADNESS", source_record_id="no-grant")
    passed = row(source_record_id="approved-candidate-row")
    out = compile([denied, passed])
    assert out["received"] == 2 and out["candidate_rows"] == 1 and out["held"] == 1
    assert [x["status"] for x in out["row_dispositions"]] == [
        "SOURCE_BRIDGE_SOURCE_UNCLASSIFIED", "SOURCE_CANDIDATE_UNVERIFIED",
    ]


@pytest.mark.parametrize("change,status", [
    ({"source_id": "PROPSMADNESS"}, "SOURCE_BRIDGE_SOURCE_UNCLASSIFIED"),
    ({"source_id": "THERUNDOWN"}, "SOURCE_BRIDGE_SOURCE_NOT_TRAINING_ELIGIBLE"),
    ({"source_id": "CRICKET_HISTORICAL_MATCHES_UNAPPROVED"},
     "SOURCE_BRIDGE_SOURCE_SPORT_MISMATCH"),
    ({"source_url": "https://propsmadness.com/nba/props/points"},
     "SOURCE_BRIDGE_PROVENANCE_INVALID"),
    ({"source_url": "https://raw.githubusercontent.com/fake-org/data/main/file"},
     "SOURCE_BRIDGE_ORIGIN_UNVERIFIED"),
    ({"source_url": "https://github.com/sportsdataverse-fake/sportsdataverse-data/file"},
     "SOURCE_BRIDGE_ORIGIN_UNVERIFIED"),
    ({"source_record_id": ""}, "SOURCE_BRIDGE_PROVENANCE_INVALID"),
    ({"payload_sha256": "fakedigest"}, "SOURCE_BRIDGE_PAYLOAD_DIGEST_INVALID"),
    ({"sport": "NFL"}, "SOURCE_BRIDGE_EVENT_IDENTITY_CONFLICT"),
    ({"event_id_claimed": "another-event"}, "SOURCE_BRIDGE_EVENT_IDENTITY_CONFLICT"),
    ({"retrieved_at": "2026-10-27T19:00:00Z"},
     "SOURCE_BRIDGE_POST_CUTOFF_OR_LEAKAGE"),
    ({"first_available_at": "2026-10-27T21:00:00Z"},
     "SOURCE_BRIDGE_POST_CUTOFF_OR_LEAKAGE"),
    ({"observed_at": "2026-10-27T17:10:00Z"},
     "SOURCE_BRIDGE_POST_CUTOFF_OR_LEAKAGE"),
    ({"observed_at": "2026-10-27T15:00:00"},
     "SOURCE_BRIDGE_TIME_INVALID"),
    ({"domain": "not_a_feature"}, "SOURCE_BRIDGE_DOMAIN_UNSUPPORTED"),
    ({"metric": ""}, "SOURCE_BRIDGE_FEATURE_IDENTITY_INVALID"),
    ({"value": float("nan")}, "SOURCE_BRIDGE_VALUE_INVALID"),
    ({"value": True}, "SOURCE_BRIDGE_VALUE_INVALID"),
    ({"evidence_kind": "BOOKMAKER_SIGNAL"}, "SOURCE_BRIDGE_KIND_INVALID"),
    ({"target_lanes": ["LLP_TEAM_EVENT", "LLP_TEAM_EVENT"]},
     "SOURCE_BRIDGE_TARGET_INVALID"),
    ({"target_lanes": ["LLP_TEAM_EVENT", []]}, "SOURCE_BRIDGE_TARGET_INVALID"),
    ({"target_lanes": ["MARKET_EDGE"]}, "SOURCE_BRIDGE_TARGET_INVALID"),
    ({"model_probability": 0.97}, "SOURCE_BRIDGE_AUTHORITY_INJECTION"),
    ({"sportsbook": "Book"}, "SOURCE_BRIDGE_AUTHORITY_INJECTION"),
    ({"market_implied_probability": 0.8}, "SOURCE_BRIDGE_AUTHORITY_INJECTION"),
    ({"source_rights_attested": True}, "SOURCE_BRIDGE_AUTHORITY_INJECTION"),
    ({"can_execute": True}, "SOURCE_BRIDGE_AUTHORITY_INJECTION"),
])
def test_row_level_holds_with_typed_reasons(change, status):
    r = compile([row(**change)])
    assert r["candidate_rows"] == 0 and r["held"] == 1
    assert r["row_dispositions"][0]["status"] == status
    assert r["shadow_handoff"]["rows"] == []
    assert r["probability_publishable"] is False


def test_duplicate_source_row_does_not_double_count():
    one = row()
    other = copy.deepcopy(one)
    out = compile([one, other])
    assert out["received"] == 2 and out["candidate_rows"] == 1
    assert out["row_dispositions"][1]["status"] == "SOURCE_BRIDGE_DUPLICATE_SOURCE_ROW"


def test_categorical_and_hypotheses_do_not_get_numeric_shadow_features():
    categorical = row(value="starter questionable")
    hypothetical = row(source_record_id="different", value=3.2,
                       evidence_kind="CONTEXT_HYPOTHESIS")
    out = compile([categorical, hypothetical])
    assert out["candidate_rows"] == 2
    assert out["shadow_handoff"]["shadow_candidates"] == 0
    assert {x["status"] for x in out["shadow_handoff"]["rows"]} == {
        "CATEGORICAL_RESEARCH_ONLY", "HYPOTHESIS_ONLY_NO_NUMERIC_FEATURE",
    }


def test_empty_input_does_not_manufacture_observations():
    out = compile([])
    assert out["received"] == out["candidate_rows"] == out["held"] == 0
    assert out["research_snapshot"]["fact_count"] == 0
    assert out["shadow_handoff"]["rows"] == []
    assert out["model_consumption_receipts"] == []


def test_no_bypass_by_faking_an_unapproved_ncaab_source():
    out = compile([row(source_id="NCAAB_APPROVED_STATS")], sport="NBA")
    assert out["candidate_rows"] == 0
    assert out["row_dispositions"][0]["status"] == "SOURCE_BRIDGE_SOURCE_SPORT_MISMATCH"


def test_invalid_common_cutoff_rejected():
    with pytest.raises(SourceBridgeHold, match="SOURCE_BRIDGE_NOT_PREGAME"):
        compile_registry_source_shadow_bridge(
            sport="NBA", official_event_id_claimed="nba-game-101",
            event_start_utc=START, as_of_utc=START,
            observations=[row()],
        )
    with pytest.raises(SourceBridgeHold, match="SOURCE_BRIDGE_INPUT_INVALID"):
        compile_registry_source_shadow_bridge(
            sport="NBA", official_event_id_claimed="nba-game-101",
            event_start_utc=START, as_of_utc=CUTOFF,
            observations=[row()] * 101,
        )
