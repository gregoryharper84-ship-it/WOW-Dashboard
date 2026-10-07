from __future__ import annotations

from v17 import fallback_provider_health as odds_health
from v17 import rundown_market_ingestor as ingestor
from v17 import rundown_provider_health as rundown_health


def test_free_core_suppresses_odds_api_health_network(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "FREE_CORE")
    monkeypatch.setenv("ODDS_API_PAID_KEY", "test-only")
    out = odds_health.probe_odds_api_health(
        opener=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Odds API health network must be suppressed")
        )
    )
    assert out["status"] == "DISABLED_BY_POLICY"
    assert out["provider_detail"] == "PAID_PROVIDER_DISABLED_FREE_CORE"
    assert out["paid_provider_network_attempted"] is False
    assert out["fallback_eligible"] is False
    assert out["can_execute"] is False


def test_free_core_suppresses_rundown_health_network(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "FREE_CORE")
    monkeypatch.setenv("THERUNDOWN_API_KEY", "test-only")
    out = rundown_health.probe_rundown_provider_health(
        date="2026-10-07",
        opener=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Rundown health network must be suppressed")
        ),
    )
    assert out["status"] == "DISABLED_BY_POLICY"
    assert out["provider_code"] == "PAID_PROVIDER_DISABLED_FREE_CORE"
    assert out["paid_provider_network_attempted"] is False
    assert out["affects_model_capability"] is False
    assert out["can_execute"] is False


def test_free_core_suppresses_rundown_collector_transport(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "FREE_CORE")
    monkeypatch.setenv("THERUNDOWN_API_KEY", "test-only")
    result = ingestor._request_json(
        "/api/v2/sports",
        opener=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Rundown collector network must be suppressed")
        ),
    )
    assert result.ok is False
    assert result.code == "PAID_PROVIDER_DISABLED_FREE_CORE"
    assert result.can_execute is False
