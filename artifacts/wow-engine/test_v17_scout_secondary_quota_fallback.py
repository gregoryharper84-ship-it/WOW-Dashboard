from __future__ import annotations

import os

from v17 import nightly_multiscout as scout
from v17 import nightly_multiscout_oidc as scout_oidc
from v17.scout_secondary_source import SecondaryResult


def test_provider_quota_and_entitlement_are_secondary_eligible():
    assert scout_oidc._eligible_for_secondary(scout.FetchResult(False, status=429, code="ODDS_PROVIDER_NON_JSON")) is True
    assert scout_oidc._eligible_for_secondary(scout.FetchResult(False, status=401, code="ODDS_API_UPSTREAM_ERROR")) is True
    assert scout_oidc._eligible_for_secondary(scout.FetchResult(False, status=403, code="ODDS_API_FEATURED_ODDS_FALLBACK_ERROR")) is True


def test_caller_auth_failures_never_enter_secondary_fallback():
    assert scout_oidc._eligible_for_secondary(scout.FetchResult(False, status=401, code="ODDS_ROUTER_AUTH_INVALID")) is False
    assert scout_oidc._eligible_for_secondary(scout.FetchResult(False, status=401, code="ODDS_PROXY_AUTH_REQUIRED")) is False
    assert scout_oidc._eligible_for_secondary(scout.FetchResult(False, status=401, code="GITHUB_ACTIONS_OIDC_INVALID")) is False


def test_live_wrapper_recovers_sports_inventory_from_secondary_after_primary_429(monkeypatch):
    monkeypatch.delenv("WOW_ODDS_PROXY_ACTION_KEY", raising=False)
    monkeypatch.delenv("WOW_GITHUB_OIDC_TOKEN", raising=False)

    def primary(path, params=None):
        assert path == "/odds-api/v4/sports"
        return scout.FetchResult(False, status=429, code="ODDS_PROVIDER_NON_JSON")

    expected = [{"key": "baseball_mlb", "title": "MLB", "active": True, "_wow_secondary_source": True}]
    seen = []

    def secondary(path, params, event_context, *, primary_failure=None):
        seen.append((path, primary_failure))
        return SecondaryResult(True, expected, 200, code="SECONDARY_SOURCE_USED")

    monkeypatch.setattr(scout_oidc.scout, "proxy_get", primary)
    monkeypatch.setattr(scout_oidc, "mint_github_actions_oidc", lambda: "fresh-oidc")
    monkeypatch.setattr(scout_oidc, "secondary_for_request", secondary)
    scout_oidc.install_refreshable_oidc_proxy_auth()

    result = scout_oidc.scout.proxy_get("/odds-api/v4/sports", {"all": "true"})
    assert result.ok is True
    assert result.data == expected
    assert result.code == "SECONDARY_SOURCE_USED"
    assert seen == [("/odds-api/v4/sports", "ODDS_PROVIDER_NON_JSON:HTTP_429")]
    assert os.environ["WOW_GITHUB_OIDC_TOKEN"] == "fresh-oidc"


def test_live_wrapper_retains_secondary_scoreboard_dates_for_event_market_lookup(monkeypatch):
    monkeypatch.delenv("WOW_ODDS_PROXY_ACTION_KEY", raising=False)
    monkeypatch.delenv("WOW_GITHUB_OIDC_TOKEN", raising=False)

    def primary(path, params=None):
        return scout.FetchResult(False, status=429, code="ODDS_PROVIDER_NON_JSON")

    seen_context = {}

    def secondary(path, params, event_context, *, primary_failure=None):
        if path.endswith("/events"):
            return SecondaryResult(
                True,
                [{
                    "id": "espn-401908014",
                    "home_team": "Los Angeles Dodgers",
                    "away_team": "Atlanta Braves",
                    "commence_time": "2026-10-05T00:00:00Z",
                    "_wow_secondary_scoreboard_dates": ["20261004"],
                }],
                200,
                code="SECONDARY_SOURCE_USED",
            )
        seen_context.update(event_context)
        return SecondaryResult(
            True,
            {
                "id": "espn-401908014",
                "bookmakers": [],
                "_wow_secondary_source": {
                    "provider": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
                    "prediction_authority": False,
                    "exact_line_authority": False,
                    "can_execute": False,
                },
            },
            200,
            code="SECONDARY_SOURCE_USED",
        )

    monkeypatch.setattr(scout_oidc.scout, "proxy_get", primary)
    monkeypatch.setattr(scout_oidc, "mint_github_actions_oidc", lambda: "fresh-oidc")
    monkeypatch.setattr(scout_oidc, "secondary_for_request", secondary)
    scout_oidc.install_refreshable_oidc_proxy_auth()

    events = scout_oidc.scout.proxy_get(
        "/odds-api/v4/sports/baseball_mlb/events",
        {
            "commenceTimeFrom": "2026-10-04T13:22:56Z",
            "commenceTimeTo": "2026-10-05T01:22:56Z",
        },
    )
    assert events.ok is True

    market = scout_oidc.scout.proxy_get(
        "/odds-api/v4/sports/baseball_mlb/events/espn-401908014/odds",
        {"markets": "h2h"},
    )
    assert market.ok is True
    assert seen_context["espn-401908014"]["_wow_secondary_scoreboard_dates"] == ["20261004"]
