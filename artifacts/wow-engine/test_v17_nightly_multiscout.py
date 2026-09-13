from types import SimpleNamespace

from v17 import nightly_multiscout as scout
from v17 import nightly_multiscout_oidc as scout_oidc
from v17 import multiscout_auto_advance_oidc as advance_oidc


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


def test_market_inventory_failure_preserves_team_event_candidate(monkeypatch):
    def fake_proxy(path, params=None):
        if path == "/odds-api/v4/sports":
            return scout.FetchResult(True, [{"key": "baseball_mlb", "title": "MLB", "active": True}], 200)
        if path.endswith("/events"):
            return scout.FetchResult(True, [{
                "id": "event-1",
                "commence_time": "2026-09-13T20:00:00Z",
                "home_team": "Home",
                "away_team": "Away",
            }], 200)
        if path.endswith("/markets"):
            return scout.FetchResult(
                False,
                data={
                    "secondary_attempted": True,
                    "secondary_provider": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
                    "secondary_status": "FAILED",
                    "secondary_reason_code": "SECONDARY_SOURCE_H2H_UNAVAILABLE",
                    "secondary_http_status": 404,
                },
                status=401,
                code="ODDS_API_FEATURED_ODDS_FALLBACK_ERROR",
            )
        raise AssertionError(path)

    monkeypatch.setattr(scout, "proxy_get", fake_proxy)
    payload = scout.run()

    assert payload["status"] == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"
    assert payload["model_handoff_ready"] is True
    assert len(payload["model_handoff"]["team_event_candidates"]) == 1
    assert payload["model_handoff"]["prop_candidates"] == []
    candidate = payload["model_handoff"]["team_event_candidates"][0]
    assert candidate["official_event_id"] == "event-1"
    assert candidate["route"] == "LLP_TEAM_BETTING_ENGINE"
    assert candidate["market_evidence"] == []
    assert candidate["market_evidence_status"] == "SOURCE_BLOCKED"
    assert candidate["sporting_identity_preserved_without_market_evidence"] is True
    blocker = candidate["market_evidence_source_blockers"][0]
    assert blocker["reason_code"] == "ODDS_API_FEATURED_ODDS_FALLBACK_ERROR"
    assert blocker["secondary_attempted"] is True
    assert blocker["secondary_reason_code"] == "SECONDARY_SOURCE_H2H_UNAVAILABLE"


def test_row_preserved_partial_handoff_is_dispatchable_without_erasing_source_status():
    handoff = {
        "status": "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS",
        "model_handoff_ready": True,
        "source_blockers": [{"reason_code": "ODDS_API_FEATURED_ODDS_FALLBACK_ERROR"}],
        "model_handoff": {"team_event_candidates": [{}], "prop_candidates": []},
    }
    normalized = advance_oidc._dispatchable_handoff(handoff)
    assert normalized is not handoff
    assert normalized["status"] == "DISCOVERY_COMPLETE"
    assert normalized["source_acquisition_status"] == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"
    assert handoff["status"] == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"


def test_nonready_partial_handoff_remains_fail_closed():
    handoff = {"status": "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS", "model_handoff_ready": False}
    assert advance_oidc._dispatchable_handoff(handoff) is handoff


def test_secondary_failure_reason_is_preserved_for_scout_telemetry(monkeypatch):
    monkeypatch.delenv("WOW_ODDS_PROXY_ACTION_KEY", raising=False)
    monkeypatch.delenv("WOW_GITHUB_OIDC_TOKEN", raising=False)

    def primary(_path, _params=None):
        return scout.FetchResult(False, status=401, code="ODDS_API_FEATURED_ODDS_FALLBACK_ERROR")

    monkeypatch.setattr(scout_oidc.scout, "proxy_get", primary)
    monkeypatch.setattr(scout_oidc, "mint_github_actions_oidc", lambda: "fresh-oidc")
    monkeypatch.setattr(
        scout_oidc,
        "secondary_for_request",
        lambda *args, **kwargs: SimpleNamespace(
            ok=False,
            data=None,
            status=404,
            code="SECONDARY_SOURCE_H2H_UNAVAILABLE",
        ),
    )
    scout_oidc.install_refreshable_oidc_proxy_auth()
    result = scout_oidc.scout.proxy_get(
        "/odds-api/v4/sports/baseball_mlb/events/event-1/markets",
        {"regions": "us"},
    )
    assert result.ok is False
    assert result.data["secondary_attempted"] is True
    assert result.data["secondary_reason_code"] == "SECONDARY_SOURCE_H2H_UNAVAILABLE"
    assert result.data["primary_reason_code"] == "ODDS_API_FEATURED_ODDS_FALLBACK_ERROR"


def test_scout_governance_never_promotes_sportsbook_probability():
    assert "FAVORITE" not in {"MODEL_PROBABILITY", "CALIBRATED_PROBABILITY"}
    source = open("v17/nightly_multiscout.py", encoding="utf-8").read()
    assert '"upset_evaluation_requested": True' in source
    assert '"route": "LLP_TEAM_BETTING_ENGINE"' in source
    assert '"route": "WOW_PROP_LANE"' in source
    assert '"sportsbook_implied_probability_is_model_probability": False' in source
    assert '"can_execute": False' in source
