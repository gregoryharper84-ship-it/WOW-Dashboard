from __future__ import annotations

import time

from v17 import cross_sport_winner_discovery as discovery
from v17.cross_sport_acquisition_fairness import (
    FAIRNESS_CONTRACT_VERSION,
    fair_discover_winner_slate,
)


def _target(family: str, sport_id: int) -> discovery.DiscoveryTarget:
    return discovery.DiscoveryTarget(
        family=family,
        provider="RUNDOWN",
        league=family,
        sport_id=sport_id,
    )


def test_slow_mlb_cannot_starve_later_configured_sports():
    families = ("MLB", "NFL", "NBA", "WNBA")
    targets = {
        family: (_target(family, index + 1),)
        for index, family in enumerate(families)
    }
    calls: list[str] = []

    def fetch(family, target):
        calls.append(family)
        if family == "MLB":
            # Deliberately spend much more than the nominal family budget.
            time.sleep(0.02)
        return []

    inventory = fair_discover_winner_slate(
        requested_slate_date="2026-09-22",
        requested_timezone="America/Chicago",
        fetch_sport_events=fetch,
        supported_sports=families,
        discovery_targets=targets,
        budget_seconds=0.001,
    )

    assert calls == list(families)
    audit = {row["family"]: row for row in inventory.acquisition_audit}
    for family in families:
        assert audit[family]["first_pass_attempted"] is True
        assert audit[family]["acquisition_opportunity_guaranteed"] is True
        assert audit[family]["coverage_complete"] is True
        assert audit[family]["request_status"] == discovery.NO_EVENTS_RETURNED
        assert audit[family]["fairness_contract_version"] == FAIRNESS_CONTRACT_VERSION
        assert audit[family]["can_execute"] is False


def test_acquisition_opportunity_is_order_independent():
    families = ("MLB", "NFL", "NBA", "WNBA", "NCAAF")
    targets = {
        family: (_target(family, index + 10),)
        for index, family in enumerate(families)
    }

    def run(order):
        calls: list[str] = []

        def fetch(family, target):
            calls.append(family)
            return []

        inventory = fair_discover_winner_slate(
            requested_slate_date="2026-09-22",
            requested_timezone="America/Chicago",
            fetch_sport_events=fetch,
            supported_sports=order,
            discovery_targets=targets,
            budget_seconds=0.0,
        )
        return calls, {row["family"]: row for row in inventory.acquisition_audit}

    forward_calls, forward = run(families)
    reverse_calls, reverse = run(tuple(reversed(families)))

    assert set(forward_calls) == set(families)
    assert set(reverse_calls) == set(families)
    for family in families:
        assert forward[family]["acquisition_opportunity_guaranteed"] is True
        assert reverse[family]["acquisition_opportunity_guaranteed"] is True
        assert forward[family]["request_status"] == reverse[family]["request_status"]
        assert forward[family]["coverage_complete"] is True
        assert reverse[family]["coverage_complete"] is True


def test_family_budget_can_limit_extra_targets_without_starving_next_sport():
    families = ("MLB", "NFL")
    targets = {
        "MLB": (_target("MLB", 1), _target("MLB", 2)),
        "NFL": (_target("NFL", 3),),
    }
    calls: list[tuple[str, int]] = []

    def fetch(family, target):
        calls.append((family, target.sport_id))
        if family == "MLB" and target.sport_id == 1:
            time.sleep(0.02)
        return []

    inventory = fair_discover_winner_slate(
        requested_slate_date="2026-09-22",
        requested_timezone="America/Chicago",
        fetch_sport_events=fetch,
        supported_sports=families,
        discovery_targets=targets,
        budget_seconds=0.001,
    )

    # MLB target 2 is bounded by MLB's own exhausted budget; NFL still gets its
    # guaranteed first attempt rather than inheriting MLB's exhaustion.
    assert calls == [("MLB", 1), ("NFL", 3)]
    audit = {row["family"]: row for row in inventory.acquisition_audit}

    assert audit["MLB"]["first_pass_attempted"] is True
    assert audit["MLB"]["coverage_complete"] is False
    assert audit["MLB"]["targets_remaining_unattempted"] == 1
    assert audit["MLB"]["blocker_if_any"] == discovery.DISCOVERY_BUDGET_EXHAUSTED

    assert audit["NFL"]["first_pass_attempted"] is True
    assert audit["NFL"]["coverage_complete"] is True
    assert audit["NFL"]["targets_remaining_unattempted"] == 0
    assert audit["NFL"]["request_status"] == discovery.NO_EVENTS_RETURNED

    fairness_blockers = [
        row
        for row in inventory.source_blockers
        if row.get("status") == discovery.DISCOVERY_BUDGET_EXHAUSTED
    ]
    assert len(fairness_blockers) == 1
    assert fairness_blockers[0]["sport"] == "MLB"
    assert fairness_blockers[0]["first_pass_attempted"] is True


def test_unconfigured_family_remains_explicit_not_fake_empty_slate():
    inventory = fair_discover_winner_slate(
        requested_slate_date="2026-09-22",
        requested_timezone="America/Chicago",
        fetch_sport_events=lambda family, target: [],
        supported_sports=("BOXING",),
        discovery_targets={"BOXING": ()},
        budget_seconds=0.0,
    )

    row = inventory.acquisition_audit[0]
    assert row["request_status"] == discovery.NO_CONFIGURED_DISCOVERY_FEED
    assert row["acquisition_opportunity_guaranteed"] is False
    assert row["coverage_complete"] is False
    assert row["can_execute"] is False
