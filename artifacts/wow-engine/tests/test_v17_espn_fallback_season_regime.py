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


def test_espn_incomplete_provenance_cannot_override_verified_provider_target():
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
            "_wow_secondary_source": "ESPN_SCOREBOARD_RESEARCH_FALLBACK",
            # Missing provenance season_phase_source: no trusted ESPN phase.
            "season_phase": "REGULAR_SEASON",
        },
        sport="MLB",
        sport_key="31",
        source="DISCOVERY_FEED",
        target=target,
    )
    assert event.regime == registry.PLAYOFFS


def test_postseason_espn_fallback_requires_matching_postseason_model_regime():
    event = _discovered({"type": 3, "slug": "postseason"})
    assert not discovery.model_supports_regime(
        event, SimpleNamespace(supported_regimes=(registry.REGULAR_SEASON,))
    )
    assert discovery.model_supports_regime(
        event, SimpleNamespace(supported_regimes=(registry.PLAYOFFS,))
    )
    # This checks only route eligibility, NEVER fitted or certified capability.
