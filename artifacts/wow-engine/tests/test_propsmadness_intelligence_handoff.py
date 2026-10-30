"""Fixture-only LLP/prop research handoff regression; no provider requests."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.propsmadness_multisport_public_explore import MARKETS, parse_multisport_public_html
from v17.propsmadness_intelligence_handoff import (
    IntelligenceHandoffHold, build_shared_intelligence_handoff,
)


def source(sport="NBA", market="points", *, player=101, match=102):
    stat, label = MARKETS[sport][market]
    html = (f'<a href="/{sport.lower()}?market=player-{market}&amp;match={match}&amp;player={player}">'
            f'A. Example</a><span>{label} 21.5· BOS @ NYK</span>'
            '<span>O -114 U -120</span><span>L10 AVG</span><span>19.8</span>').encode()
    receipt = parse_multisport_public_html(
        html, sport=sport, market=market, fetched_at="2026-10-23T18:30:00Z")
    assert receipt["row_count"] == 1 and receipt["rows"][0]["stat_type"] == stat
    return receipt


def test_four_sport_receipts_feed_research_only_and_maintain_reconciliation():
    receipts = [source("NBA", "points"), source("NFL", "passing-yards"),
                source("MLB", "strikeouts"), source("WNBA", "rebounds")]
    out = build_shared_intelligence_handoff(receipts)
    assert out["source_receipts"] == 4
    assert out["source_rows"] == out["unique_prop_hints"] == 4
    assert out["unique_event_hints"] == 4
    assert {x["sport"] for x in out["llp_event_research_hints"]} == {"NBA", "NFL", "MLB", "WNBA"}
    assert all(x["sporting_features"] == [] and x["official_event_id"] is None
               for x in out["llp_event_research_hints"])
    assert all(x["exact_line_market_authority"] is False and x["pregame_feature_eligible"] is False
               and x["model_probability"] is None for x in out["prop_discovery_hints"])
    assert out["verified_sporting_features"] == []
    assert out["model_consumption_receipts"] == []
    for key in ("source_rights_verified", "historical_training_authorized",
                "prediction_authority", "probability_publishable",
                "production_write_authorized", "can_execute"):
        assert out[key] is False


def test_nba_threes_current_public_slug_handoff_is_research_only():
    receipt = source("NBA", "threes", player=103, match=104)
    assert receipt["source_url"] == "https://propsmadness.com/nba/props/made-threes"
    result = build_shared_intelligence_handoff([receipt])
    assert result["source_rows"] == result["unique_prop_hints"] == 1
    hint = result["prop_discovery_hints"][0]
    assert hint["stat_type"] == "THREE_POINTERS_MADE"
    assert hint["source_url"] == receipt["source_url"]
    assert hint["pregame_feature_eligible"] is False
    assert hint["model_probability"] is None
    assert result["historical_training_authorized"] is False
    assert result["production_write_authorized"] is False
    assert result["can_execute"] is False

    # Old path is not valid provenance for the real published page.
    receipt["source_url"] = "https://propsmadness.com/nba/props/threes"
    with pytest.raises(IntelligenceHandoffHold, match="PM_HANDOFF_PROVENANCE_INVALID"):
        build_shared_intelligence_handoff([receipt])


def test_two_props_same_provider_event_yield_one_llp_event_without_claimed_match():
    r = source("NBA", "points")
    a = source("NBA", "assists", player=202, match=102)
    out = build_shared_intelligence_handoff([r, a])
    assert out["unique_prop_hints"] == 2
    assert out["unique_event_hints"] == 1
    assert len(out["llp_event_research_hints"][0]["prop_research_references"]) == 2


def test_empty_pages_reconcile_and_do_not_generate_fabricated_candidates():
    r = source("NBA", "points")
    r["rows"] = []
    r["row_count"] = 0
    r["status"] = "PUBLIC_HTML_NO_VERIFIED_ROWS"
    out = build_shared_intelligence_handoff([r])
    assert out["source_rows"] == out["unique_prop_hints"] == out["unique_event_hints"] == 0


@pytest.mark.parametrize("tamper,code", [
    (lambda r: r.update(probability_publishable=True), "PM_HANDOFF_SOURCE_AUTHORITY_CONFLICT"),
    (lambda r: r.update(historical_training_authorized=True), "PM_HANDOFF_SOURCE_AUTHORITY_CONFLICT"),
    (lambda r: r.update(source_rights_state="AUTHORIZED"), "PM_HANDOFF_SOURCE_AUTHORITY_CONFLICT"),
    (lambda r: r.update(row_count=2), "PM_HANDOFF_RECONCILIATION_INVALID"),
    (lambda r: r.update(source_url="https://evil.example"), "PM_HANDOFF_PROVENANCE_INVALID"),
    (lambda r: r["rows"][0].update(canonical_event_id="spoof"), "PM_HANDOFF_ROW_AUTHORITY_CONFLICT"),
    (lambda r: r["rows"][0].update(model_probability=0.99), "PM_HANDOFF_ROW_AUTHORITY_CONFLICT"),
    (lambda r: r["rows"][0].update(sportsbook="UnknownBook"), "PM_HANDOFF_ROW_AUTHORITY_CONFLICT"),
    (lambda r: r["rows"][0].update(capture_utc="2026-10-23T18:30:00"), "PM_HANDOFF_TIMESTAMP_INVALID"),
    (lambda r: r["rows"][0].update(display_line=float('nan')), "PM_HANDOFF_LINE_INVALID"),
    (lambda r: r["rows"][0].update(provider_player_id="../101"), "PM_HANDOFF_PROVIDER_ALIAS_INVALID"),
])
def test_unverified_authority_and_bad_identity_fail_closed(tamper, code):
    r = source()
    tamper(r)
    with pytest.raises(IntelligenceHandoffHold, match=code):
        build_shared_intelligence_handoff([r])


def test_duplicate_page_and_forged_duplicate_thesis_are_not_silent():
    r = source()
    with pytest.raises(IntelligenceHandoffHold, match="PM_HANDOFF_DUPLICATE_SOURCE_PAGE"):
        build_shared_intelligence_handoff([r, copy.deepcopy(r)])
    r["rows"].append(copy.deepcopy(r["rows"][0]))
    r["row_count"] = 2
    with pytest.raises(IntelligenceHandoffHold, match="PM_HANDOFF_DUPLICATE_THESIS"):
        build_shared_intelligence_handoff([r])


def test_does_not_mutate_input_receipt():
    r = source("MLB", "strikeouts")
    original = copy.deepcopy(r)
    build_shared_intelligence_handoff([r])
    assert r == original
