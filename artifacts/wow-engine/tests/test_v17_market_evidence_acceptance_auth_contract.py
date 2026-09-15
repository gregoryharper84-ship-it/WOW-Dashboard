"""Regression coverage for standalone market-evidence acceptance auth parity."""
from __future__ import annotations

from dataclasses import replace

from v17 import market_evidence_snapshot as snapshot
from v17 import market_evidence_sources as sources


def test_standalone_acceptance_installs_rundown_v2_auth_without_active_runtime(monkeypatch):
    """GitHub probes must use the same TheRundown contract as Render production."""
    original = sources.PROVIDERS["RUNDOWN"]
    stale = replace(
        original,
        auth_style="query",
        auth_name="key",
        endpoints={**original.endpoints, "sports": "/api/v1/sports"},
    )
    monkeypatch.setitem(sources.PROVIDERS, "RUNDOWN", stale)
    monkeypatch.delenv("WOW_V17_ACTIVE", raising=False)
    monkeypatch.delenv("WOW_MARKET_EVIDENCE_KILL_SWITCH", raising=False)

    snapshot._enable_research_market_evidence()

    repaired = sources.PROVIDERS["RUNDOWN"]
    assert repaired.auth_style == "header"
    assert repaired.auth_name == "X-TheRundown-Key"
    assert repaired.endpoints["sports"] == "/api/v2/sports"
    assert sources.ENABLED is True
    assert sources.CAN_EXECUTE is False
