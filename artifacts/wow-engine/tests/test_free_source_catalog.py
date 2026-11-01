"""Offline zero-cost source catalog regression; no credentials or network."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.free_source_catalog import SOURCES, CAN_EXECUTE, compile_free_source_plan


def test_propsmadness_is_present_for_all_four_sports_without_training_authority():
    for sport in ("NFL", "NBA", "MLB", "WNBA"):
        discovery = compile_free_source_plan(sport, "DISCOVERY")
        research = compile_free_source_plan(sport, "HISTORICAL_RESEARCH")
        assert "PROPSMADNESS_PUBLIC_EXPLORE" in {s["source_id"] for s in discovery["sources"]}
        assert [s["source_id"] for s in research["sources"]] == ["PROPSMADNESS_ACCOUNT_VIEW"]
        account = research["sources"][0]
        assert account["training_authorized_by_catalog"] is False
        assert account["source_rights_independently_attested"] is False
        assert account["historical_chart_threshold_is_closing_line"] is False
        assert account["exact_historical_market_line_verified"] is False
        assert account["recurring_authenticated_collection_authorized"] is False
        assert account["historical_storage_authorized"] is False
        assert research["model_consumption_receipts"] == []
        assert research["probability_publishable"] is False


def test_only_nfl_account_layout_has_real_page_observation():
    for sport in ("NFL", "NBA", "MLB", "WNBA"):
        route = compile_free_source_plan(sport, "HISTORICAL_RESEARCH")["sources"][0]
        assert route["account_view_validation"] == "NFL_VIEW_OBSERVED_NBA_MLB_WNBA_LAYOUTS_SYNTHETIC"


def test_existing_free_core_routes_are_not_rebuilt_or_erased():
    assert "NFLVERSE" in {x["source_id"] for x in compile_free_source_plan("NFL", "HISTORY")["sources"]}
    assert "SPORTSDATAVERSE_ESPN" in {x["source_id"] for x in compile_free_source_plan("NBA", "HISTORY")["sources"]}
    assert "SPORTSDATAVERSE_WNBA_STATS" in {x["source_id"] for x in compile_free_source_plan("WNBA", "HISTORY")["sources"]}
    assert "CFBD" in {x["source_id"] for x in compile_free_source_plan("NCAAF", "HISTORY")["sources"]}
    assert "MLB_STATS_API" in {x["source_id"] for x in compile_free_source_plan("MLB", "HISTORY")["sources"]}


def test_market_data_never_promotes_sporting_probability_or_claims_oddspapi_coverage():
    out = compile_free_source_plan("NFL", "MARKET")
    assert {s["source_id"] for s in out["sources"]} == {"THE_ODDS_API", "THERUNDOWN", "ODDSPAPI"}
    experimental = next(s for s in out["sources"] if s["source_id"] == "ODDSPAPI")
    assert experimental["integration"] == "UNVERIFIED_EXPERIMENT"
    assert experimental["exact_historical_market_line_verified"] is False
    assert all(s["pregame_model_feature_eligible"] is False for s in out["sources"])
    assert out["model_probability"] is None and out["calibrated_lower_bound"] is None


def test_registry_candidate_readiness_does_not_certify_observation_or_feature():
    out = compile_free_source_plan("NBA", "HISTORY")
    s = next(x for x in out["sources"] if x["source_id"] == "SPORTSDATAVERSE_ESPN")
    assert s["registry_source_id"] == "SPORTSDATAVERSE_ESPN"
    assert s["training_authorized_by_catalog"] is False
    assert s["official_identity_verified"] is False
    assert s["pregame_model_feature_eligible"] is False


@pytest.mark.parametrize("sport,purpose", [("XYZ", "HISTORY"), ("NFL", "TRAINING"), ("nfl", "MARKET")])
def test_unknown_scope_fails_closed(sport, purpose):
    with pytest.raises(ValueError):
        compile_free_source_plan(sport, purpose)


def test_sources_are_unique_non_executable_and_safely_audited():
    assert CAN_EXECUTE is False
    assert len({s.source_id for s in SOURCES}) == len(SOURCES)
    for s in SOURCES:
        for sport in s.sports:
            for purpose in s.purposes:
                plan = compile_free_source_plan(sport, purpose)
                assert plan["can_execute"] is False
                assert plan["status"] == "SOURCE_INVENTORY_ONLY"
                assert plan["source_count"] == len(plan["sources"])
                assert plan["model_consumption_receipts"] == []
                for route in plan["sources"]:
                    assert route["can_execute"] is False
                    assert route["probability_publishable"] is False

def test_ncaaf_sdv_research_archive_has_no_model_or_live_dataset_authority():
    plan = compile_free_source_plan("NCAAF", "HISTORY")
    rows = [r for r in plan["sources"] if r["source_id"] == "SPORTSDATAVERSE_NCAA_MFB_PLAYER_STATS"]
    assert len(rows) == 1
    source = rows[0]
    assert source["integration"] == "SOURCE_ARCHIVE_RESEARCH_CANARY"
    assert source["registry_candidate_training_ready"] is True
    assert source["training_authorized_by_catalog"] is False
    assert source["source_rights_independently_attested"] is False
    assert source["pregame_model_feature_eligible"] is False
    assert source["official_identity_verified"] is False
    assert source["can_execute"] is False

def test_espn_player_box_remains_research_not_canonical_cfbd_source():
    rows = compile_free_source_plan("NCAAF", "HISTORY")["sources"]
    espn = next(s for s in rows
                if s["source_id"] == "SPORTSDATAVERSE_ESPN_CFB_PLAYER_BOX")
    assert espn["integration"] == "SOURCE_ARCHIVE_CROSSWALK_RESEARCH"
    assert espn["registry_source_id"] is None
    assert espn["training_authorized_by_catalog"] is False
    assert espn["official_identity_verified"] is False
    assert espn["pregame_model_feature_eligible"] is False
    assert espn["model_probability"] is None
    assert espn["can_execute"] is False
