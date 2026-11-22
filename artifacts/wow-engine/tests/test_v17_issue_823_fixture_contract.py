from __future__ import annotations

import json
from pathlib import Path

import pytest

from v17 import cross_sport_winner_discovery as discovery
from v17 import quota_aware_degraded_discovery as quota
from v17.cross_sport_acquisition_fairness import fair_discover_winner_slate

_FIXTURE = Path(__file__).parent / "fixtures" / "issue_823_acquisition_incomplete.json"
_FIXTURE_DATA = json.loads(_FIXTURE.read_text(encoding="utf-8"))
_FORBIDDEN_MODEL_CODES = (
    "MODEL_UNAVAILABLE",
    "MODEL_SCORER_FAILED",
    "MODEL_INPUTS_INSUFFICIENT",
    "MODEL_OUTPUT_INVALID",
)


def test_fixture_invariants_fail_closed() -> None:
    assert _FIXTURE_DATA["can_execute"] is False
    invariants = _FIXTURE_DATA["invariants"]
    assert invariants["silent_row_loss_allowed"] is False
    assert invariants["probability_substitution_allowed"] is False
    assert invariants["terminal_authority"] == "V17_TERMINAL_REDUCER"


@pytest.mark.parametrize(
    "case", _FIXTURE_DATA["cases"], ids=[c["sport"] for c in _FIXTURE_DATA["cases"]]
)
def test_fixture_case_keeps_typed_acquisition_failure(case) -> None:
    family = case["sport"]
    targets = {
        family: (
            discovery.DiscoveryTarget(
                family=family,
                provider="TEST_PROVIDER",
                league=f"{family}_LEAGUE",
                sport_key=f"{family.lower()}_key",
            ),
        )
    }

    def failing_fetch(_family, _target):
        raise discovery.DiscoveryFeedError("HTTP_429")

    inventory = fair_discover_winner_slate(
        requested_slate_date="2026-09-24",
        requested_timezone="America/Chicago",
        fetch_sport_events=failing_fetch,
        supported_sports=(family,),
        discovery_targets=targets,
        budget_seconds=0.0,
    )

    assert inventory.events == []
    assert len(inventory.acquisition_audit) == 1
    assert len(inventory.acquisition_details) == 1
    assert len(inventory.source_blockers) == 1
    assert inventory.acquisition_audit[0]["request_status"] == discovery.PROVIDER_RATE_LIMITED
    assert inventory.acquisition_audit[0]["can_execute"] is False
    assert inventory.source_blockers[0]["status"] == discovery.PROVIDER_RATE_LIMITED
    blob = json.dumps(
        [inventory.acquisition_audit, inventory.source_blockers], default=str
    )
    assert not any(code in blob for code in _FORBIDDEN_MODEL_CODES)


@pytest.mark.parametrize("code", ["DEACTIVATED", "ODDS_API_DEACTIVATED_KEY"])
def test_deactivated_key_classifies_disabled_by_policy_and_trips_circuit(code) -> None:
    state = quota.classify_provider_failure(code)
    assert state == quota.DISABLED_BY_POLICY
    assert state in quota._CIRCUIT_STATES
