from v17.scout_secondary_source import espn_event_to_primary_shape, espn_h2h_payload


def _event():
    return {
        "id": "401234567",
        "date": "2026-09-12T19:00:00Z",
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"displayName": "Texas Tech Red Raiders"}},
                {"homeAway": "away", "team": {"displayName": "Oregon State Beavers"}},
            ],
            "odds": [{
                "provider": {"name": "ESPN BET"},
                "homeTeamOdds": {"moneyLine": -240},
                "awayTeamOdds": {"moneyLine": 195},
                "lastUpdated": "2026-09-12T17:10:00Z",
            }],
        }],
    }


def test_event_translation_preserves_identity_without_probability():
    row = espn_event_to_primary_shape(_event(), "americanfootball_ncaaf")
    assert row["id"] == "espn-401234567"
    assert row["home_team"] == "Texas Tech Red Raiders"
    assert row["away_team"] == "Oregon State Beavers"
    assert "probability" not in row


def test_h2h_fallback_is_research_only_and_non_authoritative():
    payload = espn_h2h_payload(_event(), primary_failure="ODDS_API_FEATURED_ODDS_FALLBACK_ERROR:HTTP_401")
    marker = payload["_wow_secondary_source"]
    assert marker["provider"] == "ESPN_SCOREBOARD_RESEARCH_FALLBACK"
    assert marker["source_tier"] == "SECONDARY_LIVE_RESEARCH"
    assert marker["prediction_authority"] is False
    assert marker["exact_line_authority"] is False
    assert marker["research_only"] is True
    assert marker["can_execute"] is False
    assert marker["primary_source_failure"].endswith("HTTP_401")

    markets = payload["bookmakers"][0]["markets"]
    assert [m["key"] for m in markets] == ["h2h"]
    outcomes = markets[0]["outcomes"]
    assert outcomes == [
        {"name": "Texas Tech Red Raiders", "price": -240},
        {"name": "Oregon State Beavers", "price": 195},
    ]
    assert all("probability" not in row for row in outcomes)


def test_missing_moneyline_fails_closed_instead_of_fabricating_market():
    event = _event()
    event["competitions"][0]["odds"][0]["homeTeamOdds"].pop("moneyLine")
    assert espn_h2h_payload(event, primary_failure="HTTP_401") is None
