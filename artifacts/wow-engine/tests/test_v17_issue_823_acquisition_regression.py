from __future__ import annotations

from v17 import cross_sport_winner_discovery as discovery
from v17.cross_sport_acquisition_fairness import fair_discover_winner_slate


def test_provider_failure_cannot_starve_later_configured_families() -> None:
    families = ("MLB", "WNBA", "NHL", "SOCCER", "TENNIS", "MMA")
    targets = {
        family: (
            discovery.DiscoveryTarget(
                family=family,
                provider="TEST_PROVIDER",
                league=f"{family}_LEAGUE",
                sport_key=f"{family.lower()}_key",
            ),
        )
        for family in families
    }
    attempts: list[str] = []

    def failing_fetch(family, target):
        attempts.append(family)
        raise discovery.DiscoveryFeedError("HTTP_429")

    inventory = fair_discover_winner_slate(
        requested_slate_date="2026-10-02",
        requested_timezone="America/Chicago",
        fetch_sport_events=failing_fetch,
        supported_sports=families,
        discovery_targets=targets,
        budget_seconds=0.0,
    )

    assert attempts == list(families)
    assert inventory.sports_queried == list(families)
    assert len(inventory.acquisition_audit) == len(families)
    assert len(inventory.acquisition_details) == len(families)
    assert len(inventory.source_blockers) == len(families)
    assert inventory.events == []
    assert all(
        row["request_status"] == discovery.PROVIDER_RATE_LIMITED
        for row in inventory.acquisition_audit
    )
    assert all(
        row["coverage_complete"] is True
        and row["first_pass_attempted"] is True
        and row["can_execute"] is False
        for row in inventory.acquisition_audit
    )
    assert all(
        blocker["status"] == discovery.PROVIDER_RATE_LIMITED
        for blocker in inventory.source_blockers
    )
