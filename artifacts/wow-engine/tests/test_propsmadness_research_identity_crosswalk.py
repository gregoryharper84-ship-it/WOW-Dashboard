"""PropsMadness identity reconciliation: synthetic/fixture-only evidence."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.propsmadness_multisport_public_explore import parse_multisport_public_html
from v17.propsmadness_intelligence_handoff import build_shared_intelligence_handoff
from v17.propsmadness_research_identity_crosswalk import (
    CrosswalkHold, reconcile_research_aliases,
)

CAPTURED = "2026-10-25T14:00:00Z"
EARLIER = "2026-10-25T13:00:00Z"
START = "2026-10-25T20:00:00Z"


def handoff(sport="NBA", market="points"):
    labels = {"NBA": "Points", "WNBA": "Points",
              "NFL": "Passing Yards", "MLB": "Strikeouts"}
    html = (f'<a href="/{sport.lower()}?market=player-{market}&amp;match=777&amp;player=123">'
            f'Example Player</a><span>{labels[sport]} 21.5· BOS @ NYK</span>').encode()
    receipt = parse_multisport_public_html(
        html, sport=sport, market=market, fetched_at=CAPTURED)
    assert receipt["row_count"] == 1
    return build_shared_intelligence_handoff([receipt])


def catalog(sport="NBA"):
    event = {
        "sport": sport, "provider_event_alias": "777",
        "official_event_id_candidate": "off-game-77",
        "fixture_text_unverified": "BOS @ NYK",
        "event_start_utc": START,
        "source_url": "https://official.example.org/schedule",
        "source_record_id": "schedule-77", "retrieved_at": EARLIER,
    }
    player = {
        "sport": sport, "provider_event_alias": "777",
        "provider_player_alias": "123",
        "official_event_id_candidate": "off-game-77",
        "official_player_id_candidate": "off-player-123",
        "source_url": "https://official.example.org/roster",
        "source_record_id": "roster-123", "retrieved_at": EARLIER,
    }
    return event, player


@pytest.mark.parametrize("sport,market", (
    ("NBA", "points"), ("NFL", "passing-yards"),
    ("MLB", "strikeouts"), ("WNBA", "points"),
))
def test_four_sport_candidate_links_are_never_attested(sport, market):
    h = handoff(sport, market)
    ev, pl = catalog(sport)
    out = reconcile_research_aliases(h, event_candidates=[ev], player_candidates=[pl])
    assert out["received"] == 1 and out["linked_unattested"] == 1
    assert out["held"] == 0 and out["reconciled_exactly_once"] is True
    item = out["rows"][0]
    assert item["status"] == "CANDIDATE_MATCH_UNATTESTED"
    assert item["suggested_official_event_id_unverified"] == "off-game-77"
    assert item["suggested_official_player_id_unverified"] == "off-player-123"
    assert item["identity_attested"] is False and item["pregame_feature_eligible"] is False
    assert out["probability_publishable"] is False and out["can_execute"] is False


def test_missing_candidates_are_explicit_holds():
    out = reconcile_research_aliases(handoff(), event_candidates=[], player_candidates=[])
    assert out["linked_unattested"] == 0 and out["held"] == 1
    assert out["rows"][0]["status"] == "CROSSWALK_CANDIDATE_MISSING"


def test_ambiguous_source_matches_are_not_picked_arbitrarily():
    event, player = catalog()
    other = copy.deepcopy(event)
    other["official_event_id_candidate"] = "off-game-88"
    other["source_record_id"] = "schedule-88"
    out = reconcile_research_aliases(
        handoff(), event_candidates=[event, other], player_candidates=[player])
    assert out["rows"][0]["status"] == "CROSSWALK_CANDIDATE_AMBIGUOUS"
    assert out["rows"][0]["suggested_official_event_id_unverified"] is None


@pytest.mark.parametrize("field,value", (
    ("official_event_id_candidate", "different-game"),
    ("fixture_text_unverified", "BOS @ CHI"),
))
def test_conflicting_event_or_fixture_holds(field, value):
    event, player = catalog()
    event[field] = value
    out = reconcile_research_aliases(
        handoff(), event_candidates=[event], player_candidates=[player])
    assert out["rows"][0]["status"] == "CROSSWALK_EVENT_OR_FIXTURE_CONFLICT"


@pytest.mark.parametrize("field,value", (
    ("retrieved_at", "2026-10-25T15:00:00Z"),
    ("event_start_utc", "2026-10-25T12:00:00Z"),
))
def test_stale_or_after_tip_catalog_cannot_link(field, value):
    event, player = catalog()
    event[field] = value
    out = reconcile_research_aliases(
        handoff(), event_candidates=[event], player_candidates=[player])
    assert out["rows"][0]["status"] == "CROSSWALK_STALE_OR_POSTSTART"


def test_player_catalog_observed_after_capture_is_not_backfilled():
    event, player = catalog()
    player["retrieved_at"] = "2026-10-25T15:00:00Z"
    out = reconcile_research_aliases(
        handoff(), event_candidates=[event], player_candidates=[player])
    assert out["rows"][0]["status"] == "CROSSWALK_STALE_OR_POSTSTART"


def test_wrong_sport_does_not_map_to_another_leagues_identical_alias():
    e, p = catalog("MLB")
    out = reconcile_research_aliases(
        handoff("NBA", "points"), event_candidates=[e], player_candidates=[p])
    assert out["rows"][0]["status"] == "CROSSWALK_CANDIDATE_MISSING"


def test_duplicate_source_reference_fails_closed():
    h = handoff()
    h["prop_discovery_hints"].append(copy.deepcopy(h["prop_discovery_hints"][0]))
    h["unique_prop_hints"] = 2
    with pytest.raises(CrosswalkHold, match="PM_CROSSWALK_DUPLICATE_REFERENCE"):
        reconcile_research_aliases(h, event_candidates=[], player_candidates=[])


@pytest.mark.parametrize("tamper,code", (
    (lambda h: h.update(probability_publishable=True), "PM_CROSSWALK_HANDOFF_AUTHORITY_CONFLICT"),
    (lambda h: h["prop_discovery_hints"][0].update(model_probability=0.99),
     "PM_CROSSWALK_ROW_AUTHORITY_CONFLICT"),
    (lambda h: h["prop_discovery_hints"][0].update(official_identity_verified=True),
     "PM_CROSSWALK_ROW_AUTHORITY_CONFLICT"),
))
def test_no_probability_or_identity_authority_injection(tamper, code):
    h = handoff()
    tamper(h)
    with pytest.raises(CrosswalkHold, match=code):
        reconcile_research_aliases(h, event_candidates=[], player_candidates=[])


def test_candidate_cannot_self_attest_or_substitute_vendor_source():
    event, player = catalog()
    event["identity_attested"] = True
    with pytest.raises(CrosswalkHold, match="PM_CROSSWALK_AUTHORITY_INJECTION"):
        reconcile_research_aliases(
            handoff(), event_candidates=[event], player_candidates=[player])
    event, player = catalog()
    event["source_url"] = "https://propsmadness.com/nba/props/points"
    with pytest.raises(CrosswalkHold, match="PM_CROSSWALK_SOURCE_MISSING"):
        reconcile_research_aliases(
            handoff(), event_candidates=[event], player_candidates=[player])


def test_empty_source_stays_empty():
    h = handoff()
    h["prop_discovery_hints"] = []
    h["unique_prop_hints"] = 0
    out = reconcile_research_aliases(h, event_candidates=[], player_candidates=[])
    assert out["received"] == out["held"] == out["linked_unattested"] == 0
    assert out["rows"] == []
