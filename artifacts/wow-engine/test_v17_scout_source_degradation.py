"""Zero prop candidates must never be silent.

When the proxy answers 200 with core markets only, the run previously recorded
no blocker, so an entitlement failure and a genuinely prop-free slate produced
identical handoffs.
"""
from v17 import nightly_multiscout as scout


def test_degradation_code_is_read_from_an_otherwise_successful_body():
    degraded = {"id": "evt", "bookmakers": [], "wow_source_degradation": "MARKET_INVENTORY_CORE_MARKETS_ONLY_FALLBACK"}
    assert scout.source_degradation(degraded) == "MARKET_INVENTORY_CORE_MARKETS_ONLY_FALLBACK"


def test_clean_body_reports_no_degradation():
    assert scout.source_degradation({"id": "evt", "bookmakers": []}) is None
    assert scout.source_degradation([{"key": "batter_hits"}]) is None
    assert scout.source_degradation(None) is None


def test_core_only_inventory_yields_no_prop_markets():
    """The mechanism the blocker exists to make visible."""
    core_only = {"bookmakers": [{"key": "book", "markets": [
        {"key": "h2h"}, {"key": "spreads"}, {"key": "totals"},
    ]}]}
    keys = scout.market_keys_from_inventory(core_only)
    assert keys == ["h2h", "spreads", "totals"]
    assert [k for k in keys if scout.is_prop_market(k)] == []


def test_prop_markets_are_recognised_when_inventory_is_not_degraded():
    full = {"bookmakers": [{"key": "book", "markets": [
        {"key": "h2h"}, {"key": "batter_hits"}, {"key": "pitcher_strikeouts"},
    ]}]}
    keys = scout.market_keys_from_inventory(full)
    assert [k for k in keys if scout.is_prop_market(k)] == ["batter_hits", "pitcher_strikeouts"]


def test_run_records_a_blocker_when_inventory_is_degraded(monkeypatch):
    def fake_proxy_get(path, params=None):
        if path == "/odds-api/v4/sports":
            return scout.FetchResult(True, [{"key": "baseball_mlb", "title": "MLB", "active": True}], 200)
        if path.endswith("/events"):
            return scout.FetchResult(True, [{"id": "evt-1", "home_team": "H", "away_team": "A"}], 200)
        if path.endswith("/markets"):
            return scout.FetchResult(True, {
                "bookmakers": [{"key": "book", "markets": [{"key": "h2h"}]}],
                "wow_source_degradation": "MARKET_INVENTORY_CORE_MARKETS_ONLY_FALLBACK",
            }, 200)
        return scout.FetchResult(True, {"bookmakers": []}, 200)

    monkeypatch.setattr(scout, "proxy_get", fake_proxy_get)
    result = scout.run()

    blockers = [b for b in result["source_blockers"] if b.get("scope") == "event_market_inventory"]
    assert len(blockers) == 1
    assert blockers[0]["reason_code"] == "MARKET_INVENTORY_CORE_MARKETS_ONLY_FALLBACK"
    assert blockers[0]["prop_markets_unavailable"] is True
    assert blockers[0]["status"] == "MARKET_INVENTORY_DEGRADED"

    # The run stays honest about being incomplete rather than clean-and-empty.
    assert result["status"] == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"
    assert result["model_handoff"]["prop_candidates"] == []
    assert result["can_execute"] is False


def test_undegraded_run_records_no_degradation_blocker(monkeypatch):
    def fake_proxy_get(path, params=None):
        if path == "/odds-api/v4/sports":
            return scout.FetchResult(True, [{"key": "baseball_mlb", "title": "MLB", "active": True}], 200)
        if path.endswith("/events"):
            return scout.FetchResult(True, [{"id": "evt-1", "home_team": "H", "away_team": "A"}], 200)
        if path.endswith("/markets"):
            return scout.FetchResult(True, {"bookmakers": [{"key": "book", "markets": [
                {"key": "h2h"}, {"key": "batter_hits"},
            ]}]}, 200)
        return scout.FetchResult(True, {"bookmakers": [{"key": "book", "title": "Book", "markets": [
            {"key": "batter_hits", "outcomes": [{"name": "Over", "description": "Player", "price": -110, "point": 0.5}]},
        ]}]}, 200)

    monkeypatch.setattr(scout, "proxy_get", fake_proxy_get)
    result = scout.run()

    assert [b for b in result["source_blockers"] if b.get("scope") == "event_market_inventory"] == []
    assert result["status"] == "DISCOVERY_COMPLETE"
    assert len(result["model_handoff"]["prop_candidates"]) == 1
    assert result["model_handoff"]["prop_candidates"][0]["research_ceiling"] == "RESEARCH_INTEREST"
