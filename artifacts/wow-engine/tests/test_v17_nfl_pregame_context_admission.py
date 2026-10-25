"""Adversarial tests for shadow-only point-in-time feature admission."""
from __future__ import annotations

from copy import deepcopy

import pytest

from v17.nfl_pregame_context_admission import (
    NFLContextAdmissionError,
    build_pregame_context_packet,
)

GAME = {
    "official_event_id": "2026_07_SF_ATL",
    "home_team": "ATL",
    "away_team": "SF",
    "event_start_time_utc": "2026-10-25T17:00:00Z",
}
CUTOFF = "2026-10-25T16:30:00Z"


def _sources():
    sources = []
    for side, team in [("HOME", "ATL"), ("AWAY", "SF")]:
        for family in ("QB_STARTER", "ROSTER_AVAILABILITY", "RUSH_PASS_MATCHUP"):
            fields = {
                "QB_STARTER": {
                    "starter_id": "test-starter-" + team,
                    "starter_confirmed": True,
                    "starter_changed_from_previous_game": side == "HOME",
                },
                "ROSTER_AVAILABILITY": {
                    "ol_starters_expected": 4 if side == "HOME" else 3,
                    "skill_starters_expected": 5 if side == "HOME" else 2,
                    "defense_starters_expected": 10 if side == "HOME" else 8,
                },
                "RUSH_PASS_MATCHUP": {
                    "rush_off_epa_pp": 0.15 if side == "HOME" else 0.20,
                    "pass_off_epa_pp": 0.17 if side == "HOME" else 0.22,
                    "rush_def_epa_allowed_pp": 0.11 if side == "HOME" else 0.08,
                    "pass_def_epa_allowed_pp": 0.09 if side == "HOME" else 0.12,
                    "sample_games": 6,
                    "last_input_game_end_at": "2026-10-20T20:00:00Z",
                },
            }[family]
            sources.append({
                "official_event_id": GAME["official_event_id"],
                "side": side, "team": team, "family": family,
                "data_domain": "SPORTING",
                "source_kind": "OFFICIAL_TEAM_REPORT" if family != "RUSH_PASS_MATCHUP" else "LICENSED_STATS_FEED",
                "rights_status": "RESEARCH_APPROVED",
                "source_receipt_id": f"fixture-{side}-{family}",
                "source_publisher": "licensed-fixture",
                "rights_receipt_id": "test-entitlement",
                "content_sha256": "a" * 64 if side == "HOME" else "b" * 64,
                "observed_at": "2026-10-25T14:00:00Z",
                "published_at": "2026-10-25T14:10:00Z",
                "captured_at": "2026-10-25T14:15:00Z",
                "measurements": fields,
            })
    return sources


def _run(evidence=None, game=None, cutoff=CUTOFF):
    return build_pregame_context_packet(
        GAME if game is None else game, _sources() if evidence is None else evidence,
        decision_as_of=cutoff,
    )


