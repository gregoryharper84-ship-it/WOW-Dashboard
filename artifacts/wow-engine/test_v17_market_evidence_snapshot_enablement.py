from __future__ import annotations

import os

from v17 import market_evidence_snapshot as snapshot
from v17 import market_evidence_sources as sources


def test_snapshot_enables_research_evidence_despite_stale_disabled_env(monkeypatch):
    original_rundown = sources.PROVIDERS["RUNDOWN"]
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_ENABLED", "false")
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_KILL_SWITCH", "false")
    sources.ENABLED = False

    try:
        snapshot._enable_research_market_evidence()

        assert sources.ENABLED is True
        assert os.environ["WOW_MARKET_EVIDENCE_ENABLED"] == "true"
    finally:
        sources.PROVIDERS["RUNDOWN"] = original_rundown


def test_snapshot_respects_dedicated_market_evidence_kill_switch(monkeypatch):
    original_rundown = sources.PROVIDERS["RUNDOWN"]
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_KILL_SWITCH", "true")
    sources.ENABLED = True

    try:
        snapshot._enable_research_market_evidence()

        assert sources.ENABLED is False
        assert os.environ["WOW_MARKET_EVIDENCE_ENABLED"] == "false"
    finally:
        sources.PROVIDERS["RUNDOWN"] = original_rundown
