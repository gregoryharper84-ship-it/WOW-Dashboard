from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import v17
from v17 import mlb_event_bridge_repair as mlb_repair
from v17 import team_event_bridge_runtime as bridge_runtime


def test_deferred_mlb_repair_restores_authoritative_cross_sport_health(monkeypatch):
    """The MLB repair may patch scoring, but it cannot remain global /health owner."""
    app = FastAPI()
    calls: list[str] = []

    def _install_repair(*, market_api, team_event_module):
        calls.append("mlb_repair")
        return True

    def _restore_authoritative_health():
        calls.append("cross_sport_health")

    monkeypatch.setattr(mlb_repair, "install_mlb_event_bridge_repair", _install_repair)
    monkeypatch.setattr(bridge_runtime, "_install_health_overlay", _restore_authoritative_health)
    monkeypatch.delenv("WOW_V17_MLB_BRIDGE_SELF_ACCEPTANCE", raising=False)

    market_api = SimpleNamespace(
        app=app,
        prod=SimpleNamespace(event_api=object()),
    )
    team_runtime = SimpleNamespace(_run_mlb_llp_governance=object())

    assert v17._defer_mlb_event_bridge_install(
        market_api=market_api,
        team_runtime=team_runtime,
    ) is True

    with TestClient(app):
        pass

    assert calls == ["mlb_repair", "cross_sport_health"]
    assert app.state.v17_mlb_event_bridge_deferred is True
