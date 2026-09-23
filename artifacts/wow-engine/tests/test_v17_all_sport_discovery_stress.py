from __future__ import annotations

from datetime import datetime, timezone

from v17 import cross_sport_winner_discovery as discovery
from v17 import rundown_sport_registry as registry
from v17.llp_governed_package_scoring import (
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_SCORER_FAILED,
    MODEL_UNAVAILABLE,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS


SLATE_DATE = "2026-09-23"
SLATE_TZ = "UTC"
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _inventory(monkeypatch, *, rows_per_target: int):
    # Exercise the canonical registry rather than any operator-local override.
    monkeypatch.delenv("WOW_RUNDOWN_DISCOVERY_SPORT_IDS_JSON", raising=False)
    targets = discovery.default_discovery_targets(
        EXPECTED_TEAM_EVENT_SPORTS,
        include_regime_variants=True,
    )

    def fetch(family, target=None):
        assert target is not None
        return [
            {
                "id": f"stress-{target.sport_id}-{index}",
                "home_team": f"{family}-HOME-{target.sport_id}-{index}",
                "away_team": f"{family}-AWAY-{target.sport_id}-{index}",
                "commence_time": f"{SLATE_DATE}T18:00:00Z",
                "status": "SCHEDULED",
            }
            for index in range(rows_per_target)
        ]

    inventory = discovery.discover_winner_slate(
        requested_slate_date=SLATE_DATE,
        requested_timezone=SLATE_TZ,
        fetch_sport_events=fetch,
        supported_sports=EXPECTED_TEAM_EVENT_SPORTS,
        discovery_targets=targets,
        now=NOW,
        budget_seconds=30,
    )
    return inventory, targets


def test_full_catalog_regime_stress_retains_every_row_when_models_are_unavailable(monkeypatch):
    rows_per_target = 20
    inventory, targets = _inventory(monkeypatch, rows_per_target=rows_per_target)

    target_count = sum(len(value) for value in targets.values())
    expected_events = target_count * rows_per_target
    assert target_count == len(registry.ALL_SPORTS)
    assert expected_events >= 600
    assert len(inventory.events) == expected_events
    assert inventory.sports_queried == list(EXPECTED_TEAM_EVENT_SPORTS)

    audit = {row["family"]: row for row in inventory.acquisition_audit}
    assert set(audit) == set(EXPECTED_TEAM_EVENT_SPORTS)
    assert audit["BOXING"]["request_status"] == discovery.NO_CONFIGURED_DISCOVERY_FEED
    for family, family_targets in targets.items():
        if not family_targets:
            continue
        assert audit[family]["request_status"] == discovery.EVENTS_RETURNED
        assert audit[family]["events_returned"] == len(family_targets) * rows_per_target
        assert audit[family]["duplicate_rows_suppressed"] == 0

    # Explicitly lock the regime/catalog cases that originally motivated the fix.
    provider_ids = {(event.sport, event.provider_sport_id) for event in inventory.events}
    assert ("CRICKET", 21) in provider_ids
    assert {sport_id for sport, sport_id in provider_ids if sport == "NHL"} == {6, 27, 28}
    assert {sport_id for sport, sport_id in provider_ids if sport == "MLB"} == {3, 30, 31}
    assert {sport_id for sport, sport_id in provider_ids if sport == "NBA"} == {4, 23, 24, 32}

    rows = discovery.route_discovered_slate(
        inventory,
        resolve_model=lambda event: None,
        score_row=lambda event, model: (_ for _ in ()).throw(
            AssertionError("scorer must not run without a resolved fitted model")
        ),
    )
    result = discovery.reconcile(inventory, rows)

    assert len(rows) == expected_events
    assert result["row_reconciliation"] == "PASS"
    assert result["run_status"] == "COMPLETED"
    assert result["events_discovered"] == expected_events
    assert result["events_accounted"] == expected_events
    assert result["retained_unsupported_rows"] == expected_events
    assert result["buckets"][MODEL_UNAVAILABLE] == expected_events
    assert result["can_execute"] is False
    assert all(row.as_dict()["rank_eligible"] is False for row in rows)
    assert all(row.as_dict()["probability_publishable"] is False for row in rows)
    assert all(row.as_dict()["market_probability_substitution_allowed"] is False for row in rows)
    assert all(row.as_dict()["generic_reasoning_substitution_allowed"] is False for row in rows)
    assert all(row.as_dict()["can_execute"] is False for row in rows)


def test_full_catalog_routing_stress_preserves_typed_failures_and_reconciles(monkeypatch):
    inventory, _ = _inventory(monkeypatch, rows_per_target=10)

    class StubModel:
        supported_regimes = {
            registry.REGULAR_SEASON,
            registry.PRESEASON,
            registry.PLAYOFFS,
            registry.SPRING_TRAINING,
            registry.SUMMER_LEAGUE,
        }

    model = StubModel()

    def row_index(event) -> int:
        return int(str(event.official_event_id).rsplit("-", 1)[-1])

    def resolve_model(event):
        index = row_index(event)
        if index % 6 == 0:
            return None
        if index % 6 == 1:
            raise RuntimeError("synthetic registry-resolution failure")
        return model

    typed_codes = (
        MODEL_INPUTS_INSUFFICIENT,
        MODEL_SCORER_FAILED,
        MODEL_OUTPUT_INVALID,
        "MODEL_QUALIFIED_HOLD",
    )

    def score_row(event, resolved_model):
        assert resolved_model is model
        code = typed_codes[row_index(event) % len(typed_codes)]
        return {
            "code": code,
            "model_invoked": True,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }

    rows = discovery.route_discovered_slate(
        inventory,
        resolve_model=resolve_model,
        score_row=score_row,
    )
    result = discovery.reconcile(inventory, rows)

    assert len(rows) == len(inventory.events)
    assert len(rows) >= 300
    assert result["row_reconciliation"] == "PASS"
    assert result["run_status"] == "COMPLETED"
    assert result["events_accounted"] == result["events_discovered"]
    assert result["buckets"][MODEL_UNAVAILABLE] > 0
    assert result["buckets"][MODEL_INPUTS_INSUFFICIENT] > 0
    assert result["buckets"][MODEL_SCORER_FAILED] > 0
    assert result["buckets"][MODEL_OUTPUT_INVALID] > 0
    assert result["buckets"][discovery.OTHER_GOVERNED_HOLD] > 0
    assert result["can_execute"] is False
    assert all(row.as_dict()["market_probability_substitution_allowed"] is False for row in rows)
    assert all(row.as_dict()["generic_reasoning_substitution_allowed"] is False for row in rows)
    assert all(row.as_dict()["can_execute"] is False for row in rows)