def test_complete_packet_builds_interpretable_edges_without_any_publication():
    original = _sources()
    a = _run(original)
    assert original == _sources()
    assert a == _run(original)
    assert a["status"] == "RESEARCH_CONTEXT_PACKET_COMPLETE_UNCERTIFIED"
    assert a["missing_families"] == []
    assert a["features"]["home_rush_matchup_epa_edge"] == pytest.approx(0.15 - 0.08)
    assert a["features"]["away_pass_matchup_epa_edge"] == pytest.approx(0.22 - 0.09)
    assert a["features"]["ol_available_edge"] == 1
    assert a["features"]["qb_change_edge"] == -1
    assert len(a["packet_sha256"]) == 64
    assert a["model_fit_or_validation_complete"] is False
    assert a["provider_authenticity_independently_verified"] is False
    assert a["active_champion_features_changed"] is False
    assert a["probability_publishable"] is False
    assert a["promotion_authorized"] is False
    assert a["can_execute"] is False
    assert a["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert "starter_id" not in str(a)


def test_missing_family_never_defaults_to_zero_or_creates_partial_vector():
    e = [item for item in _sources() if not (
        item["side"] == "AWAY" and item["family"] == "ROSTER_AVAILABILITY"
    )]
    result = _run(e)
    assert result["status"] == "PREGAME_CONTEXT_EVIDENCE_INCOMPLETE"
    assert result["missing_families"] == ["AWAY:ROSTER_AVAILABILITY"]
    assert result["features"] is None
    assert result["probability_publishable"] is False


@pytest.mark.parametrize("field,value,code", [
    ("official_event_id", "2026_07_TB_CAR", "NFL_CONTEXT_EVENT_MISMATCH"),
    ("side", "OTHER", "NFL_CONTEXT_FAMILY_OR_SIDE_INVALID"),
    ("family", "SPREAD_ODDS", "NFL_CONTEXT_FAMILY_OR_SIDE_INVALID"),
    ("team", "CHI", "NFL_CONTEXT_SIDE_TEAM_MISMATCH"),
    ("data_domain", "MARKET", "NFL_CONTEXT_SOURCE_DOMAIN_INVALID"),
    ("source_kind", "UNLICENSED_SCRAPE", "NFL_CONTEXT_SOURCE_DOMAIN_INVALID"),
    ("rights_status", "LICENSE_REVIEW_REQUIRED", "NFL_CONTEXT_SOURCE_RIGHTS_NOT_APPROVED"),
    ("rights_receipt_id", "", "NFL_CONTEXT_RIGHTS_RECEIPT_MISSING"),
    ("source_receipt_id", "", "NFL_CONTEXT_RECEIPT_ID_MISSING"),
    ("content_sha256", "aa", "NFL_CONTEXT_CONTENT_HASH_INVALID"),
    ("observed_at", "2026-10-25T14:30:00Z", "NFL_CONTEXT_SOURCE_TIME_LEAKAGE"),
    ("published_at", "2026-10-25T13:55:00Z", "NFL_CONTEXT_SOURCE_TIME_LEAKAGE"),
    ("captured_at", "2026-10-25T16:45:00Z", "NFL_CONTEXT_SOURCE_TIME_LEAKAGE"),
    ("captured_at", "2026-10-25T16:15:00", "NFL_CONTEXT_SOURCE_TIME_INVALID"),
])
def test_bad_source_fails_closed(field, value, code):
    e = _sources()
    e[0][field] = value
    with pytest.raises(NFLContextAdmissionError, match=code):
        _run(e)


def test_duplicate_family_receipt_blocks_double_counting():
    e = _sources()
    e.append(deepcopy(e[0]))
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_DUPLICATE_SOURCE_FAMILY"):
        _run(e)


def test_decision_at_or_after_kickoff_is_invalid():
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_DECISION_NOT_PREGAME"):
        _run(cutoff="2026-10-25T17:00:00Z")


def test_source_observation_after_publication_is_leakage():
    e = _sources()
    e[0]["observed_at"] = "2026-10-25T14:12:00Z"
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_SOURCE_TIME_LEAKAGE"):
        _run(e)


def test_nan_and_boolean_numerics_are_not_features():
    e = _sources()
    e[2]["measurements"]["rush_off_epa_pp"] = float("nan")
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_MATCHUP_EPA_INVALID"):
        _run(e)
    e = _sources()
    e[1]["measurements"]["ol_starters_expected"] = True
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_OL_COUNT_INVALID"):
        _run(e)


def test_future_stats_inside_pregame_evidence_are_not_accepted():
    e = _sources()
    e[2]["measurements"]["last_input_game_end_at"] = "2026-10-25T17:00:01Z"
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_PRIOR_GAME_LEAKAGE"):
        _run(e)


def test_starter_status_must_have_actual_confirmed_identity():
    e = _sources()
    e[0]["measurements"]["starter_confirmed"] = False
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_STARTER_NOT_CONFIRMED"):
        _run(e)
    e = _sources()
    e[0]["measurements"]["starter_id"] = ""
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_STARTER_ID_MISSING"):
        _run(e)


def test_same_team_and_cross_event_evidence_are_rejected():
    same = deepcopy(GAME)
    same["away_team"] = "ATL"
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_TEAM_ID_INVALID"):
        _run(game=same)
    e = _sources()
    e[1]["official_event_id"] = "2026_07_NYG_HOU"
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_EVENT_MISMATCH"):
        _run(e)


def test_shuffling_sources_does_not_change_evidence_digest():
    items = _sources()
    assert _run(items)["packet_sha256"] == _run(list(reversed(items)))["packet_sha256"]


def test_changing_raw_football_evidence_changes_digest():
    e = _sources()
    e[2]["measurements"]["rush_off_epa_pp"] = 0.04
    assert _run(e)["packet_sha256"] != _run()["packet_sha256"]


def test_no_market_or_fitted_probabilities_appear_in_packet():
    packet = _run()
    assert "calibrated_home_probability" not in packet
    assert "sportsbook" not in str(packet).lower()
    assert "odds" not in packet["features"]


def test_matchup_stats_must_precede_the_timestamp_claimed_by_source():
    e = _sources()
    e[2]["measurements"]["last_input_game_end_at"] = "2026-10-25T14:01:00Z"
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_PRIOR_GAME_LEAKAGE"):
        _run(e)


def test_unknown_market_and_postgame_metric_fields_are_not_admitted():
    for injected in ("sportsbook_implied_prob", "final_home_score", "future_injury_status"):
        e = _sources()
        e[2]["measurements"][injected] = 0.90
        with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_METRIC_CONTRACT_INVALID"):
            _run(e)


def test_missing_required_feature_never_defaults_from_other_values():
    e = _sources()
    del e[1]["measurements"]["ol_starters_expected"]
    with pytest.raises(NFLContextAdmissionError, match="NFL_CONTEXT_METRIC_CONTRACT_INVALID"):
        _run(e)
