"""Paid TheRundown winner calls must be narrow or absent (local unit tests)."""
from __future__ import annotations

from v17 import llp_rundown_market_bridge as bridge
from v17 import rundown_ml_board as board
from v17 import rundown_paid_scope as scope
from v17 import market_evidence_sources as sources


class _Request:
    sport = "MLB"
    league = "MLB"
    requested_slate_date = "2026-10-14"
    home_team = "Home"
    away_team = "Away"
    market_input = None


def test_unresolved_scope_never_calls_paid_llp_or_board(monkeypatch):
    monkeypatch.delenv("WOW_RUNDOWN_WINNER_MARKET_IDS", raising=False)
    monkeypatch.delenv("WOW_RUNDOWN_WINNER_AFFILIATE_IDS", raising=False)
    monkeypatch.setattr(sources, "_rundown_market_id_map", lambda: {})
    monkeypatch.setattr(bridge.live, "get_sport_date_odds_snapshot", lambda *a, **k: (_ for _ in ()).throw(AssertionError("paid LLP request")))
    monkeypatch.setattr(board.live, "get_sport_date_odds_snapshot", lambda *a, **k: (_ for _ in ()).throw(AssertionError("paid board request")))
    context = bridge.resolve_rundown_market_context(_Request())
    assert context["status"] == "MARKET_DATA_UNOBTAINABLE"
    assert context["reason_code"] == scope.SCOPE_BLOCKER
    assert context["can_execute"] is False
    rows, codes = board._collect_rundown("baseball_mlb")
    assert rows == []
    assert codes == [scope.SCOPE_BLOCKER]


def test_missing_affiliates_block_even_with_known_market(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_WINNER_MARKET_IDS", "1")
    monkeypatch.delenv("WOW_RUNDOWN_WINNER_AFFILIATE_IDS", raising=False)
    assert scope.winner_paid_scope() is None


def test_non_numeric_scope_never_broadens_request(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_WINNER_MARKET_IDS", "h2h")
    monkeypatch.setenv("WOW_RUNDOWN_WINNER_AFFILIATE_IDS", "2")
    assert scope.winner_paid_scope() is None
    monkeypatch.setenv("WOW_RUNDOWN_WINNER_MARKET_IDS", "1")
    monkeypatch.setenv("WOW_RUNDOWN_WINNER_AFFILIATE_IDS", "2,")
    assert scope.winner_paid_scope() is None


def test_verified_scope_is_forwarded_to_both_paths(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_WINNER_MARKET_IDS", "1")
    monkeypatch.setenv("WOW_RUNDOWN_WINNER_AFFILIATE_IDS", "2,3")
    monkeypatch.setattr(board, "snapshot_dates", lambda: ["2026-10-14"])
    observed = []
    def snapshot(*args, **kwargs):
        observed.append(kwargs)
        return sources.MarketEvidenceResult(False, "RUNDOWN", "events", code="PROVIDER_REQUEST_FAILED")
    monkeypatch.setattr(bridge.live, "get_sport_date_odds_snapshot", snapshot)
    monkeypatch.setattr(bridge.source_policy, "check_paid_call", lambda *a, **k: (None, False, "DISCOVERY_BUDGET_EXHAUSTED"))
    bridge.resolve_rundown_market_context(_Request())
    board._collect_rundown("baseball_mlb")
    assert len(observed) == 2
    for kwargs in observed:
        assert kwargs["market_ids"] == ("1",)
        assert kwargs["affiliate_ids"] == ("2", "3")
        assert kwargs["main_line"] is True
        assert kwargs["hide_closed"] is True
