from __future__ import annotations

from v17 import market_evidence_native_live as live
from v17 import market_evidence_sources as sources


def test_free_core_blocks_rundown_before_sport_registry_or_network(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "FREE_CORE")
    monkeypatch.setattr(
        sources,
        "rundown_sport_id",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Rundown registry must not be called in FREE_CORE")
        ),
    )
    out = live.get_sport_date_odds_snapshot(
        "baseball_mlb",
        "2026-10-07",
        capability="events",
    )
    assert out.ok is False
    assert out.code == live.source_policy.BLOCK_FREE_CORE
    assert out.request_audit["paid_provider_network_attempted"] is False
    assert out.prediction_authority is False
    assert out.can_execute is False


def test_free_core_blocks_sharpapi_before_provider_lookup_or_network(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "FREE_CORE")
    monkeypatch.setattr(
        sources,
        "sharpapi_league",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("SharpAPI mapping must not be called in FREE_CORE")
        ),
    )
    out = live.sharpapi_market_evidence("baseball_mlb")
    assert out.ok is False
    assert out.code == live.source_policy.BLOCK_FREE_CORE
    assert out.request_audit["paid_provider_network_attempted"] is False
    assert out.research_only is True
    assert out.can_execute is False
