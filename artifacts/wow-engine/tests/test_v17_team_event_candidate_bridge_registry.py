"""Regression: research registration must never activate V17 team/event scoring."""
from types import SimpleNamespace

import pytest

from v17 import team_event_candidate_bridge_registry as candidates
from v17 import team_event_bridge_runtime as production
from v17.team_event_capability_manifest import team_event_capability


@pytest.fixture
def candidate_registry_isolation():
    registrations = dict(candidates.CANDIDATE_TEAM_EVENT_BRIDGES)
    failures = dict(candidates.CANDIDATE_BRIDGE_IMPORT_FAILURES)
    try:
        candidates.CANDIDATE_TEAM_EVENT_BRIDGES.clear()
        candidates.CANDIDATE_BRIDGE_IMPORT_FAILURES.clear()
        yield
    finally:
        candidates.CANDIDATE_TEAM_EVENT_BRIDGES.clear()
        candidates.CANDIDATE_TEAM_EVENT_BRIDGES.update(registrations)
        candidates.CANDIDATE_BRIDGE_IMPORT_FAILURES.clear()
        candidates.CANDIDATE_BRIDGE_IMPORT_FAILURES.update(failures)


def test_three_candidate_bridges_register_but_never_enter_live_registry(
    monkeypatch, candidate_registry_isolation,
):
    live_before = dict(production.TEAM_EVENT_BRIDGES)
    monkeypatch.setattr(
        candidates,
        "import_module",
        lambda _name: SimpleNamespace(
            prepare_nba_current_pregame_scorer_receipt=lambda **_kw: None,
            score_current_event_snapshot=lambda **_kw: None,
            verify_frozen_replay=lambda **_kw: None,
        ),
    )
    outcome = candidates.install_candidate_team_event_bridges()
    assert outcome["candidate_bridges_registered"] == ["NBA", "NCAAB", "NCAAF"]
    assert outcome["production_route_changed"] is False
    assert production.TEAM_EVENT_BRIDGES == live_before
    for sport in ("NBA", "NCAAF", "NCAAB"):
        receipt = candidates.candidate_bridge_health(sport)
        assert receipt["candidate_bridge_registered"] is True
        assert receipt["candidate_bridge_status"] == "REGISTERED_RESEARCH_ONLY"
        assert receipt["candidate_bridge_live_routable"] is False
        assert production.team_event_bridge_health()[sport]["registered"] is (
            sport in live_before
        )
        capability = team_event_capability(sport)
        assert capability.status == "MODEL_UNAVAILABLE"
        assert capability.can_execute is False
        registration = candidates.CANDIDATE_TEAM_EVENT_BRIDGES[sport]
        assert registration.probability_publishable is False
        assert registration.rank_eligible is False
        assert registration.can_execute is False


def test_missing_candidate_entrypoint_fails_closed_without_touching_live_router(
    monkeypatch, candidate_registry_isolation,
):
    live_before = dict(production.TEAM_EVENT_BRIDGES)
    monkeypatch.setattr(candidates, "import_module", lambda _name: SimpleNamespace())
    outcome = candidates.install_candidate_team_event_bridges()
    assert outcome["candidate_bridges_registered"] == []
    assert outcome["candidate_bridges_unavailable"] == ["NBA", "NCAAB", "NCAAF"]
    assert production.TEAM_EVENT_BRIDGES == live_before
    for sport in ("NBA", "NCAAF", "NCAAB"):
        health = candidates.candidate_bridge_health(sport)
        assert health["candidate_bridge_registered"] is False
        assert health["candidate_bridge_status"] == "CANDIDATE_ENTRYPOINT_UNAVAILABLE"
        assert health["candidate_bridge_import_failure_type"] == "AttributeError"
        assert health["candidate_bridge_live_routable"] is False


def test_unknown_sports_and_aliases_do_not_promote_capability(
    monkeypatch, candidate_registry_isolation,
):
    monkeypatch.setattr(
        candidates, "import_module",
        lambda _name: SimpleNamespace(
            prepare_nba_current_pregame_scorer_receipt=lambda: None,
            score_current_event_snapshot=lambda: None,
            verify_frozen_replay=lambda: None,
        ),
    )
    candidates.install_candidate_team_event_bridges()
    assert candidates.candidate_bridge_health("CBB")["candidate_bridge_registered"] is True
    assert candidates.candidate_bridge_health("CFB")["candidate_bridge_registered"] is True
    assert candidates.candidate_bridge_health("MLB")["candidate_bridge_registered"] is False
    assert candidates.candidate_bridge_health("MLB")["candidate_bridge_status"] == "NOT_APPLICABLE"
    assert production.TEAM_EVENT_BRIDGES.get("NBA") is None or (
        production.TEAM_EVENT_BRIDGES["NBA"].scorer is not None
    )
