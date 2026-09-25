from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17 import cross_sport_discovery_feed as feed
from v17 import cross_sport_resilience_overlay as resilience
from v17 import cross_sport_winner_discovery as discovery
from v17 import scout_secondary_source as secondary


class _Result:
    def __init__(self, ok: bool, *, code: str | None = None, data=None):
        self.ok = ok
        self.code = code
        self.data = data


def test_odds_api_enabled_by_default_preserves_event_discovery(monkeypatch):
    monkeypatch.delenv("WOW_CROSS_SPORT_ODDS_API_ENABLED", raising=False)
    calls: list[str] = []

    def proxy_get(path, _params=None):
        calls.append(path)
        return _Result(
            True,
            data=[{
                "id": "odds-soccer-1",
                "home_team": "Home",
                "away_team": "Away",
                "commence_time": "2026-09-25T23:00:00Z",
            }],
        )

    fetch = feed.odds_proxy_feed(
        proxy_get=proxy_get,
        now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        sport_keys={"SOCCER": ("soccer_usa_mls",)},
    )

    rows = fetch("SOCCER")

    assert calls == ["/odds-api/v4/sports/soccer_usa_mls/events"]
    assert rows[0]["provider_event_id"] == "odds-soccer-1"
    assert rows[0]["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert "official_event_id" not in rows[0]
    assert rows[0]["can_execute"] is False


def test_odds_api_disabled_makes_zero_proxy_calls(monkeypatch):
    monkeypatch.setenv("WOW_CROSS_SPORT_ODDS_API_ENABLED", "false")
    calls: list[str] = []

    def proxy_get(path, _params=None):
        calls.append(path)
        raise AssertionError("Odds proxy must not be called while discovery is disabled")

    fetch = feed.odds_proxy_feed(proxy_get=proxy_get)

    with pytest.raises(discovery.DiscoveryFeedError) as exc:
        fetch("SOCCER")

    assert exc.value.code == feed.ODDS_API_DISCOVERY_DISABLED
    assert calls == []


def test_union_continues_to_non_odds_feed_when_odds_api_disabled(monkeypatch):
    monkeypatch.setenv("WOW_CROSS_SPORT_ODDS_API_ENABLED", "0")
    proxy_calls: list[str] = []
    fallback_calls: list[str] = []

    def proxy_get(path, _params=None):
        proxy_calls.append(path)
        raise AssertionError("disabled Odds API must not make a proxy request")

    def fallback(family, _target=None):
        fallback_calls.append(family)
        return [{
            "id": "rundown-1",
            "home_team": "Home",
            "away_team": "Away",
            "commence_time": "2026-09-25T23:00:00Z",
        }]

    union = feed.union_feed(feed.odds_proxy_feed(proxy_get=proxy_get), fallback)
    rows = union("SOCCER")

    assert proxy_calls == []
    assert fallback_calls == ["SOCCER"]
    assert rows[0]["id"] == "rundown-1"


def test_schedule_first_team_sport_runs_with_odds_api_disabled(monkeypatch):
    monkeypatch.setenv("WOW_CROSS_SPORT_ODDS_API_ENABLED", "false")
    proxy_calls: list[str] = []
    espn_calls: list[str] = []

    def proxy_get(path, _params=None):
        proxy_calls.append(path)
        raise AssertionError("disabled Odds API must not make a proxy request")

    def fake_secondary(path, params, event_context, *, primary_failure=None):
        espn_calls.append(path)
        return secondary.SecondaryResult(
            True,
            [{
                "id": "espn-nfl-1",
                "sport_key": "americanfootball_nfl",
                "commence_time": "2026-09-25T23:00:00Z",
                "home_team": "Home",
                "away_team": "Away",
            }],
            200,
        )

    monkeypatch.setattr(secondary, "secondary_for_request", fake_secondary)
    odds_fetch = feed.odds_proxy_feed(proxy_get=proxy_get)
    resilient = resilience._schedule_first_fetch(
        odds_fetch,
        started=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        horizon_hours=36,
    )

    rows = resilient("NFL")

    assert proxy_calls == []
    assert len(espn_calls) == 1
    assert rows[0]["discovery_provider"] == "ESPN_SCOREBOARD"
    assert rows[0]["prediction_authority"] is False
    assert rows[0]["can_execute"] is False


def test_total_feed_failure_remains_typed_when_odds_api_disabled(monkeypatch):
    monkeypatch.setenv("WOW_CROSS_SPORT_ODDS_API_ENABLED", "false")

    def other_failed(_family, _target=None):
        raise discovery.DiscoveryFeedError("RUNDOWN_QUOTA_EXHAUSTED")

    union = feed.union_feed(feed.odds_proxy_feed(proxy_get=lambda *_args: None), other_failed)

    with pytest.raises(discovery.DiscoveryFeedError) as exc:
        union("SOCCER")

    assert exc.value.code == feed.ODDS_API_DISCOVERY_DISABLED
