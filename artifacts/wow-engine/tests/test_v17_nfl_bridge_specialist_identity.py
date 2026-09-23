from __future__ import annotations

import inspect

from nfl_event_model_contract import CONTROLLING_SPECIALIST as NFL_CONTRACT_SPECIALIST
import v17.team_event_bridge_runtime as bridge_runtime


def test_nfl_bridge_reuses_governed_controlling_specialist_identity():
    source = inspect.getsource(bridge_runtime)

    assert NFL_CONTRACT_SPECIALIST == "wow.nfl-game-win-probability-expert"
    assert bridge_runtime.NFL_CONTROLLING_SPECIALIST == NFL_CONTRACT_SPECIALIST
    assert "NFL_OUTRIGHT_WIN_FITTED_MODEL_V1" not in source
    assert "controlling_specialist=NFL_CONTROLLING_SPECIALIST" in source
    assert bridge_runtime.CAN_EXECUTE is False


def test_registered_nfl_bridge_health_reports_governed_identity(monkeypatch):
    original = dict(bridge_runtime.TEAM_EVENT_BRIDGES)
    bridge_runtime.TEAM_EVENT_BRIDGES.clear()
    try:
        monkeypatch.setenv("WOW_V17_NFL_TEAM_EVENT_BRIDGE", "1")
        assert bridge_runtime._register_nfl_bridge_if_available() is True
        health = bridge_runtime.team_event_bridge_health()["NFL"]
        assert health["registered"] is True
        assert health["controlling_specialist"] == NFL_CONTRACT_SPECIALIST
        assert health["can_execute"] is False
    finally:
        bridge_runtime.TEAM_EVENT_BRIDGES.clear()
        bridge_runtime.TEAM_EVENT_BRIDGES.update(original)
