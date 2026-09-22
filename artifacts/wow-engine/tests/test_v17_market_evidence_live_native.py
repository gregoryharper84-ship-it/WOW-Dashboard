"""Regression fixtures pinned from the credentialed 2026-09-14 provider probes."""
from __future__ import annotations

from v17 import market_evidence_native_live as live


def test_sharpapi_observed_field_names_translate_without_provider_probability():
    rows = [
        {
            "event_id": "sharp-live-1",
            "event_start_time": "2026-09-14T23:05:00Z",
            "home_team": "Chicago Cubs",
            "away_team": "Milwaukee Brewers",
            "league": "MLB",
            "market_type": "moneyline",
            "selection": "Chicago Cubs",
            "sportsbook": "draftkings",
            "odds_american": -135,
            "odds_decimal": 1.74,
            "odds_probability": 0.574,
            "timestamp": "2026-09-14T19:00:00Z",
        },
        {
            "event_id": "sharp-live-1",
            "event_start_time": "2026-09-14T23:05:00Z",
            "home_team": "Chicago Cubs",
            "away_team": "Milwaukee Brewers",
            "league": "MLB",
            "market_type": "moneyline",
            "selection": "Milwaukee Brewers",
            "sportsbook": "draftkings",
            "odds_american": 115,
            "odds_probability": 0.465,
            "timestamp": "2026-09-14T19:00:00Z",
        },
    ]
    events = live.sharpapi_rows_to_odds_api_v4(rows, sport_key="baseball_mlb")
    assert len(events) == 1
    event = events[0]
    assert event["commence_time"] == "2026-09-14T23:05:00Z"
    outcomes = event["bookmakers"][0]["markets"][0]["outcomes"]
    assert {(o["name"], o["price"]) for o in outcomes} == {
        ("Chicago Cubs", -135),
        ("Milwaukee Brewers", 115),
    }
    dumped = repr(event)
    assert "odds_probability" not in dumped
    assert "0.574" not in dumped


def test_sharpapi_native_prop_preserves_player_identity_and_distinct_same_line_players():
    base = {
        "event_id": "sharp-prop-1",
        "event_start_time": "2026-09-14T23:05:00Z",
        "home_team": "Golden State Warriors",
        "away_team": "Los Angeles Lakers",
        "league": "NBA",
        "market_type": "player_points",
        "selection": "Over",
        "sportsbook": "pinnacle",
        "odds_american": -110,
        "line": 24.5,
        "timestamp": "2026-09-14T19:00:00Z",
    }
    rows = [
        {**base, "player_name": "Stephen Curry"},
        {**base, "player_name": "Jimmy Butler"},
    ]
    events = live.sharpapi_rows_to_odds_api_v4(rows, sport_key="basketball_nba")
    assert len(events) == 1
    outcomes = events[0]["bookmakers"][0]["markets"][0]["outcomes"]
    assert {(o["name"], o["description"], o["point"]) for o in outcomes} == {
        ("Over", "Stephen Curry", 24.5),
        ("Over", "Jimmy Butler", 24.5),
    }


def test_sharpapi_native_prop_fails_closed_without_player_or_exact_line():
    base = {
        "event_id": "sharp-prop-2",
        "event_start_time": "2026-09-14T23:05:00Z",
        "home_team": "Golden State Warriors",
        "away_team": "Los Angeles Lakers",
        "league": "NBA",
        "market_type": "player_points",
        "selection": "Over",
        "sportsbook": "pinnacle",
        "odds_american": -110,
        "line": 24.5,
        "player_name": "Stephen Curry",
        "timestamp": "2026-09-14T19:00:00Z",
    }
    assert live.sharpapi_rows_to_odds_api_v4([{**base, "player_name": None}], sport_key="basketball_nba") == []
    assert live.sharpapi_rows_to_odds_api_v4([{**base, "line": None}], sport_key="basketball_nba") == []


def _rundown_live_event():
    return {
        "event_id": "rd-live-1",
        "event_date": "2026-09-14T23:05:00Z",
        "teams_normalized": [
            {"name": "Chicago Cubs", "is_home": True, "is_away": False},
            {"name": "Milwaukee Brewers", "is_home": False, "is_away": True},
        ],
        "markets": [
            {
                "market_id": 1,
                "name": "moneyline",
                "participants": [
                    {
                        "id": 101,
                        "name": "Chicago Cubs",
                        "type": "home",
                        "lines": [
                            {"id": "ml-h", "value": 0, "prices": {
                                "3": {"affiliate_name": "Pinnacle", "price": -135, "updated_at": "2026-09-14T19:00:00Z"},
                                "19": {"affiliate_name": "DraftKings", "price": -132},
                            }}
                        ],
                    },
                    {
                        "id": 102,
                        "name": "Milwaukee Brewers",
                        "type": "away",
                        "lines": [
                            {"id": "ml-a", "value": 0, "prices": {
                                "3": {"affiliate_name": "Pinnacle", "price": 115},
                                "19": {"affiliate_name": "DraftKings", "price": 112},
                            }}
                        ],
                    },
                ],
            },
            {
                "market_id": 2,
                "name": "spread",
                "participants": [
                    {"name": "Chicago Cubs", "type": "home", "lines": [
                        {"value": -1.5, "prices": {"3": {"affiliate_name": "Pinnacle", "price": 140}}}
                    ]},
                    {"name": "Milwaukee Brewers", "type": "away", "lines": [
                        {"value": 1.5, "prices": {"3": {"affiliate_name": "Pinnacle", "price": -165}}}
                    ]},
                ],
            },
            {
                "market_id": 3,
                "name": "total",
                "participants": [
                    {"name": "Over", "type": "over", "lines": [
                        {"selection": "over", "value": 8.5, "prices": {"3": {"affiliate_name": "Pinnacle", "price": -105}}}
                    ]},
                    {"name": "Under", "type": "under", "lines": [
                        {"selection": "under", "value": 8.5, "prices": {"3": {"affiliate_name": "Pinnacle", "price": -115}}}
                    ]},
                ],
            },
        ],
    }


def test_rundown_observed_participant_line_nesting_translates():
    event = live.rundown_v2_event_to_odds_api_v4(_rundown_live_event(), sport_key="baseball_mlb")
    assert event is not None
    assert event["home_team"] == "Chicago Cubs"
    assert event["away_team"] == "Milwaukee Brewers"
    books = {book["title"]: book for book in event["bookmakers"]}
    assert set(books) == {"Pinnacle", "DraftKings"}
    pinnacle = {market["key"]: market for market in books["Pinnacle"]["markets"]}
    assert set(pinnacle) == {"h2h", "spreads", "totals"}
    assert {(o["name"], o["price"]) for o in pinnacle["h2h"]["outcomes"]} == {
        ("Chicago Cubs", -135),
        ("Milwaukee Brewers", 115),
    }
    assert {(o["name"], o["point"]) for o in pinnacle["totals"]["outcomes"]} == {
        ("Over", 8.5),
        ("Under", 8.5),
    }


def test_rundown_off_board_sentinel_is_not_a_price():
    event = _rundown_live_event()
    event["markets"][0]["participants"][0]["lines"][0]["prices"] = {
        "3": {"affiliate_name": "Pinnacle", "price": 0.0001}
    }
    built = live.rundown_v2_event_to_odds_api_v4(event, sport_key="baseball_mlb")
    assert built is not None
    books = {book["title"]: book for book in built["bookmakers"]}
    pinnacle_h2h = [m for m in books["Pinnacle"]["markets"] if m["key"] == "h2h"][0]
    assert all(o["name"] != "Chicago Cubs" for o in pinnacle_h2h["outcomes"])
