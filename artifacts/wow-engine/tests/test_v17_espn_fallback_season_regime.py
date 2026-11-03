"""Regression: ESPN scoreboard fallback must never lend postseason games
regular-season fitted probability authority (P0 #1562).
"""
from types import SimpleNamespace

import pytest

from v17 import cross_sport_winner_discovery as discovery
from v17 import rundown_sport_registry as registry
from v17 import scout_secondary_source as secondary


def _espn_event(season):
    return {
        "id": "849831",
        "date": "2026-10-11T00:00:00Z",
        "season": season,
        "competitions": [
            {
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"id": "114", "displayName": "Cleveland Guardians"},
                    },
                    {
                        "homeAway": "away",
                        "team": {"id": "4", "displayName": "Chicago White Sox"},
                    },
                ],
            }
        ],
    }


def _discovered(espn_season):
    row = secondary.espn_event_to_primary_shape(_espn_event(espn_season), "baseball_mlb")
    assert row is not None
    return discovery.normalize_discovered_event(
        row,
        sport="MLB",
        sport_key="3",
        source="DISCOVERY_FEED",
        target=discovery.DiscoveryTarget(
            family="MLB",
            provider=registry.PROVIDER,
            league="MLB",
            regime=registry.REGULAR_SEASON,
            sport_id=3,
        ),
    )


@pytest.mark.parametrize(
    ("season", "regime"),
    [
        ({"type": 3, "slug": "postseason"}, registry.PLAYOFFS),
        ({"type": 2, "slug": "regular-season"}, registry.REGULAR_SEASON),
        ({"type": 1, "slug": "preseason"}, registry.PRESEASON),
        ({}, "UNKNOWN"),
        ({"type": 999, "slug": "unclassified"}, "UNKNOWN"),
    ],
)
def test_espn_secondary_regime_never_inherits_regular_target(season, regime):
    event = _discovered(season)
    assert event.regime == regime
    assert event.provider_sport_id == 3
    assert event.official_event_id == "espn-849831"  # Alias, NOT official MLB ID
    regular_only = SimpleNamespace(supported_regimes=(registry.REGULAR_SEASON,))
    assert discovery.model_supports_regime(event, regular_only) is (
        regime == registry.REGULAR_SEASON
    )
    assert discovery.model_supports_regime(event, SimpleNamespace()) is (
        regime == registry.REGULAR_SEASON
    )
    assert discovery.CAN_EXECUTE is False


def test_verified_primary_provider_regime_still_owns_classification():
    target = discovery.DiscoveryTarget(
        family="MLB",
        provider=registry.PROVIDER,
        league="MLB",
        regime=registry.PLAYOFFS,
        sport_id=31,
    )
    event = discovery.normalize_discovered_event(
        {
            "id": "provider-31-123",
            "commence_time": "2026-10-11T00:00:00Z",
            "home_team": "Cleveland Guardians",
            "away_team": "Chicago White Sox",
            "season_phase": "REGULAR_SEASON",  # Untrusted unsourced field.
        },
        sport="MLB",
        sport_key="31",
        source="DISCOVERY_FEED",
        target=target,
    )
    assert event.regime == registry.PLAYOFFS
    assert not discovery.model_supports_regime(
        event, SimpleNamespace(supported_regimes=(registry.REGULAR_SEASON,))
    )


@pytest.mark.parametrize("target_regime", [registry.REGULAR_SEASON, registry.PLAYOFFS])
def test_espn_incomplete_provenance_cannot_inherit_provider_regime(target_regime):
    target = discovery.DiscoveryTarget(
        family="MLB",
        provider=registry.PROVIDER,
        league="MLB",
        regime=target_regime,
        sport_id=31 if target_regime == registry.PLAYOFFS else 3,
    )
    event = discovery.normalize_discovered_event(
        {
            "id": "espn-849831",
            "commence_time": "2026-10-11T00:00:00Z",
            "_wow_secondary_source": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
            # Missing season_phase_source means a claimed regular season is not trusted.
            "season_phase": "REGULAR_SEASON",
        },
        sport="MLB",
        sport_key="31",
        source="DISCOVERY_FEED",
        target=target,
    )
    assert event.regime == "UNKNOWN"
    assert not discovery.model_supports_regime(
        event, SimpleNamespace(supported_regimes=(target_regime,))
    )


def test_postseason_espn_fallback_requires_matching_postseason_model_regime():
    event = _discovered({"type": 3, "slug": "postseason"})
    assert not discovery.model_supports_regime(
        event, SimpleNamespace(supported_regimes=(registry.REGULAR_SEASON,))
    )
    assert discovery.model_supports_regime(
        event, SimpleNamespace(supported_regimes=(registry.PLAYOFFS,))
    )
    # This checks only route eligibility, NEVER fitted or certified capability.


def test_2026_world_series_game1_and_game2_are_not_regular_season():
    """Reproduce Oct 23 2026 MLB Daily aliases without granting official identity."""
    from datetime import datetime, timezone

    from v17 import quota_aware_degraded_discovery as quota

    def candidate(utc_start):
        raw_event = _espn_event({"type": 3, "slug": "postseason"})
        raw_event["date"] = utc_start
        raw_event["competitions"][0]["competitors"][0]["team"]["displayName"] = "Milwaukee Brewers"
        raw_event["competitions"][0]["competitors"][1]["team"]["displayName"] = "Tampa Bay Rays"
        public_row = secondary.espn_event_to_primary_shape(raw_event, "baseball_mlb")
        assert public_row["season_phase"] == "POSTSEASON"
        event = discovery.normalize_discovered_event(
            public_row,
            sport="MLB",
            sport_key="3",
            source="DISCOVERY_FEED",
            target=discovery.DiscoveryTarget(
                family="MLB", provider=registry.PROVIDER,
                league="MLB", regime=registry.REGULAR_SEASON, sport_id=3,
            ),
            now=datetime(2026, 10, 23, 12, tzinfo=timezone.utc),
        )
        return quota._normalize_public_alias(
            {"id": public_row["id"], "discovery_provider": "ESPN_SCOREBOARD"},
            event,
        )

    game1 = candidate("2026-10-24T00:00:00Z")  # Oct 23 at 7 p.m. Central
    game2 = candidate("2026-10-25T00:00:00Z")  # Oct 24 at 7 p.m. Central

    assert game1.regime == game2.regime == registry.PLAYOFFS
    assert game1.home_team == "Milwaukee Brewers"
    assert game1.away_team == "Tampa Bay Rays"
    assert game1.official_event_id is None  # ESPN alias is never official MLB identity
    assert not discovery.model_supports_regime(
        game1, SimpleNamespace(supported_regimes=(registry.REGULAR_SEASON,))
    )
    assert discovery._purge_bucket(
        game1, slate_date="2026-10-23", tz_name="America/Chicago"
    ) == discovery.IDENTITY_UNRESOLVED
    assert discovery._purge_bucket(
        game2, slate_date="2026-10-23", tz_name="America/Chicago"
    ) == discovery.WRONG_DATE
    assert not game1.raw.get("prediction_authority", False)
    assert discovery.CAN_EXECUTE is False
