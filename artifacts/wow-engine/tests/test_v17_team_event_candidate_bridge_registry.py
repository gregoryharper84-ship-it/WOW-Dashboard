"""Regression: research registration must never activate V17 team/event scoring."""
from types import SimpleNamespace

import pytest

from v17 import team_event_candidate_bridge_registry as candidates
from v17 import team_event_bridge_runtime as production
from v17.team_event_capability_manifest import team_event_capability


@pytest.fixture
def candidate_registry_isolation():
    """Start with no production/candidate routes and restore both on exit."""
    registrations = dict(candidates.CANDIDATE_TEAM_EVENT_BRIDGES)
    failures = dict(candidates.CANDIDATE_BRIDGE_IMPORT_FAILURES)
    production_registrations = dict(production.TEAM_EVENT_BRIDGES)
    try:
        candidates.CANDIDATE_TEAM_EVENT_BRIDGES.clear()
        candidates.CANDIDATE_BRIDGE_IMPORT_FAILURES.clear()
        production.TEAM_EVENT_BRIDGES.clear()
        yield
    finally:
        production.TEAM_EVENT_BRIDGES.clear()
        production.TEAM_EVENT_BRIDGES.update(production_registrations)
        candidates.CANDIDATE_TEAM_EVENT_BRIDGES.clear()
        candidates.CANDIDATE_TEAM_EVENT_BRIDGES.update(registrations)
        candidates.CANDIDATE_BRIDGE_IMPORT_FAILURES.clear()
        candidates.CANDIDATE_BRIDGE_IMPORT_FAILURES.update(failures)


def test_three_candidate_bridges_register_but_never_enter_live_registry(
    monkeypatch, candidate_registry_isolation,
):
    """Three research entries must not populate the live scorer registry."""
    assert production.TEAM_EVENT_BRIDGES == {}
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
    assert production.TEAM_EVENT_BRIDGES == {}
    for sport in ("NBA", "NCAAF", "NCAAB"):
        receipt = candidates.candidate_bridge_health(sport)
        assert receipt["candidate_bridge_registered"] is True
        assert receipt["candidate_bridge_status"] == "REGISTERED_RESEARCH_ONLY"
        assert receipt["candidate_bridge_live_routable"] is False
        assert sport not in production.TEAM_EVENT_BRIDGES
        assert production.team_event_bridge_health()[sport]["registered"] is False
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
    """Unavailable research entrypoints cannot change production routing."""
    assert production.TEAM_EVENT_BRIDGES == {}
    monkeypatch.setattr(candidates, "import_module", lambda _name: SimpleNamespace())
    outcome = candidates.install_candidate_team_event_bridges()
    assert outcome["candidate_bridges_registered"] == []
    assert outcome["candidate_bridges_unavailable"] == ["NBA", "NCAAB", "NCAAF"]
    assert production.TEAM_EVENT_BRIDGES == {}
    for sport in ("NBA", "NCAAF", "NCAAB"):
        health = candidates.candidate_bridge_health(sport)
        assert health["candidate_bridge_registered"] is False
        assert health["candidate_bridge_status"] == "CANDIDATE_ENTRYPOINT_UNAVAILABLE"
        assert health["candidate_bridge_import_failure_type"] == "AttributeError"
        assert health["candidate_bridge_live_routable"] is False


def test_unknown_sports_and_aliases_do_not_promote_capability(
    monkeypatch, candidate_registry_isolation,
):
    """College sport aliases resolve to shadow-only candidate state."""
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
    assert all(
        sport not in production.TEAM_EVENT_BRIDGES
        for sport in ("NBA", "NCAAF", "NCAAB")
    )


def test_real_candidate_entrypoints_are_importable(candidate_registry_isolation):
    """Verify existing research implementations rather than mocked names."""
    result = candidates.install_candidate_team_event_bridges()
    assert result["candidate_bridges_unavailable"] == [], result
    assert result["candidate_bridges_registered"] == ["NBA", "NCAAB", "NCAAF"]
    assert production.TEAM_EVENT_BRIDGES == {}
    assert all(
        candidates.candidate_bridge_health(sport)["candidate_bridge_live_routable"] is False
        for sport in ("NBA", "NCAAF", "NCAAB")
    )


def test_production_bootstrap_wires_candidate_registry_without_live_route(
    monkeypatch, candidate_registry_isolation,
):
    """Production startup must invoke the shadow installer, not add live scorers."""
    calls = []
    monkeypatch.setattr(production, "_INSTALLED", False)
    monkeypatch.setattr(
        production, "register_team_event_bridge",
        lambda sport, **_kwargs: calls.append(("production", sport)),
    )
    monkeypatch.setattr(
        production, "_register_nfl_bridge_if_available",
        lambda: calls.append(("nfl", "checked")),
    )
    monkeypatch.setattr(
        production, "install_candidate_team_event_bridges",
        lambda: calls.append(("candidate", "research_only")),
    )
    monkeypatch.setattr(production, "_install_health_overlay", lambda: None)
    monkeypatch.setattr(production, "team_event_bridge_health", lambda: {})
    monkeypatch.setattr(production._base_runtime, "score_team_event_request", lambda *_a, **_k: {})

    result = production.install_team_event_bridge_runtime()

    assert result["status"] == "INSTALLED"
    assert calls == [
        ("production", "MLB"),
        ("nfl", "checked"),
        ("candidate", "research_only"),
    ]
    assert production.TEAM_EVENT_BRIDGES == {}
    assert result["can_execute"] is False
