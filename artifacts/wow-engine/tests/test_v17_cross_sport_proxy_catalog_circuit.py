from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17 import cross_sport_discovery_feed as feed
from v17 import cross_sport_winner_discovery as discovery


def test_failed_proxy_catalog_is_memoized_for_feed_lifetime():
    calls: list[tuple[str, dict[str, str]]] = []

    def proxy_get(path, params=None):
        calls.append((path, dict(params or {})))
        return SimpleNamespace(ok=False, code="HTTP_429", data=None)

    fetch = feed.odds_proxy_feed(proxy_get=proxy_get)

    for family in ("MLB", "NFL", "NBA", "WNBA"):
        with pytest.raises(discovery.DiscoveryFeedError) as exc_info:
            fetch(family)
        assert exc_info.value.code == "HTTP_429"

    assert calls == [("/odds-api/v4/sports", {"all": "true"})]


def test_catalog_circuit_does_not_block_independent_union_feed():
    primary_calls = 0
    secondary_calls: list[str] = []

    def proxy_get(path, params=None):
        nonlocal primary_calls
        primary_calls += 1
        return SimpleNamespace(ok=False, code="ODDS_PROVIDER_NON_JSON", data=None)

    primary = feed.odds_proxy_feed(proxy_get=proxy_get)

    def secondary(family, target=None):
        secondary_calls.append(family)
        return [
            {
                "id": f"secondary-{family.lower()}",
                "home_team": f"{family} Home",
                "away_team": f"{family} Away",
                "commence_time": "2026-09-22T20:00:00Z",
            }
        ]

    combined = feed.union_feed(primary, secondary)

    mlb_rows = combined("MLB")
    nfl_rows = combined("NFL")

    assert primary_calls == 1
    assert secondary_calls == ["MLB", "NFL"]
    assert [row["id"] for row in mlb_rows] == ["secondary-mlb"]
    assert [row["id"] for row in nfl_rows] == ["secondary-nfl"]
