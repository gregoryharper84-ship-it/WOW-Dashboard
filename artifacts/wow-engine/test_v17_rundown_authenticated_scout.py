from v17 import rundown_authenticated_scout as rundown


def test_market_batches_never_exceed_provider_limit():
    batches = rundown._chunked(list(range(1, 38)), rundown.MAX_MARKETS_PER_REQUEST)
    assert rundown.MAX_MARKETS_PER_REQUEST <= 12
    assert max(len(batch) for batch in batches) <= 12
    assert [item for batch in batches for item in batch] == list(range(1, 38))


def test_known_sports_preserve_existing_specialist_keys_and_politics_is_explicitly_excluded():
    assert rundown._sport_key("NCAA Football", 1) == "americanfootball_ncaaf"
    assert rundown._sport_key("NFL", 2) == "americanfootball_nfl"
    assert rundown._sport_key("MLB", 3) == "baseball_mlb"
    assert rundown._sport_key("NBA", 4) == "basketball_nba"
    assert rundown._sport_key("NCAA Men's Basketball", 5) == "basketball_ncaab"
    assert rundown._sport_key("NHL", 6) == "icehockey_nhl"
    assert rundown._sport_key("WNBA", 8) == "basketball_wnba"
    assert "Politics" in rundown.NON_SPORT_NAMES


def test_available_market_inventory_excludes_live_variants():
    catalog = {
        1: {"id": 1, "live": False},
        51: {"id": 51, "live": False},
        99: {"id": 99, "live": True},
    }
    payload = {"3": [{"id": 99}, {"id": 51}, {"id": 1}, {"id": 51}]}
    assert rundown._available_ids(payload, 3, catalog) == [1, 51]


def test_price_normalization_keeps_only_main_usable_lines_and_prop_metadata():
    catalog = {
        51: {
            "id": 51,
            "name": "passing_yards",
            "class": "prop",
            "family": "player_ou",
            "scope": "player",
        }
    }
    event = {
        "teams": [],
        "markets": [{
            "id": 9001,
            "market_id": 51,
            "participants": [{
                "id": 7001,
                "name": "Player A",
                "lines": [{
                    "value": 275.5,
                    "prices": {
                        "3": {"id": "p1", "is_main_line": True, "price": -110, "updated_at": "2026-09-18T15:00:00Z"},
                        "19": {"id": "p2", "is_main_line": False, "price": -105, "updated_at": "2026-09-18T15:00:00Z"},
                        "22": {"id": "p3", "is_main_line": True, "price": 0.0001, "updated_at": "2026-09-18T15:00:00Z"},
                    },
                }],
            }],
        }],
    }
    rows = rundown._price_rows(event, catalog, {"3": "Book A", "19": "Book B", "22": "Book C"})
    assert len(rows) == 1
    row = rows[0]
    assert row["bookmaker"] == "Book A"
    assert row["market_key"] == "passing_yards"
    assert row["description"] == "Player A"
    assert row["point"] == 275.5
    assert row["price"] == -110
    assert row["is_prop"] is True
    assert row["prediction_authority"] is False
    assert row["research_only"] is True
    assert row["can_execute"] is False


def test_team_identity_and_core_market_mapping_are_preserved():
    catalog = {1: {"id": 1, "name": "moneyline", "class": "core", "family": "moneyline", "scope": "event"}}
    event = {
        "teams": [
            {"team_id": 10, "name": "Away", "is_away": True, "is_home": False},
            {"team_id": 11, "name": "Home", "is_home": True, "is_away": False},
        ],
        "markets": [{
            "id": 8001,
            "market_id": 1,
            "participants": [{
                "id": 11,
                "name": "Home",
                "lines": [{"prices": {"3": {"id": "m1", "is_main_line": True, "price": -135, "updated_at": "2026-09-18T15:00:00Z"}}}],
            }],
        }],
    }
    home, away, names = rundown._team_names(event)
    assert (home, away) == ("Home", "Away")
    assert names == {10: "Away", 11: "Home"}
    rows = rundown._price_rows(event, catalog, {"3": "Book A"})
    assert rows[0]["market_key"] == "h2h"
    assert rows[0]["is_prop"] is False


def test_source_contains_only_governed_handoff_routes():
    source = open("v17/rundown_authenticated_scout.py", encoding="utf-8").read()
    assert '"route": "WOW_PROP_LANE"' in source
    assert "_team_event_candidate" in source
    assert '"can_execute": False' in source
    assert '"prediction_authority": False' in source
    assert 'RESEARCH_CEILING = "RESEARCH_INTEREST"' in source
