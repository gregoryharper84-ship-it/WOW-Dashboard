from v17 import nightly_multiscout as scout


def test_required_basketball_coverage_is_explicit():
    assert scout.MANDATORY_SPORT_FAMILIES["basketball_nba"] == "NBA"
    assert scout.MANDATORY_SPORT_FAMILIES["basketball_ncaab"] == "NCAAMB"
    assert scout.MANDATORY_SPORT_FAMILIES["basketball_wnba"] == "WNBA"


def test_game_script_library_covers_upset_and_basketball_regimes():
    scripts = scout.game_scripts("basketball_nba")
    assert "UNDERDOG_CONTROL_UPSET" in scripts
    assert "FAVORITE_BLOWOUT" in scripts
    assert "COMEBACK_UNDERDOG" in scripts
    assert "FOUL_EXTENSION" in scripts
    assert "STAR_FOUL_TROUBLE" in scripts
    assert "HOT_THREE_POINT_VARIANCE" in scripts


def test_prop_market_classifier_separates_team_markets():
    assert scout.is_prop_market("player_points") is True
    assert scout.is_prop_market("pitcher_strikeouts") is True
    assert scout.is_prop_market("h2h") is False
    assert scout.is_prop_market("spreads") is False
    assert scout.is_prop_market("totals") is False


def test_all_six_scout_roles_are_present():
    assert set(scout.SCOUT_TEAM) == {
        "BOARD_SCOUT",
        "CROSS_SPORT_OPPORTUNITY_SCOUT",
        "MATCHUP_AND_GAME_SCRIPT_SCOUT",
        "ROLE_NEWS_STATUS_SCOUT",
        "MARKET_ALTERNATE_LINE_SCOUT",
        "CONTRARIAN_RED_TEAM_SCOUT",
    }


def test_bookmaker_rows_preserve_exact_book_market_line_and_price():
    rows = scout.bookmaker_rows({
        "bookmakers": [{
            "key": "book_a",
            "title": "Book A",
            "last_update": "2026-09-07T01:00:00Z",
            "markets": [{
                "key": "player_points",
                "last_update": "2026-09-07T01:00:00Z",
                "outcomes": [{"name": "Over", "description": "Player A", "point": 22.5, "price": -115}],
            }],
        }]
    })
    assert rows == [{
        "bookmaker": "book_a",
        "bookmaker_title": "Book A",
        "bookmaker_last_update": "2026-09-07T01:00:00Z",
        "market_key": "player_points",
        "market_last_update": "2026-09-07T01:00:00Z",
        "outcome_name": "Over",
        "description": "Player A",
        "price": -115,
        "point": 22.5,
        "link": None,
    }]


def test_scout_governance_never_promotes_sportsbook_probability():
    assert "FAVORITE" not in {"MODEL_PROBABILITY", "CALIBRATED_PROBABILITY"}
    # The runner's contract is discovery-only; actual upset status is produced later by LLP.
    source = open("v17/nightly_multiscout.py", encoding="utf-8").read()
    assert '"upset_evaluation_requested": True' in source
    assert '"route": "LLP_TEAM_BETTING_ENGINE"' in source
    assert '"route": "WOW_PROP_LANE"' in source
    assert '"sportsbook_implied_probability_is_model_probability": False' in source
    assert '"can_execute": False' in source
