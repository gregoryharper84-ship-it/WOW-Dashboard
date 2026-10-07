from __future__ import annotations

from v17 import market_evidence_scout_bridge as scout_bridge
from v17 import market_evidence_snapshot as snapshot


def test_free_core_market_acceptance_requires_no_paid_capture(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "FREE_CORE")
    out = snapshot.collect_acceptance(
        "baseball_mlb",
        date="2026-10-07",
        opener=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("paid provider must not be called")
        ),
    )
    assert out["status"] == "FREE_CORE_MARKET_OPTIONAL_READY"
    assert out["market_evidence_required_for_model"] is False
    assert out["paid_provider_network_attempted"] is False
    assert out["captured_rows"] == 0
    assert snapshot.acceptance_blockers(out) == []
    assert out["can_execute"] is False


def test_free_core_daily_market_snapshot_has_zero_paid_lanes(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "FREE_CORE")
    out = snapshot.collect(
        ["baseball_mlb", "basketball_nba"],
        dates=["2026-10-07"],
        opener=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("paid provider must not be called")
        ),
    )
    assert out["status"] == "FREE_CORE_MARKET_OPTIONAL_READY"
    assert out["lanes"] == []
    assert out["events"] == []
    assert out["reconciliation"]["balanced"] is True
    assert out["paid_provider_network_attempted"] is False
    assert out["can_execute"] is False


def test_free_core_scout_market_bridge_does_not_reuse_paid_snapshot(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "FREE_CORE")
    monkeypatch.setattr(
        scout_bridge,
        "_snapshot_events",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("paid snapshot cache must not be reused in FREE_CORE")
        ),
    )
    rows, codes = scout_bridge._collect("baseball_mlb")
    assert rows == []
    assert codes == ["SOURCE_POLICY:PAID_PROVIDER_DISABLED_FREE_CORE"]



def test_free_core_default_snapshot_cli_exits_success(monkeypatch, tmp_path):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "FREE_CORE")
    output = tmp_path / "market-evidence.json"
    code = snapshot.main([
        "--output", str(output),
        "--sports", "baseball_mlb,basketball_nba",
    ])
    assert code == 0
    assert "FREE_CORE_MARKET_OPTIONAL_READY" in output.read_text()
